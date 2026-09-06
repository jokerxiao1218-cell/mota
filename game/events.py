"""事件系统:魔塔的"剧情导演"(batch 4 —— 事件解释器 + NPC + 祭坛 + 道具使用)

================================================================
一、这个文件是干什么的?(大白话)
================================================================
魔塔里除了走路、打怪、捡东西,还有大量"剧情":开场魔王抢你装备、10 层中埋伏、
49 层破封印、公主请你上 50 层……设计文档 §4.2 选择 3 定的方案是:

    事件 = 一张【动作清单】(actions),代码只是一个【解释器】,照单逐条演。

清单本体在 game/data/events.json(27 个事件,由参考仓库 tacthgin 转换而来),长这样:

    {"id": 1, "trigger": null,
     "actions": [{"type": "appear", "data": [...]}, {"type": "chat", "data": [...]}, ...],
     "meta": {"save": 29, "monsterDoor": {...}}}

本文件提供四样东西:
    1. EventRunner —— 事件解释器(§4.4-C 的 step / feed 协议)
    2. NpcFlow    —— NPC 对话 / 买卖 / 祝福 / 送礼(小偷、老人、商人、公主)
    3. AltarFlow  —— 祭坛商店(4/12/32/46 层,三选一,全塔共享购买次数)
    4. 道具使用函数 + manual_data(怪物手册数据)+ notebook_data(记事本数据)

================================================================
二、怎么做到"没有画面也能测试"?(intent / feed 协议)
================================================================
本文件【禁止 import pygame】(设计文档 §4.2 选择 6)。跟引擎的来往全靠两句话:

    runner.step()  —— 我问:"下一步给玩家看什么?" → 返回一个"意图"(intent)
    runner.feed(x) —— 我答:"玩家操作完了"       → x 是操作结果

意图一共 6 种(§4.4-C 契约的完整版):
    {"op": "chat", "lines": ["..."]}                     对白的一页(lines=这一页的行)
    {"op": "choices", "options": [{"label": "买"}, ...]} 选择支
    {"op": "sound", "name": "xxx", "loop": 布尔}          播音效(name=None 表示停循环音)
    {"op": "effect", "kind": "..."}                      纯演出(挨打特效、场景切换等)
    {"op": "pending", "floor": 24}                       事件挂起:等玩家到 24 层再演
    {"op": "done"}                                        演完了
可选附加字段:done 可以带 "remove_npc": True(提示引擎把这个 NPC 从地图上拿掉)。

喂法:chat / sound / effect 喂 None;choices 喂选项序号(从 0 数)。
done 和 pending 是终点,后面不用再喂。测试 = 喂一串 canned 回答往下推,全程不开窗口。

================================================================
三、改地图、算数值,都走"注入的适配器"
================================================================
【api 字典】(引擎注入;测试用 FakeApi 模拟)——地图上的增删挪全走这里:
    get_floor(floor)                       -> 11×11 的格子栈矩阵(当前可见状态)
    set_cell(floor, x, y, stack)           -> 整格替换(stack=None 表示清空该格)
    spawn_monster(floor, x, y, 怪id)       -> 出怪(§4.4-C 点名)
    add_npc(floor, x, y, npc_id)          -> 出 NPC
    remove_npc(floor, x, y)               -> 删 NPC
    add_trigger(floor, x, y, 事件id)       -> 放一个"踩上去触发事件"的隐形格
    remove_trigger(floor, x, y)           -> 删踩格触发器
    move(floor, "monster"/"npc", 起, 止)   -> 挪一格(起止都是展平坐标)
    weaken(怪id, 比例)                     -> 该怪 HP/攻/防 ×= 比例(49 层封印)
    register_monster_door(门位置, 守卫位置列表) -> 怪物门:守卫全灭,门自动开
    collide(floor, x, y)                   -> 模拟勇者撞该格(战斗/对话/地板伤害)
    jump_floor(floor, x, y)                -> 换层传送;x,y 传 None 表示落在楼梯口
    show(floor, x, y)                      -> 显现该位置藏着的格子(10 层隐藏楼梯等)
    clear_npc_event(floor, x, y)           -> 清掉该位置 NPC 的 event_talk
【rules 适配器】(core 规则层注入;测试用 FakeRules)——凡是"算数"的都归它
    (new_state / area / calc_battle / altar_price / altar_buy ……签名见 §4.4-B)。
祭坛价格、买卖结算、战斗预测,本文件一概不自己算,免得和 core 打架。

================================================================
四、事件数据里几个容易踩坑的约定(源码考古结论)
================================================================
1. 坐标:事件数据里的数字是【展平一维坐标 0~120】(11×11=121 格):
       x = pos % 11,y = pos // 11
   例外:jump 的数据是 [层号, x, y] 三元组,后两个直接就是格子坐标。
2. appear 的数据是 [{layer: {层名: [[位置, 元素id], ...]}, interval, delay}, ...]:
       层名有 monster/door/prop/npc/event/wall;
       wall 层的元素id 无意义,一律摆"墙门"(1006)。
3. monsterDoor 是【事件级】字段(在 meta 里),事件一构造就要注册:
       数据形如 {"守卫位置们": [门位置...]} → 逐门挂上自己的守卫列表。
4. meta.save:事件跨层挂起——构造 runner 时如果 save ≠ 当前层,step() 直接给
   {"op": "pending", "floor": save},由引擎登记"玩家到那层再重开一个 runner 演"。
   (案例:事件8→29层、事件9→2层、事件10→35层、事件26→24层。)
5. 事件里【同类动作】共用一份数组 data,解释器按全局游标一条条轮流取
   (照抄参考实现 GameEventSystem 的 chatStep/appearStep/disappearStep/moveStep)。
   游标越界 = 没得演 = 空跳过(参考实现读 undefined 后 for-in 空转,同效)。
6. 一次性:事件演完把 id 记进 state["flags"]["events_done"],第二次构造直接 done。
"""

# ============================================================
# 常量与错误
# ============================================================

# 开场剧情被魔王手下围殴后的"虚弱数值"。来源:参考实现 global.json 的 weakenAttr,
# 数值恰好就是设计文档 §1 的开局值(400/10/10)。开场事件的 sceneAppear 动作
# 用它重置勇士——"装备被夺"就是靠这个重置表达的。
WEAKEN_ATTRS = {"hp": 400, "attack": 10, "defence": 10}

# 墙门(暗道门)的门 id。数据里"暗道门"有的记成 kind=wall,有的记成 kind=door id=1006,
# 所以判断"是不是一堵墙"时两种都要认(小偷开的暗道、镐敲的墙,都可能是任一种)。
WALL_DOOR_ID = 1006

# 被动道具 / 查看型道具之外,能"主动使用"的道具的 tool 名 → 本文件的处理函数
TOOL_NAMES = (
    "fly_wand", "wing", "center_wing", "pickaxe", "quake_scroll",
    "ice_magic", "bomb", "magic_key", "holy_water",
)


