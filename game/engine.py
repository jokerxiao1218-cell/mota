"""引擎层:pygame 主循环 + 键盘输入 + 移动结算 + 渲染调度。

架构约束(多智能体并行开发的关键,设计文档 §4.4):
core(规则层)和 events(事件层)由另外两个智能体同时开发、现在还不存在,
所以本文件【绝不 import game.core / game.events】,一切调用走"适配器注入"——
engine.py 只定义两个最小接口 RulesAdapter / EventsAdapter(方法签名 = §4.4-B/C),
引擎通过构造参数收到适配器实例;真实模块由 main.py 延迟 import 后拼装注入。
测试用 Fake 适配器喂固定返回值,这就是"模拟测试"。

移动结算顺序(设计文档 §4.4-D):
a. 目标格是叠放栈,任一层不可通行就阻挡,只跟最上层(stack[-1])交互:
   墙→不动;黄/蓝/红门→查对应钥匙;墙门(1006)→一撞就开;
   监狱门(1004)/怪物门(1005)→只挡路,等事件/守卫;
b. 撞怪:calc_battle 预判,打不过→移动拒绝;打得过→扣血杀怪、弹栈、金币+;
c. 道具:apply_pickup 后弹栈;
d. 楼梯:landing_resolver 换层(落点规则见 default_landing_resolver);
e. NPC 格/踩格触发器/祭坛 → 调注入的 events 适配器;
f. 每步后:adjacent_damage(巫师魔伤)/ guard_trap(警卫夹击)。
"""

import copy

import pygame

from game import assets as assets_mod
from game import ui

GRID = 11                 # 地图 11×11
FPS = 60

# 方向键 / WASD 都能走格子
KEY_DIRS = {
    pygame.K_UP: (0, -1), pygame.K_w: (0, -1),
    pygame.K_DOWN: (0, 1), pygame.K_s: (0, 1),
    pygame.K_LEFT: (-1, 0), pygame.K_a: (-1, 0),
    pygame.K_RIGHT: (1, 0), pygame.K_d: (1, 0),
}
CONFIRM_KEYS = (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER)

# 门 id → (state 里钥匙名, 中文名);只有这三种是钥匙门
KEY_DOORS = {1001: ("yellow", "黄"), 1002: ("blue", "蓝"), 1003: ("red", "红")}


# ================================================================ 适配器契约
# 下面两个类是"接口说明书":真实实现不强制继承,但方法名和参数必须对上。
# 集成阶段(main.py)负责把 game.core / game.events 包成这两个接口的实例。


class RulesAdapter:
    """规则层适配器:签名 = 设计文档 §4.4-B(引擎只用到其中这些)。

    - new_state() -> dict               开局状态(序章:1000/100/100+神圣剑盾,
                                        出生 1 层 (5,10);3 层被夺后 400/10/10
                                        由事件改写)
    - calc_battle(hero, monster, flags) -> {"can_fight":bool, "hero_damage":int,
      "turns":int, "gold":int}          纯函数预判战斗;gold 已含幸运金币倍数
    - apply_pickup(state, item_id, data) -> dict
                                        拾取结算(血瓶宝石×区域、武器盾取最高级、钥匙计数);
                                        item_id 为道具数字 id;data 是 loader.load_all() 的
                                        整个数据大字典(规则层内部要查 data["items"]),
                                        不是单个道具条目——传条目会 KeyError,别传错
    - adjacent_damage(state, floor_doc, x, y) -> int
                                        巫师相邻魔伤合计(持神圣盾=0);floor_doc 是
                                        运行时楼层文档(引擎已把 grid 换成当前实际格子,
                                        规则层要挪巫师位置可直接改 floor_doc["grid"])
    - guard_trap(state, floor_doc, x, y) -> dict
                                        警卫夹击(HP 向上取整减半),原地修改并返回 state
    - save_game(state, slot)            存档(save/save_N.json);失败抛异常(带原因)
    - load_game(slot) -> dict           读档;坏档抛异常(带文件名)
    """

    def new_state(self):
        raise NotImplementedError

    def calc_battle(self, hero, monster, flags):
        raise NotImplementedError

    def apply_pickup(self, state, item_id, item):
        raise NotImplementedError

    def adjacent_damage(self, state, floor_doc, x, y):
        raise NotImplementedError

    def guard_trap(self, state, floor_doc, x, y):
        raise NotImplementedError

    def save_game(self, state, slot):
        raise NotImplementedError

    def load_game(self, slot):
        raise NotImplementedError


class EventsAdapter:
    """事件层适配器:签名按设计文档 §4.4-C 的 intent 协议(batch 6 扩展版)。

    - talk(npc, state, npc_pos=None)   撞 NPC:npc 为 npcs.json 条目(纯 dict);
                              npc_pos=(x,y) 这个 NPC 站哪(小偷走位/离场要用)
    - altar_flow(state)       撞祭坛(4/12/32/46 层商店)
    - start(event, state)     启动事件:event 为 events.json 条目(纯 dict)
    - usable_tools(state) -> [(道具id, 菜单文案)]   背包里能主动使用的道具
    - use_tool(道具id, direction=None) -> {"ok":bool, "msg":str} 或 {"ok":True,
                              "flow":True}(飞行魔杖这类要选目标的,适配器开了
                              flow,引擎随后 _poll_intent 接管对话)
    - enter_floor(楼层号) -> bool   换层钩子:该层有没有挂起事件(pending)要演,
                              True = 开演了,引擎要进对话模式
    - step() -> intent        取当前意图:{"op":"chat","lines":[...]} /
                              {"op":"choices","options":[...]} / {"op":"done"};
                              没在跑事件时返回 None 或 done
                              (sound/effect/pending 由适配器自己消化,引擎不见)
    - feed(response)          chat 喂 None(按键推进);choices 喂 0 起的选择序号
    """

    def talk(self, npc, state, npc_pos=None):
        raise NotImplementedError

    def altar_flow(self, state):
        raise NotImplementedError

    def start(self, event, state):
        raise NotImplementedError

    def usable_tools(self, state):
        raise NotImplementedError

    def use_tool(self, item_id, direction=None):
        raise NotImplementedError

    def enter_floor(self, floor):
        raise NotImplementedError

    def step(self):
        raise NotImplementedError

    def feed(self, response):
        raise NotImplementedError


