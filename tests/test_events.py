"""batch 4 事件系统测试:喂 canned 回应的模拟测试,全程无显示。

测试对象 game/events.py:EventRunner(事件解释器)/ NpcFlow(NPC 对话)/
AltarFlow(祭坛商店)/ 道具使用 / manual_data(怪物手册)。

核心思路(设计文档 §4.4-C):不真开窗口,把"玩家会按什么键"提前写好,
一口口喂给 flow,然后断言两件事:
    1. flow 交出来的"意图"(intent)对不对;
    2. 注入的假 api / 假 rules 收到的调用对不对(坐标换算 x=pos%11、y=pos//11)。
"""

import pytest

from game import loader
from game.events import (
    AltarFlow,
    EventError,
    EventRunner,
    FlyWandFlow,
    NpcFlow,
    WEAKEN_ATTRS,
    fly_targets,
    manual_data,
    notebook_data,
    use_tool,
)


# ==================================================================
# 测试脚手架:假规则层(FakeRules)、假引擎(FakeApi)、公用小函数
# ==================================================================

def make_state(floor=2, hp=400, attack=10, defence=10, gold=0, props=None, visited=None):
    """照设计文档 §4.4-A 的结构现造一份 state(纯字典)。"""
    return {
        "floor": floor,
        "hero": {
            "hp": hp, "attack": attack, "defence": defence, "gold": gold,
            "keys": {"yellow": 0, "blue": 0, "red": 0},
            "props": {str(k): v for k, v in (props or {}).items()},
            "sword": None, "shield": None, "pos": [5, 10],
        },
        "flags": {"altar_count": 0, "events_done": [], "monsters_dead": []},
        "floors_state": {},
        "visited": [floor] if visited is None else visited,
    }


class FakeRules:
    """假规则层:把设计文档 §3.1 / §4.4-B 的公式照抄一份 canned 实现。
    真正的 core 由规则智能体并行开发,这里只保证事件层测试不依赖它。"""

    def new_state(self):
        return make_state()

    def area(self, floor):
        return floor // 10 + 1

    def calc_battle(self, hero, monster, flags):
        atk, dfc = hero["attack"], hero["defence"]
        mhp, matk, mdfc = monster["hp"], monster["attack"], monster["defence"]
        eff = atk                                  # 十字架/屠龙匕:攻×2,但只对特定怪
        if flags.get("cross") and monster["id"] in (111, 112, 115):
            eff = atk * 2
        if flags.get("dragon_slayer") and monster["id"] == 122:
            eff = atk * 2
        if atk <= mdfc:                             # 破防判定用【裸攻击】(原版怪癖)
            return {"can_fight": False, "hero_damage": 0, "turns": 0, "gold": 0}
        d1 = eff - mdfc
        n = (mhp - 1) // d1 + 1                     # 玩家出手次数
        d2 = max(0, matk - dfc)
        damage = (n - 1) * d2                        # 玩家先手,怪只反击 n-1 次
        gold = monster["gold"] * (2 if flags.get("lucky_coin") else 1)
        return {"can_fight": damage < hero["hp"], "hero_damage": damage,
                "turns": n, "gold": gold}

    def altar_price(self, n):
        return 10 * (n * n - n + 2)                  # §3.1:20/40/80/140/220/320…

    def altar_buy(self, state, choice):
        n = state["flags"].get("altar_count", 0) + 1
        price = self.altar_price(n)
        if state["hero"]["gold"] < price:
            raise ValueError(f"金币不够:需要 {price}")
        state["hero"]["gold"] -= price
        area = self.area(state["floor"])
        if choice == "hp":
            state["hero"]["hp"] += 100 * n
        elif choice == "attack":
            state["hero"]["attack"] += 2 * area
        elif choice == "defence":
            state["hero"]["defence"] += 4 * area
        else:
            raise ValueError(f"未知祭坛选项:{choice}")
        state["flags"]["altar_count"] = n
        return state


class FakeApi:
    """假引擎:api 契约的 16 个函数全做成"真会改内存地图 + 记录每次调用"的替身。
    既能把事件跑通,又能回过头断言收到了什么。"""

    def __init__(self):
        self.floors = {}
        self.calls = []

    def __getitem__(self, name):
        if hasattr(self, name):
            return getattr(self, name)
        raise KeyError(name)

    # —— 地图仓库(模拟引擎手里的楼层栈)——
    def grid(self, floor):
        if floor not in self.floors:
            self.floors[floor] = [[None] * 11 for _ in range(11)]
        return self.floors[floor]

    def put(self, floor, x, y, cell):
        g = self.grid(floor)
        g[y][x] = (g[y][x] or []) + [cell]

    # —— api 契约函数 ——
    def get_floor(self, floor):
        return self.grid(floor)

    def set_cell(self, floor, x, y, stack):
        self.calls.append(("set_cell", floor, x, y, tuple(stack) if stack else None))
        self.grid(floor)[y][x] = list(stack) if stack else None

    def spawn_monster(self, floor, x, y, monster_id):
        self.calls.append(("spawn_monster", floor, x, y, monster_id))
        self.put(floor, x, y, {"kind": "monster", "id": monster_id})

    def add_npc(self, floor, x, y, npc_id):
        self.calls.append(("add_npc", floor, x, y, npc_id))
        self.put(floor, x, y, {"kind": "npc", "id": npc_id})

    def remove_npc(self, floor, x, y):
        self.calls.append(("remove_npc", floor, x, y))

    def add_trigger(self, floor, x, y, event_id):
        self.calls.append(("add_trigger", floor, x, y, event_id))

    def remove_trigger(self, floor, x, y):
        self.calls.append(("remove_trigger", floor, x, y))

    def move(self, floor, kind, from_pos, to_pos):
        self.calls.append(("move", floor, kind, from_pos, to_pos))

    def weaken(self, monster_id, ratio):
        self.calls.append(("weaken", monster_id, ratio))

    def register_monster_door(self, door_pos, guards):
        self.calls.append(("register_monster_door", door_pos, guards))

    def collide(self, floor, x, y):
        self.calls.append(("collide", floor, x, y))

    def jump_floor(self, floor, x, y):
        self.calls.append(("jump_floor", floor, x, y))

    def show(self, floor, x, y):
        self.calls.append(("show", floor, x, y))

    def clear_npc_event(self, floor, x, y):
        self.calls.append(("clear_npc_event", floor, x, y))

    # —— 断言辅助 ——
    def calls_of(self, name):
        return [c for c in self.calls if c[0] == name]


