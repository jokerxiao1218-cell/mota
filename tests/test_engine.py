"""batch 3 引擎测试:大部分走 Fake 适配器(模拟测试),SDL 无头跑。

为什么用 Fake:引擎对 core(规则层)/ events(事件层)的一切调用都走
适配器注入(设计文档 §4.4),用"罐头返回值"的替身把引擎自身的分派逻辑
钉死——真实模块的行为各有各的测试(test_core/test_events),这里只关心
"引擎有没有把该传的参数传对、该走的流程走对"。数值结论(如先攻怪损血)
单靠罐头验不出来,所以 test_first_attack_real_data_floor40 那条特意
换上真实 calc_battle + 真实 40 层数据做端到端。
"""

import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")   # 无头跑(命令行没带也兜底)

import pygame
import pytest

pygame.init()          # 字体/显示子系统初始化(dummy 模式下安全)

from game import assets as assets_mod
from game import loader
from game import ui
from game.engine import (Engine, LandingError, default_landing_resolver)

BASE = loader.load_all()      # 真实数据表(怪物/道具/NPC/事件),只读共享


# ================================================================ 造数据工具

def cell(kind, **kw):
    """造一个格子数据(栈里的一项)。"""
    d = {"kind": kind}
    d.update(kw)
    return d


def make_floor(stacks=None, npcs=None, triggers=None, stairs=None,
               first_attack=None, guard_doors=None):
    """造一张测试楼层:空地图 + 指定格子栈。stacks = {(x, y): [cell, ...]}"""
    grid = [[None] * 11 for _ in range(11)]
    for (x, y), stack in (stacks or {}).items():
        grid[y][x] = list(stack)
    return {
        "floor": 2, "tmx": "test",
        "grid": grid,
        "stairs": stairs or [],
        "npcs": npcs or [],
        "triggers": triggers or [],
        "guard_doors": guard_doors or [],
        "kill_triggers": [],
        "first_attack": first_attack or [],
        "layer_props": {},
    }


def make_data(**floors):
    """真实表 + 测试楼层(替换全部楼层,好精确控制每格)。"""
    data = {k: BASE[k] for k in ("monsters", "items", "npcs", "events", "tiles")}
    data["floors"] = {str(k): v for k, v in floors.items()}
    return data


def make_state(floor=2, pos=(3, 3), hp=400):
    """造一份符合 §4.4-A 的开局状态。"""
    return {
        "floor": floor,
        "hero": {
            "hp": hp, "attack": 10, "defence": 10, "gold": 0,
            "keys": {"yellow": 0, "blue": 0, "red": 0},
            "props": {}, "sword": None, "shield": None,
            "pos": list(pos),
        },
        "flags": {"altar_count": 0, "events_done": [], "monsters_dead": []},
        "floors_state": {},
        "visited": [floor],
    }


# ================================================================ Fake 适配器

class FakeRules:
    """规则层替身:返回值可预设,调用全部记录。"""

    def __init__(self):
        self.can_fight = True
        self.hero_damage = 7
        self.gold_gain = 10
        self.turns = 3
        self.battles = []          # (怪物id, flags)
        self.pickups = []          # (道具id, 名字)
        self.adjacent = 0          # 巫师魔伤返回值
        self.adjacent_calls = []
        self.guard_calls = []
        self.saved = []            # (槽位, 存档时血量)
        self.loaded = None        # load_game 的返回值
        self.load_error = None     # 设了就抛

    def new_state(self):
        return make_state()

    def calc_battle(self, hero, monster, flags):
        self.battles.append((monster["id"], flags))
        return {"can_fight": self.can_fight, "hero_damage": self.hero_damage,
                "turns": self.turns, "gold": self.gold_gain}

    def apply_pickup(self, state, item_id, data):
        # 真实契约(§4.4-B):第三参是 loader 大字典,规则层自己查 items 表
        item = data["items"][str(item_id)]
        self.pickups.append((item_id, item["name"]))
        if item["kind"] == "key":                 # 模拟真实规则:钥匙计数
            color = {1001: "yellow", 1002: "blue", 1003: "red"}[item["door"]]
            state["hero"]["keys"][color] += 1
        else:
            state["hero"]["props"][str(item_id)] = 1
        return state

    def adjacent_damage(self, state, floor_doc, x, y):
        self.adjacent_calls.append((x, y))
        return self.adjacent

    def guard_trap(self, state, floor_doc, x, y):
        self.guard_calls.append((x, y))
        return state

    def save_game(self, state, slot):
        self.saved.append((slot, state["hero"]["hp"]))

    def load_game(self, slot):
        if self.load_error:
            raise self.load_error
        return self.loaded


class FakeEvents:
    """事件层替身:调用记录 + 可脚本化的 intent 队列。
    方法集 = §4.4-C 契约的 batch 6 扩展版(talk 带 npc_pos、enter_floor、
    usable_tools/use_tool);不做任何真实逻辑,只记调用、按脚本吐意图。"""

    def __init__(self):
        self.talks = []           # 撞过的 NPC 条目
        self.talk_pos = []        # 撞到时的 NPC 位置
        self.altars = 0
        self.starts = []          # 启动过的事件条目
        self.feeds = []           # feed 过的响应
        self.script = []          # step() 依次吐这些,吐完就是 done
        self.entered = []         # 进层钩子收到过的层号
        self.tools_used = []      # use_tool 收到过的 (道具id, 方向)

    def talk(self, npc, state, npc_pos=None):
        self.talks.append(npc)
        self.talk_pos.append(npc_pos)

    def altar_flow(self, state):
        self.altars += 1

    def start(self, event, state):
        self.starts.append(event)

    def usable_tools(self, state):
        return []

    def use_tool(self, item_id, direction=None):
        self.tools_used.append((item_id, direction))
        return {"ok": False, "msg": ""}

    def enter_floor(self, floor):
        self.entered.append(floor)
        return False

    def step(self):
        if self.script:
            return self.script.pop(0)
        return {"op": "done"}

    def feed(self, response):
        self.feeds.append(response)