class EventError(Exception):
    """事件系统里"数据不对 / 用法不对"的错误(带人话说明,不静默)。"""


# ============================================================
# 小工具函数(纯函数,谁都能用)
# ============================================================

def _xy(pos):
    """展平一维坐标(0~120)→ (x, y):x 向右、y 向下,各 0~10。"""
    pos = int(pos)
    if not (0 <= pos <= 120):
        raise EventError(f"展平坐标越界:{pos}(合法范围 0~120)")
    return pos % 11, pos // 11


def _flat(x, y):
    """(x, y) → 展平一维坐标。"""
    return y * 11 + x


def _flags(state):
    """state["flags"] 的顺手取法(没有就建一个空表)。"""
    return state.setdefault("flags", {})


def _props(state):
    """勇士道具栏 state["hero"]["props"](道具id字符串→数量)。"""
    return state["hero"].setdefault("props", {})


def _has_prop(state, item_id):
    """勇士身上有没有某个道具(数量>0)。"""
    return _props(state).get(str(item_id), 0) > 0


def _records(state):
    """对白记录(state["records"],记事本用,没建过就建空表)。"""
    return state.setdefault("records", [])


def _add_visited(state, floor):
    """把去过的楼层记进 state["visited"](飞行魔杖的目标列表)。"""
    visited = state.setdefault("visited", [])
    if floor not in visited:
        visited.append(floor)


def _prop_pairs(prop_list):
    """NPC 数据里 value.prop 是一串 [道具id, 数量, 道具id, 数量……] 的扁平数组,
    两两配成 (道具id, 数量) 的列表。"""
    pairs = []
    lst = list(prop_list or [])
    for i in range(0, len(lst) - 1, 2):
        pairs.append((lst[i], lst[i + 1]))
    return pairs


def _api_call(api, name, *args):
    """从注入的 api 字典里取函数并调用;缺了哪个就明说,不许静默吞掉。"""
    try:
        fn = api[name]
    except (KeyError, TypeError, IndexError):
        raise EventError(
            f"api 注入不完整:缺少 '{name}'——引擎侧请按 events.py 文件头的清单提供"
        ) from None
    return fn(*args)


def _is_wallish(cell):
    """这个格子是不是"墙":普通墙(kind=wall)或墙门(kind=door id=1006)都算。"""
    kind = cell.get("kind")
    return kind == "wall" or (kind == "door" and cell.get("id") == WALL_DOOR_ID)


def _push_cell(api, floor, x, y, cell):
    """往 (x,y) 的格子栈顶上再压一个格子(比如事件凭空变出一扇门)。"""
    grid = _api_call(api, "get_floor", floor)
    stack = grid[y][x] or []
    _api_call(api, "set_cell", floor, x, y, list(stack) + [cell])


def _pop_cell(api, floor, x, y, want):
    """从 (x,y) 的格子栈【顶往下】找第一个满足条件 want(小函数,给 True/False)的格子,
    删掉它并返回删掉的格子;一个都没有就返回 None。
    (从顶往下找是因为墙/门下面可能压着道具,栈顶才是当前挡路的东西。)"""
    grid = _api_call(api, "get_floor", floor)
    stack = grid[y][x] or []
    for i in range(len(stack) - 1, -1, -1):
        if want(stack[i]):
            new = stack[:i] + stack[i + 1:]
            _api_call(api, "set_cell", floor, x, y, new if new else None)
            return stack[i]
    return None


def _consume_tool(state, data, item_id):
    """用过道具后按道具表的 uses 扣次数:
    uses=None(无限用,比如飞行魔杖/冰冻魔法)不扣;
    uses=数字(一次性=1、中心飞行器=3)就扣 1,扣到 0 删掉道具栏里这个键
    (设计文档 §4.4-A:"工具恒 1,用完删键")。"""
    item = (data.get("items") or {}).get(str(item_id))
    if item is None or item.get("uses") is None:
        return
    props = _props(state)
    key = str(item_id)
    left = props.get(key, 0) - 1
    if left > 0:
        props[key] = left
    else:
        props.pop(key, None)


# ============================================================
# 一、EventRunner:事件解释器
# ============================================================

