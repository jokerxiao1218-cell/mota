"""batch 6 集成测试:真数据 + 真 core + 真 events,从 main.py 拼装层过。

前三批的测试各用 Fake 替身钉死了"自己的契约";这里验证"拼起来能玩"——
引擎通过 main.py 的 RealRules/RealEvents 驱动真实的规则层与事件层,
跑的是 game/data/ 的真楼层。这些用例对应设计文档 §5 的跨层场景:

- 序章:3 层被夺装备 → 数值重置 400/10/10、卸剑盾、传送 2 层监狱
- 祭坛:4 层真开商店 → 真扣钱真加属性(价格 20,攻 +2)
- 道具:T 键菜单 → 圣水真喝(HP += 攻+防,用完删键)
- 49 层封印:杀 4 魔法警卫 → 事件 21 → 假魔王 ×0.1;先杀守角的 → 永久破阵
- 跨层挂起:事件 8 挂 29 层,进 29 层自动演
- 动态摆放:事件摆的 NPC/触发器 存档后还在(placements 快照)
- 结局:50 层杀真身 133 → 通关画面
"""

import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")   # 无头跑(命令行没带也兜底)

import pygame
import pytest

import game.main as main_mod
from game import core, loader
from game import events as events_mod
from game.engine import Engine

pygame.init()

DATA = loader.load_all()      # 真实数据表,只读共享


def make_engine(floor=1, pos=(5, 10)):
    """真·全家桶引擎:真数据 + 真 core(RealRules)+ 真 events(RealEvents)。"""
    rules = main_mod.build_rules_adapter(core)
    state = rules.new_state()
    state["floor"] = floor
    state["hero"]["pos"] = list(pos)
    state.setdefault("visited", [floor])
    engine = Engine(DATA, rules, None, state)
    engine.events = main_mod.build_events_adapter(events_mod, engine, DATA, rules)
    return engine


def drain_dialog(eng, limit=60):
    """把正在演的对话一路回车到底(对白/选择支全喂确认)。"""
    for _ in range(limit):
        if not eng.event_active:
            return
        eng.handle_key(pygame.K_RETURN)


# ================================================================ 序章

def test_opening_robbery_to_prison():
    """3 层 (4,8) 踩上事件 1:被夺装备 → 400/10/10、剑盾没了、落到 2 层监狱。"""
    eng = make_engine(floor=3, pos=(4, 7))
    eng.try_move(0, 1)                       # 走上触发格
    assert eng.event_active, "踩上 3 层触发格应该开演事件 1"
    drain_dialog(eng)
    assert not eng.event_active
    hero = eng.state["hero"]
    assert (hero["hp"], hero["attack"], hero["defence"]) == (400, 10, 10)
    assert hero["sword"] is None and hero["shield"] is None   # 被夺的不只三维
    assert eng.state["floor"] == 2                            # sceneAppear [2,79]
    assert hero["pos"] == [2, 7]
    assert 1 in eng.state["flags"]["events_done"]             # 一次性,演完了


# ================================================================ 祭坛

def test_altar_shop_real_buy():
    """4 层撞祭坛(5,0):AltarFlow 三选一;买攻第 1 次 = 花 20 金 +2 攻(区1)。"""
    eng = make_engine(floor=4, pos=(5, 1))
    assert eng.floor_doc()["grid"][0][5][-1]["kind"] == "altar"   # 数据前提
    eng.state["hero"]["gold"] = 500
    eng.try_move(0, -1)                      # 撞祭坛
    assert eng.mode == "dialog" and eng.intent["op"] == "choices"
    assert any("20" in opt["label"] for opt in eng.intent["options"])  # 第1次价格
    eng.events.feed(1)                       # 选"攻击"
    eng._poll_intent()                       # 买完回菜单(继续逛)
    hero = eng.state["hero"]
    assert hero["gold"] == 480                # 500-20
    assert hero["attack"] == 102              # 100 + 2×(4//10+1)
    assert eng.state["flags"]["altar_count"] == 1
    eng.events.feed(3)                       # 离开
    eng._poll_intent()
    assert eng.mode == "play"