class RealCalcRules(FakeRules):
    """calc_battle 换成真实 game.core 版本(其余仍走 Fake):
    专门验证"引擎传出去的 flags + §3.1 真公式"端到端算得对——
    先攻怪这类的数值结论,光看 Fake 的罐头返回值是验不出来的。"""

    def calc_battle(self, hero, monster, flags):
        from game.core import calc_battle
        self.battles.append((monster["id"], flags))
        return calc_battle(hero, monster, flags)


def make_engine(floor=None, rules=None, events=None, state=None,
                floors=None, **kwargs):
    """拼一个引擎:默认第 2 层测试地图,勇士站在 (3,3)。
    floors 可补额外楼层(比如换层目标层)。"""
    all_floors = dict(floors or {})
    all_floors.setdefault("2", floor if floor is not None else make_floor())
    data = make_data(**all_floors)
    rules = rules or FakeRules()
    events = events or FakeEvents()
    state = state or make_state()
    return Engine(data, rules, events, state, **kwargs)


# ================================================================ 贴图测试

def test_assets_every_kind_generates_16x16():
    """每个 kind 的代表 id(含全部 34 怪、32 道具、6 种门、4 种 NPC)都能画出 16×16。"""
    a = assets_mod.Assets(BASE)
    cases = [
        {"kind": "floor"}, {"kind": "wall"}, {"kind": "lava"},
        {"kind": "star"}, {"kind": "big_part"}, {"kind": "altar"},
        {"kind": "unknown"},
        {"kind": "stair", "dir": "up"}, {"kind": "stair", "dir": "down"},
    ]
    cases += [{"kind": "door", "id": d} for d in (1001, 1002, 1003, 1004, 1005, 1006)]
    cases += [{"kind": "monster", "id": int(mid)} for mid in BASE["monsters"]]
    cases += [{"kind": "prop", "id": int(pid)} for pid in BASE["items"]]
    cases += [{"kind": "npc", "sprite_id": int(nid)} for nid in BASE["npcs"]]
    for case in cases:
        surface = a.cell(case)
        assert isinstance(surface, pygame.Surface), case
        assert surface.get_size() == (16, 16), case
    assert a.hero().get_size() == (16, 16)


def test_assets_scaled_and_cached():
    """32×32 放大版正常,且同 id 两次取到同一个对象(缓存生效)。"""
    a = assets_mod.Assets(BASE)
    case = {"kind": "monster", "id": 100}
    assert a.cell_big(case).get_size() == (32, 32)
    assert a.cell(case) is a.cell(case)
    assert a.cell_big(case) is a.cell_big(case)


# ================================================================ 移动与阻挡

def test_move_empty_cell():
    """走空地:位置更新,每步后的特殊机制被调过。"""
    eng = make_engine()
    eng.try_move(1, 0)                       # (3,3) -> (4,3)
    assert eng.state["hero"]["pos"] == [4, 3]
    assert eng.rules.adjacent_calls == [(4, 3)]
    assert eng.rules.guard_calls == [(4, 3)]


def test_move_blocked_by_wall():
    """撞墙:原地不动。"""
    eng = make_engine(make_floor(stacks={(5, 3): [cell("wall")]}))
    eng.try_move(1, 0)
    assert eng.state["hero"]["pos"] == [4, 3]
    eng.try_move(1, 0)                       # 撞墙
    assert eng.state["hero"]["pos"] == [4, 3]
    assert eng.floor_doc()["grid"][3][5][0]["kind"] == "wall"


def test_move_out_of_map_does_nothing():
    """走出地图边缘:原地不动不报错。"""
    eng = make_engine()
    eng.state["hero"]["pos"] = [0, 0]
    eng.try_move(-1, 0)
    eng.try_move(0, -1)
    assert eng.state["hero"]["pos"] == [0, 0]


def test_bump_monster_cannot_fight():
    """撞怪打不过(calc_battle 预判 can_fight=False):移动被拒,怪还在。"""
    rules = FakeRules()
    rules.can_fight = False
    eng = make_engine(make_floor(stacks={(4, 3): [cell("monster", id=100)]}), rules=rules)
    eng.try_move(1, 0)
    assert eng.state["hero"]["pos"] == [3, 3]              # 没动
    assert eng.floor_doc()["grid"][3][4][0]["id"] == 100  # 怪没消失
    assert rules.battles[0][0] == 100                      # 预判调过


def test_bump_monster_win_settles():
    """撞怪打得过:扣血/加金币/弹栈/顺势走进格子/记 monsters_dead。"""
    rules = FakeRules()
    eng = make_engine(make_floor(stacks={(4, 3): [cell("monster", id=100)]}), rules=rules)
    eng.try_move(1, 0)
    hero = eng.state["hero"]
    assert hero["pos"] == [4, 3]                 # 打赢走进怪物的格子
    assert hero["hp"] == 400 - 7
    assert hero["gold"] == 10
    assert eng.floor_doc()["grid"][3][4] is None          # 怪从栈移除
    assert eng.state["flags"]["monsters_dead"] == ["2:4,3"]
    # 战斗开关:没带十字架/幸运金币/屠龙匕;普通位置怪不先攻
    assert rules.battles[0][1] == {"cross": False, "lucky_coin": False,
                                   "dragon_slayer": False, "first_attack": False}


def test_bump_monster_passes_battle_flags():
    """带十字架(道具28)撞怪:flags 传对。"""
    rules = FakeRules()
    eng = make_engine(make_floor(stacks={(4, 3): [cell("monster", id=111)]}), rules=rules)
    eng.state["hero"]["props"]["28"] = 1
    eng.try_move(1, 0)
    assert rules.battles[0][1] == {"cross": True, "lucky_coin": False,
                                   "dragon_slayer": False, "first_attack": False}


def test_bump_monster_with_event_starts_event():
    """杀掉挂事件的怪(骷髅队长107挂事件4):事件适配器被调,拿到真实事件条目。"""
    events = FakeEvents()
    eng = make_engine(make_floor(stacks={(4, 3): [cell("monster", id=107)]}),
                      events=events)
    eng.try_move(1, 0)
    assert len(events.starts) == 1
    assert events.starts[0]["id"] == 4            # events.json 里真的有这个事件