def drive(flow, feeds):
    """喂一串 canned 回应,把一路拿到的意图全部收集起来。"""
    intents = []
    i = 0
    while True:
        intent = flow.step()
        intents.append(intent)
        if intent["op"] in ("done", "pending"):
            return intents
        flow.feed(feeds[i] if i < len(feeds) else None)
        i += 1
        assert i <= 300, "跑了 300 步还没演完,疑似死循环"


@pytest.fixture(scope="module")
def data():
    """全量数据表(顺带再过一遍 batch 1 的自洽校验)。"""
    return loader.load_all()


# ==================================================================
# EventRunner:事件解释器
# ==================================================================

def test_event1_intro_full_chain(data):
    """事件 1(开场剧情):appear→sound→chat→appear→chat→beAttack→sceneDisappear
    →disappear→chat→sceneAppear 全链走通;坐标换算、拆格、重置、传送全对。"""
    api = FakeApi()
    st = make_state(floor=3, hp=1000, attack=100, defence=100)
    st["hero"]["sword"], st["hero"]["shield"] = 12, 17
    st["hero"]["pos"] = [4, 8]                       # 序章踩上的触发格
    runner = EventRunner(data["events"]["1"], st, api, data=data)

    intents = drive(runner, [None] * 10)

    # 1) 意图序列:魔王台词→什么?→(警卫围上来)挨打→眼前一黑→回忆→场景切换
    assert [it["op"] for it in intents] == \
        ["sound", "chat", "chat", "effect", "effect", "chat", "effect", "done"]
    assert intents[0] == {"op": "sound", "name": "level3Event", "loop": False}
    assert intents[1]["lines"] == ["魔王Zero说：欢迎来到魔塔，你是第一百位挑战者。你若能打败我所有的手下，我就与你一对一的决斗。现在你必须接受我的安排。"]
    assert intents[2]["lines"] == ["什么？"]
    assert intents[5]["lines"] == ["------", "------喂！", "------喂！醒醒！"]
    assert intents[3] == {"op": "effect", "kind": "beAttack", "target": "hero",
                           "positions": [[3, 8], [4, 7], [5, 8], [4, 9]]}
    assert intents[4] == {"op": "effect", "kind": "sceneDisappear"}
    assert intents[6] == {"op": "effect", "kind": "sceneAppear", "floor": 2, "pos": [2, 7]}

    # 2) 出怪:展平坐标 70/91/81/93/103 → (x=pos%11, y=pos//11),一只魔王+四个警卫
    assert api.calls_of("spawn_monster") == [
        ("spawn_monster", 3, 4, 6, 132),             # 70 → (4,6) 魔王Zero
        ("spawn_monster", 3, 3, 8, 130),             # 91 → (3,8) 魔法警卫×4
        ("spawn_monster", 3, 4, 7, 130),             # 81 → (4,7)
        ("spawn_monster", 3, 5, 8, 130),             # 93 → (5,8)
        ("spawn_monster", 3, 4, 9, 130),             # 103 → (4,9)
    ]
    # 3) 拆格:disappear 把触发器(92=(4,8))和上面 5 只怪全撤了
    assert api.calls_of("remove_trigger") == [("remove_trigger", 3, 4, 8)]
    emptied = {(c[1], c[2], c[3]) for c in api.calls_of("set_cell") if c[4] is None}
    assert {(3, 4, 6), (3, 3, 8), (3, 4, 7), (3, 5, 8), (3, 4, 9)} <= emptied
    # 4) 传送:sceneAppear [2, 79] → 第 2 层 (2,7);数值重置成开局 400/10/10
    assert api.calls_of("jump_floor") == [("jump_floor", 2, 2, 7)]
    assert (st["floor"], st["hero"]["pos"]) == (2, [2, 7])
    assert (st["hero"]["hp"], st["hero"]["attack"], st["hero"]["defence"]) == \
        (WEAKEN_ATTRS["hp"], WEAKEN_ATTRS["attack"], WEAKEN_ATTRS["defence"])
    assert st["hero"]["sword"] is None and st["hero"]["shield"] is None
    assert 2 in st["visited"]
    # 5) 演完记一次性
    assert st["flags"]["events_done"] == [1]


def test_event21_seal_weakens_zeno(data):
    """事件 21(49 层封印):chat + weak [27, 0.1]——27 是【位置】,(5,2) 上的假魔王
    只剩一成功力:api.weaken 收到 (怪132, ×0.1)。"""
    api = FakeApi()
    api.put(49, 5, 2, {"kind": "monster", "id": 132})     # 假魔王站在展平 27=(5,2)
    st = make_state(floor=49)
    runner = EventRunner(data["events"]["21"], st, api, data=data)
    intents = drive(runner, [None])
    assert [it["op"] for it in intents] == ["chat", "done"]
    assert intents[0]["lines"] == ["啊！我怎么被封印了，我只剩下一成的功力了！！！"]
    assert api.calls_of("weaken") == [("weaken", 132, 0.1)]
    assert 21 in st["flags"]["events_done"]