class EventRunner:
    """把一个事件(来自 events.json 的字典)逐条动作"演"出来。

    用法(引擎/测试都一样):
        runner = EventRunner(event, state, api, rules=None, data=None)
        intent = runner.step()      # 拿到意图,渲染给玩家
        runner.feed(回应)            # chat/sound/effect 喂 None,choices 喂序号
        ……直到 intent["op"] 是 "done" 或 "pending"。

    event / state / api 三个参数是 §4.4-C 的契约签名;rules 和 data 是本批(batch 4)
    补充的可选参数:rules=规则适配器(解释器本身暂时用不上,统一接口方便接力),
    data=全量数据表(有了它才能校验"查无此怪"——事件 11/12 引用了不存在的怪物 135,
    按勘误结论保持原样、跳过并记录)。
    """

    def __init__(self, event, state, api, rules=None, data=None):
        self.event = event
        self.state = state
        self.api = api
        self.rules = rules
        self.data = data
        self.warnings = []       # "跳过了什么、为什么"都攒在这,报告/调试用
        self._actions = list(event.get("actions") or [])
        self._pos = 0            # 演到第几条动作
        self._cursor = {}        # 同类动作的全局游标(照抄参考实现的 *Step 计数)
        self._mode = "run"       # run=往下演 / wait=等玩家 / done / pending
        self._intent = None      # wait 时反复交出的那个意图
        self._pages = []         # 当前对白的页(一页=一串行)
        self._page = 0
        self._pending_floor = None

        # 一次性:已经演完的事件不再演(§4.4-A flags.events_done)
        eid = event.get("id")
        if eid is not None and eid in _flags(state).setdefault("events_done", []):
            self._mode = "done"
            return

        # monsterDoor 是【事件级】字段:事件一构造就要逐门注册(参考实现 initliaze
        # 阶段就干这件事;引擎侧负责在门位置摆上门、记下"守卫全灭→门开")
        for guards_key, doors in ((event.get("meta") or {}).get("monsterDoor") or {}).items():
            guards = [int(g) for g in str(guards_key).split(",") if str(g).strip()]
            for door in doors:
                _api_call(self.api, "register_monster_door", int(door), guards)

    # ---------- 对外协议 ----------

    def step(self):
        """问一句"下一步演什么",返回意图。处于等待时,重复调它会把同一个意图再给一遍。"""
        if self._mode == "done":
            return {"op": "done"}
        if self._mode == "pending":
            return self._intent
        if self._mode == "wait":
            return self._intent

        # 跨层挂起:meta.save 指定的层还没到,本事件先不演(勘误第 4 条)。
        # intent 里带上自己的事件 id:引擎侧登记"到这层再演"要知道演谁
        save = (self.event.get("meta") or {}).get("save")
        if save is not None and save != self.state.get("floor"):
            self._mode = "pending"
            self._pending_floor = save
            self._intent = {"op": "pending", "floor": save,
                           "event": self.event.get("id")}
            return self._intent

        # 正常推进:一条一条往下演,直到需要玩家输入(返回意图)或演完
        while True:
            if self._pos >= len(self._actions):
                self._finish()
                return {"op": "done"}
            act = self._actions[self._pos]
            self._pos += 1
            intent = self._exec(act)
            if intent is None:
                continue          # 这条动作不需要玩家,马上演下一条
            self._intent = intent
            # pending(挂起)和普通意图一样等引擎一句"知道了",但之后进入挂起态
            self._mode = "pending" if intent["op"] == "pending" else "wait"
            return intent

    def feed(self, response):
        """玩家操作完了:chat/sound/effect 喂 None;本解释器不出选择支,choices 不会来。"""
        if self._mode != "wait":
            return                 # 不在等待时喂进来,当没这回事(引擎不会这么用)
        if self._intent.get("op") == "chat":
            self._page += 1
            if self._page < len(self._pages):
                self._intent = {"op": "chat", "lines": self._pages[self._page]}
                return
        self._mode = "run"         # 音效/演出播完、对白翻完页 → 继续演

    # ---------- 内部实现 ----------

    def _finish(self):
        """动作清单演完:把事件 id 记入 flags.events_done(一次性判定)。"""
        eid = self.event.get("id")
        if eid is not None:
            done = _flags(self.state).setdefault("events_done", [])
            if eid not in done:
                done.append(eid)
        self._mode = "done"

    def _entry(self, kind, data):
        """取同类动作数组里"轮到我的那条"。物化后的每条同类动作带的都是同一份
        数组,靠全局游标保证一条条轮流取(照抄参考实现);越界返回 None=空跳过。"""
        if data is None:
            data = []
        if isinstance(data, (dict, str)):
            data = [data]
        i = self._cursor.get(kind, 0)
        self._cursor[kind] = i + 1
        return data[i] if i < len(data) else None

    def _floor(self):
        """事件发生在哪层?就是勇士当前层(事件都是踩格/撞怪触发的)。"""
        return self.state["floor"]

    def _exec(self, act):
        """执行一条动作,返回意图;返回 None 表示这条不产生输出,继续演下一条。"""
        t = act.get("type")
        data = act.get("data")
        if t == "chat":
            return self._act_chat(data)
        if t == "sound":
            return self._act_sound(data)
        if t == "stopSound":
            return {"op": "sound", "name": None, "loop": False}   # 停掉循环音效
        if t == "appear":
            self._act_appear(data)
            return None
        if t == "disappear":
            self._act_disappear(data)
            return None
        if t == "move":
            self._act_move(data)
            return None
        if t == "specialMove":
            # 【语义存疑】参考实现里这是纯演出(20 层吸血鬼登场时"召群"的动画:
            # from 一圈位置涌向 to),引擎没有对应演出时可安全忽略。
            return {"op": "effect", "kind": "specialMove", "data": data}
        if t == "beAttack":
            return self._act_be_attack(data)
        if t == "sceneDisappear":
            # 场景淡出(开场剧情的"眼前一黑"),纯演出
            return {"op": "effect", "kind": "sceneDisappear"}
        if t == "sceneAppear":
            return self._act_scene_appear(data)
        if t == "do":
            # 模拟勇者去撞某格:怪=强制开战、NPC=强制对话、空地=地板伤害判定
            x, y = _xy(data)
            _api_call(self.api, "collide", self._floor(), x, y)
            return None
        if t == "show":
            x, y = _xy(data)                       # 显示该位置藏着的元素(10 层隐藏楼梯)
            _api_call(self.api, "show", self._floor(), x, y)
            return None
        if t == "weak":
            self._act_weak(data)
            return None
        if t == "jump":
            self._act_jump(data)
            return None
        if t == "clearNpcEvent":
            x, y = _xy(data)                       # 清掉该位置 NPC 的 event_talk(29 层小偷)
            _api_call(self.api, "clear_npc_event", self._floor(), x, y)
            return None
        if t == "save":
            # 物化数据里 save 只出现在 meta;真遇到动作型 save 也按挂起处理
            return {"op": "pending", "floor": data}
        raise EventError(
            f"未知事件动作类型:{t!r}(事件 {self.event.get('id')})——"
            f"设计文档要求明确报错,不许静默跳过"
        )

    # ---- 各动作的具体实现(对照参考实现 GameEventSystem.ts 逐条考据)----

    def _act_chat(self, data):
        """对白:数组里轮到的这一条,字符串=一页一行;嵌套列表=一页多行。"""
        entry = self._entry("chat", data)
        if entry is None:
            return None
        lines = [entry] if isinstance(entry, str) else [str(x) for x in entry]
        self._pages = [lines]      # 一条 chat 动作就是一页(参考实现一次弹一个框)
        self._page = 0
        return {"op": "chat", "lines": lines}

    def _act_sound(self, data):
        """音效:data=[名字] 或 [名字, 循环]。"""
        info = data or []
        if not info:
            return None
        loop = False
        if len(info) > 1:
            loop = str(info[1]).lower() != "false" if isinstance(info[1], str) else bool(info[1])
        return {"op": "sound", "name": info[0], "loop": loop}

    def _act_appear(self, data):
        """凭空出现。数据是 [{layer:{层名:[[位置,元素id],...]}, interval, delay}, ...]。
        interval/delay 是演出节奏,本项目"移动瞬间结算"(§1)不做等待。"""
        entry = self._entry("appear", data)
        if not entry:
            return
        floor = self._floor()
        for layer, items in (entry.get("layer") or {}).items():
            for pair in items or []:
                pos, element_id = pair[0], pair[1]
                x, y = _xy(pos)
                if layer == "monster":
                    if self.data is not None and str(element_id) not in (self.data.get("monsters") or {}):
                        # 事件 11/12 引用了不存在的怪物 135(tacthgin 数据损坏,
                        # 勘误结论:保持原样,查无此怪跳过并记录)
                        self.warnings.append(
                            f"appear:怪物 {element_id} 不在怪物表(事件 {self.event.get('id')}),跳过"
                        )
                        continue
                    _api_call(self.api, "spawn_monster", floor, x, y, element_id)
                elif layer == "npc":
                    _api_call(self.api, "add_npc", floor, x, y, element_id)
                elif layer == "event":
                    _api_call(self.api, "add_trigger", floor, x, y, element_id)
                elif layer == "wall":
                    # 墙层元素 id 无意义,一律摆"墙门"(勘误第 5 条)
                    _push_cell(self.api, floor, x, y, {"kind": "door", "id": WALL_DOOR_ID})
                elif layer == "door":
                    _push_cell(self.api, floor, x, y, {"kind": "door", "id": element_id})
                elif layer == "prop":
                    _push_cell(self.api, floor, x, y, {"kind": "prop", "id": element_id})
                else:
                    self.warnings.append(f"appear:不认识的层名 {layer!r},跳过")

    def _act_disappear(self, data):
        """凭空消失。数据是 [{层名: [位置, ...]}, ...]:
        wall/door/monster/prop 直接从格子栈里删;event 是删踩格触发器;npc 是删 NPC。"""
        entry = self._entry("disappear", data)
        if not entry:
            return
        floor = self._floor()
        for kind, positions in entry.items():
            for pos in positions or []:
                x, y = _xy(pos)
                if kind == "event":
                    _api_call(self.api, "remove_trigger", floor, x, y)
                elif kind == "npc":
                    _api_call(self.api, "remove_npc", floor, x, y)
                elif kind == "wall":
                    _pop_cell(self.api, floor, x, y, _is_wallish)
                elif kind == "monster":
                    _pop_cell(self.api, floor, x, y, lambda c: c.get("kind") == "monster")
                else:                              # door / prop
                    _pop_cell(self.api, floor, x, y, lambda c: c.get("kind") == kind)

    def _act_move(self, data):
        """移动。数据是 [{path:{层名:[[延时, 起点, 终点], ...]}, interval, speed}, ...]。
        延时/速度是演出节奏,瞬间完成。"""
        entry = self._entry("move", data)
        if not entry:
            return
        floor = self._floor()
        for layer, segments in (entry.get("path") or {}).items():
            for seg in segments or []:
                _delay, frm, to = seg[0], seg[1], seg[2]
                _api_call(self.api, "move", floor, layer, int(frm), int(to))

    def _act_be_attack(self, data):
        """【语义考据】"挨打特效":参考实现只是播 beAttacked 音效 + 放光效图标,
        一点血都不扣(开场被四个警卫围殴抢装备,靠后面的 sceneAppear 数值重置表达)。"""
        data = data or {}
        if data.get("hero"):
            positions = [list(_xy(p)) for p in data["hero"]]
            return {"op": "effect", "kind": "beAttack", "target": "hero", "positions": positions}
        if data.get("monster") is not None:
            x, y = _xy(data["monster"])
            return {"op": "effect", "kind": "beAttack", "target": "monster", "positions": [[x, y]]}
        return None

    def _act_scene_appear(self, data):
        """场景淡入 + 勇士"被夺装备后醒来":参考实现在这里调 HeroModel.weak()
        ——HP/攻/防 重置成 WEAKEN_ATTRS(400/10/10)、卸下剑盾,然后传送到指定层。
        data=[层号, 展平落点]。"""
        floor, pos = data[0], data[1]
        x, y = _xy(pos)
        hero = self.state["hero"]
        for attr, value in WEAKEN_ATTRS.items():
            hero[attr] = value
        hero["sword"] = None
        hero["shield"] = None
        hero["pos"] = [x, y]
        self.state["floor"] = floor
        _add_visited(self.state, floor)
        _api_call(self.api, "jump_floor", floor, x, y)
        return {"op": "effect", "kind": "sceneAppear", "floor": floor, "pos": [x, y]}

    def _act_weak(self, data):
        """削弱。data=[怪物位置(展平), 比例]:该位置那只怪 HP/攻/防 ×= 比例。
        注意第一个数是【位置】不是怪物 id(49 层封印:[27, 0.1]=27 号格上的假魔王
        只剩一成功力),api.weaken 收怪物 id,所以先从地图上找到那只怪。"""
        pos, ratio = data[0], data[1]
        x, y = _xy(pos)
        grid = _api_call(self.api, "get_floor", self._floor())
        stack = grid[y][x] or []
        monster_id = None
        for cell in reversed(stack):
            if cell.get("kind") == "monster":
                monster_id = cell.get("id")
                break
        if monster_id is None:
            self.warnings.append(f"weak:位置 {pos}({x},{y}) 上没有怪物,跳过")
            return
        _api_call(self.api, "weaken", monster_id, ratio)

    def _act_jump(self, data):
        """直接传送。data=[层号, x, y]——勘误第 1 条:后两个就是格子坐标(事件 27:
        jump [50,5,5]=传送到 50 层 (5,5),这是公主线通往真结局的暗门)。"""
        floor, x, y = data[0], data[1], data[2]
        self.state["floor"] = floor
        self.state["hero"]["pos"] = [x, y]
        _add_visited(self.state, floor)
        _api_call(self.api, "jump_floor", floor, x, y)