def test_battle_death_when_hp_runs_out():
    """虽然预判挡住了必败战斗,但血量被特殊机制扣光时进死亡画面。"""
    rules = FakeRules()
    rules.adjacent = 500                          # 巫师魔伤直接致死
    eng = make_engine(rules=rules)
    eng.try_move(1, 0)                            # 走一步触发每步结算
    assert eng.state["hero"]["hp"] == 0
    assert eng.mode == "gameover"


# ================================================================ 门

def test_key_door_opens_with_key():
    """黄门:有钥匙→开门+钥匙-1(开门耗一步,人没走进去);无钥匙→纹丝不动。"""
    stacks = {(4, 3): [cell("door", id=1001)]}
    state = make_state()
    state["hero"]["keys"]["yellow"] = 1
    eng = make_engine(make_floor(stacks=stacks), state=state)
    eng.try_move(1, 0)
    assert eng.floor_doc()["grid"][3][4] is None           # 门没了
    assert eng.state["hero"]["keys"]["yellow"] == 0        # 钥匙花了
    assert eng.state["hero"]["pos"] == [3, 3]              # 开门消耗这一步
    assert "黄" in eng.message


def test_key_door_without_key():
    eng = make_engine(make_floor(stacks={(4, 3): [cell("door", id=1001)]}))
    eng.try_move(1, 0)
    assert eng.floor_doc()["grid"][3][4][0]["id"] == 1001  # 门还在
    assert eng.state["hero"]["keys"]["yellow"] == 0
    assert eng.state["hero"]["pos"] == [3, 3]


@pytest.mark.parametrize("did", [1004, 1005])
def test_event_doors_stay_shut(did):
    """监狱门(1004)/怪物门(1005):只挡路,等事件/守卫,引擎不开。"""
    eng = make_engine(make_floor(stacks={(4, 3): [cell("door", id=did)]}))
    eng.try_move(1, 0)
    assert eng.floor_doc()["grid"][3][4][0]["id"] == did
    assert eng.state["hero"]["pos"] == [3, 3]
    assert "打不开" in eng.message


def test_wall_door_opens_on_bump():
    """墙门(1006,勘误终审):一撞就开,弹栈露出下层。"""
    eng = make_engine(make_floor(
        stacks={(4, 3): [cell("prop", id=1), cell("door", id=1006)]}))  # 门压着黄钥匙
    eng.try_move(1, 0)
    grid_cell = eng.floor_doc()["grid"][3][4]
    assert grid_cell[0]["kind"] == "prop"        # 门弹掉,钥匙露出来
    assert eng.state["hero"]["keys"]["yellow"] == 0
    assert eng.state["hero"]["pos"] == [3, 3]    # 撞门这一步没走


def test_pick_up_prop_revealed_by_door():
    """叠放栈:门在道具上层 → 开门 → 道具可见 → 走上去捡起来。"""
    stacks = {(4, 3): [cell("prop", id=1), cell("door", id=1001)]}
    state = make_state()
    state["hero"]["keys"]["yellow"] = 1
    eng = make_engine(make_floor(stacks=stacks), state=state)
    eng.try_move(1, 0)                           # 开黄门
    assert eng.floor_doc()["grid"][3][4][0]["id"] == 1
    eng.try_move(1, 0)                           # 走上去捡钥匙
    assert eng.floor_doc()["grid"][3][4] is None          # 道具弹出,栈空置 None
    assert eng.state["hero"]["pos"] == [4, 3]
    assert eng.rules.pickups == [(1, "黄色钥匙")]
    assert eng.state["hero"]["keys"]["yellow"] == 1       # Fake 规则把钥匙记上了


def test_floors_state_records_changes():
    """格子栈的每次变化都记进 floors_state(存档=整包快照的底账)。"""
    stacks = {(4, 3): [cell("monster", id=100)]}
    eng = make_engine(make_floor(stacks=stacks))
    eng.try_move(1, 0)                           # 杀怪
    changes = eng.state["floors_state"]["2"]
    assert changes == [[4, 3, None]]              # 该格变空


# ================================================================ 楼梯换层

def test_stair_calls_landing_resolver():
    """踩楼梯:换层走注入的 landing_resolver,落点/楼层/visited 都对。"""
    calls = []

    def resolver(floor, direction):
        calls.append((floor, direction))
        return (7, 2, 5)

    stacks = {(4, 3): [cell("stair", dir="up")]}
    eng = make_engine(make_floor(stacks=stacks), floors={"7": make_floor()},
                      landing_resolver=resolver)
    eng.try_move(1, 0)
    assert calls == [(2, "up")]
    assert eng.state["floor"] == 7
    assert eng.state["hero"]["pos"] == [2, 5]
    assert 7 in eng.state["visited"]


def test_default_landing_resolver():
    """默认落点(stair_links 规则,勘误终审):
    上楼→新层 down_stand / 下楼→新层 up_stand;43 层 up_diff=+2 绕过 44 层。"""
    resolve = default_landing_resolver(BASE)
    # 2 层上 → 3 层,落在 3 层 down_stand(下梯 (0,10) 旁的站位 (1,10))
    assert resolve(2, "up") == (3, 1, 10)
    # 2 层下 → 1 层,落在 1 层 up_stand(1 层只有一张上梯,站位在梯旁)
    assert resolve(2, "down") == (1, 1, 0)
    # 43 层上梯 diff=+2 直接跳到 45 层(44 层是异空间)
    assert resolve(43, "up") == (45, 1, 0)
    # 45 层下梯 diff=-2 回 43 层
    assert resolve(45, "down") == (43, 0, 9)
    with pytest.raises(LandingError):             # 50 层顶上没有 51 层
        resolve(50, "up")
    with pytest.raises(LandingError):             # 49 层上楼 → 50 层无楼梯
        resolve(49, "up")