# ================================================================ 楼梯落点


class LandingError(Exception):
    """换层失败(前面没路/目标层缺落点),引擎捕住后给玩家提示,不崩。"""


def default_landing_resolver(data):
    """造一个默认落点函数(签名:floor, direction -> (新层号, x, y))。

    规则(勘误终审):踩楼梯 → 新层 = 当前层 + stair_links 里对应的 diff;
    上楼落在【新层的 down_stand】,下楼落在【新层的 up_stand】。
    特例:43 层上梯 diff=+2(绕过 44 层异空间),45 层下梯 diff=-2。

    楼层 JSON 还在并行补写 stair_links 时,退回老兜底:
    新层找反向楼梯,再不行找任意楼梯,都没有 → LandingError。
    集成阶段若接线"真实落点",直接传自定义 resolver 覆盖本函数。
    """

    def resolve(floor, direction):
        src = data["floors"].get(str(floor), {})
        links = src.get("stair_links") or {}
        if direction == "up":
            diff = links.get("up_diff", 1)
            stand_key = "down_stand"
        else:
            diff = links.get("down_diff", -1)
            stand_key = "up_stand"

        target = floor + diff
        tgt = data["floors"].get(str(target))
        if tgt is None:
            raise LandingError(
                f"第 {floor} 层向{'上' if direction == 'up' else '下'}没有路(第 {target} 层不存在)")

        tlinks = tgt.get("stair_links") or {}
        stand = tlinks.get(stand_key)
        if stand:
            return target, stand[0], stand[1]

        # ---- 数据还没写 stair_links:老规则兜底 ----
        want = "down" if direction == "up" else "up"
        for s in tgt.get("stairs", []):
            if s["dir"] == want:
                return target, s["x"], s["y"]
        for s in tgt.get("stairs", []):          # 单楼梯层(如序章 f0→f1)
            return target, s["x"], s["y"]
        raise LandingError(f"第 {target} 层没有任何楼梯,落不了地")

    return resolve


# ================================================================ 引擎主体