def test_event21_weak_without_monster_records_warning(data):
    """weak 位置上没怪(比如已经死了):跳过并记进 warnings,不崩。"""
    api = FakeApi()
    st = make_state(floor=49)
    runner = EventRunner(data["events"]["21"], st, api, data=data)
    drive(runner, [None])
    assert api.calls_of("weaken") == []
    assert runner.warnings and "没有怪物" in runner.warnings[0]


def test_event2_ambush_registers_monster_doors(data):
    """事件 2(10 层中埋伏):对白→拆墙→音效→怪物走位→摆 4 扇监狱门→停音效→
    拆触发器;monsterDoor 在【构造时】注册,守卫组→门逐门挂上。"""
    api = FakeApi()
    api.put(10, 4, 5, {"kind": "wall", "sprite": "door1006_0"})   # 59=(4,5)
    api.put(10, 6, 5, {"kind": "wall", "sprite": "door1006_0"})   # 61=(6,5)
    st = make_state(floor=10)
    runner = EventRunner(data["events"]["2"], st, api, data=data)

    # 还没 step,构造期就把 4 扇门注册了:{"5":[36,40,71], "37,…,61":[27]}
    assert sorted(api.calls_of("register_monster_door")) == sorted([
        ("register_monster_door", 36, [5]),
        ("register_monster_door", 40, [5]),
        ("register_monster_door", 71, [5]),
        ("register_monster_door", 27, [37, 38, 39, 48, 50, 59, 60, 61]),
    ])

    intents = drive(runner, [None] * 6)
    ops = [it["op"] for it in intents]
    assert ops == ["chat", "sound", "sound", "done"]      # chat→拆墙(无输出)→音效→…
    sounds = [it for it in intents if it["op"] == "sound"]
    assert sounds[0] == {"op": "sound", "name": "skeleton", "loop": True}
    assert sounds[1] == {"op": "sound", "name": None, "loop": False}   # stopSound

    # 拆掉两堵墙(59/61),摆上 4 扇监狱门(1004@71/27/36/40),拆掉触发器(49=(5,4))
    emptied = {(c[1], c[2], c[3]) for c in api.calls_of("set_cell") if c[4] is None}
    assert {(10, 4, 5), (10, 6, 5)} <= emptied
    door_pushes = {(c[1], c[2], c[3]) for c in api.calls_of("set_cell")
                   if c[4] and c[4][-1].get("kind") == "door" and c[4][-1].get("id") == 1004}
    assert door_pushes == {(10, 5, 6), (10, 5, 2), (10, 3, 3), (10, 7, 3)}
    assert api.calls_of("remove_trigger") == [("remove_trigger", 10, 5, 4)]

    # 9 段怪物走位(38→5、42→38、……)逐段转给引擎
    moves = api.calls_of("move")
    assert len(moves) == 9 and moves[0] == ("move", 10, "monster", 38, 5)
    assert 2 in st["flags"]["events_done"]


def test_event11_unknown_monster_135_skips(data):
    """事件 11(25 层大法师)引用了不存在的怪物 135(tacthgin 数据损坏,勘误第 6 条
    保持原样):appear 查无此怪跳过并记录;do 104 → 代替勇者撞 (5,9)。"""
    api = FakeApi()
    st = make_state(floor=25)
    runner = EventRunner(data["events"]["11"], st, api, data=data)
    intents = drive(runner, [None] * 4)
    assert api.calls_of("spawn_monster") == []            # 135 不在怪物表,没出怪
    assert any("135" in w for w in runner.warnings)
    assert ("collide", 25, 5, 9) in api.calls             # do 104 → (5,9)
    assert ("remove_trigger", 25, 5, 9) in api.calls      # 开头 disappear event[104]
    assert 11 in st["flags"]["events_done"]
    assert intents[0]["op"] == "chat"


def test_event27_jump_to_floor_50(data):
    """事件 27(jump [50,5,5]):直接传送到 50 层 (5,5)——公主线的真结局暗门。"""
    api = FakeApi()
    st = make_state(floor=24)
    runner = EventRunner(data["events"]["27"], st, api, data=data)
    assert runner.step() == {"op": "done"}                # 唯一一条动作,立即生效
    assert (st["floor"], st["hero"]["pos"]) == (50, [5, 5])
    assert 50 in st["visited"]
    assert api.calls_of("jump_floor") == [("jump_floor", 50, 5, 5)]
    assert 27 in st["flags"]["events_done"]


def test_event8_save_equal_current_floor_runs(data):
    """事件 8 挂到 29 层(meta.save=29):在 29 层被触发时正常演(clearNpcEvent 16
    → 清 (5,1) 那个 NPC 的 event_talk),在别的层触发则挂起。"""
    # 在 29 层:正常执行
    api = FakeApi()
    st = make_state(floor=29)
    runner = EventRunner(data["events"]["8"], st, api, data=data)
    assert runner.step() == {"op": "done"}
    assert api.calls_of("clear_npc_event") == [("clear_npc_event", 29, 5, 1)]
    assert 8 in st["flags"]["events_done"]
    # 在别的层(比如被引擎提前误触发):挂起到 29
    api2 = FakeApi()
    st2 = make_state(floor=23)
    runner2 = EventRunner(data["events"]["8"], st2, api2, data=data)
    # batch 6 契约扩展:pending 意图自带事件 id(引擎登记"到这层再演"要知道演谁)
    assert runner2.step() == {"op": "pending", "floor": 29, "event": 8}
    assert api2.calls == [] and 8 not in st2["flags"]["events_done"]