def test_hidden_stair_does_not_change_floor():
    """隐藏楼梯(hide 属性):踩了不换层,也不进渲染视野。"""
    calls = []

    def resolver(floor, direction):
        calls.append((floor, direction))
        return (3, 0, 10)

    stacks = {(4, 3): [cell("stair", dir="up", hide=True)]}
    eng = make_engine(make_floor(stacks=stacks), landing_resolver=resolver)
    eng.try_move(1, 0)
    assert eng.state["floor"] == 2                # 没换层
    assert calls == []                           # resolver 根本没被调
    assert eng.state["hero"]["pos"] == [4, 3]     # 格子本身能踩
    assert eng._view()[3][4] is None             # 渲染视野里看不见它


# ================================================================ NPC / 触发器 / 祭坛

def test_bump_npc_calls_events():
    """撞 NPC:把 npcs.json 的真实条目交给事件适配器,人不过去。"""
    events = FakeEvents()
    npcs = [{"npc": 5, "x": 4, "y": 3}]           # 5 号 = 送圣水的老人
    stacks = {(4, 3): [cell("npc", sprite_id=2)]}
    eng = make_engine(make_floor(stacks=stacks, npcs=npcs), events=events)
    eng.try_move(1, 0)
    assert len(events.talks) == 1
    assert events.talks[0]["id"] == 5
    assert events.talks[0]["type"] == "elder"
    assert eng.state["hero"]["pos"] == [3, 3]     # NPC 挡路,没走过去


def test_step_onto_placed_npc_without_grid_cell():
    """数据里没画 NPC 格的摆放点(如 39 层商人):走到格子上也算撞见,触发对话。"""
    events = FakeEvents()
    npcs = [{"npc": 7, "x": 4, "y": 3}]           # 7 号 = 卖蓝钥匙的商人
    eng = make_engine(make_floor(npcs=npcs), events=events)
    eng.try_move(1, 0)
    assert len(events.talks) == 1
    assert events.talks[0]["id"] == 7


def test_step_onto_trigger_starts_event():
    """踩到触发格:事件适配器拿到该触发的真实事件条目。"""
    events = FakeEvents()
    triggers = [{"x": 4, "y": 3, "event": 3}]
    eng = make_engine(make_floor(triggers=triggers), events=events)
    eng.try_move(1, 0)
    assert len(events.starts) == 1
    assert events.starts[0]["id"] == 3


def test_bump_altar_calls_events():
    """撞祭坛:走 events.altar_flow。"""
    events = FakeEvents()
    stacks = {(4, 3): [cell("altar")]}
    eng = make_engine(make_floor(stacks=stacks), events=events)
    eng.try_move(1, 0)
    assert events.altars == 1
    assert eng.state["hero"]["pos"] == [3, 3]     # 祭坛挡路


# ================================================================ intent 协议

def test_chat_intent_feed_protocol():
    """chat intent:进对话模式,空格推进(feed None),吐完 done 回到游玩。"""
    events = FakeEvents()
    events.script = [{"op": "chat", "lines": ["小偷:我们一起越狱吧。"]}]
    eng = make_engine(events=events)
    eng.event_active = True                       # 模拟事件已被触发
    eng._poll_intent()
    assert eng.mode == "dialog"
    assert eng.intent["op"] == "chat"
    eng.handle_key(pygame.K_SPACE)
    assert events.feeds == [None]                 # chat 喂 None
    assert eng.mode == "play"                     # 脚本吐完 → done → 回游玩


def test_choices_intent_feed_index():
    """choices intent:↑↓ 选,回车喂 0 起序号。"""
    events = FakeEvents()
    events.script = [
        {"op": "choices", "options": [{"label": "买"}, {"label": "不买"}]},
        {"op": "done"},
    ]
    eng = make_engine(events=events)
    eng.event_active = True
    eng._poll_intent()
    assert eng.mode == "dialog"
    eng.handle_key(pygame.K_DOWN)                 # 光标到 1
    assert eng.choice_sel == 1
    eng.handle_key(pygame.K_RETURN)
    assert events.feeds == [1]                    # choices 喂序号
    assert eng.mode == "play"


def test_unknown_intent_reports_clearly():
    """未知 op 的 intent:给明确提示并退出事件,不许静默卡死。"""
    events = FakeEvents()
    events.script = [{"op": "shop", "data": []}]
    eng = make_engine(events=events)
    eng.event_active = True
    eng._poll_intent()
    assert eng.mode == "play"
    assert "未知事件意图" in eng.message


def test_movement_locked_during_dialog():
    """对话期间方向键不走路。"""
    events = FakeEvents()
    events.script = [{"op": "chat", "lines": ["……"]}]
    eng = make_engine(events=events)
    eng.event_active = True
    eng._poll_intent()
    eng.handle_key(pygame.K_RIGHT)
    assert eng.state["hero"]["pos"] == [3, 3]     # 没动
    assert eng.mode == "dialog"


# ================================================================ Esc 菜单/存读档

def test_menu_save_then_load():
    """Esc 菜单:存档把状态交给规则层;读档换掉整个状态。
    (存档门槛"需怪物手册"见 test_menu_save_requires_manual,这里持有手册。)"""
    eng = make_engine()
    eng.state["hero"]["props"]["18"] = 1             # 有怪物手册才能存档
    eng.handle_key(pygame.K_ESCAPE)               # 打开菜单
    assert eng.mode == "menu"
    eng.handle_key(pygame.K_RETURN)               # 选"存档"→ 槽位子菜单
    eng.handle_key(pygame.K_RETURN)               # 槽位 1
    assert eng.rules.saved == [(1, 400)]
    assert eng.mode == "play"

    eng.rules.loaded = make_state(pos=(9, 9), hp=123)   # 假装存档里是这个
    eng.handle_key(pygame.K_ESCAPE)
    eng.handle_key(pygame.K_DOWN)                # 光标到"读档"
    eng.handle_key(pygame.K_RETURN)
    eng.handle_key(pygame.K_RETURN)               # 槽位 1
    assert eng.state["hero"]["hp"] == 123
    assert eng.state["hero"]["pos"] == [9, 9]