class Engine:
    """游戏引擎:一个实例 = 一局游戏。测试里直接调 try_move/handle_key,不必开主循环。"""

    def __init__(self, data, rules, events, state,
                 screen=None, landing_resolver=None):
        self.data = data
        self.rules = rules            # RulesAdapter
        self.events = events          # EventsAdapter
        self.state = state            # 纯 dict 状态(§4.4-A)
        self.assets = assets_mod.Assets(data)
        self.screen = screen          # None 时只在 run() 里建窗口;测试可不传
        self.landing_resolver = landing_resolver or default_landing_resolver(data)

        self._runtime = {}            # 层号(str) -> 运行时楼层文档(grid 是可变副本)
        self._weakened = {}           # 怪物id(str) -> 倍率(49 层封印用)

        self.mode = "play"            # play / dialog / menu / manual / gameover / ending
        self.event_active = False     # events 适配器有没有事件在跑
        self.intent = None            # 当前要渲染的 intent(chat/choices)
        self.choice_sel = 0
        self.message = ""            # 底部提示条文本
        self.menu_stack = []          # Esc 菜单栈(支持"存档→选槽位"两级)
        self.manual_rows = []
        self.manual_sel = 0
        self.running = False
        self._last_dir = None         # 最近一次成功移动的方向(镐往面前挖)
        self._event_queue = []        # 事件里又触发事件 → 排队,演完一个再演下一个

        self.floor_doc()              # 开局就把当前层物化进运行时缓存

    # ------------------------------------------------------------ 楼层运行时

    def floor_doc(self, floor=None):
        """拿"运行时楼层文档":结构与楼层 JSON 相同,但 grid 是深拷贝的可变副本,
        并且已经叠加 state['floors_state'] 记录过的改动(读档恢复也靠它)。"""
        fno = self.state["floor"] if floor is None else floor
        key = str(fno)
        if key not in self._runtime:
            src = self.data["floors"][key]
            grid = copy.deepcopy(src["grid"])
            for x, y, stack in self.state.get("floors_state", {}).get(key, []):
                grid[y][x] = copy.deepcopy(stack)
            doc = dict(src)                 # 其余字段共用引用,只有 grid 是自己的
            doc["grid"] = grid
            pl = self.state.get("placements", {}).get(key)
            if pl:
                # batch 6:事件/道具动态改过的 NPC/触发器摆放表(全量快照)。
                # 格子栈的变化走 floors_state、摆放表走 placements,互不掺和
                # (floors_state 只能放 [x, y, stack],塞别的会破坏解包约定)。
                doc["npcs"] = copy.deepcopy(pl.get("npcs", []))
                doc["triggers"] = copy.deepcopy(pl.get("triggers", []))
            self._runtime[key] = doc
        return self._runtime[key]

    def _save_placements(self, fno):
        """把该层当前的 NPC/触发器摆放表全量快照进 state['placements']。

        add/remove_npc、add/remove_trigger、clear_npc_event 只改运行时楼层
        文档;不落进 state 的话读档就丢。存"该层当前全量"最简单:重建
        floor_doc 时整表覆盖,不用回放一串增删。
        """
        doc = self.floor_doc(fno)
        self.state.setdefault("placements", {})[str(fno)] = {
            "npcs": copy.deepcopy(doc.get("npcs", [])),
            "triggers": copy.deepcopy(doc.get("triggers", [])),
        }

    def _record_change(self, fno, x, y):
        """格子栈变了就记进 state['floors_state'](存档=整包快照的靠山)。"""
        key = str(fno)
        stack = self.floor_doc(fno)["grid"][y][x]
        entry = [x, y, copy.deepcopy(stack)]
        lst = self.state.setdefault("floors_state", {}).setdefault(key, [])
        for ent in lst:
            if ent[0] == x and ent[1] == y:     # 同一格只留最新
                ent[2] = entry[2]
                return
        lst.append(entry)

    def _pop_cell(self, fno, x, y, stack):
        """弹掉栈顶(开门/杀怪/捡东西共用):栈空了就把格子置 None。"""
        if stack:
            stack.pop()
        if not stack:
            self.floor_doc(fno)["grid"][y][x] = None
        self._record_change(fno, x, y)

    def _monster(self, mid):
        """取怪物表条目;被 weaken(49 层封印)过的返回缩水副本,不动原数据。"""
        entry = self.data["monsters"].get(str(mid), {})
        ratio = self._weakened.get(str(mid))
        if ratio is None or not entry:
            return entry
        weakened = dict(entry)
        for field in ("hp", "attack", "defence"):
            if field in weakened:
                weakened[field] = int(weakened[field] * ratio)
        return weakened

    # ------------------------------------------------------------ 走格子

    def _walkable(self, cell):
        """一格可不可通行:查 tiles 表;没登记过的 kind 一律当作不可通行(安全第一)。"""
        kind = cell.get("kind")
        meta = self.data["tiles"]["kinds"].get(kind)
        return bool(meta and meta.get("walkable"))

    def _blocked(self, stack):
        """叠放栈里【任何一层】不可通行,整格就算挡路(门下压着道具也进不去)。"""
        return any(not self._walkable(cell) for cell in stack)

    def try_move(self, dx, dy):
        """朝 (dx,dy) 走一格:先判阻挡,阻挡就跟栈顶交互;能走就结算到达效果。"""
        if self.mode != "play":
            return
        hero = self.state["hero"]
        x, y = hero["pos"]
        nx, ny = x + dx, y + dy
        if not (0 <= nx < GRID and 0 <= ny < GRID):
            return
        stack = self.floor_doc()["grid"][ny][nx] or []
        if self._blocked(stack):
            self._interact(nx, ny, stack)
            return
        hero["pos"] = [nx, ny]
        self._last_dir = (dx, dy)                # 记住朝向(镐往面前挖)
        self._arrive(nx, ny)

    def _interact(self, nx, ny, stack):
        """撞上了:只跟最上层交互。"""
        cell = stack[-1]
        if cell.get("hide"):                    # 隐藏格:不可交互(踩楼梯也不换层)
            return
        kind = cell.get("kind")
        if kind == "door":
            self._bump_door(nx, ny, stack)
        elif kind == "monster":
            self._bump_monster(nx, ny, stack)
        elif kind == "npc":
            self._bump_npc(nx, ny)
        elif kind == "altar":
            self._bump_altar()
        # 墙/岩浆/其他:不动,也没动静

    def _bump_door(self, nx, ny, stack):
        """撞门分派(勘误终审):
        1001/1002/1003=钥匙门;1006 墙门=一撞就开;1004/1005=只挡路等事件/守卫。"""
        fno = self.state["floor"]
        cell = stack[-1]
        did = cell.get("id")

        if did == 1006:                        # 墙门:假墙,撞开露出下层
            self._pop_cell(fno, nx, ny, stack)
            self.message = "撞开了一道暗门!"
            return

        if did in KEY_DOORS:                    # 钥匙门
            key_name, cn = KEY_DOORS[did]
            keys = self.state["hero"]["keys"]
            if keys.get(key_name, 0) > 0:
                keys[key_name] -= 1
                self._pop_cell(fno, nx, ny, stack)
                self.message = f"用一把{cn}钥匙打开了{cn}门"
            else:
                self.message = f"需要一把{cn}钥匙"
            return

        # 监狱门(1004,事件生成)/ 怪物门(1005,杀光守卫自动开):引擎只挡路
        name = self.data["tiles"]["doors"].get(str(did), {}).get("name", "门")
        self.message = f"{name}打不开(得想别的办法)"

    def _bump_monster(self, nx, ny, stack):
        """撞怪:预判 → 打得过就结算(扣血/弹栈/金币),打不过原地不动。"""
        hero = self.state["hero"]
        fno = self.state["floor"]
        cell = stack[-1]
        monster = self._monster(cell.get("id"))
        if not monster:
            return
        result = self.rules.calc_battle(hero, monster, self._battle_flags(nx, ny))
        if not result.get("can_fight"):
            self.message = f"打不过 {monster.get('name', '怪物')},先绕开它吧"
            return

        hero["hp"] -= result.get("hero_damage", 0)
        hero["gold"] += result.get("gold", 0)
        self._pop_cell(fno, nx, ny, stack)
        opened = self._note_monster_death(fno, nx, ny)
        self.message = (f"打败了 {monster.get('name')},损血 {result.get('hero_damage', 0)},"
                        f"得 {result.get('gold', 0)} 金币")
        if opened:                               # 杀光一组守卫 → 1005 怪物门自动开
            self.message += ";守卫清空,怪物门开了!"
        if monster.get("event") is not None:    # 杀怪触发的剧情(骷髅队长/魔王等)
            self._start_event(monster["event"])

        if cell.get("id") == 133:               # 真身魔王:胜利结局
            self.mode = "ending"
            self.event_active = False
            self.intent = None
            return

        if hero["hp"] > 0 and not self._blocked(self.floor_doc()["grid"][ny][nx] or []):
            hero["pos"] = [nx, ny]              # 打赢了顺势走进这格(原版行为)
            self._arrive(nx, ny)
        self._check_dead()

    def _npc_entry_at(self, x, y):
        """(x,y) 处摆放的 NPC:拿 npcs.json 条目;若摆放条目被事件清过
        event_talk(clear_npc_event),返回套用覆盖的副本,源数据不动。"""
        for placed in self.floor_doc().get("npcs", []):
            if placed["x"] == x and placed["y"] == y:
                entry = self.data["npcs"].get(str(placed["npc"]))
                if entry is None:
                    return None
                if "event_talk" in placed and placed["event_talk"] is None:
                    return dict(entry, event_talk=None)
                return entry
        return None

    def _bump_npc(self, nx, ny):
        """撞 NPC:查本层摆放表,把 npcs.json 条目交给事件适配器去聊。"""
        entry = self._npc_entry_at(nx, ny)
        if entry:
            self.events.talk(entry, self.state, npc_pos=(nx, ny))
            self._event_started()
            return
        # 摆放表里没有(纯装饰/剧情脚本用的 NPC 格):不响应

    def _bump_altar(self):
        """撞祭坛(4/12/32/46 层商店):交给事件适配器。"""
        self.events.altar_flow(self.state)
        self._event_started()

    def _arrive(self, x, y):
        """走到 (x,y) 后的结算:捡道具 → 楼梯换层 → 踩格触发器/NPC → 特殊机制。"""
        doc = self.floor_doc()
        grid = doc["grid"]
        stack = grid[y][x] or []

        # a) 顶上是道具就捡(可能连着叠几个)
        while stack and stack[-1].get("kind") == "prop" and not stack[-1].get("hide"):
            prop = stack[-1]
            item = self.data["items"].get(str(prop.get("id")), {})
            # 第三参传整个数据大字典(core 内部查 data["items"]),不是道具条目
            self.state = self.rules.apply_pickup(self.state, prop.get("id"), self.data)
            self._pop_cell(self.state["floor"], x, y, stack)
            self.message = f"获得 {item.get('name', '未知道具')}"

        # b) 楼梯:换层(隐藏楼梯踩了不换层)
        if stack and stack[-1].get("kind") == "stair" and not stack[-1].get("hide"):
            self._land(stack[-1].get("dir", "up"))
            return                              # 换层后本层剩余结算不再做

        # c) 踩格触发器
        for trig in doc.get("triggers", []):
            if trig["x"] == x and trig["y"] == y:
                self._start_event(trig["event"])

        # d) NPC 摆放点没画 NPC 格的(数据里有这种接线),走到格子上也算撞见
        entry = self._npc_entry_at(x, y)
        if entry:
            self.events.talk(entry, self.state, npc_pos=(x, y))
            self._event_started()

        # e) 每步后的特殊机制:巫师相邻魔伤 / 警卫夹击
        self._post_step(x, y)

    def _post_step(self, x, y):
        doc = self.floor_doc()
        hero = self.state["hero"]
        dmg = self.rules.adjacent_damage(self.state, doc, x, y)
        if dmg:
            hero["hp"] -= dmg
            self.message = f"被魔法击中,损失 {dmg} 生命"
        self.state = self.rules.guard_trap(self.state, doc, x, y)
        self._check_dead()

    def _land(self, direction):
        """踩楼梯换层。"""
        try:
            result = self.landing_resolver(self.state["floor"], direction)
        except LandingError as exc:
            self.message = str(exc)
            return
        if result is None:
            self.message = "这条路走不通"
            return
        new_floor, x, y = result
        if str(new_floor) not in self.data["floors"]:   # resolver 给了不存在的层
            self.message = f"落点第 {new_floor} 层不在数据里(数据或落点接线问题)"
            return
        self.state["floor"] = new_floor
        self.state["hero"]["pos"] = [x, y]
        visited = self.state.setdefault("visited", [])
        if new_floor not in visited:
            visited.append(new_floor)
        self.floor_doc(new_floor)               # 新层物化进缓存
        self.message = f"来到第 {new_floor} 层"
        self._on_enter_floor(new_floor)

    def _battle_flags(self, x=None, y=None):
        """战斗开关(§4.4-B):道具三个由持有推导;first_attack 由楼层位置表决定。

        x/y 是被撞怪物的格子坐标(怪物手册预测也传):当前层的 first_attack
        位置表里有这一格、且这格的怪还没死过 → 该怪先攻(损血 = n×d2)。
        """
        props = self.state["hero"].get("props", {})
        return {
            "cross": "28" in props,             # 十字架
            "lucky_coin": "27" in props,        # 幸运金币
            "dragon_slayer": "29" in props,     # 屠龙匕
            "first_attack": self._is_first_attack(x, y),
        }

    def _is_first_attack(self, x, y):
        """(x,y) 这格的怪是不是先攻怪(2026-09-06 用户拍板启用,40 层 12 个位置)。

        失效方式选了【经 monsters_dead 判断】而不是运行时从表里删,理由:
        1. floor_doc() 的其余字段(含 first_attack)与 loader 源数据共享引用,
           运行时删元素会污染源数据——同进程里读档重建缓存、别的 Engine 实例
           都会看到被删过的表,还会漏记进 floors_state 导致存档丢状态;
        2. monsters_dead 本来就是杀怪台账(state 的一部分,随存档走),
           "位置失效 = 这格的怪死过了"一个 in 查询就还原,零写入零副作用。
        """
        if x is None or y is None:
            return False
        doc = self.floor_doc()
        if [x, y] not in (doc.get("first_attack") or []):
            return False
        # 怪死过这格就失效(位置表是给"这只怪"的授权,不传给后来者)
        dead = self.state["flags"].get("monsters_dead", [])
        return f"{self.state['floor']}:{x},{y}" not in dead

    def _check_guard_doors(self):
        """杀完怪查本层 guard_doors(勘误终审:1005 怪物门 = 杀光守卫自动开)。

        一组守卫的格子里一只怪都不剩(栈里翻遍无 monster)→ 这组 doors
        里还关着的 1005 门弹栈,露出压在下面的东西(原版机制)。
        只在引擎杀怪后调用;事件层杀怪走 api 的集成由 batch 6 接线。
        """
        doc = self.floor_doc()
        fno = self.state["floor"]
        opened = False
        for group in doc.get("guard_doors") or []:
            guards = group.get("guards") or []
            doors = group.get("doors") or []
            if not guards or not doors:
                continue            # 数据不完整就跳过这组(坐标合法性加载器已校验)
            still_alive = False
            for gx, gy in guards:
                for cell in doc["grid"][gy][gx] or []:
                    if cell.get("kind") == "monster":
                        still_alive = True
                        break
                if still_alive:
                    break
            if still_alive:
                continue
            for dx, dy in doors:
                stack = doc["grid"][dy][dx]
                if (stack and stack[-1].get("kind") == "door"
                        and stack[-1].get("id") == 1005):
                    self._pop_cell(fno, dx, dy, stack)
                    opened = True
        return opened

    def _note_monster_death(self, fno, x, y):
        """杀怪统一台账(玩家亲手杀和事件/道具杀都必须流经这里):
        记 monsters_dead → 查守卫门(1005)→ 查杀怪触发器(kill_triggers)。
        先攻位置失效、守卫门自动开、49 层封印阵全靠这份台账,两个口径就会漏机制。
        返回:守卫门有没有因此打开(给提示文案用)。"""
        dead = self.state["flags"].setdefault("monsters_dead", [])
        key = f"{fno}:{x},{y}"
        if key not in dead:
            dead.append(key)
        opened = self._check_guard_doors()
        self._check_kill_triggers(fno)
        return opened

    def _any_dead(self, fno, positions):
        return any(f"{fno}:{x},{y}" in self.state["flags"].get("monsters_dead", [])
                   for x, y in positions)

    def _check_kill_triggers(self, fno):
        """杀怪触发器(楼层 kill_triggers 字段,转换器从怪物层属性接线)。

        两种形态(考古终审):
        - {"event":id, "kill":[[x,y]...]}:kill 列表里的怪全死 → 触发事件;
        - 带 "keep_alive":[[x,y]...] 的(49 层封印阵):keep_alive 里的怪
          【先死了任何一个】→ 这个触发器永久作废(杀了守角的怪就破不了阵)。
        触发过/作废过分别记进 flags(kill_triggers_done / _cancelled),
        存档读档不重放。
        """
        doc = self.floor_doc(fno)
        fired = self.state["flags"].setdefault("kill_triggers_done", [])
        cancelled = self.state["flags"].setdefault("kill_triggers_cancelled", [])
        for i, kt in enumerate(doc.get("kill_triggers") or []):
            key = f"{fno}:{i}"
            if key in fired or key in cancelled:
                continue
            if kt.get("keep_alive") and self._any_dead(fno, kt["keep_alive"]):
                cancelled.append(key)          # 守角的怪死了:封印永久破不了
                continue
            if kt.get("kill") and self._all_dead(fno, kt["kill"]):
                fired.append(key)
                self._start_event(kt["event"])

    def _all_dead(self, fno, positions):
        dead = self.state["flags"].get("monsters_dead", [])
        return all(f"{fno}:{x},{y}" in dead for x, y in positions)

    def _check_dead(self):
        if self.state["hero"]["hp"] <= 0:
            self.state["hero"]["hp"] = 0
            self.mode = "gameover"
            self.event_active = False
            self.intent = None

    # ------------------------------------------------------------ 事件推进

    def _start_event(self, event_id):
        event = self.data["events"].get(str(event_id))
        if event is None:
            self.message = f"事件 {event_id} 不在事件表(数据问题)"
            return
        if self.event_active:
            # 事件演到一半又触发新事件(杀怪触发器接力等):
            # 直接换 runner 会把没演完的剧情丢掉,排队等当前事件演完
            self._event_queue.append(event_id)
            return
        self.events.start(event, self.state)
        self._event_started()

    def _event_started(self):
        self.event_active = True
        self._poll_intent()

    def _poll_intent(self):
        """向事件适配器要当前意图:chat/choices 就进入对话模式;done 就回到游玩。"""
        if not self.event_active:
            return
        intent = self.events.step()
        if intent is None or intent.get("op") == "done":
            self.event_active = False
            self.intent = None
            if self.mode == "dialog":
                self.mode = "play"
            if self._event_queue:                # 排队的事件接着演
                self._start_event(self._event_queue.pop(0))
            return
        op = intent.get("op")
        if op == "chat":
            self.intent = intent
            self.mode = "dialog"
        elif op == "choices":
            self.intent = intent
            self.choice_sel = 0
            self.mode = "dialog"
        else:                                   # 未知意图:明确提示,不许静默吞
            self.message = f"未知事件意图:{op!r}(事件层与引擎协议没对齐?)"
            self.event_active = False
            self.intent = None
            if self.mode == "dialog":
                self.mode = "play"

    # ------------------------------------------------------------ Esc 菜单

    def open_menu(self):
        self.menu_stack = [{
            "title": "魔塔 50 层",
            "options": ["存档", "读档", "回到游戏", "退出游戏"],
            "sel": 0,
            "on_pick": self._menu_root_pick,
        }]
        self.mode = "menu"

    def _menu_root_pick(self, idx):
        if idx == 0:                             # 存档 → 先过"需怪物手册"门槛(原版设定)
            if "18" not in self.state["hero"].get("props", {}):
                # 契约 §4.4-B:core 不判这个门槛,由引擎菜单层拒绝
                self.message = "还没有怪物手册(3 层老人送的),存不了档"
                self.close_menu()
                return
            self.menu_stack.append({
                "title": "存档到哪个槽位?",
                "options": ["槽位 1", "槽位 2", "槽位 3"],
                "sel": 0,
                "on_pick": lambda i: self._do_save(i + 1),
            })
        elif idx == 1:                           # 读档 → 选槽位
            self.menu_stack.append({
                "title": "读取哪个槽位?",
                "options": ["槽位 1", "槽位 2", "槽位 3"],
                "sel": 0,
                "on_pick": lambda i: self._do_load(i + 1),
            })
        elif idx == 2:
            self.close_menu()
        else:
            self.close_menu()
            self.running = False

    def close_menu(self):
        self.menu_stack = []
        if self.mode == "menu":
            self.mode = "play"

    def _do_save(self, slot):
        try:
            self.rules.save_game(copy.deepcopy(self.state), slot)
            self.message = f"已存档到槽位 {slot}"
        except Exception as exc:                 # 没手册/写盘失败都走这里,给清晰提示
            self.message = f"存档失败:{exc}"
        self.close_menu()

    def _do_load(self, slot):
        try:
            loaded = self.rules.load_game(slot)
            if not self._valid_state(loaded):
                raise ValueError("存档内容不是有效的游戏状态")
        except Exception as exc:
            self.message = f"读档失败:{exc}"
            self.close_menu()
            return
        self.state = loaded
        self._runtime = {}                       # 楼层缓存全部作废,按新状态重建
        self.event_active = False
        self.intent = None
        self.close_menu()
        self.floor_doc()
        self.message = f"已读取槽位 {slot}"

    @staticmethod
    def _valid_state(state):
        """读档最基本的形状检查,坏数据不让进引擎。"""
        try:
            return (isinstance(state, dict)
                    and isinstance(state["hero"], dict)
                    and isinstance(state["hero"]["pos"], list)
                    and len(state["hero"]["pos"]) == 2
                    and isinstance(state["floor"], int))
        except (KeyError, TypeError):
            return False

    # ------------------------------------------------------------ 怪物手册

    def open_manual(self):
        """H 键:必须持有怪物手册(道具 18)才能看。

        同一种怪可能既有先攻位置又有普通位置(40 层就是):按 (怪id, 先攻否)
        去重各列一行,先攻那行带标记,预测损血按各自位置的开关算。
        """
        if "18" not in self.state["hero"].get("props", {}):
            self.message = "你没有怪物手册(3 层老人送的),看不了"
            return
        rows, seen = [], set()
        for gy, row in enumerate(self.floor_doc()["grid"]):
            for gx, stack in enumerate(row):
                if not stack:
                    continue
                for cell in stack:
                    if cell.get("kind") != "monster" or cell.get("hide"):
                        continue
                    mid = cell.get("id")
                    first = self._is_first_attack(gx, gy)
                    if (mid, first) in seen:
                        continue
                    seen.add((mid, first))
                    monster = self._monster(mid)
                    if not monster:
                        continue
                    result = self.rules.calc_battle(
                        self.state["hero"], monster, self._battle_flags(gx, gy))
                    if result.get("can_fight"):
                        verdict = f"损血{result.get('hero_damage', 0)}/{result.get('turns', 0)}回合"
                    else:
                        verdict = "打不过"
                    rows.append({
                        "name": monster.get("name", "?") + ("(先攻)" if first else ""),
                        "hp": monster.get("hp", 0),
                        "attack": monster.get("attack", 0),
                        "defence": monster.get("defence", 0),
                        "gold": monster.get("gold", 0),
                        "verdict": verdict,
                    })
        rows.sort(key=lambda r: (r["attack"], r["hp"]))
        self.manual_rows = rows
        self.manual_sel = 0
        self.mode = "manual"

    # ------------------------------------------------------------ 按键分派

    def handle_key(self, key):
        """一个按键 = 一个动作。测试直接调这个函数驱动,不用真开窗口。"""
        if self.mode == "gameover":
            if key == pygame.K_ESCAPE:
                self.running = False
            return
        if self.mode == "ending":                # 通关画面:任意确认键谢幕
            if key in CONFIRM_KEYS or key == pygame.K_ESCAPE:
                self.running = False
            return
        if self.mode == "dialog":
            self._key_dialog(key)
        elif self.mode == "menu":
            self._key_menu(key)
        elif self.mode == "manual":
            self._key_manual(key)
        else:
            if key in KEY_DIRS:
                dx, dy = KEY_DIRS[key]
                self.try_move(dx, dy)
            elif key == pygame.K_h:
                self.open_manual()
            elif key == pygame.K_t:
                self.open_tools()
            elif key == pygame.K_ESCAPE:
                self.open_menu()

    # ------------------------------------------------------------ 道具使用(T 键)

    def open_tools(self):
        """T 键:使用道具。菜单由事件适配器出(它知道哪些道具能主动用);
        背包里没有可用道具时说一句,不开空菜单。"""
        tools = self.events.usable_tools(self.state)
        if not tools:
            self.message = "没有能主动使用的道具(手册按 H,被动道具带上就生效)"
            return
        self.menu_stack = [{
            "title": "使用道具",
            "options": [label for _, label in tools],
            "sel": 0,
            "on_pick": lambda i: self._use_tool_pick(tools[i][0]),
        }]
        self.mode = "menu"

    def _use_tool_pick(self, item_id):
        """菜单里选中一件道具:事件适配器去用;开了 flow(选层传送)就进对话模式。"""
        try:
            result = self.events.use_tool(item_id, direction=self._last_dir)
        except Exception as exc:                # 用坏了(数据/坐标问题)提示清楚,不崩
            result = {"ok": False, "msg": f"道具用不了:{exc}"}
        self.close_menu()
        if isinstance(result, dict) and result.get("flow"):
            self._event_started()
        else:
            self.message = (result or {}).get("msg", "") if isinstance(result, dict) else ""

    def _key_dialog(self, key):
        op = (self.intent or {}).get("op")
        if op == "chat":
            if key in CONFIRM_KEYS or key == pygame.K_ESCAPE:
                self.events.feed(None)
                self._poll_intent()
        elif op == "choices":
            options = self.intent.get("options", [])
            if not options:
                return
            if key in (pygame.K_UP, pygame.K_w):
                self.choice_sel = max(0, self.choice_sel - 1)
            elif key in (pygame.K_DOWN, pygame.K_s):
                self.choice_sel = min(len(options) - 1, self.choice_sel + 1)
            elif pygame.K_1 <= key <= pygame.K_9:    # 数字键直达
                idx = key - pygame.K_1
                if idx < len(options):
                    self.choice_sel = idx
            elif key in CONFIRM_KEYS:
                self.events.feed(self.choice_sel)
                self._poll_intent()

    def _key_menu(self, key):
        menu = self.menu_stack[-1]
        options = menu["options"]
        if key in (pygame.K_UP, pygame.K_w):
            menu["sel"] = max(0, menu["sel"] - 1)
        elif key in (pygame.K_DOWN, pygame.K_s):
            menu["sel"] = min(len(options) - 1, menu["sel"] + 1)
        elif key in CONFIRM_KEYS:
            pick = menu.get("on_pick")
            if pick:
                pick(menu["sel"])
        elif key == pygame.K_ESCAPE:
            self.menu_stack.pop()
            if not self.menu_stack:
                self.close_menu()

    def _key_manual(self, key):
        if key in (pygame.K_UP, pygame.K_w):
            self.manual_sel = max(0, self.manual_sel - 1)
        elif key in (pygame.K_DOWN, pygame.K_s):
            self.manual_sel = min(max(0, len(self.manual_rows) - 1), self.manual_sel + 1)
        elif key in (pygame.K_ESCAPE, pygame.K_h):
            self.mode = "play"

    # ------------------------------------------------------------ 事件层的 api
    # 契约以 game/events.py 文件头 docstring 为准(14 键,§4.4-C 的完整版)。
    # 事件层对地图的一切增删挪都走这里:引擎负责物化进运行时楼层文档、
    # 记 floors_state 台账;凡"把怪变没"的都顺手记 monsters_dead(要点①:
    # 事件/道具引发的怪物格增删都流经 set_cell,记账挂这里,先攻失效/
    # 守卫门判定才和玩家亲手杀的怪一个口径)。
    # 注意:npcs/triggers 摆放表的变化暂不进 floors_state(存档会丢),
    # 台账怎么扩由集成阶段(batch 6)定,这里先留 TODO。

    def api_get_floor(self, floor):
        """某层当前实际格子(11×11 栈数组,含已被打开的门/被杀的怪的变化)。"""
        return self.floor_doc(floor)["grid"]

    def api_set_cell(self, floor, x, y, stack):
        """整格替换(stack=None 表示清空该格)。旧栈有怪、新栈没怪 → 记怪死。"""
        if not (0 <= x < GRID and 0 <= y < GRID):
            raise ValueError(f"set_cell 坐标越界:{(x, y)}")
        grid = self.floor_doc(floor)["grid"]
        old = grid[y][x] or []
        new = copy.deepcopy(list(stack)) if stack else None
        grid[y][x] = new
        had_monster = any(c.get("kind") == "monster" for c in old)
        has_monster = bool(new) and any(c.get("kind") == "monster" for c in new)
        if had_monster and not has_monster:
            self._note_monster_death(floor, x, y)
        self._record_change(floor, x, y)

    def api_spawn_monster(self, floor, x, y, monster_id):
        """在某格栈顶冒一只怪(appear 动作)。"""
        grid = self.floor_doc(floor)["grid"]
        stack = grid[y][x]
        if stack is None:
            stack = []
            grid[y][x] = stack
        stack.append({"layer": "monster", "kind": "monster",
                      "id": monster_id, "sprite": f"{monster_id}_0"})
        self._record_change(floor, x, y)

    def api_add_npc(self, floor, x, y, npc_id):
        """事件摆一个 NPC 上场(摆放表 + 格子都登记,撞他才能对上话)。"""
        doc = self.floor_doc(floor)
        doc.setdefault("npcs", []).append({"npc": npc_id, "x": x, "y": y})
        stack = doc["grid"][y][x]
        if stack is None:
            stack = []
            doc["grid"][y][x] = stack
        stack.append({"layer": "npc", "kind": "npc", "sprite_id": npc_id})
        self._record_change(floor, x, y)
        self._save_placements(floor)

    def api_remove_npc(self, floor, x, y):
        """把某位置的 NPC 撤下场(摆放表删条目 + 弹掉格子上的 NPC 层)。"""
        doc = self.floor_doc(floor)
        doc["npcs"] = [p for p in doc.get("npcs", [])
                       if not (p["x"] == x and p["y"] == y)]
        stack = doc["grid"][y][x] or []
        for i in range(len(stack) - 1, -1, -1):
            if stack[i].get("kind") == "npc":
                stack.pop(i)
                break
        if not stack:
            doc["grid"][y][x] = None
        self._record_change(floor, x, y)
        self._save_placements(floor)

    def api_add_trigger(self, floor, x, y, event_id):
        """放一个"踩上去触发事件"的隐形格(事件摆放的机关)。"""
        doc = self.floor_doc(floor)
        doc.setdefault("triggers", []).append(
            {"x": x, "y": y, "event": event_id})
        self._save_placements(floor)

    def api_remove_trigger(self, floor, x, y):
        """删掉某坐标的踩格触发器(一次性事件演完撤场)。"""
        doc = self.floor_doc(floor)
        doc["triggers"] = [t for t in doc.get("triggers", [])
                           if not (t["x"] == x and t["y"] == y)]
        self._save_placements(floor)

    def api_move(self, floor, kind, from_pos, to_pos):
        """把一个东西挪一格(小偷走位等)。起止都是展平坐标 0~120。

        kind 是格子类型("monster"/"npc"/...):从起格栈里挑最上面那个
        该类型的 cell,压到止格栈顶。起格没这东西属于数据对不上,抛错
        说明白,不悄悄吞掉。
        """
        fx, fy = int(from_pos) % GRID, int(from_pos) // GRID
        tx, ty = int(to_pos) % GRID, int(to_pos) // GRID
        grid = self.floor_doc(floor)["grid"]
        if not (0 <= fx < GRID and 0 <= fy < GRID
                and 0 <= tx < GRID and 0 <= ty < GRID):
            raise ValueError(f"move 坐标越界:{from_pos}→{to_pos}")
        stack = grid[fy][fx] or []
        for i in range(len(stack) - 1, -1, -1):
            if stack[i].get("kind") == kind:
                cell = stack.pop(i)
                break
        else:
            raise ValueError(
                f"move:第 {floor} 层 ({fx},{fy}) 格里没有 {kind} 可挪")
        if grid[ty][tx] is None:
            grid[ty][tx] = []
        grid[ty][tx].append(cell)
        if not stack:
            grid[fy][fx] = None
        self._record_change(floor, fx, fy)
        self._record_change(floor, tx, ty)

    def api_weaken(self, monster_id, ratio):
        """削弱某种怪(49 层封印:假魔王 ×0.1)。"""
        self._weakened[str(monster_id)] = float(ratio)

    def api_register_monster_door(self, door_pos, guards):
        """登记一扇"守卫门"(事件 meta.monsterDoor):door_pos 是展平坐标,
        guards 是守卫的展平坐标列表。门位置还没门就先摆一扇 1005,
        然后挂进本层 guard_doors——守卫全灭自动开(和地图原生 1005 同一套)。"""
        dx, dy = int(door_pos) % GRID, int(door_pos) // GRID
        fno = self.state["floor"]
        doc = self.floor_doc(fno)
        stack = doc["grid"][dy][dx]
        if stack is None:
            stack = []
            doc["grid"][dy][dx] = stack
        if not (stack and stack[-1].get("kind") == "door"
                and stack[-1].get("id") == 1005):
            stack.append({"layer": "door", "kind": "door", "id": 1005})
        guard_xy = [[int(g) % GRID, int(g) // GRID] for g in (guards or [])]
        doc.setdefault("guard_doors", []).append(
            {"doors": [[dx, dy]], "guards": guard_xy})
        self._record_change(fno, dx, dy)

    def api_collide(self, floor, x, y):
        """事件里"模拟勇者撞这格"(do 动作):怪=强制开战、NPC=对话、
        门=照常开门——就是玩家自己撞上去会发生什么,原样演一遍。"""
        if int(floor) != int(self.state["floor"]):
            raise ValueError(
                f"collide 只能撞当前层(当前第 {self.state['floor']} 层,"
                f"事件却要撞第 {floor} 层)——事件数据的坐标多半不对")
        stack = self.floor_doc(floor)["grid"][y][x] or []
        self._interact(x, y, stack)

    def api_jump_floor(self, floor, x=None, y=None):
        """换层传送(事件 jump / 飞行魔杖)。x,y 传 None = 落到目标层楼梯口。"""
        floor = int(floor)
        if str(floor) not in self.data["floors"]:
            raise ValueError(f"jump_floor:第 {floor} 层不在数据里(传送目标没这层)")
        if x is None or y is None:
            x, y = self._stair_landing(floor)
        self.state["floor"] = floor
        self.state["hero"]["pos"] = [x, y]
        visited = self.state.setdefault("visited", [])
        if floor not in visited:
            visited.append(floor)
        self.floor_doc(floor)
        self._on_enter_floor(floor)

    def _on_enter_floor(self, floor):
        """进层钩子:事件层登记的跨层挂起事件(meta.save=这层)在这层开演。"""
        if self.events is not None and self.events.enter_floor(floor):
            self._event_started()

    def _stair_landing(self, floor):
        """某层的"楼梯口"落点:优先 stair_links.up_stand,退回第一张上楼梯。"""
        doc = self.data["floors"][str(floor)]
        stand = (doc.get("stair_links") or {}).get("up_stand")
        if stand:
            return stand[0], stand[1]
        for s in doc.get("stairs", []):
            if s["dir"] == "up":
                return s["x"], s["y"]
        raise LandingError(f"第 {floor} 层没有楼梯数据,算不出楼梯口落点")

    def api_show(self, floor, x, y):
        """显现该位置藏着的格子(10 层隐藏楼梯等):把 hide 标记摘掉。"""
        stack = self.floor_doc(floor)["grid"][y][x] or []
        for cell in stack:
            cell.pop("hide", None)
        self._record_change(floor, x, y)

    def api_clear_npc_event(self, floor, x, y):
        """清掉该位置 NPC 的 event_talk(29 层小偷):之后撞他只普通聊天。

        npcs.json 是共享源数据不能直接改,所以在【摆放条目】上记一个
        event_talk=None 的覆盖标记,_npc_entry_at 取条目时套用。"""
        doc = self.floor_doc(floor)
        for placed in doc.get("npcs", []):
            if placed["x"] == x and placed["y"] == y:
                placed["event_talk"] = None
                self._save_placements(floor)
                return
        raise ValueError(
            f"clear_npc_event:第 {floor} 层 ({x},{y}) 没有摆放 NPC,数据对不上")

    def build_api(self):
        """给事件层的回调字典(§4.4-C 的 api,键名以 game/events.py 文件头为准)。"""
        return {
            "get_floor": self.api_get_floor,
            "set_cell": self.api_set_cell,
            "spawn_monster": self.api_spawn_monster,
            "add_npc": self.api_add_npc,
            "remove_npc": self.api_remove_npc,
            "add_trigger": self.api_add_trigger,
            "remove_trigger": self.api_remove_trigger,
            "move": self.api_move,
            "weaken": self.api_weaken,
            "register_monster_door": self.api_register_monster_door,
            "collide": self.api_collide,
            "jump_floor": self.api_jump_floor,
            "show": self.api_show,
            "clear_npc_event": self.api_clear_npc_event,
        }

    # ------------------------------------------------------------ 渲染

    def _hud(self):
        """把状态栏要显示的字段整理成纯 dict(界面层不碰游戏数据结构)。"""
        hero = self.state["hero"]
        items = self.data["items"]

        def prop_name(pid):
            return items.get(str(pid), {}).get("name", f"道具{pid}")

        return {
            "hp": hero["hp"], "attack": hero["attack"],
            "defence": hero["defence"], "gold": hero["gold"],
            "keys": hero["keys"], "floor": self.state["floor"],
            "sword": prop_name(hero["sword"]) if hero.get("sword") else None,
            "shield": prop_name(hero["shield"]) if hero.get("shield") else None,
            "props": [prop_name(pid) for pid in hero.get("props", {})],
            "pos": hero["pos"],
        }

    def _view(self):
        """11×11 的"每格最上层可见物"(跳过 hide 的隐藏格)。"""
        view = []
        for row in self.floor_doc()["grid"]:
            line = []
            for stack in row:
                top = None
                for cell in reversed(stack or []):   # 从顶往下找第一个可见的
                    if not cell.get("hide"):
                        top = cell
                        break
                line.append(top)
            view.append(line)
        return view

    def draw(self):
        """画一帧。测试里传个 Surface 进来就能冒烟。"""
        if self.screen is None:
            return
        ui.draw_frame(self.screen, self._hud(), self._view(), self.assets)
        if self.mode == "dialog" and self.intent:
            if self.intent.get("op") == "chat":
                ui.draw_chat(self.screen, self.intent.get("lines", []))
            else:
                ui.draw_choices(self.screen, self.intent.get("options", []),
                                self.choice_sel)
        elif self.mode == "menu" and self.menu_stack:
            ui.draw_menu(self.screen, self.menu_stack[-1])
        elif self.mode == "manual":
            ui.draw_manual(self.screen, self.manual_rows, self.manual_sel)
        elif self.mode == "gameover":
            ui.draw_gameover(self.screen)
        elif self.mode == "ending":
            ui.draw_ending(self.screen, self.state["hero"])
        else:
            ui.draw_hint(self.screen, self.message or ui.DEFAULT_HINT)
        pygame.display.flip()

    # ------------------------------------------------------------ 主循环

    def run(self):
        """开窗口跑 60FPS 主循环(QUIT/KEYDOWN 事件泵)。"""
        pygame.init()
        if self.screen is None:
            self.screen = ui.create_screen()
        pygame.display.set_caption("魔塔 50 层")
        clock = pygame.time.Clock()
        self.running = True
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    self.handle_key(event.key)
            self._poll_intent()
            self.draw()
            clock.tick(FPS)