def test_event_one_shot_second_run_instantly_done(data):
    """一次性:同一事件第二次构造 runner,step() 直接 done,一个 api 调用都没有。"""
    api = FakeApi()
    st = make_state(floor=10)
    first = EventRunner(data["events"]["3"], st, api, data=data)
    drive(first, [None])
    assert api.calls_of("remove_trigger") == [("remove_trigger", 10, 5, 1)]  # 16=(5,1)

    api2 = FakeApi()
    second = EventRunner(data["events"]["3"], st, api2, data=data)
    assert second.step() == {"op": "done"}
    assert api2.calls == []                                # 什么都没干
    assert st["flags"]["events_done"] == [3]


def test_unknown_action_type_raises_clear_error(data):
    """未知动作类型:明确报错(设计文档 §1 错误处理约定),不许静默跳过。"""
    bad = {"id": 99, "trigger": None,
           "actions": [{"type": "开个挂", "data": None}], "meta": {}}
    runner = EventRunner(bad, make_state(), FakeApi(), data=data)
    with pytest.raises(EventError, match="未知事件动作类型"):
        runner.step()


# ==================================================================
# NpcFlow:NPC 对话 / 商人买卖 / 祝福 / 送礼 / 小偷走位 / 事件接力
# ==================================================================

def test_npc_merchant_sell_blue_key(data):
    """商人 7(6 层,卖蓝钥匙 50 金):两句台词→问"买/不买"→买,扣 50 金、
    蓝钥匙+1;NPC 消费一次性(第二次撞直接完事)。"""
    api = FakeApi()
    st = make_state(floor=6, gold=100)
    flow = NpcFlow(7, st, data, api, npc_pos=(7, 3))       # 6 层商人站在 (7,3)
    intents = drive(flow, [None, None, 0])

    assert [it["op"] for it in intents] == ["chat", "chat", "choices", "done"]
    assert intents[0]["lines"] == ["我有一把蓝钥匙，你出50个金币就卖给你。"]
    assert intents[2]["options"][0]["label"].startswith("买:蓝色钥匙")
    assert st["hero"]["gold"] == 50 and st["hero"]["props"]["2"] == 1
    assert 7 in st["flags"]["npcs_done"]
    assert intents[-1].get("remove_npc") is True          # 提示引擎把商人撤下地图


def test_npc_merchant_not_enough_gold(data):
    """钱不够:拒绝买卖,金币道具原封不动,商人不消费(下次还能来买)。"""
    api = FakeApi()
    st = make_state(floor=6, gold=30)
    flow = NpcFlow(7, st, data, api, npc_pos=(7, 3))
    intents = drive(flow, [None, None, 0])
    assert [it["op"] for it in intents] == ["chat", "chat", "choices", "chat", "done"]
    assert "你的钱不够" in intents[3]["lines"][0]
    assert st["hero"]["gold"] == 30 and "2" not in st["hero"]["props"]
    assert 7 not in st["flags"]["npcs_done"]
    assert "remove_npc" not in intents[-1]


def test_npc_merchant_decline_keeps_npc(data):
    """选"不买":什么也不发生,商人原地待命(和原版一致)。"""
    api = FakeApi()
    st = make_state(floor=6, gold=100)
    flow = NpcFlow(7, st, data, api, npc_pos=(7, 3))
    intents = drive(flow, [None, None, 1])
    assert intents[-1]["op"] == "done" and "remove_npc" not in intents[-1]
    assert st["hero"]["gold"] == 100 and "2" not in st["hero"]["props"]
    assert 7 not in st["flags"]["npcs_done"]


def test_npc_unlimit_merchant_can_buy_again(data):
    """unlimit 商人 10(12 层,1000 金一把黄钥匙,无限货源):买完第一次还能再买。"""
    api = FakeApi()
    st = make_state(floor=12, gold=2000)
    first = NpcFlow(10, st, data, api, npc_pos=(10, 0))
    drive(first, [None, 0])
    assert st["hero"]["gold"] == 1000 and st["hero"]["props"]["1"] == 1
    assert 10 not in st["flags"]["npcs_done"]              # 永不消费
    second = NpcFlow(10, st, data, api, npc_pos=(10, 0))
    intents = drive(second, [None, 0])
    assert [it["op"] for it in intents] == ["chat", "choices", "done"]  # 还能买
    assert st["hero"]["gold"] == 0 and st["hero"]["props"]["1"] == 2
    assert "remove_npc" not in intents[-1]


def test_npc_buyback_merchant_sells_keys(data):
    """收购商人 20(28 层,100 金一把收黄钥匙,unlimit):卖两次,钥匙-2、金+200;
    手里没钥匙时提示卖不了。"""
    api = FakeApi()
    st = make_state(floor=28, gold=0, props={"1": 2})
    first = NpcFlow(20, st, data, api, npc_pos=(7, 3))
    intents = drive(first, [None, 0])
    assert intents[1]["options"][0]["label"].startswith("卖:黄色钥匙")
    assert st["hero"]["gold"] == 100 and st["hero"]["props"]["1"] == 1
    second = NpcFlow(20, st, data, api, npc_pos=(7, 3))
    drive(second, [None, 0])
    assert st["hero"]["gold"] == 200 and st["hero"]["props"].get("1", 0) == 0  # 卖光删键
    # 钥匙卖光了:再卖提示没货,不扣不加
    third = NpcFlow(20, st, data, api, npc_pos=(7, 3))
    intents3 = drive(third, [None, 0])
    assert [it["op"] for it in intents3] == ["chat", "choices", "chat", "done"]
    assert "没有可以卖" in intents3[2]["lines"][0]
    assert st["hero"]["gold"] == 200 and "1" not in st["hero"]["props"]