def test_menu_save_requires_manual():
    """原版设定:没拿怪物手册(道具18)→ 菜单选存档直接被拒(core 不判,引擎菜单层拒)。"""
    eng = make_engine()
    eng.handle_key(pygame.K_ESCAPE)
    eng.handle_key(pygame.K_RETURN)               # 选"存档"
    assert eng.rules.saved == []                  # 根本没走到存档
    assert eng.menu_stack == []                   # 连槽位子菜单都没进
    assert "怪物手册" in eng.message

    eng2 = make_engine()
    eng2.state["hero"]["props"]["18"] = 1
    eng2.handle_key(pygame.K_ESCAPE)
    eng2.handle_key(pygame.K_RETURN)               # 选"存档"
    assert len(eng2.menu_stack) == 2              # 持手册:正常进槽位子菜单
    eng2.handle_key(pygame.K_RETURN)               # 槽位 1
    assert eng2.rules.saved == [(1, 400)]


def test_menu_load_bad_save_shows_message():
    """坏档:读档失败只给提示,不崩,菜单正常关掉。"""
    rules = FakeRules()
    rules.load_error = ValueError("save/save_1.json 损坏:不是合法 JSON")
    eng = make_engine(rules=rules)
    eng.handle_key(pygame.K_ESCAPE)
    eng.handle_key(pygame.K_DOWN)
    eng.handle_key(pygame.K_RETURN)
    eng.handle_key(pygame.K_RETURN)
    assert eng.mode == "play"
    assert "读档失败" in eng.message


def test_menu_cancel_with_escape():
    """子菜单里按 Esc 一层层退回,不残留。(持手册进存档子菜单来测退回路径)"""
    eng = make_engine()
    eng.state["hero"]["props"]["18"] = 1             # 存档门槛:得有怪物手册
    eng.handle_key(pygame.K_ESCAPE)
    eng.handle_key(pygame.K_RETURN)               # 进槽位子菜单
    assert len(eng.menu_stack) == 2
    eng.handle_key(pygame.K_ESCAPE)               # 退一层
    assert len(eng.menu_stack) == 1
    eng.handle_key(pygame.K_ESCAPE)               # 退到底
    assert eng.mode == "play"


# ================================================================ 怪物手册

def test_manual_requires_book():
    """没拿怪物手册(道具18)按 H:提示看不了;拿了:进面板,本层怪按表列出。"""
    eng = make_engine(make_floor(stacks={(4, 3): [cell("monster", id=120)],
                                         (6, 3): [cell("monster", id=120)]}))
    eng.handle_key(pygame.K_h)
    assert eng.mode == "play"
    assert "怪物手册" in eng.message

    eng.state["hero"]["props"]["18"] = 1
    eng.handle_key(pygame.K_h)
    assert eng.mode == "manual"
    assert len(eng.manual_rows) == 1              # 两只同 id 怪只列一行
    assert eng.manual_rows[0]["name"] == "中级卫兵"
    assert eng.manual_rows[0]["verdict"].startswith("损血")   # Fake 预判
    eng.handle_key(pygame.K_ESCAPE)
    assert eng.mode == "play"


# ================================================================ 先攻怪(2026-09-06 用户拍板启用)

def test_first_attack_flags_on_bump():
    """楼层 first_attack 位置表:撞表里的怪 flags.first_attack=True,表外同 id 怪 False。"""
    rules = FakeRules()
    fl = make_floor(stacks={(4, 3): [cell("monster", id=124)]},
                    first_attack=[[4, 3]])
    eng = make_engine(fl, rules=rules)
    eng.try_move(1, 0)                              # 撞 (4,3):在先攻表里
    assert rules.battles[0][1]["first_attack"] is True

    rules2 = FakeRules()
    fl2 = make_floor(stacks={(4, 3): [cell("monster", id=124)]})
    fl2["first_attack"] = []                        # 表里没有这一格
    eng2 = make_engine(fl2, rules=rules2)
    eng2.try_move(1, 0)
    assert rules2.battles[0][1]["first_attack"] is False


def test_first_attack_expires_after_kill():
    """先攻怪死后该位置失效:授权跟"这只怪"走,原位再冒新怪不先攻。

    失效经 monsters_dead 判断(不运行时删表):位置上的怪死过即作废,
    台账在 state 里,存档读档天然还原,也不会污染 loader 源数据。
    """
    fl = make_floor(stacks={(4, 3): [cell("monster", id=124)]},
                    first_attack=[[4, 3]])
    eng = make_engine(fl)
    eng.try_move(1, 0)                              # 杀掉先攻怪(顺势走进格子)
    assert eng.rules.battles[0][1]["first_attack"] is True
    assert eng.state["flags"]["monsters_dead"] == ["2:4,3"]

    eng.state["hero"]["pos"] = [3, 3]               # 挪开,给新怪腾位
    eng.api_spawn_monster(2, 4, 3, 124)             # 事件在同格又冒一只同 id 怪
    eng.try_move(1, 0)                              # 再撞:不先攻了
    assert eng.rules.battles[1][1]["first_attack"] is False


def test_first_attack_manual_lists_both_rows():
    """怪物手册:同 id 怪先攻/普通各一只 → 列两行,先攻行带标记、预测各自算。"""
    rules = FakeRules()
    fl = make_floor(stacks={(4, 3): [cell("monster", id=124)],
                            (6, 3): [cell("monster", id=124)]},
                    first_attack=[[4, 3]])
    eng = make_engine(fl, rules=rules)
    eng.state["hero"]["props"]["18"] = 1            # 先拿到手册
    eng.handle_key(pygame.K_h)
    assert eng.mode == "manual"
    assert len(eng.manual_rows) == 2                # 不再按怪 id 合并成一行
    names = sorted(r["name"] for r in eng.manual_rows)
    assert names == ["骑士队长", "骑士队长(先攻)"]
    flags_seen = sorted(b[1]["first_attack"] for b in rules.battles)
    assert flags_seen == [False, True]              # 两种位置各自带开关去预测