# ============================================================
# 二、NpcFlow:撞上一个 NPC 之后的一场对话
# ============================================================

class NpcFlow:
    """跟一个 NPC 的完整对话(小偷/老人/商人/公主),同样走 step / feed 协议。

    用法:
        flow = NpcFlow(npc_id, state, data, api, rules=None, npc_pos=None)
        intent = flow.step(); …… 直到 done。

    npc_pos=(x, y) 是可选的"这个 NPC 站在哪"(引擎撞到它时肯定知道)。小偷类 NPC
    说完话要开暗道、走位、离场,没这个位置就算不了起点——不传也能跑,只是
    走位会被跳过并记进 warnings。

    一场对话的流程(大白话):
        1. event_talk 没解锁(比如 29 层小偷还在挖暗道)→ 只说那句"我还在忙",散会;
        2. 把 talk 数组的台词一句句说掉(小偷每说完一句,开一扇暗道门、走一步路);
        3. 按类型办事:
             商人(卖你东西,propGold<0)→ 问"买/不买";
             商人(收购,propGold>0)     → 问"卖/不卖";
             祝福商人(attack/defence)  → 问"接受/拒绝",接受后攻防各乘 (1+比例);
             老人送钱/送道具/送血      → 直接入账,不用问;
        4. npc.event 非空 → 对白结束后接力一个 EventRunner 继续演;
        5. 收尾:非 unlimit 的 NPC 消费一次(记 flags.npcs_done,done 意图带
           remove_npc=True 提示引擎把它从地图上拿掉);unlimit 的(回收商人等)永远在。

    【简化说明】原版是"每撞一次说一句",我们一场对话把台词全说完;拒绝买卖的
    NPC 保持原样可以再撞(和原版一致),已消费的第二次撞上来直接 done。
    """

    def __init__(self, npc_id, state, data, api, rules=None, npc_pos=None):
        self.npc_id = int(npc_id)
        self.state = state
        self.data = data
        self.api = api
        self.rules = rules
        self.npc_pos = tuple(npc_pos) if npc_pos else None
        self.warnings = []
        npc = (data.get("npcs") or {}).get(str(npc_id))
        if npc is None:
            raise EventError(f"NPC {npc_id} 不在数据表 npcs.json 里")
        self.npc = npc
        self._talk = list(npc.get("talk") or [])
        self._i = 0                     # 台词说到第几句
        self._last_flat = None         # 小偷最近走到的一维坐标(走下一步的起点)
        self._phase = "talk"            # talk → legs → offer → msg → chain → end
        self._mode = "run"              # run / wait / event / done
        self._intent = None
        self._msg = None
        self._offer = None             # buy / sell / bless
        self._sub = None               # 接力的 EventRunner
        self._consumed = False         # 这场对话有没有把 NPC 的"一次性"用掉
        self._removed = False          # 小偷走位后已经自己删过自己了

        flags = _flags(state)
        # 一次性 NPC:礼物已送过 / 买卖已成交 → 第二次撞上来直接完事
        if self.npc_id in flags.setdefault("npcs_done", []):
            self._phase = "end"
            self._mode = "done"
            return
        # event_talk 没解锁:参考实现 Npc.talk() 的优先级——只说 eventTalk 这一句,
        # 别的(台词/走位/挂的事件)统统不发生,直到事件 clearNpcEvent 把它清掉
        self._talk_locked = bool(npc.get("event_talk")) and \
            self.npc_id not in flags.setdefault("npcs_event_talk_cleared", [])
        if self._talk_locked:
            self._phase = "event_talk"

    # ---------- 对外协议 ----------

    def step(self):
        """和 EventRunner 一样:问一句"下一步给玩家看什么"。"""
        if self._mode == "done":
            return self._done_intent()
        if self._mode == "wait":
            return self._intent
        if self._mode == "event":                    # 接力的事件在演,让它说
            intent = self._sub.step()
            if intent["op"] in ("done", "pending"):
                self._mode = "done"
            return intent

        guard = 0
        while True:
            guard += 1
            if guard > 60:
                raise EventError(f"NPC {self.npc_id} 的对话流程疑似死循环")
            if self._phase == "event_talk":          # 只说那句"我还在忙"
                return self._wait({"op": "chat", "lines": [self.npc["event_talk"]]})
            if self._phase == "end":
                self._mode = "done"
                return self._done_intent()
            if self._phase == "talk":
                if self._i < len(self._talk):
                    page = self._talk[self._i]
                    lines = [page] if isinstance(page, str) else [str(x) for x in page]
                    self._record(self._i, page)
                    return self._wait({"op": "chat", "lines": lines})
                self._phase = "legs"                 # 台词说完 → 小偷收尾(没有就空过)
            if self._phase == "legs":
                self._run_thief_finish()
                self._phase = "offer"
            if self._phase == "offer":
                value = self.npc.get("value") or {}
                if self._needs_choices(value):
                    return self._wait(self._offer_choices(value))
                self._apply_gift(value)              # 送钱/送道具/送血:直接入账
                self._consume_once()
                self._phase = "chain"
            if self._phase == "msg":                 # 一句提示(钱不够之类),说完继续
                return self._wait({"op": "chat", "lines": [self._msg]})
            if self._phase == "chain":
                ev = self.npc.get("event")
                if ev is not None:
                    ev_data = (self.data.get("events") or {}).get(str(ev))
                    if ev_data is None:
                        raise EventError(f"NPC {self.npc_id} 挂的事件 {ev} 不在 events.json 里")
                    self._sub = EventRunner(ev_data, self.state, self.api, self.rules, self.data)
                    self._mode = "event"
                    return self.step()
                self._phase = "end"

    def feed(self, response):
        """玩家操作完了:对白/提示喂 None;选择支喂选项序号(0 起)。"""
        if self._mode == "event":
            self._sub.feed(response)
            return
        if self._mode != "wait":
            return
        op = self._intent.get("op")
        if op == "chat":
            if self._phase == "talk":
                self._i += 1
                if self._is_thief():
                    self._thief_leg(self._i - 1)      # 小偷每说完一句,开一扇门+走一步
            elif self._phase == "event_talk":
                self._phase = "end"                   # 那句"我还在忙"说完就散会
            elif self._phase == "msg":
                self._phase = "chain"                 # 提示说完,收尾
            self._mode = "run"
        elif op == "choices":
            self._on_choice(response)

    # ---------- 内部实现 ----------

    def _is_thief(self):
        return self.npc.get("type") == "thief"

    def _wait(self, intent):
        self._intent = intent
        self._mode = "wait"
        return intent

    def _record(self, index, page):
        """拿着记事本(道具 19)才会把对白记下来(原版 recordTalk 同样的门槛)。
        event_talk 那句不记(原版记录的是数字下标,"event"下标被跳过)。"""
        if not _has_prop(self.state, 19):
            return
        text = page if isinstance(page, str) else " ".join(str(x) for x in page)
        _records(self.state).append({"npc": self.npc_id, "index": index, "text": text})

    def _cur_flat(self):
        """小偷当前在哪(一维坐标):最近走到的地方,否则出场位置。"""
        if self._last_flat is not None:
            return self._last_flat
        if self.npc_pos:
            return _flat(self.npc_pos[0], self.npc_pos[1])
        return None

    def _thief_leg(self, idx):
        """小偷说完第 idx 句(0 起)台词后干的活:开第 idx 扇暗道门 + 走到第 idx 个
        落脚点(参考实现 NpcInteractiveSystem:npcMove → 开 wall[moveIndex] 的门 → 走)。"""
        walls = self.npc.get("wall") or []
        moves = self.npc.get("move") or []
        if idx < len(walls):
            x, y = _xy(walls[idx])
            _pop_cell(self.api, self.state["floor"], x, y, _is_wallish)
        if idx < len(moves):
            frm = self._cur_flat()
            if frm is None:
                self.warnings.append(
                    f"NPC {self.npc_id} 要走位但没有起点(npc_pos 没传),跳过这一步"
                )
                return
            _api_call(self.api, "move", self.state["floor"], "npc", frm, int(moves[idx]))
            self._last_flat = int(moves[idx])

    def _run_thief_finish(self):
        """台词说完后小偷收尾:把还没走的落脚点走完,走到头就消失
        (参考实现:moveEnd → DisappearCommand;不认识位置就留给引擎收尾)。"""
        if not self._is_thief():
            return
        moves = self.npc.get("move") or []
        for idx in range(len(self._talk), len(moves)):
            frm = self._cur_flat()
            if frm is None:
                self.warnings.append(f"NPC {self.npc_id} 剩余走位缺起点,跳过")
                return
            _api_call(self.api, "move", self.state["floor"], "npc", frm, int(moves[idx]))
            self._last_flat = int(moves[idx])
        if self._last_flat is not None:
            x, y = _xy(self._last_flat)
            _api_call(self.api, "remove_npc", self.state["floor"], x, y)
            self._removed = True

    def _needs_choices(self, value):
        """要不要问玩家?三种商人要问:卖东西的、收购的、祝福的;老人送礼直接给。"""
        if value.get("propGold") is not None:
            self._offer = "buy" if value["propGold"] < 0 else "sell"
            return True
        if value.get("attack") is not None or value.get("defence") is not None:
            self._offer = "bless"
            return True
        return False

    def _offer_choices(self, value):
        """拼"买/不买、卖/不卖、接受/拒绝"的选择支。"""
        pg = value.get("propGold")
        if self._offer == "buy":
            what = self._trade_summary(value, sign=1)
            return {"op": "choices", "options": [
                {"label": f"买:{what}(花 {-pg} 金币)", "choice": "buy"},
                {"label": "不买", "choice": "no"},
            ]}
        if self._offer == "sell":
            what = self._trade_summary(value, sign=-1)
            return {"op": "choices", "options": [
                {"label": f"卖:{what}(得 {pg} 金币)", "choice": "sell"},
                {"label": "不卖", "choice": "no"},
            ]}
        # 祝福商人:attack/defence 是比例(0.03=乘 1.03,勘误第 9 条:是乘法!)
        pct = []
        if value.get("attack") is not None:
            pct.append(f"攻击+{round(value['attack'] * 100)}%")
        if value.get("defence") is not None:
            pct.append(f"防御+{round(value['defence'] * 100)}%")
        return {"op": "choices", "options": [
            {"label": f"接受祝福({','.join(pct)})", "choice": "bless"},
            {"label": "拒绝", "choice": "no"},
        ]}

    def _trade_summary(self, value, sign):
        """买卖东西的清单文字:道具名×数量;卖血的商人(34 层)写"生命+2000"。"""
        parts = []
        if value.get("hp"):
            parts.append(f"生命+{value['hp']}")
        names = (self.data.get("items") or {})
        for pid, cnt in _prop_pairs(value.get("prop") or []):
            if cnt * sign > 0:                       # 买只列正数数量,卖只列负数
                name = names.get(str(pid), {}).get("name", f"道具{pid}")
                parts.append(f"{name}×{abs(cnt)}")
        return ",".join(parts) or "神秘货物"

    def _apply_gift(self, value):
        """老人送礼(没有 propGold 的 value):送钱 / 送道具 / 送血,直接入账。"""
        hero = self.state["hero"]
        if value.get("gold"):
            hero["gold"] += value["gold"]
        if value.get("hp"):
            hero["hp"] += value["hp"]
        props = _props(self.state)
        for pid, cnt in _prop_pairs(value.get("prop") or []):
            if cnt > 0:
                props[str(pid)] = props.get(str(pid), 0) + cnt

    def _consume_once(self):
        """这场对话把 NPC 的"一次性"用掉了:记进 flags.npcs_done(第二次撞直接完事)。"""
        if self.npc.get("unlimit") or self._consumed:
            return
        self._consumed = True
        done = _flags(self.state).setdefault("npcs_done", [])
        if self.npc_id not in done:
            done.append(self.npc_id)

    def _on_choice(self, idx):
        """玩家在"买/不买、卖/不卖、接受/拒绝"里选了一个。"""
        options = self._intent.get("options") or []
        if not isinstance(idx, int) or not (0 <= idx < len(options)):
            return                                    # 手滑喂了个没用的序号:原样重问
        value = self.npc.get("value") or {}
        pg = value.get("propGold")
        hero = self.state["hero"]
        if self._offer == "buy" and idx == 0:
            cost = -pg
            if hero["gold"] < cost:                    # 原版:"你的钱不够",买卖不成
                self._msg = f"你的钱不够,需要 {cost} 金币。"
                self._phase = "msg"
            else:
                hero["gold"] -= cost
                if value.get("hp"):
                    hero["hp"] += value["hp"]
                props = _props(self.state)
                for pid, cnt in _prop_pairs(value.get("prop") or []):
                    if cnt > 0:
                        props[str(pid)] = props.get(str(pid), 0) + cnt
                self._consume_once()
                self._phase = "chain"
        elif self._offer == "sell" and idx == 0:
            pairs = [(p, c) for p, c in _prop_pairs(value.get("prop") or []) if c < 0]
            props = _props(self.state)
            for pid, cnt in pairs:                    # 手里一个都没有就别卖了
                if props.get(str(pid), 0) < -cnt:
                    self._msg = "你没有可以卖出的物品。"
                    self._phase = "msg"
                    break
            else:
                for pid, cnt in pairs:
                    left = props.get(str(pid), 0) + cnt
                    if left > 0:
                        props[str(pid)] = left
                    else:
                        props.pop(str(pid), None)
                hero["gold"] += pg
                self._consume_once()
                self._phase = "chain"
        elif self._offer == "bless" and idx == 0:
            # 祝福:攻/防 乘 (1+比例)。比如 0.03 就是各乘 1.03(保留小数,越买越划算)
            if value.get("attack") is not None:
                hero["attack"] = hero["attack"] * (1 + value["attack"])
            if value.get("defence") is not None:
                hero["defence"] = hero["defence"] * (1 + value["defence"])
            self._consume_once()
            self._phase = "chain"
        else:
            self._phase = "chain"                     # 不买/不卖/拒绝:什么都不动
        self._mode = "run"                            # 无论哪条路,下一轮 step 接着走

    def _done_intent(self):
        """收尾意图:非 unlimit 且消费过的 NPC 提示引擎"把他从地图上拿掉"
        (原版:买卖成交/台词说完 → NPC 消失;小偷自己走位消失的不再重复提示)。"""
        out = {"op": "done"}
        if self._consumed and not self._removed:
            out["remove_npc"] = True
        return out