def test_npc_blessing_merchant_multiplies(data):
    """祝福商人 3(2 层):确认后攻防各乘 1.03(乘法不是加 3 点,勘误第 9 条),
    浮点容差断言;拒绝则不变。"""
    api = FakeApi()
    st = make_state(floor=2, attack=100, defence=100)
    flow = NpcFlow(3, st, data, api, npc_pos=(10, 6))
    intents = drive(flow, [None, 0])
    assert [it["op"] for it in intents] == ["chat", "choices", "done"]
    assert intents[1]["options"][0]["label"] == "接受祝福(攻击+3%,防御+3%)"
    assert st["hero"]["attack"] == pytest.approx(103.0)
    assert st["hero"]["defence"] == pytest.approx(103.0)
    assert 3 in st["flags"]["npcs_done"]

    # 拒绝:不变
    st2 = make_state(floor=2, attack=100, defence=100)
    flow2 = NpcFlow(3, st2, data, api, npc_pos=(10, 6))
    drive(flow2, [None, 1])
    assert (st2["hero"]["attack"], st2["hero"]["defence"]) == (100, 100)


def test_npc_elder_gives_gold(data):
    """老人 2:道谢送 1000 金,对白后入账,一次性。"""
    api = FakeApi()
    st = make_state(floor=2, gold=0)
    flow = NpcFlow(2, st, data, api, npc_pos=(10, 3))
    intents = drive(flow, [None])
    assert [it["op"] for it in intents] == ["chat", "done"]
    assert st["hero"]["gold"] == 1000
    assert 2 in st["flags"]["npcs_done"] and intents[-1].get("remove_npc") is True


def test_npc_elder_gives_monster_manual(data):
    """老人 4(3 层):对白后怪物手册(道具 18)入包。"""
    api = FakeApi()
    st = make_state(floor=3)
    flow = NpcFlow(4, st, data, api, npc_pos=(10, 3))
    drive(flow, [None])
    assert st["hero"]["props"]["18"] == 1


def test_npc_consumed_second_talk_instantly_done(data):
    """一次性 NPC 消费后:第二次再撞,直接 done(台词都不说了)。"""
    api = FakeApi()
    st = make_state(floor=3)
    drive(NpcFlow(4, st, data, api, npc_pos=(10, 3)), [None])   # 第一次送手册
    again = NpcFlow(4, st, data, api, npc_pos=(10, 3))
    assert again.step() == {"op": "done"}


def test_npc_tip_elder_talks_then_leaves(data):
    """纯提示老人 5:说一句就走(原版:台词说完 NPC 消失)。"""
    api = FakeApi()
    st = make_state(floor=4)
    flow = NpcFlow(5, st, data, api, npc_pos=(9, 0))
    intents = drive(flow, [None])
    assert intents[0]["lines"] == ["有些门不能用钥匙打开，只有当你打败它的守卫后才会自动打开。"]
    assert 5 in st["flags"]["npcs_done"] and intents[-1].get("remove_npc") is True


def test_npc_event_talk_gate(data):
    """小偷 21(29 层,event_talk 未解锁):只说"我还在挖暗道",不开门、不走位、
    不接力事件;被事件 8 解锁后才是完整流程。"""
    api = FakeApi()
    st = make_state(floor=29)
    flow = NpcFlow(21, st, data, api, npc_pos=(5, 1))
    intents = drive(flow, [None])
    assert intents == [
        {"op": "chat", "lines": ["你先到别的地方走走，我还在挖暗道。"]},
        {"op": "done"},
    ]
    assert api.calls == []                                  # 什么都没干


def test_npc_thief_full_flow_chains_pending_event(data):
    """解锁后的小偷 21:台词→开暗道门(27=(5,2))→走到 115=(5,10)→消失→
    接力事件 9,而事件 9 挂起到 2 层(勘误第 4 条)。"""
    api = FakeApi()
    api.put(29, 5, 2, {"kind": "door", "id": 1006})        # 暗道门(墙门 1006)
    st = make_state(floor=29)
    st["flags"]["npcs_event_talk_cleared"] = [21]          # 事件 8 已解锁
    flow = NpcFlow(21, st, data, api, npc_pos=(5, 1))
    intents = drive(flow, [None] * 3)

    assert intents[-1] == {"op": "pending", "floor": 2, "event": 9}  # 事件 9 挂起到 2 层(带 id:batch 6 契约)
    # 暗道门被拆、小偷从 (5,1)[=16] 走到 (5,10)[=115] 后消失
    assert any(c[:4] == ("set_cell", 29, 5, 2) and c[4] is None for c in api.calls)
    assert ("move", 29, "npc", 16, 115) in api.calls
    assert ("remove_npc", 29, 5, 10) in api.calls
    # 事件 9 没演完就挂起,不算"演完"
    assert 9 not in st["flags"]["events_done"]


def test_npc_thief_f2_opens_tunnel_and_walks_away(data):
    """开局小偷 1(2 层监狱):两句台词间开暗道门(67=(1,6))、走 88→110,走到头消失。"""
    api = FakeApi()
    api.put(2, 1, 6, {"kind": "door", "id": 1006})          # 监狱暗道门
    st = make_state(floor=2)
    flow = NpcFlow(1, st, data, api, npc_pos=(2, 6))
    intents = drive(flow, [None, None])
    assert [it["op"] for it in intents] == ["chat", "chat", "done"]
    assert any(c[:4] == ("set_cell", 2, 1, 6) and c[4] is None for c in api.calls)
    assert ("move", 2, "npc", 68, 88) in api.calls          # 68=(2,6) → 88
    assert ("move", 2, "npc", 88, 110) in api.calls         # 88 → 110=(0,10)
    assert ("remove_npc", 2, 0, 10) in api.calls             # 走到头,小偷消失