def test_first_attack_real_data_floor40():
    """端到端(真实 40 层数据 + 真实 core.calc_battle):
    先攻位 (1,1) 的双手剑士 121 比非先攻位 (9,8) 的同种怪多挨一整刀。

    英雄 5000/75/600:每击 75-50=25 → 4 刀;怪每击 680-600=80;
    普通 = (4-1)×80 = 240,先攻 = 4×80 = 320,差 80 = 恰好一刀(§3.1)。
    """
    rules = RealCalcRules()

    def state_at(pos):
        s = make_state(floor=40, pos=pos, hp=5000)
        s["hero"]["attack"] = 75
        s["hero"]["defence"] = 600
        return s

    eng1 = make_engine(floors={"40": BASE["floors"]["40"]}, rules=rules,
                       state=state_at((1, 2)))
    eng1.try_move(0, -1)                            # 撞 (1,1):先攻表里的 121
    assert eng1.rules.battles[0][1]["first_attack"] is True
    assert eng1.state["hero"]["hp"] == 5000 - 320

    eng2 = make_engine(floors={"40": BASE["floors"]["40"]}, rules=rules,
                       state=state_at((9, 7)))
    eng2.try_move(0, 1)                             # 撞 (9,8):同种怪但不在表里
    assert eng2.rules.battles[1][1]["first_attack"] is False
    assert eng2.state["hero"]["hp"] == 5000 - 240

    # 结论钉死:先攻比普通多挨的正是"怪一刀"的量(d2 = 怪攻680 − 防600)
    assert (5000 - eng1.state["hero"]["hp"]) - (5000 - eng2.state["hero"]["hp"]) == 80


# ================================================================ 1005 守卫门

def test_guard_door_opens_when_all_guards_dead():
    """怪物门(1005,勘误终审):守卫还剩一只 → 门关着;杀光 → 自动弹开露出下层。"""
    fl = make_floor(
        stacks={(4, 3): [cell("monster", id=100)],            # 守卫甲
                (6, 3): [cell("monster", id=100)],            # 守卫乙
                (4, 5): [cell("prop", id=1), cell("door", id=1005)]},  # 门压着黄钥匙
        guard_doors=[{"doors": [[4, 5]], "guards": [[4, 3], [6, 3]]}])
    eng = make_engine(fl)
    eng.state["hero"]["pos"] = [3, 3]
    eng.try_move(1, 0)                                # 杀守卫甲(顺势走进它的格子)
    assert eng.floor_doc()["grid"][5][4][-1]["id"] == 1005   # 乙还活着:门没开

    eng.state["hero"]["pos"] = [5, 3]                 # 挪到乙旁边
    eng.try_move(1, 0)                                # 杀守卫乙 → 这组守卫全灭
    grid_cell = eng.floor_doc()["grid"][5][4]
    assert grid_cell[0]["kind"] == "prop"             # 门弹掉,下层钥匙露出来
    assert "怪物门开了" in eng.message
    # 开门也进了 floors_state 台账(存档快照的一部分)
    assert eng.state["floors_state"]["2"][-1][0:2] == [4, 5]


# ================================================================ 事件层 api

def test_events_api_keys():
    """build_api 的键名以 game/events.py 文件头 docstring 为准(14 键),
    少一个事件层就会在演出中途报"api 注入不完整"。"""
    api = make_engine().build_api()
    expected = {
        "get_floor", "set_cell", "spawn_monster", "add_npc", "remove_npc",
        "add_trigger", "remove_trigger", "move", "weaken",
        "register_monster_door", "collide", "jump_floor", "show",
        "clear_npc_event",
    }
    assert set(api) == expected, f"api 键不对:多 {set(api) - expected} / 少 {expected - set(api)}"
    assert all(callable(v) for v in api.values())


def test_events_api():
    """给事件层的 api:get_floor/set_cell/spawn_monster/weaken + 记账规则。"""
    eng = make_engine()
    grid = eng.api_get_floor(2)
    assert len(grid) == 11 and len(grid[0]) == 11
    assert grid[3][4] is None

    eng.api_set_cell(2, 4, 3, [{"kind": "wall"}])
    assert eng.api_get_floor(2)[3][4][0]["kind"] == "wall"

    eng.api_spawn_monster(2, 4, 3, 100)            # 怪压在墙上面
    assert eng.api_get_floor(2)[3][4][-1]["id"] == 100

    eng.api_weaken(132, 0.1)                      # 49 层封印:假魔王 ×0.1
    weakened = eng._monster(132)
    assert weakened["hp"] == 800 and weakened["attack"] == 500

    # 削弱对真实怪物表无副作用(引擎存的是倍率副本)
    assert BASE["monsters"]["132"]["hp"] == 8000


def test_api_set_cell_records_monsters_dead():
    """要点①:事件把怪"变没"(旧栈有怪、新栈没怪)→ 记 monsters_dead,
    和玩家亲手杀怪一个口径(先攻失效/守卫门判定都认这本账)。"""
    eng = make_engine(make_floor(stacks={(4, 3): [cell("monster", id=100)]}))
    eng.api_set_cell(2, 4, 3, None)                # 事件让怪直接消失
    assert eng.state["flags"]["monsters_dead"] == ["2:4,3"]
    # 反向:空格摆怪不记死;同格再清一遍也不重复记
    eng.api_spawn_monster(2, 4, 3, 100)
    eng.api_set_cell(2, 4, 3, None)
    assert eng.state["flags"]["monsters_dead"] == ["2:4,3"]


def test_api_set_cell_killing_guard_opens_door():
    """事件炸守卫(set_cell 清怪)同样触发守卫门检查——道具杀的也是杀。"""
    fl = make_floor(stacks={(4, 3): [cell("monster", id=100)],
                            (6, 3): [cell("monster", id=100)],
                            (4, 5): [cell("door", id=1005)]},
                    guard_doors=[{"doors": [[4, 5]], "guards": [[4, 3], [6, 3]]}])
    eng = make_engine(fl)
    eng.api_set_cell(2, 4, 3, None)                # 炸掉守卫一:门还关着
    assert eng.api_get_floor(2)[5][4][-1]["id"] == 1005
    eng.api_set_cell(2, 6, 3, None)                # 炸掉守卫二:门开了
    assert eng.api_get_floor(2)[5][4] is None