def test_altar_shop_not_enough_gold():
    """钱不够:提示一句、可重选、次数不涨(§5 祭坛用例)。"""
    eng = make_engine(floor=4, pos=(5, 1))
    eng.state["hero"]["gold"] = 10
    eng.try_move(0, -1)
    eng.events.feed(1)                       # 想买攻
    eng._poll_intent()                       # → 变成提示页(chat)
    assert eng.intent["op"] == "chat" and "不够" in eng.intent["lines"][0]
    eng.events.feed(None)                    # 提示说完 → 回菜单
    eng._poll_intent()
    assert eng.intent["op"] == "choices"
    assert eng.state["flags"].get("altar_count", 0) == 0     # 没买成,不耗次数
    assert eng.state["hero"]["gold"] == 10                    # 一分没少
    eng.events.feed(3)
    eng._poll_intent()


# ================================================================ 道具(T 键)

def test_holy_water_via_tools_menu():
    """T 键菜单用圣水(26):HP += 攻+防,用完从道具栏消失。"""
    eng = make_engine(floor=1, pos=(5, 10))
    hero = eng.state["hero"]
    hero["props"] = {"26": 1}
    eng.handle_key(pygame.K_t)
    assert eng.mode == "menu"
    assert any("圣水" in opt for opt in eng.menu_stack[-1]["options"])
    before = hero["hp"]
    eng.handle_key(pygame.K_RETURN)          # 菜单里唯一一件,直接选它
    assert hero["hp"] == before + hero["attack"] + hero["defence"]
    assert "26" not in hero["props"]          # 用完删键
    assert eng.mode == "play"


def test_fly_wand_requires_stair_and_flies():
    """飞行魔杖(20):不在楼梯旁用不了;在楼梯旁用 → 选去过的层 → 落楼梯口。"""
    eng = make_engine(floor=2, pos=(5, 5))
    eng.state["hero"]["props"] = {"20": 1}
    eng.state["visited"] = [1, 2, 3]
    # (5,5) 不挨着楼梯:被拒,道具还在
    result = eng.events.use_tool(20)
    assert result["ok"] is False and "楼梯" in result["msg"]
    assert "20" in eng.state["hero"]["props"]
    # 2 层楼梯块:下梯在 (0,0)、上梯在 (0,10)(stair_links 的 stand 是落点,
    # 不是楼梯块本身)——站到 (0,1)(下梯旁)再用
    eng.state["hero"]["pos"] = [0, 1]
    started = eng.events.use_tool(20)
    assert started.get("flow") is True
    intent = eng.events.step()
    assert intent["op"] == "choices"
    labels = [o["label"] for o in intent["options"]]
    assert "第 1 层" in labels and "第 3 层" in labels
    eng.events.feed(0)                        # 飞去 1 层
    assert eng.state["floor"] == 1
    # 落点 = 1 层 up_stand(楼梯旁),不是乱落
    stand = DATA["floors"]["1"]["stair_links"]["up_stand"]
    assert eng.state["hero"]["pos"] == list(stand)


# ================================================================ 49 层封印

def _enter_49f(eng):
    """走进 49 层封印阵:踩 (5,5) 触发事件 20 → 刷出 8 警卫环 + 假魔王。"""
    eng.state["hero"]["pos"] = [5, 4]
    eng.try_move(0, 1)                         # 踩上 (5,5)
    assert eng.event_active, "踩 49 层 (5,5) 应该开演事件 20"
    drain_dialog(eng)
    grid = eng.floor_doc()["grid"]
    assert grid[2][5] and grid[2][5][-1].get("id") == 132      # 假魔王(5,2)
    for x, y in [(5, 1), (4, 2), (6, 2), (5, 3), (4, 1), (6, 1), (4, 3), (6, 3)]:
        stack = grid[y][x]
        assert stack and stack[-1].get("id") == 130, f"警卫({x},{y})没刷出来"