def test_npc_39_chains_event25_true_ending(data):
    """50 层小偷 39:两句台词后接力事件 25(真结局链):小偷自己消失、
    真魔王 133 在 (5,4) 出现、8 行长对白。"""
    api = FakeApi()
    st = make_state(floor=50)
    flow = NpcFlow(39, st, data, api, npc_pos=(5, 4))
    intents = drive(flow, [None] * 4)

    assert [it["op"] for it in intents] == ["chat", "chat", "chat", "done"]
    assert intents[2]["lines"][0].startswith("勇者问：“啊！你就是魔王！")
    assert len(intents[2]["lines"]) == 9                    # 一页 9 行的大对白
    assert ("remove_npc", 50, 5, 4) in api.calls             # 小偷消失(事件 25 干的)
    assert ("spawn_monster", 50, 5, 4, 133) in api.calls    # 真魔王出现(展平 49)
    assert 25 in st["flags"]["events_done"]


def test_notebook_records_and_replays(data):
    """记事本:拿着它(道具 19)对话才会记录;notebook_data 能翻出来看。"""
    api = FakeApi()
    st = make_state(floor=4, props={"19": 1})
    flow = NpcFlow(5, st, data, api, npc_pos=(9, 0))
    drive(flow, [None])
    book = notebook_data(st, data)
    assert len(book) == 1
    assert book[0]["npc"] == 5 and book[0]["name"] == "老人"
    assert book[0]["line"].startswith("有些门不能用钥匙打开")

    # 没记事本就不记
    st2 = make_state(floor=4)
    drive(NpcFlow(5, st2, data, api, npc_pos=(9, 0)), [None])
    assert notebook_data(st2, data) == []


# ==================================================================
# AltarFlow:祭坛商店
# ==================================================================

def test_altar_flow_prices_and_effects(data):
    """祭坛:第 1 次 20 金/第 2 次 40 金(全塔共享计数);三选一效果按当前区域
    (4 层=区域 1:攻+2、防+4;生命第 n 次 +100×n);买完可以继续买,最后能退出。"""
    st = make_state(floor=4, gold=200)
    flow = AltarFlow(st, data, FakeRules())

    it = flow.step()
    assert it["op"] == "choices"
    assert "20" in it["options"][0]["label"]                # 第 1 次价格 20
    assert "生命 +100" in it["options"][0]["label"]         # 第 1 次生命 +100
    flow.feed(0)                                            # 买生命
    it = flow.step()
    assert it["op"] == "choices" and "40" in it["options"][0]["label"]   # 第 2 次 40
    assert "生命 +200" in it["options"][0]["label"]         # 第 2 次生命 +200
    flow.feed(1)                                            # 买攻击(4 层区域 1:+2)
    flow.step()
    flow.feed(2)                                            # 买防御(区域 1:+4)
    it = flow.step()
    assert it["op"] == "choices" and "140" in it["options"][0]["label"]   # 第 4 次 140
    assert "生命 +400" in it["options"][0]["label"]         # 第 4 次生命 +400
    flow.feed(3)                                            # 离开
    assert flow.step() == {"op": "done"}

    assert st["hero"]["gold"] == 200 - 20 - 40 - 80
    assert (st["hero"]["hp"], st["hero"]["attack"], st["hero"]["defence"]) == (500, 12, 14)
    assert st["flags"]["altar_count"] == 3


def test_altar_flow_area_scaling_12f(data):
    """12 层祭坛(区域 2):买攻 +4、买防 +8(设计文档 §5 用例)。"""
    st = make_state(floor=12, gold=100)
    flow = AltarFlow(st, data, FakeRules())
    flow.step()
    flow.feed(1)                                            # 买攻击
    flow.step()
    flow.feed(2)                                            # 买防御
    flow.step()
    flow.feed(3)
    assert (st["hero"]["attack"], st["hero"]["defence"]) == (10 + 4, 10 + 8)
    assert st["hero"]["gold"] == 100 - 20 - 40


def test_altar_flow_insufficient_gold_no_count(data):
    """钱不够:提示后可以重选,购买次数不涨(价格不涨);支持直接退出。"""
    st = make_state(floor=4, gold=30)
    st["flags"]["altar_count"] = 1                          # 已买过 1 次,下次 40 金
    flow = AltarFlow(st, data, FakeRules())
    it = flow.step()
    assert "40" in it["options"][0]["label"]
    flow.feed(0)                                            # 想买,钱不够
    it = flow.step()
    assert it["op"] == "chat" and "40" in it["lines"][0]
    flow.feed(None)
    it = flow.step()
    assert it["op"] == "choices" and "40" in it["options"][0]["label"]   # 原价重选
    assert st["flags"]["altar_count"] == 1                  # 次数没涨
    assert st["hero"]["gold"] == 30
    flow.feed(3)
    assert flow.step() == {"op": "done"}


def test_altar_flow_requires_rules(data):
    """没注入 rules 适配器:明确报错(§4.4-B:祭坛归 core 算)。"""
    with pytest.raises(EventError, match="rules"):
        AltarFlow(make_state(), data, None)


# ==================================================================
# manual_data:怪物手册(预测面板)
# ==================================================================