# ============================================================
# 三、AltarFlow:祭坛商店(4/12/32/46 层,三选一)
# ============================================================

class AltarFlow:
    """撞上祭坛格子(kind="altar")后的商店对话。价格和效果全走注入的 rules:
    rules.altar_price(n)=第 n 次的价格(设计文档 §3.1:10×(n²−n+2),即 20/40/80/140…),
    rules.altar_buy(state, choice) 负责真正扣钱加属性(钱不够会抛 ValueError)。

    流程:三选一 + "离开";买完可以继续买(菜单循环);钱不够先提示一句,再回菜单
    重选;次数(价格)由 core 记在 state["flags"]["altar_count"],全塔跨楼层共享。
    """

    def __init__(self, state, data, rules):
        if rules is None:
            raise EventError("altar_flow 需要 rules 适配器(祭坛价格/购买都归 core 算)")
        self.state = state
        self.data = data
        self.rules = rules
        self._mode = "menu"          # menu / wait / msg / done
        self._intent = None
        self._msg = None

    def step(self):
        if self._mode == "done":
            return {"op": "done"}
        if self._mode == "wait":
            return self._intent
        if self._mode == "msg":                       # 钱不够的提示说一句,再回菜单
            return self._wait({"op": "chat", "lines": [self._msg]})
        return self._wait(self._menu())               # 正常:出菜单

    def feed(self, response):
        """喂选项序号:0/1/2=买生命/买攻/买防,3=离开;提示页喂 None。"""
        if self._mode == "wait" and self._intent.get("op") == "chat":
            self._mode = "menu"                       # 提示说完 → 回菜单
            return
        if self._mode != "wait" or self._intent.get("op") != "choices":
            return
        if not isinstance(response, int) or not (0 <= response < 4):
            return                                   # 手滑喂了没用的序号:原样重问
        if response == 3:                             # 离开
            self._mode = "done"
            return
        choice = ("hp", "attack", "defence")[response]
        n = _flags(self.state).get("altar_count", 0) + 1     # 这是第几次买
        price = self.rules.altar_price(n)
        try:
            self.rules.altar_buy(self.state, choice)         # 钱不够:core 抛 ValueError
        except ValueError:
            self._msg = f"你的钱不够,需要 {price} 金币。"    # 提示后可重选,次数不涨
            self._mode = "msg"
            return
        self._mode = "menu"                                  # 买完继续逛

    # ---------- 内部实现 ----------

    def _wait(self, intent):
        self._intent = intent
        self._mode = "wait"
        return intent

    def _menu(self):
        """拼菜单:显示当前价格和三样效果(祭坛区倍率跟当前层走:攻 +2×区、防 +4×区)。
        生命 +100×n(n=全塔第 n 次买)。"""
        n = _flags(self.state).get("altar_count", 0) + 1
        price = self.rules.altar_price(n)
        area = self.state["floor"] // 10 + 1
        return {"op": "choices", "options": [
            {"label": f"生命 +{100 * n}(花费 {price} 金币)", "choice": "hp"},
            {"label": f"攻击 +{2 * area}(花费 {price} 金币)", "choice": "attack"},
            {"label": f"防御 +{4 * area}(花费 {price} 金币)", "choice": "defence"},
            {"label": "离开商店", "choice": "leave"},
        ]}