def test_49f_seal_weakens_fake_boss():
    """杀光 4 个魔法警卫(5,1)(4,2)(6,2)(5,3) → 事件 21 → 假魔王 132 ×0.1。"""
    eng = make_engine(floor=49, pos=(0, 0))
    _enter_49f(eng)
    for x, y in [(5, 1), (4, 2), (6, 2), (5, 3)]:
        eng.api_set_cell(49, x, y, None)
    drain_dialog(eng)                          # 事件 21:先一句对白,再 weak
    assert eng._weakened.get("132") == pytest.approx(0.1)
    fake = eng._monster(132)
    assert fake["hp"] == 800 and fake["attack"] == 500 and fake["defence"] == 100


def test_49f_seal_cancelled_by_corner_kill():
    """先杀守角的警卫(4,1) → 封印永久作废:再杀光 4 个警卫也不缩水。"""
    eng = make_engine(floor=49, pos=(0, 0))
    _enter_49f(eng)
    eng.api_set_cell(49, 4, 1, None)           # 守角的(keep_alive)先死
    for x, y in [(5, 1), (4, 2), (6, 2), (5, 3)]:
        eng.api_set_cell(49, x, y, None)
    assert "132" not in eng._weakened
    assert "49:1" in eng.state["flags"]["kill_triggers_cancelled"]


def test_49f_fake_boss_death_opens_treasure():
    """49 层杀假魔王(事件 22):守卫消失、宝物房显现(prop 出现在 34~50 号位)。"""
    eng = make_engine(floor=49, pos=(0, 0))
    eng.api_spawn_monster(49, 5, 2, 132)       # 假魔王站在封印中心(数据:动态刷)
    hero = eng.state["hero"]
    hero.update(hp=100000, attack=2000, defence=250)   # 缩水前的数值也能打过
    eng._bump_monster(5, 2, eng.floor_doc()["grid"][2][5])   # 直接调撞怪
    drain_dialog(eng)
    assert 22 in eng.state["flags"]["events_done"]
    # 事件 22 的 appear:34~50 号展平位(1~4 行)冒出道具
    grid = eng.floor_doc()["grid"]
    appeared = [(x, y) for y in range(5) for x in range(11)
                if grid[y][x] and grid[y][x][-1].get("kind") == "prop"]
    assert len(appeared) >= 9                  # 宝物房整排


# ================================================================ 跨层挂起

def test_pending_event_plays_on_floor_entry():
    """事件 8(meta.save=29)在别的层触发 → 挂起;进 29 层自动演完。"""
    eng = make_engine(floor=23, pos=(0, 0))
    eng.events.start(DATA["events"]["8"], eng.state)
    eng._event_started()
    assert eng.state["flags"]["pending_events"] == [{"floor": 29, "event": 8}]
    assert not eng.event_active                 # 挂起了,没占着对话
    eng.api_jump_floor(29)                      # 进层钩子自动开演
    assert 8 in eng.state["flags"]["events_done"]
    # 事件 8 的效果:29 层 (5,1) 小偷的 event_talk 被清(可以正常对话了)
    placed = [p for p in eng.floor_doc(29)["npcs"]
              if p["x"] == 5 and p["y"] == 1]
    assert placed and placed[0].get("event_talk") is None


# ================================================================ 动态摆放持久化