def test_manual_data_matches_calc_battle(data):
    """手册预测和 rules.calc_battle 完全一致(数字来自设计文档 §5 的标准用例):
    绿色史莱姆 1 回合 0 损血;兽人武士 4 回合 255 损血。"""
    rules = FakeRules()
    st = make_state(floor=1, hp=400, attack=120, defence=35)
    manual = manual_data(st, data, rules, [100, 112])

    assert [m["name"] for m in manual] == ["绿色史莱姆", "兽人武士"]
    slime = manual[0]
    assert (slime["hp"], slime["attack"], slime["defence"]) == (35, 18, 1)
    assert slime["can_fight"] is True and slime["hero_damage"] == 0 and slime["turns"] == 1

    orc = manual[1]
    assert orc["hero_damage"] == 255 and orc["turns"] == 4 and orc["can_fight"] is True
    # "与 calc_battle 返回一致":逐字段对表
    raw = rules.calc_battle(st["hero"], data["monsters"]["112"],
                            {"cross": False, "lucky_coin": False, "dragon_slayer": False})
    assert {k: orc[k] for k in ("can_fight", "hero_damage", "turns", "gold")} == \
        {k: raw[k] for k in ("can_fight", "hero_damage", "turns", "gold")}


def test_manual_data_unfightable_and_lucky_coin(data):
    """不破防(攻 10 ≤ 石头人防 68):手册如实显示"不可战斗";
    拿着幸运金币(道具 27)杀怪金币 ×2 也体现在预测里。"""
    rules = FakeRules()
    st = make_state(floor=1, attack=10)
    manual = manual_data(st, data, rules, [113])
    assert manual[0]["can_fight"] is False and manual[0]["hero_damage"] == 0

    st2 = make_state(floor=1, attack=120, defence=35, props={"27": 1})
    manual2 = manual_data(st2, data, rules, [100])
    assert manual2[0]["gold"] == 2                            # 幸运金币 ×2


# ==================================================================
# 道具使用
# ==================================================================

def test_fly_wand_targets_and_jump(data):
    """飞行魔杖(道具 20):目标=去过的层且不能去 44 层(异空间);选完跳层,不消耗。"""
    api = FakeApi()
    st = make_state(floor=2, visited=[1, 2, 3, 44], props={"20": 1})
    flow = use_tool(st, data, api, 20)
    assert isinstance(flow, FlyWandFlow)
    it = flow.step()
    assert it["op"] == "choices"
    assert [o["floor"] for o in it["options"]] == [1, 2, 3]   # 44 层不在列表里
    flow.feed(2)                                              # 选 3 层
    assert st["floor"] == 3
    assert api.calls_of("jump_floor") == [("jump_floor", 3, None, None)]  # None=楼梯口落点
    assert flow.step() == {"op": "done"}
    assert st["hero"]["props"]["20"] == 1                     # 无限使用,没消耗


def test_fly_targets_helper(data):
    """fly_targets:44 层被剔除、其余按楼层排好。"""
    assert fly_targets(make_state(floor=5, visited=[44, 5, 2])) == [2, 5]


def test_wing_up_down_and_bounds(data):
    """上/下飞行器(道具 30/31):±1 层,越界拒绝不消耗。"""
    api = FakeApi()
    st = make_state(floor=5, props={"30": 1})
    result = use_tool(st, data, api, 30)
    assert result["ok"] is True and st["floor"] == 6 and 6 in st["visited"]
    assert "30" not in st["hero"]["props"]                    # 一次性,用完删键
    assert api.calls_of("jump_floor") == [("jump_floor", 6, None, None)]

    # 0 层想再往下:拒绝,道具不消耗
    st2 = make_state(floor=0, props={"31": 1})
    result2 = use_tool(st2, data, api, 31)
    assert result2["ok"] is False and st2["floor"] == 0
    assert st2["hero"]["props"]["31"] == 1


def test_center_wing_symmetry_and_blocked(data):
    """中心飞行器(道具 32):飞到 (10-x, 10-y),目标得是空地;3 次机会逐次扣。"""
    api = FakeApi()
    st = make_state(floor=44, props={"32": 3})
    st["hero"]["pos"] = [2, 3]
    result = use_tool(st, data, api, 32)
    assert result["ok"] is True and st["hero"]["pos"] == [8, 7]
    assert st["hero"]["props"]["32"] == 2
    assert api.calls_of("jump_floor") == [("jump_floor", 44, 8, 7)]

    # 目标有障碍物:失败,次数不扣
    api2 = FakeApi()
    api2.put(44, 10 - 8, 10 - 7, {"kind": "wall"})
    st2 = make_state(floor=44, props={"32": 2})
    st2["hero"]["pos"] = [8, 7]
    result2 = use_tool(st2, data, api2, 32)
    assert result2["ok"] is False and st2["hero"]["pos"] == [8, 7]
    assert st2["hero"]["props"]["32"] == 2


def test_pickaxe_removes_wall_ahead(data):
    """镐(道具 21):敲掉面前一格墙(墙门 1006 也算墙),一次性。"""
    api = FakeApi()
    api.put(10, 5, 9, {"kind": "wall"})
    st = make_state(floor=10, props={"21": 1})
    st["hero"]["pos"] = [5, 10]
    result = use_tool(st, data, api, 21, direction=(0, -1))    # 面朝上
    assert result["ok"] is True
    assert api.grid(10)[9][5] is None
    assert "21" not in st["hero"]["props"]

    # 面前没墙:失败不消耗
    st2 = make_state(floor=10, props={"21": 1})
    st2["hero"]["pos"] = [5, 10]
    result2 = use_tool(st2, data, api, 21, direction=(0, -1))
    assert result2["ok"] is False and st2["hero"]["props"]["21"] == 1