# ============================================================
# 四、飞行魔杖(唯一要玩家选目标的道具,所以做成小 flow)
# ============================================================

def fly_targets(state):
    """飞行魔杖能去的楼层:去过的层,但 44 层(异空间)去不了。"""
    return sorted({int(v) for v in state.get("visited", []) if int(v) != 44})


class FlyWandFlow:
    """飞行魔杖:选一个去过的楼层(44 层除外)飞过去。
    【引擎侧注意】原版要求勇士站在楼梯旁边才能用(附录 B),这个校验归引擎做
    (它知道楼梯在哪);不满足就别开这个 flow。"""

    def __init__(self, state, api, data=None):
        self.state = state
        self.api = api
        self.targets = fly_targets(state)
        self._mode = "choose" if self.targets else "done"

    def step(self):
        if self._mode == "done":
            return {"op": "done"}
        return {"op": "choices",
                "options": [{"label": f"第 {f} 层", "floor": f} for f in self.targets]}

    def feed(self, response):
        if self._mode != "choose":
            return
        if not isinstance(response, int) or not (0 <= response < len(self.targets)):
            return                                    # 手滑:原样重问
        target = self.targets[response]
        self.state["floor"] = target
        _add_visited(self.state, target)
        # x,y 传 None = 落到目标层的楼梯口(引擎按楼梯数据定落点)
        _api_call(self.api, "jump_floor", target, None, None)
        self._mode = "done"