def test_placements_survive_save_load(tmp_path, monkeypatch):
    """事件摆的 NPC/触发器进 placements 快照:存档 → 新引擎读档还在。"""
    import game.core.state as cstate
    monkeypatch.setattr(cstate, "SAVE_DIR", tmp_path)   # 存档写临时目录
    eng = make_engine(floor=2, pos=(5, 5))
    eng.api_add_npc(2, 5, 6, 3)                 # 事件摆的 NPC
    eng.api_add_trigger(2, 5, 7, 15)            # 事件摆的踩格触发器
    eng.rules.save_game(eng.state, 1)

    eng2 = make_engine(floor=1)
    eng2.state = eng.rules.load_game(1)
    eng2._runtime = {}                          # 强制按新状态重建楼层缓存
    doc = eng2.floor_doc(2)
    assert any(p == {"npc": 3, "x": 5, "y": 6} for p in doc["npcs"])
    assert {"x": 5, "y": 7, "event": 15} in doc["triggers"]
    # 格子上也有 NPC 层(撞他才能对上话)
    assert any(c.get("kind") == "npc" for c in doc["grid"][6][5])


# ================================================================ 机关语义(v5 物化)

def test_f23_invisible_maze_reveal():
    """23 层隐形墙迷宫:撞一下显形(显形后永远是墙);43 面全撞现 → 事件 8 挂起到 29 层。"""
    eng = make_engine(floor=23, pos=(0, 0))
    doc = eng.floor_doc()
    positions = [tuple(p) for p in doc["appear_event"]["positions"]]
    assert len(positions) == 43
    x, y = positions[0]
    eng.state["hero"]["pos"] = [x + 1 if x < 10 else x - 1, y]
    eng.try_move(-1 if x < 10 else 1, 0)       # 撞上第一面隐形墙
    stack = eng.floor_doc()["grid"][y][x]
    assert stack and stack[0].get("kind") == "wall" and "appear" not in stack[0]
    assert "显出一面墙" in eng.message
    for x, y in positions[1:]:                 # 其余 42 面按同一规则显形
        cell = next((c for c in (eng.floor_doc()["grid"][y][x] or [])
                     if c.get("appear")), None)
        if cell:
            eng._reveal_appear(x, y, cell)
    pend = eng.state["flags"].get("pending_events", [])
    assert {"floor": 29, "event": 8} in pend   # 全撞现→事件8→挂起等 29 层


def test_f33_hidden_door_over_stair():
    """33 层 (10,0):隐形黄门压着下梯——隐藏格不挡路,踩上去直接下楼(原版短路机关)。"""
    eng = make_engine(floor=33, pos=(10, 1))
    eng.try_move(0, -1)                        # 踩上 (10,0)
    assert eng.state["floor"] == 32            # 顺利下楼,没被隐形门挡住
    assert eng.state["hero"]["pos"] == list(DATA["floors"]["32"]["stair_links"]["up_stand"])


def test_f14_guard_wall_door_opens():
    """14 层:杀光 3 只兽人武士 → 压着钥匙的 passive 墙门自动开(守卫门不看门 id)。"""
    eng = make_engine(floor=14, pos=(0, 0))
    for x, y in [(0, 0), (2, 0), (1, 1)]:      # 守卫位(楼层 guard_doors 数据)
        eng.api_set_cell(14, x, y, None)
    stack = eng.floor_doc()["grid"][2][0]      # 门位 (0,2)
    assert stack and all(c.get("kind") != "door" for c in stack)   # 墙门弹开了
    assert any(c.get("kind") == "prop" for c in stack)             # 露出压着的钥匙


def test_f41_unlock_wall_reveal_wizard():
    """41 层连锁:杀 (1,1) 巫师 → (9,1) 假墙解锁 → 撞开 → 显现墙后藏着的第二只巫师。"""
    eng = make_engine(floor=41, pos=(8, 1))
    eng.api_set_cell(41, 1, 1, None)            # 杀 (1,1) 高级巫师(它本不可正面战)
    door = eng.floor_doc()["grid"][1][9][-1]
    assert door.get("kind") == "door" and not door.get("passive")  # passive 解除
    eng.try_move(1, 0)                          # 撞开假墙
    stack = eng.floor_doc()["grid"][1][9] or []
    assert all(c.get("kind") != "door" for c in stack)             # 墙开了
    wizard = next((c for c in stack if c.get("kind") == "monster"), None)
    assert wizard is not None and not wizard.get("hide")           # 墙后巫师现身