def test_api_jump_floor_defaults_to_stair():
    """jump_floor:x/y 传 None → 落目标层楼梯口(stair_links.up_stand,
    没写 stair_links 就退回第一张上楼梯);传坐标就直接落。"""
    fl7 = make_floor(stairs=[{"dir": "up", "x": 2, "y": 5}])
    fl8 = make_floor(stairs=[{"dir": "down", "x": 9, "y": 9}])
    fl8["stair_links"] = {"up_stand": [3, 7]}
    eng = make_engine(floors={"7": fl7, "8": fl8})
    eng.state["hero"]["pos"] = [1, 1]
    eng.api_jump_floor(7, None, None)             # 无 stair_links → 上梯兜底
    assert eng.state["floor"] == 7
    assert eng.state["hero"]["pos"] == [2, 5]
    assert 7 in eng.state["visited"]
    eng.api_jump_floor(8, None, None)              # 有 up_stand → 落站位
    assert eng.state["hero"]["pos"] == [3, 7]
    eng.api_jump_floor(7, 8, 8)                    # 指定坐标:原样落
    assert eng.state["hero"]["pos"] == [8, 8]
    with pytest.raises(ValueError):                # 目标层不存在:明说,不静默
        eng.api_jump_floor(99, 1, 1)


def test_api_move_flattened_coords():
    """move:起止是展平坐标(0~120);起格没这东西抛错不静默。"""
    eng = make_engine(make_floor(stacks={(4, 3): [cell("npc", sprite_id=1)]}))
    eng.api_move(2, "npc", 3 * 11 + 4, 6 * 11 + 4)   # (4,3) → (4,6)
    assert eng.api_get_floor(2)[3][4] is None
    assert eng.api_get_floor(2)[6][4][0]["kind"] == "npc"
    with pytest.raises(ValueError):                  # 起格空了再挪:报清楚
        eng.api_move(2, "npc", 3 * 11 + 4, 0)


def test_api_register_monster_door_end_to_end():
    """register_monster_door(展平坐标):自动摆 1005 门 + 登记守卫组;
    守卫全灭 → 门开(和地图原生 guard_doors 同一条路)。"""
    fl = make_floor(stacks={(4, 3): [cell("monster", id=100)],     # 守卫
                            (6, 3): [cell("monster", id=100)]})    # 守卫
    eng = make_engine(fl)
    eng.api_register_monster_door(4 * 11 + 4, [3 * 11 + 4, 3 * 11 + 6])
    # 门位置原来是空格:现在顶上是 1005
    assert eng.api_get_floor(2)[4][4][-1]["id"] == 1005

    eng.state["hero"]["pos"] = [3, 3]
    eng.try_move(1, 0)                              # 杀守卫一
    eng.state["hero"]["pos"] = [5, 3]
    eng.try_move(1, 0)                              # 杀守卫二 → 门开
    assert eng.api_get_floor(2)[4][4] is None
    assert "怪物门开了" in eng.message


def test_api_collide_fights_monster():
    """collide:事件模拟勇者撞格——当前层的怪直接开战结算。"""
    rules = FakeRules()
    eng = make_engine(make_floor(stacks={(4, 3): [cell("monster", id=100)]}),
                      rules=rules)
    eng.api_collide(2, 4, 3)
    assert rules.battles[0][0] == 100               # 真打了
    assert eng.state["hero"]["hp"] == 400 - 7
    assert eng.api_get_floor(2)[3][4] is None      # 怪没了
    with pytest.raises(ValueError):                # 撞别的层:坐标对不上要报错
        eng.api_collide(3, 4, 3)


def test_api_show_reveals_hidden_cells():
    """show:摘掉隐藏标记,渲染视野里立刻可见。"""
    eng = make_engine(make_floor(stacks={(4, 3): [cell("stair", dir="up", hide=True)]}))
    assert eng._view()[3][4] is None               # 藏着看不见
    eng.api_show(2, 4, 3)
    assert eng._view()[3][4] is not None           # 现形了


def test_api_npc_and_trigger_plumbing():
    """add_npc/remove_npc/add_trigger/remove_trigger/clear_npc_event 的增删。"""
    events = FakeEvents()
    eng = make_engine(make_floor(), events=events)
    eng.api_add_npc(2, 4, 3, 7)                    # 摆个 7 号商人
    assert eng._npc_entry_at(4, 3)["id"] == 7
    stack = eng.api_get_floor(2)[3][4]
    assert stack and stack[-1]["kind"] == "npc"
    eng.try_move(1, 0)                              # 撞他 → 对话
    assert events.talks[-1]["id"] == 7

    eng.state["hero"]["pos"] = [3, 3]
    eng.api_remove_npc(2, 4, 3)                     # 撤场:摆放表和格子都清
    assert eng._npc_entry_at(4, 3) is None
    assert eng.api_get_floor(2)[3][4] is None

    eng.api_add_trigger(2, 4, 3, 3)                 # 隐形触发格
    eng.state["hero"]["pos"] = [3, 3]
    eng.try_move(1, 0)
    assert events.starts[-1]["id"] == 3
    eng.api_remove_trigger(2, 4, 3)                 # 撤掉,再踩不触发
    eng.state["hero"]["pos"] = [3, 3]
    eng.try_move(1, 0)
    assert events.starts[-1]["id"] == 3             # 没有新触发

    # clear_npc_event:摆放条目打上覆盖标记,拿到的条目 event_talk=None
    eng.api_add_npc(2, 4, 3, 21)                    # 21 号 = 29 层小偷
    assert eng._npc_entry_at(4, 3)["event_talk"] is not None
    eng.api_clear_npc_event(2, 4, 3)
    assert eng._npc_entry_at(4, 3)["event_talk"] is None
    assert BASE["npcs"]["21"]["event_talk"] is not None   # 源数据没被动过
    with pytest.raises(ValueError):                # 清一个没摆 NPC 的格:报错
        eng.api_clear_npc_event(2, 9, 9)