# ============================================================
# 五、其余道具:用了立刻生效,返回 {"ok": 布尔, "msg": 提示}
# ============================================================

def use_wing(state, data, api, item_id):
    """上/下飞行器:直接去上/下一层(一次性)。能不能越界 core 不管,这里拦住。"""
    item = data["items"][str(item_id)]
    delta = item.get("floor_delta")
    if delta is None:
        return {"ok": False, "msg": "这个道具没有上下飞行数据"}
    target = state["floor"] + delta
    if not (0 <= target <= 50):
        return {"ok": False, "msg": "你已经到最边上的楼层了"}
    state["floor"] = target
    _add_visited(state, target)
    _api_call(api, "jump_floor", target, None, None)   # None,None=落在楼梯口
    _consume_tool(state, data, item_id)
    return {"ok": True, "msg": f"飞到了第 {target} 层"}


def use_center_wing(state, data, api, item_id):
    """中心飞行器:飞到本层"中心对称"的格子(共 3 次)。目标必须是空地,有东西就失败。
    44 层的密宝就是靠它飞进去的(附录 B)。"""
    x, y = state["hero"]["pos"]
    tx, ty = 10 - x, 10 - y                           # 11×11 地图的中心对称
    grid = _api_call(api, "get_floor", state["floor"])
    if grid[ty][tx]:
        return {"ok": False, "msg": "飞行的地方有障碍物"}
    state["hero"]["pos"] = [tx, ty]
    _api_call(api, "jump_floor", state["floor"], tx, ty)
    _consume_tool(state, data, item_id)
    return {"ok": True, "msg": f"飞到了 ({tx},{ty})"}


def use_pickaxe(state, data, api, item_id, direction=None):
    """镐:敲掉面前一格墙(一次性)。direction=(dx, dy) 是勇士当前面朝的方向,
    引擎最清楚(最后一次按键),所以要传进来。"""
    if not direction:
        return {"ok": False, "msg": "需要知道面朝方向才能用镐"}
    x, y = state["hero"]["pos"]
    tx, ty = x + direction[0], y + direction[1]
    if not (0 <= tx <= 10 and 0 <= ty <= 10):
        return {"ok": False, "msg": "面前没有墙"}
    popped = _pop_cell(api, state["floor"], tx, ty, _is_wallish)
    if popped is None:
        return {"ok": False, "msg": "面前没有墙"}
    _consume_tool(state, data, item_id)
    return {"ok": True, "msg": "敲掉了一面墙"}


def use_quake_scroll(state, data, api, item_id):
    """地震卷轴:摧毁本层全部的墙(一次性,39 层商人 4000 金币卖的就是它)。
    【语义说明】参考实现遍历的是"墙层",所以只删 kind=wall 的格子;
    连地图四周的边界墙也一起删——原版就这样,走出地图靠引擎的边界判定挡住。"""
    floor = state["floor"]
    grid = _api_call(api, "get_floor", floor)
    walls = [(x, y) for y, row in enumerate(grid) for x, st in enumerate(row)
            if st and any(c.get("kind") == "wall" for c in st)]
    if not walls:
        return {"ok": False, "msg": "本层没有墙"}
    for x, y in walls:
        _pop_cell(api, floor, x, y, lambda c: c.get("kind") == "wall")
    _consume_tool(state, data, item_id)
    return {"ok": True, "msg": f"摧毁了 {len(walls)} 面墙"}