def test_f47_wizard_mirror_teleport():
    """47 层:吃巫师魔伤后,该巫师镜像瞬移到以勇士为中心的对称格。"""
    eng = make_engine(floor=47, pos=(6, 1))
    # (6,1)/(8,1) 原是暗道墙贴图,清出来当通路,对称格 (8,1) 才算空地
    eng.api_set_cell(47, 6, 1, None)
    eng.api_set_cell(47, 8, 1, None)
    eng.state["hero"]["shield"] = None          # 序章自带神圣盾会免疫魔伤,先卸掉
    eng.state["hero"]["pos"] = [6, 1]
    eng._post_step(6, 1)                        # 站到 (6,1) 的结算(巫师在 (7,1) 相邻)
    grid = eng.floor_doc()["grid"]
    assert any(c.get("id") == 126 for c in (grid[1][8] or []))    # 瞬移到 (8,1)
    assert not grid[1][7]                                       # 原位腾空


def _open_f39_door(eng, x, y):
    eng.state["hero"]["pos"] = [x, y - 1]
    eng.try_move(0, 1)                          # 从上往下撞门


def test_f39_puzzle_cancelled_by_wrong_door():
    """39 层黄门机关:先开"错门"(1,1) → 整个机关永久作废,开齐对的也不再触发。"""
    eng = make_engine(floor=39, pos=(1, 0))
    eng.state["hero"]["keys"]["yellow"] = 3
    _open_f39_door(eng, 1, 1)                   # cancel 名单里的错门
    assert eng.state["flags"]["disappear_events"]["39"]["cancelled"] is True
    _open_f39_door(eng, 3, 1)                   # 完成门1
    _open_f39_door(eng, 5, 3)                   # 完成门2
    assert 16 not in eng.state["flags"].get("events_done", [])


def test_f39_puzzle_completed_opens_prison():
    """39 层黄门机关:开齐 (3,1)+(5,3) 两扇"对门" → 事件16:监狱门开+中心飞行器出现。"""
    eng = make_engine(floor=39, pos=(3, 0))
    eng.state["hero"]["keys"]["yellow"] = 2
    _open_f39_door(eng, 3, 1)
    _open_f39_door(eng, 5, 3)
    drain_dialog(eng)
    assert 16 in eng.state["flags"]["events_done"]
    stack = eng.floor_doc()["grid"][3][3] or []     # (3,3)=展平36
    assert all(c.get("kind") != "door" for c in stack)               # 监狱门开了
    assert any(c.get("kind") == "prop" and c.get("id") == 32 for c in stack)  # 飞行器


# ================================================================ 结局

def test_kill_real_boss_wins():
    """50 层杀真身魔王 133 → 通关画面(mode=ending)。"""
    eng = make_engine(floor=50, pos=(5, 5))
    eng.state["hero"].update(hp=100000, attack=2000, defence=250)
    eng.api_spawn_monster(50, 5, 4, 133)        # 事件 25 给的真身落点
    eng.try_move(0, -1)                         # 撞上去
    assert eng.mode == "ending"
    assert eng.state["hero"]["hp"] < 100000      # 真打了一场(1580-250>0 有损血)


def test_fake_boss_death_does_not_win():
    """杀剧情假魔王 132 不算通关(剧情战;真身 133 才是结局)。"""
    eng = make_engine(floor=49, pos=(0, 0))
    eng.api_spawn_monster(49, 5, 2, 132)
    eng.state["hero"].update(hp=100000, attack=2000, defence=250)
    eng._bump_monster(5, 2, eng.floor_doc()["grid"][2][5])
    assert eng.mode != "ending"