def test_quake_scroll_destroys_all_walls(data):
    """地震卷轴(道具 22):本层普通墙全拆,但"墙门"(door 1006)不是 wall 不拆。"""
    api = FakeApi()
    api.put(39, 1, 1, {"kind": "wall"})
    api.put(39, 2, 2, {"kind": "wall"})
    api.put(39, 3, 3, {"kind": "wall"})
    api.put(39, 4, 4, {"kind": "door", "id": 1006})
    st = make_state(floor=39, props={"22": 1})
    result = use_tool(st, data, api, 22)
    assert result["ok"] is True and "3 面墙" in result["msg"]
    assert api.grid(39)[1][1] is None and api.grid(39)[2][2] is None
    assert api.grid(39)[3][3] is None
    assert api.grid(39)[4][4] and api.grid(39)[4][4][-1]["id"] == 1006   # 墙门还在
    assert "22" not in st["hero"]["props"]


def test_ice_magic_freezes_adjacent_lava(data):
    """冰冻魔法(道具 23):冻住上下左右四格的岩浆;无限使用不消耗。"""
    api = FakeApi()
    api.put(44, 5, 4, {"kind": "lava"})      # 上
    api.put(44, 4, 5, {"kind": "lava"})      # 左
    api.put(44, 8, 8, {"kind": "lava"})      # 斜对角,够不着
    st = make_state(floor=44, props={"23": 1})
    st["hero"]["pos"] = [5, 5]
    result = use_tool(st, data, api, 23)
    assert result["ok"] is True and result["msg"] == "冻住了 2 格岩浆"
    assert api.grid(44)[4][5] is None and api.grid(44)[5][4] is None
    assert api.grid(44)[8][8] and api.grid(44)[8][8][-1]["kind"] == "lava"
    assert st["hero"]["props"]["23"] == 1                    # 无限,没消耗


def test_bomb_kills_ring_but_not_boss(data):
    """炸弹(道具 24):炸死周围一圈(8 格)的非头目怪,头目炸不动(附录 B:
    "炸死周围一圈非头目怪"——8 格,见 events.py 里【语义存疑】注记)。"""
    api = FakeApi()
    api.put(10, 4, 4, {"kind": "monster", "id": 100})         # 左上
    api.put(10, 5, 4, {"kind": "monster", "id": 102})         # 上
    api.put(10, 6, 4, {"kind": "monster", "id": 116})         # 右上:大法师(boss)
    st = make_state(floor=10, props={"24": 1})
    st["hero"]["pos"] = [5, 5]
    result = use_tool(st, data, api, 24)
    assert result["ok"] is True and "2 只怪物" in result["msg"]
    assert api.grid(10)[4][4] is None and api.grid(10)[4][5] is None
    boss_cell = api.grid(10)[4][6]
    assert boss_cell and boss_cell[-1]["id"] == 116           # 头目还在
    assert "24" not in st["hero"]["props"]

    # 周围没有可炸的怪:失败不消耗
    st2 = make_state(floor=10, props={"24": 1})
    result2 = use_tool(st2, data, api, 24)
    assert result2["ok"] is False and st2["hero"]["props"]["24"] == 1


def test_magic_key_opens_all_yellow_doors(data):
    """魔法钥匙(道具 25):本层黄门(1001)全开,蓝门(1002)不动。"""
    api = FakeApi()
    api.put(12, 1, 1, {"kind": "door", "id": 1001})
    api.put(12, 2, 2, {"kind": "door", "id": 1001})
    api.put(12, 3, 3, {"kind": "door", "id": 1002})
    st = make_state(floor=12, props={"25": 1})
    result = use_tool(st, data, api, 25)
    assert result["ok"] is True and "2 扇黄门" in result["msg"]
    assert api.grid(12)[1][1] is None and api.grid(12)[2][2] is None
    assert api.grid(12)[3][3] and api.grid(12)[3][3][-1]["id"] == 1002
    assert "25" not in st["hero"]["props"]


def test_holy_water_adds_attack_plus_defence(data):
    """圣水(道具 26):HP += 攻 + 防,一次性。"""
    api = FakeApi()
    st = make_state(floor=14, hp=100, attack=50, defence=30, props={"26": 1})
    result = use_tool(st, data, api, 26)
    assert result["ok"] is True and result["msg"] == "生命 +80"
    assert st["hero"]["hp"] == 180 and "26" not in st["hero"]["props"]


def test_use_tool_rejects_non_tools(data):
    """钥匙这种捡了就生效的东西不能"主动使用"。"""
    api = FakeApi()
    st = make_state(floor=2, props={"1": 3})
    result = use_tool(st, data, api, 1)
    assert result["ok"] is False


def test_use_tool_without_item_errors(data):
    """没拿到的道具:直接拒绝,不崩。"""
    api = FakeApi()
    st = make_state(floor=2)
    result = use_tool(st, data, api, 26)
    assert result["ok"] is False and "没有" in result["msg"]


# ==================================================================
# api / rules 适配器的完整性(给引擎接线看的护栏)
# ==================================================================

def test_eventrunner_requires_api_function(data):
    """api 注入不完整(比如没有 weaken):明确报错,不许静默跳过。"""
    fake = FakeApi()
    fake.put(49, 5, 2, {"kind": "monster", "id": 132})
    api = {"get_floor": fake.get_floor}                  # 故意只给一个函数
    st = make_state(floor=49)
    runner = EventRunner(data["events"]["21"], st, api, data=data)
    runner.step()                                        # 台词页正常
    runner.feed(None)                                    # 翻页,轮到 weak
    with pytest.raises(EventError, match="weaken"):
        runner.step()