# ================================================================ 渲染冒烟

def test_render_smoke_full_cycle():
    """dummy 显示模式:窗口建得起,真实第 2 层画一帧不崩;
    chat/choices/菜单/手册/死亡画面各画一帧也都不崩。"""
    screen = ui.create_screen()
    assert screen.get_size() == (ui.WIDTH, ui.HEIGHT)

    events = FakeEvents()
    eng = Engine(BASE, FakeRules(), events, make_state(floor=2, pos=(3, 6)),
                 screen=screen)
    eng.draw()                                    # 普通一帧(有怪有门有 NPC 有楼梯)

    eng.intent = {"op": "chat", "lines": ["魔王Zero说:欢迎来到魔塔。",
                                          "你是第一百位挑战者。"]}
    eng.mode = "dialog"
    eng.draw()                                    # 对话帧

    eng.intent = {"op": "choices",
                  "options": [{"label": "提升生命"}, {"label": "提升攻击"},
                              {"label": "提升防御"}]}
    eng.choice_sel = 1
    eng.draw()                                    # 选择支帧

    eng.open_menu()
    eng.draw()                                    # Esc 菜单帧
    eng.close_menu()

    eng.state["hero"]["props"]["18"] = 1
    eng.open_manual()
    eng.draw()                                    # 手册帧(真实第 2 层的怪)
    eng.mode = "play"

    eng.mode = "gameover"
    eng.draw()                                    # 死亡帧
    assert screen.get_size() == (ui.WIDTH, ui.HEIGHT)   # 画完窗口还活着


# ================================================================ 程序化像素目检
# 本环境的模型 API 只吃文本,看不了图(读图片文件会当场断线),所以"渲染得
# 对不对"不用存 PNG 肉眼验——直接在测试里读像素下断言,结论全用文字输出。

def _count_not(screen, rect, bg):
    """数一块区域里有多少像素 ≠ 背景色(文字笔画、贴图纹理都算)。"""
    bgc = pygame.Color(*bg)
    n = 0
    for y in range(rect.top, rect.bottom):
        for x in range(rect.left, rect.right):
            if screen.get_at((x, y)) != bgc:
                n += 1
    return n


def test_pixel_assertions_play_frame():
    """开局帧像素体检:窗口尺寸/状态栏有字有底色/地图铺了贴图/英雄画上去了/
    底部条有字/改 HP 后状态栏跟着变(HUD 是活的)。"""
    screen = ui.create_screen()
    assert screen.get_size() == (ui.WIDTH, ui.HEIGHT) == (576, 472)

    # 序章开局状态:1000/100/100,站 2 层 (3,6)
    state = make_state(floor=2, pos=(3, 6), hp=1000)
    state["hero"]["attack"] = 100
    state["hero"]["defence"] = 100
    state["hero"]["sword"] = "12"
    state["hero"]["shield"] = "17"
    eng = Engine(BASE, FakeRules(), FakeEvents(), state, screen=screen)
    eng.draw()

    # 1) 左侧状态栏:面板底色对,且画了字(标题/生命1000/攻100/防100/楼层/装备…)
    panel = pygame.Rect(0, 0, ui.PANEL_W, ui.MAP_H)
    assert screen.get_at((2, 2)) == pygame.Color(*ui.PANEL_BG)
    assert _count_not(screen, panel, ui.PANEL_BG) > 300, "状态栏区域几乎没有文字像素"

    # 2) 右侧地图:11×11 每格都铺了地板贴图,不是一块纯色背景
    mapa = pygame.Rect(ui.MAP_X, 0, ui.MAP_W, ui.MAP_H)
    assert _count_not(screen, mapa, ui.MAP_BG) > 3000, "地图区域几乎没有贴图纹理"

    # 3) 英雄真的画上去了:英雄格中心像素 ≠ 相邻格中心像素
    hx, hy = state["hero"]["pos"]

    def center(gx, gy):
        return (ui.MAP_X + gx * ui.CELL + ui.CELL // 2, gy * ui.CELL + ui.CELL // 2)

    assert screen.get_at(center(hx, hy)) != screen.get_at(center(hx + 1, hy)), \
        "英雄格和旁边的格像素一样,贴图八成没画"

    # 4) 底部提示条:有操作提示文字
    bar = pygame.Rect(0, ui.MAP_H, ui.WIDTH, ui.DIALOG_H)
    assert _count_not(screen, bar, ui.DIALOG_BG) > 50, "底部提示条没有文字"

    # 5) HUD 是活的:改生命值再画一帧,状态栏像素必须有变化
    before = _count_not(screen, panel, ui.PANEL_BG)
    state["hero"]["hp"] = 432
    eng.draw()
    after = _count_not(screen, panel, ui.PANEL_BG)
    assert after != before, "生命值改了状态栏一个像素没动,HUD 画的是死数据?"
    print(f"[像素目检] 状态栏非背景像素 {before}→{after},地图非背景像素 "
          f"{_count_not(screen, mapa, ui.MAP_BG)},英雄格与邻格颜色不同:全部通过")


def test_main_loop_headless_smoke():
    """SDL dummy 下真开 run() 主循环:投进事件队列的方向键真的驱动了移动,
    QUIT 正常退出循环——事件泵→handle_key→try_move 全链路无头跑通。"""
    pygame.event.clear()                            # 别让别的测试残留的事件插队
    screen = ui.create_screen()
    eng = make_engine(screen=screen)
    start = eng.state["hero"]["pos"][:]
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT))
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    eng.run()                                       # 收到 QUIT 自己返回,不卡死
    assert eng.state["hero"]["pos"] == [start[0] + 1, start[1]], \
        "主循环吃掉了方向键事件,英雄没动"