def use_ice_magic(state, data, api):
    """冰冻魔法:把周围(上下左右四格)的岩浆冻成平地。无限使用,不消耗。
    【语义说明】附录 B 说"周围岩浆"没定几格;参考实现是四直邻,照它来。"""
    floor = state["floor"]
    x, y = state["hero"]["pos"]
    frozen = 0
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        tx, ty = x + dx, y + dy
        if 0 <= tx <= 10 and 0 <= ty <= 10:
            if _pop_cell(api, floor, tx, ty, lambda c: c.get("kind") == "lava") is not None:
                frozen += 1
    if not frozen:
        return {"ok": False, "msg": "周围没有岩浆"}
    return {"ok": True, "msg": f"冻住了 {frozen} 格岩浆"}


def use_bomb(state, data, api, item_id):
    """炸弹:炸死周围一圈(8 格)的非头目怪(一次性)。
    【语义存疑】附录 B 写"周围一圈"(8 格),参考实现只炸 4 直邻——按附录 B 实现
    8 格;头目(boss=true,如大法师/魔龙)炸不动,两边说法一致。"""
    floor = state["floor"]
    x, y = state["hero"]["pos"]
    killed = 0
    for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
        tx, ty = x + dx, y + dy
        if not (0 <= tx <= 10 and 0 <= ty <= 10):
            continue
        popped = _pop_cell(api, floor, tx, ty, lambda c: c.get("kind") == "monster")
        if popped is None:
            continue
        info = (data.get("monsters") or {}).get(str(popped.get("id"))) or {}
        if info.get("boss"):                          # 头目炸不动,把格子放回去
            _push_cell(api, floor, tx, ty, popped)
            continue
        killed += 1
    if not killed:
        return {"ok": False, "msg": "周围没有可以炸的怪物"}
    _consume_tool(state, data, item_id)
    return {"ok": True, "msg": f"炸死了 {killed} 只怪物"}


def use_magic_key(state, data, api, item_id):
    """魔法钥匙:打开本层全部黄门(一次性)。"""
    floor = state["floor"]
    grid = _api_call(api, "get_floor", floor)
    doors = [(x, y) for y, row in enumerate(grid) for x, st in enumerate(row)
             if st and any(c.get("kind") == "door" and c.get("id") == 1001 for c in st)]
    if not doors:
        return {"ok": False, "msg": "本层没有黄门"}
    for x, y in doors:
        _pop_cell(api, floor, x, y, lambda c: c.get("kind") == "door" and c.get("id") == 1001)
    _consume_tool(state, data, item_id)
    return {"ok": True, "msg": f"打开了 {len(doors)} 扇黄门"}


def use_holy_water(state, data, item_id):
    """圣水:HP += 攻 + 防(一次性;附录 B:"越晚用越赚",攻击防御攒高了再喝)。"""
    hero = state["hero"]
    gain = hero["attack"] + hero["defence"]
    hero["hp"] += gain
    _consume_tool(state, data, item_id)
    return {"ok": True, "msg": f"生命 +{gain}"}


def use_tool(state, data, api, item_id, direction=None):
    """道具统一入口(引擎点"使用道具"就调这里)。
    飞行魔杖返回 FlyWandFlow(有 step/feed,要玩家选目标);
    其余立刻生效,返回 {"ok": 布尔, "msg": 提示文字} 给引擎去弹提示。"""
    item = (data.get("items") or {}).get(str(item_id))
    if item is None:
        raise EventError(f"道具 {item_id} 不在数据表 items.json 里")
    if not _has_prop(state, item_id):
        return {"ok": False, "msg": "你还没有这个道具"}
    tool = item.get("tool")
    if tool == "fly_wand":
        return FlyWandFlow(state, api, data)
    if tool == "wing":
        return use_wing(state, data, api, item_id)
    if tool == "center_wing":
        return use_center_wing(state, data, api, item_id)
    if tool == "pickaxe":
        return use_pickaxe(state, data, api, item_id, direction)
    if tool == "quake_scroll":
        return use_quake_scroll(state, data, api, item_id)
    if tool == "ice_magic":
        return use_ice_magic(state, data, api)
    if tool == "bomb":
        return use_bomb(state, data, api, item_id)
    if tool == "magic_key":
        return use_magic_key(state, data, api, item_id)
    if tool == "holy_water":
        return use_holy_water(state, data, item_id)
    if tool in ("monster_manual", "notebook"):
        return {"ok": False, "msg": "查看型道具:怪物手册按 H 键,记事本看 UI 界面"}
    if tool in ("lucky_coin", "cross", "dragon_slayer"):
        return {"ok": False, "msg": "被动道具(带上就生效),不用主动使用"}
    return {"ok": False, "msg": f"不认识的道具类型:{tool!r}"}


# ============================================================
# 六、怪物手册 / 记事本(纯数据,引擎拿去渲染)
# ============================================================

def manual_data(state, data, rules, floor_monsters):
    """怪物手册数据(H 键)。floor_monsters 是【本层还活着的怪】,元素可以是怪物 id
    (int)也可以是怪物表条目(dict),引擎按出现顺序去重后传进来。
    每只怪返回:属性(名字/HP/攻/防/金币)+ 战斗预测(能不能打、损血、几个回合),
    预测全部来自注入的 rules.calc_battle(和实战是同一个函数,保证手册不骗人)。
    十字架/幸运金币/屠龙匕的开关从道具栏自动识别。"""
    flags = {
        "cross": _has_prop(state, 28),
        "lucky_coin": _has_prop(state, 27),
        "dragon_slayer": _has_prop(state, 29),
    }
    out = []
    for m in floor_monsters:
        entry = m if isinstance(m, dict) else (data.get("monsters") or {}).get(str(m))
        if entry is None:
            raise EventError(f"怪物手册:怪物 {m} 不在怪物表里")
        battle = rules.calc_battle(state["hero"], entry, flags)
        out.append({
            "id": entry.get("id"),
            "name": entry.get("name"),
            "hp": entry.get("hp"),
            "attack": entry.get("attack"),
            "defence": entry.get("defence"),
            "gold": battle.get("gold", entry.get("gold")),
            "can_fight": battle.get("can_fight"),
            "hero_damage": battle.get("hero_damage"),
            "turns": battle.get("turns"),
        })
    return out


def notebook_data(state, data):
    """记事本数据:把 NPC 对话时攒下的记录(state["records"],只有拿着记事本才会
    记)翻译成能直接渲染的列表。"""
    npcs = data.get("npcs") or {}
    out = []
    for rec in state.get("records", []):
        npc = npcs.get(str(rec.get("npc"))) or {}
        out.append({
            "npc": rec.get("npc"),
            "name": npc.get("desc", "?"),
            "index": rec.get("index"),
            "line": rec.get("text"),
        })
    return out
