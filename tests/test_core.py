"""batch 2(规则核心)+ batch 5(存档)测试:战斗/祭坛/拾取/特殊机制/存档。

数字全部来自设计文档 §3.1/§5、附录 A 权威数值表,以及源码考古勘误
(开局序章数值、area 边界、祭坛独立公式、40 层魔伤门槛)。
原版怪癖在这里用单测钉死——裸攻破防、先手少挨一击、警卫夹击向上取整、
第 10 层宝石 ×1 但祭坛区 ×2——谁"顺手修正"谁挂红。
跑法:cd ~/mota50 && env -u PYTHONPATH .venv/bin/python -m pytest tests/test_core.py -v
"""
import copy
import json
from pathlib import Path

import pytest

from game import loader
from game.core import (SaveError, adjacent_damage, altar_buy, altar_price,
                       apply_pickup, area, calc_battle, guard_trap,
                       load_game, new_state, save_game, use_tool,
                       weaken_monster)
from game.core import state as core_state


# ---------------------------------------------------------------- 测试小工具

@pytest.fixture(scope="module")
def data():
    """一次性加载全部真实数据(怪物/道具表都从这拿)。"""
    return loader.load_all()


@pytest.fixture
def st():
    """每条测试都从干净开局状态开始,互相不污染。"""
    return new_state()


def hero_reset(state, hp=400, attack=10, defence=10, gold=0):
    """把勇士数值重置成好算的整数(面板含装备,所以装备也一并清空)。"""
    hero = state["hero"]
    hero.update(hp=hp, attack=attack, defence=defence, gold=gold)
    hero["sword"] = None
    hero["shield"] = None
    return state


def plain_hero(hp, attack, defence):
    """手工拼一个勇士——战斗是纯函数,只读 hp/attack/defence 三个数。"""
    return {"hp": hp, "attack": attack, "defence": defence}


def make_floor(cells):
    """拼一张测试用楼层图:cells 是 {(x, y): 叠放栈},其余全是空地。

    形状和 game/data/floors/*.json 一致:{"grid": 11×11 二维数组}。
    """
    grid = [[None] * 11 for _ in range(11)]
    for (x, y), stack in cells.items():
        grid[y][x] = stack
    return {"grid": grid}


def mon_cell(monster_id):
    """一格怪物栈(叠放栈的栈顶就是它)。"""
    return [{"kind": "monster", "id": monster_id}]


def guard_cell():
    return mon_cell(130)      # 魔法警卫


def magic_state(floor=41):
    """巫师魔伤/警卫夹击 40 层起才生效,特殊机制的测试默认站在 41 层。

    注意:序章开局自带神圣盾(免疫一切魔伤),测魔伤前先卸掉,
    不然所有用例都会"莫名其妙全免疫"。
    """
    state = new_state()
    state["floor"] = floor
    state["hero"]["shield"] = None
    return state


@pytest.fixture
def save_dir(tmp_path, monkeypatch):
    """把存档目录指到 pytest 临时目录——测试绝不碰玩家的真存档。"""
    monkeypatch.setattr(core_state, "SAVE_DIR", tmp_path)
    return tmp_path


# ================================================================ 战斗纯函数
# 怪物数值对照:绿色史莱姆100=35/18/1 小蝙蝠102=35/38/3 大史莱姆108=130/60/3
# 兽人武士112=320/120/15 石头人113=20/100/68 吸血鬼115=444/199/66 魔龙122=1500/600/250


def test_battle_green_slime_one_hit(data):
    """正常胜:400/120/35 撞绿色史莱姆,一击秒杀,损血 0。"""
    result = calc_battle(plain_hero(400, 120, 35), data["monsters"]["100"], {})
    assert result == {"can_fight": True, "hero_damage": 0, "turns": 1, "gold": 1}


def test_battle_orc_warrior_four_turns(data):
    """多回合:400/120/35 撞兽人武士,每击 105、出手 4 次、损血 3×85=255。"""
    result = calc_battle(plain_hero(400, 120, 35), data["monsters"]["112"], {})
    assert result == {"can_fight": True, "hero_damage": 255, "turns": 4, "gold": 30}


def test_battle_cannot_pierce(data):
    """不破防:ATK 10 ≤ 石头人防 68 → 不可战斗;ATK 恰好 == 怪防也一样。"""
    assert calc_battle(plain_hero(400, 10, 10), data["monsters"]["113"], {})["can_fight"] is False
    assert calc_battle(plain_hero(400, 68, 10), data["monsters"]["113"], {})["can_fight"] is False
    # 攻 69 > 防 68 破防了,但 20 击要挨 1710,HP 400 打不起(预览数据要给全)
    result = calc_battle(plain_hero(400, 69, 10), data["monsters"]["113"], {})
    assert result == {"can_fight": False, "hero_damage": 1710, "turns": 20, "gold": 28}


def test_battle_zero_damage_still_wins(data):
    """0 伤害:防 38 ≥ 小蝙蝠攻 38 → 怪每击 0,损血 0 仍然获胜。"""
    result = calc_battle(plain_hero(400, 120, 38), data["monsters"]["102"], {})
    assert result == {"can_fight": True, "hero_damage": 0, "turns": 1, "gold": 3}


def test_battle_first_strike_saves_one_hit(data):
    """先手优势:损血 = (n-1)×d2 不是 n×d2。

    兽人武士一仗要挨 3×85=255:HP 300 打得过;若按 n×d2=340 算就该打不过了。
    """
    result = calc_battle(plain_hero(300, 120, 35), data["monsters"]["112"], {})
    assert result["hero_damage"] == 255 == (result["turns"] - 1) * 85
    assert result["can_fight"] is True          # 255 < 300;340 ≥ 300 就该是 False


def test_battle_cross_doubles_vampire(data):
    """十字架:对吸血鬼攻×2。同一个勇士,没十字架打不过、有十字架 6 回合 495 血拿下。"""
    hero = plain_hero(2000, 70, 100)
    plain = calc_battle(hero, data["monsters"]["115"], {})
    assert plain == {"can_fight": False, "hero_damage": 10890, "turns": 111, "gold": 144}
    cross = calc_battle(hero, data["monsters"]["115"], {"cross": True})
    assert cross == {"can_fight": True, "hero_damage": 495, "turns": 6, "gold": 144}


def test_battle_cross_pierce_uses_bare_attack(data):
    """原版怪癖:破防判定用【裸攻击力】——攻 50×2=100 > 吸血鬼防 66,
    但裸攻 50 ≤ 66,照样整场不可战斗(单测钉死,不许"顺手修正")。"""
    result = calc_battle(plain_hero(400, 50, 10),
                         data["monsters"]["115"], {"cross": True})
    assert result == {"can_fight": False, "hero_damage": 0, "turns": 0, "gold": 0}


def test_battle_cross_orc_warrior(data):
    """十字架对兽人武士同样生效:4 回合 255 血变 2 回合 85 血。"""
    hero = plain_hero(400, 120, 35)
    cross = calc_battle(hero, data["monsters"]["112"], {"cross": True})
    assert cross == {"can_fight": True, "hero_damage": 85, "turns": 2, "gold": 30}


def test_battle_lucky_coin_doubles_gold(data):
    """幸运金币:战后金币 ×2。"""
    hero = plain_hero(400, 120, 35)
    assert calc_battle(hero, data["monsters"]["100"], {"lucky_coin": True})["gold"] == 2
    assert calc_battle(hero, data["monsters"]["112"], {"lucky_coin": True})["gold"] == 60
    assert calc_battle(hero, data["monsters"]["112"], {})["gold"] == 30


def test_battle_exact_division(data):
    """边界:d1 恰好整除怪血时出手数不多不少(210 血每击 105 → 正好 2 次)。"""
    fake = {"id": 999, "name": "测试假怪", "hp": 210, "attack": 120,
            "defence": 15, "gold": 30, "special": {}}
    result = calc_battle(plain_hero(400, 120, 35), fake, {})
    assert result["turns"] == 2 and result["hero_damage"] == 85
    # 真实数据里再钉一个:大史莱姆 130 血、每击 117 → 2 次、损 25
    real = calc_battle(plain_hero(400, 120, 35), data["monsters"]["108"], {})
    assert real == {"can_fight": True, "hero_damage": 25, "turns": 2, "gold": 8}


def test_battle_dragon_slayer(data):
    """屠龙匕:只对魔龙 ×2;十字架对魔龙无效、屠龙匕对别的怪也无效。"""
    hero = plain_hero(1000, 300, 400)
    dragon = data["monsters"]["122"]
    assert calc_battle(hero, dragon, {}) == \
        {"can_fight": False, "hero_damage": 5800, "turns": 30, "gold": 800}
    assert calc_battle(hero, dragon, {"dragon_slayer": True}) == \
        {"can_fight": True, "hero_damage": 800, "turns": 5, "gold": 800}
    # 十字架对魔龙不翻倍(还是 5800),屠龙匕对兽人武士不翻倍(还是 255)
    assert calc_battle(hero, dragon, {"cross": True})["hero_damage"] == 5800
    assert calc_battle(plain_hero(400, 120, 35), data["monsters"]["112"],
                       {"dragon_slayer": True})["hero_damage"] == 255


def test_battle_unfightable_flag_respected(data):
    """数据若给怪标 unfightable(通用机制位,当前全塔无怪使用):攻防再高也打不了。
    勘误记录:当初误标给巫师 125/126,走查到 48 层发现上梯唯一通路被初级巫师
    堵死、原版显然能过;回查上游 monster.json 无此字段(v6 已删误标)。"""
    fake = dict(data["monsters"]["100"], special={"unfightable": True})
    result = calc_battle(plain_hero(99999, 9999, 9999), fake, {})
    assert result == {"can_fight": False, "hero_damage": 0, "turns": 0, "gold": 0}


def test_battle_wizards_are_fightable(data):
    """巫师 125/126 可正面战斗(上游数据无"不可战斗"标记;魔伤只是守关特性)。"""
    hero = plain_hero(4000, 250, 150)      # 两回合杀:巫师能还手一次,损血可见
    for mid in ("125", "126"):
        result = calc_battle(hero, data["monsters"][mid], {})
        assert result["can_fight"] is True and result["hero_damage"] > 0


def test_battle_death_preview(data):
    """打不过(会死)时 can_fight=False,但损血/回合/金币照常算——
    怪物手册的预测面板靠这个展示"这仗要挨多少下"。"""
    result = calc_battle(plain_hero(100, 120, 35), data["monsters"]["112"], {})
    assert result == {"can_fight": False, "hero_damage": 255, "turns": 4, "gold": 30}


def test_battle_flags_default_false(data):
    """flags 缺键 / 传 None 都当全 False,行为和传空字典完全一致。"""
    hero = plain_hero(400, 120, 35)
    monster = data["monsters"]["112"]
    expected = {"can_fight": True, "hero_damage": 255, "turns": 4, "gold": 30}
    assert calc_battle(hero, monster, {"lucky_coin": False}) == expected
    assert calc_battle(hero, monster, None) == expected


def test_battle_first_attack_flag(data):
    """预留的 first_attack 开关(40 层先攻怪,项目默认不做):
    True 时损血 = n×d2(怪抢先手),默认 False 时 = (n-1)×d2,行为不变。"""
    hero = plain_hero(300, 120, 35)
    monster = data["monsters"]["112"]
    normal = calc_battle(hero, monster, {})
    assert normal["hero_damage"] == 255 and normal["can_fight"] is True
    rushing = calc_battle(hero, monster, {"first_attack": True})
    assert rushing["hero_damage"] == 4 * 85 == 340 and rushing["can_fight"] is False


# ==================================================================== 祭坛

def test_altar_price_sequence():
    """第 1/2/3/4/5 次价格 20/40/80/140/220(10×(n²−n+2))。"""
    assert [altar_price(n) for n in range(1, 6)] == [20, 40, 80, 140, 220]


def test_altar_floor12_bonuses(st):
    """12 层祭坛(区域 2):买攻 +4、买防 +8;价格第 1/2 次是 20/40。"""
    hero_reset(st, gold=100)
    st["floor"] = 12
    altar_buy(st, "attack")
    altar_buy(st, "defence")
    assert (st["hero"]["attack"], st["hero"]["defence"]) == (14, 18)
    assert (st["hero"]["gold"], st["flags"]["altar_count"]) == (40, 2)


def test_altar_count_shared_across_floors(st):
    """祭坛计数全塔共享:两次购买之间换楼层,第 2 次还是按 40 收钱、
    攻击加成按买的那层区域算(4 层 +2、12 层 +4)。"""
    hero_reset(st, gold=1000)
    st["floor"] = 4
    altar_buy(st, "attack")
    st["floor"] = 12
    altar_buy(st, "attack")
    assert st["hero"]["attack"] == 10 + 2 + 4 == 16
    assert (st["hero"]["gold"], st["flags"]["altar_count"]) == (1000 - 60, 2)


def test_altar_hp_scales_with_purchase_number(st):
    """买生命:第 n 次 +100×n(连买三次就是 +100/+200/+300)。"""
    hero_reset(st, gold=1000)
    for _ in range(3):
        altar_buy(st, "hp")
    assert st["hero"]["hp"] == 400 + 100 + 200 + 300 == 1000
    assert st["hero"]["gold"] == 1000 - (20 + 40 + 80)
    assert st["flags"]["altar_count"] == 3


def test_altar_zone_formula_differs_from_gem_area(st, data):
    """原版怪癖:第 10 层宝石按 ×1 算,但祭坛区按 floor//10+1=2 算
    (TS 源码里就是两个不同的公式,这里钉死,不许"顺手统一")。"""
    hero_reset(st, gold=1000)
    st["floor"] = 10
    altar_buy(st, "attack")
    assert st["hero"]["attack"] == 10 + 2 * 2 == 14      # 祭坛区 2 → 攻+4
    apply_pickup(st, 6, data)                            # 红宝石,area(10)=1 → 攻+1
    assert st["hero"]["attack"] == 15


def test_altar_not_enough_gold(st):
    """钱不够抛 ValueError,而且次数/金币/属性全都不动。"""
    hero_reset(st, gold=19)                              # 第 1 次要 20,差 1 块
    with pytest.raises(ValueError, match="金币不够"):
        altar_buy(st, "attack")
    assert (st["hero"]["gold"], st["hero"]["attack"], st["flags"]["altar_count"]) == (19, 10, 0)
    st["hero"]["gold"] = 20                              # 刚好够也允许买
    altar_buy(st, "attack")
    assert (st["hero"]["gold"], st["flags"]["altar_count"]) == (0, 1)
    with pytest.raises(ValueError, match="金币不够"):     # 第 2 次要 40,没钱了
        altar_buy(st, "attack")
    assert st["flags"]["altar_count"] == 1                # 次数没被这次失败消耗


def test_altar_invalid_choice(st):
    """choice 只认 hp/attack/defence,别的直接 ValueError 且不改状态。"""
    with pytest.raises(ValueError, match="三选一"):
        altar_buy(st, "power")
    assert st["hero"]["gold"] == 0 and st["flags"]["altar_count"] == 0


def test_altar_buy_returns_same_state(st):
    """altar_buy 原地修改并返回同一个 state(方便链式调用)。"""
    hero_reset(st, gold=1000)
    assert altar_buy(st, "hp") is st


# ================================================================ 拾取与区域

def test_pickup_gem_area_boundaries(st, data):
    """红宝石攻 +1×区域:1 层 +1、10 层还是 +1、11 层 +2、41 层 +5。"""
    for floor, gain in ((1, 1), (10, 1), (11, 2), (41, 5)):
        state = hero_reset(new_state(), attack=10)
        state["floor"] = floor
        apply_pickup(state, 6, data)
        assert state["hero"]["attack"] == 10 + gain, f"{floor} 层红宝石应攻 +{gain}"


def test_pickup_potions_area(data):
    """蓝瓶 HP+200×区(10 层 ×1、11 层 ×2、41 层 ×5);红瓶 +50×区。"""
    for floor, gain in ((1, 200), (10, 200), (11, 400), (41, 1000)):
        state = hero_reset(new_state())
        state["floor"] = floor
        apply_pickup(state, 5, data)
        assert state["hero"]["hp"] == 400 + gain, f"{floor} 层蓝瓶应 HP +{gain}"
    state = hero_reset(new_state())
    state["floor"] = 1
    apply_pickup(state, 4, data)
    assert state["hero"]["hp"] == 400 + 50


def test_pickup_blue_gem(st, data):
    """蓝宝石防 +1×区(11 层 ×2)。"""
    hero_reset(st, defence=10)
    st["floor"] = 11
    apply_pickup(st, 7, data)
    assert st["hero"]["defence"] == 12


def test_pickup_sword_upgrade_no_downgrade(st, data):
    """武器只升不降:铁剑→银剑换装补差价,再捡铁剑无视,神圣剑 +100 直升。"""
    hero_reset(st, attack=10)
    apply_pickup(st, 8, data)                    # 铁剑 +10:攻 20,装备"8"
    assert (st["hero"]["attack"], st["hero"]["sword"]) == (20, "8")
    apply_pickup(st, 9, data)                   # 银剑 +20:攻 20+(20-10)=30,换"9"
    assert (st["hero"]["attack"], st["hero"]["sword"]) == (30, "9")
    apply_pickup(st, 8, data)                   # 再捡铁剑:低级不回退、不叠加
    assert (st["hero"]["attack"], st["hero"]["sword"]) == (30, "9")
    apply_pickup(st, 12, data)                  # 神圣剑 +100:攻 30+(100-20)=110
    assert (st["hero"]["attack"], st["hero"]["sword"]) == (110, "12")


def test_pickup_shield_upgrade_no_downgrade(st, data):
    """盾同样只升不降:铁盾→银盾→(再捡铁盾不动)→神圣盾。"""
    hero_reset(st, defence=10)
    apply_pickup(st, 13, data)
    apply_pickup(st, 14, data)
    assert (st["hero"]["defence"], st["hero"]["shield"]) == (30, "14")
    apply_pickup(st, 13, data)                  # 低级盾不回退
    assert (st["hero"]["defence"], st["hero"]["shield"]) == (30, "14")
    apply_pickup(st, 17, data)                  # 神圣盾 +100:防 30+80=110
    assert (st["hero"]["defence"], st["hero"]["shield"]) == (110, "17")


def test_pickup_keys_count(st, data):
    """钥匙进三色计数:黄 1/蓝 2/红 3 各 +1,重复捡就累加。"""
    for item_id in (1, 2, 3, 1):
        apply_pickup(st, item_id, data)
    assert st["hero"]["keys"] == {"yellow": 2, "blue": 1, "red": 1}


def test_pickup_tools_into_props(st, data):
    """工具(手册/幸运金币/飞行魔杖)记进 hero.props,id 恒 1。"""
    for item_id in (18, 27, 20):
        apply_pickup(st, item_id, data)
    assert st["hero"]["props"] == {"18": 1, "27": 1, "20": 1}


def test_pickup_unknown_item(st, data):
    """捡到道具表里没有的东西:明确报错,不许静默。"""
    with pytest.raises(ValueError, match="未知道具"):
        apply_pickup(st, 999, data)


def test_pickup_returns_same_state(st, data):
    """apply_pickup 原地修改并返回同一个 state。"""
    st["floor"] = 1
    assert apply_pickup(st, 6, data) is st


def test_holy_water_use(st, data):
    """圣水:捡到进 props(不马上生效);主动用时 HP += 攻+防,用完删键;
    没有了再用/用别的道具 → ValueError。"""
    hero_reset(st, hp=400, attack=100, defence=60)
    apply_pickup(st, 26, data)
    assert st["hero"]["props"] == {"26": 1} and st["hero"]["hp"] == 400
    use_tool(st, 26)
    assert st["hero"]["hp"] == 400 + 100 + 60 == 560
    assert "26" not in st["hero"]["props"]        # 一次性,用完删键
    with pytest.raises(ValueError, match="没有圣水"):
        use_tool(st, 26)
    with pytest.raises(ValueError, match="规则层"):
        use_tool(st, 21)                          # 镐是地图类道具,引擎层管


# ================================================================ 特殊机制

def test_adjacent_damage_basic(data):
    """相邻一只初级巫师扣 100、高级巫师扣 200(都在 41 层)。"""
    state = magic_state()
    assert adjacent_damage(state, make_floor({(5, 4): mon_cell(125)}), 5, 5) == 100
    assert adjacent_damage(state, make_floor({(5, 4): mon_cell(126)}), 5, 5) == 200


def test_adjacent_damage_stacks(data):
    """两只巫师同时挨着就叠加:两只初级 200、初级+高级 300。"""
    state = magic_state()
    two_junior = make_floor({(4, 5): mon_cell(125), (6, 5): mon_cell(125)})
    assert adjacent_damage(state, two_junior, 5, 5) == 200
    mixed = make_floor({(5, 4): mon_cell(125), (5, 6): mon_cell(126)})
    assert adjacent_damage(state, mixed, 5, 5) == 300


def test_adjacent_damage_shield_immune():
    """持神圣盾免疫巫师魔伤:装备在 hero.shield、或记在 hero.props 都算数。"""
    fl = make_floor({(5, 4): mon_cell(125), (5, 6): mon_cell(126)})
    via_shield = magic_state()
    via_shield["hero"]["shield"] = "17"
    via_props = magic_state()
    via_props["hero"]["props"] = {"17": 1}
    assert adjacent_damage(via_shield, fl, 5, 5) == 0
    assert adjacent_damage(via_props, fl, 5, 5) == 0


def test_adjacent_damage_geometry():
    """只有上下左右紧挨着才算:斜对角/隔一格/被墙压住都不扣,地图边缘不越界。"""
    state = magic_state()
    assert adjacent_damage(state, make_floor({(4, 4): mon_cell(125)}), 5, 5) == 0   # 斜角
    assert adjacent_damage(state, make_floor({(5, 3): mon_cell(125)}), 5, 5) == 0   # 隔一格
    buried = make_floor({(5, 4): [{"kind": "monster", "id": 125},   # 巫师被墙压住
                                   {"kind": "wall", "sprite": "door1006_0"}]})
    assert adjacent_damage(state, buried, 5, 5) == 0
    # 勇士在 (0,5):左边越界必须是真空,不能让 Python 负下标绕到行尾 (10,5)
    edge = make_floor({(10, 5): mon_cell(125)})
    assert adjacent_damage(state, edge, 0, 5) == 0
    # 真挨着地图边上的巫师要正常扣:勇士 (0,0) 右边 (1,0) 有初级巫师
    corner = make_floor({(1, 0): mon_cell(125)})
    assert adjacent_damage(state, corner, 0, 0) == 100


def test_adjacent_damage_requires_floor40():
    """魔伤只在 ≥40 层生效(MAGIC_DAMAGE_LEVEL=40):3 层/39 层不扣,40 层起扣。"""
    fl = make_floor({(5, 4): mon_cell(125)})
    for floor in (3, 39):
        state = magic_state(floor)
        assert adjacent_damage(state, fl, 5, 5) == 0, f"{floor} 层不该有魔伤"
    assert adjacent_damage(magic_state(40), fl, 5, 5) == 100


def test_guard_trap_halves():
    """警卫夹击 HP 减半向上取整:999→500(奇数)、100→50(偶数)、1→1。"""
    horizontal = make_floor({(4, 5): guard_cell(), (6, 5): guard_cell()})
    vertical = make_floor({(5, 4): guard_cell(), (5, 6): guard_cell()})
    state = magic_state()
    state["hero"]["hp"] = 999
    guard_trap(state, horizontal, 5, 5)
    assert state["hero"]["hp"] == 500
    state["hero"]["hp"] = 100
    guard_trap(state, vertical, 5, 5)
    assert state["hero"]["hp"] == 50
    state["hero"]["hp"] = 1
    guard_trap(state, horizontal, 5, 5)
    assert state["hero"]["hp"] == 1                    # ceil(1/2)=1,不会扣死


def test_guard_trap_requires_flanking_pair():
    """必须是一对警卫左右(或上下)正好把这一格夹住,别的摆法都不触发。"""
    state = magic_state()
    state["hero"]["hp"] = 999
    single = make_floor({(4, 5): guard_cell()})                          # 只有一只
    diagonal = make_floor({(4, 4): guard_cell(), (6, 6): guard_cell()})  # 斜对角
    offset = make_floor({(3, 5): guard_cell(), (6, 5): guard_cell()})    # 不对称
    same_side = make_floor({(4, 5): guard_cell(), (5, 4): guard_cell()}) # 同边两只
    for fl in (single, diagonal, offset, same_side):
        guard_trap(state, fl, 5, 5)
        assert state["hero"]["hp"] == 999, f"{fl} 不该触发夹击"
    assert guard_trap(state, single, 5, 5) is state      # 原地返回同一个 state


def test_guard_trap_requires_floor40():
    """夹击也只在 ≥40 层生效:3 层被夹 HP 不动,40 层起才减半。"""
    fl = make_floor({(4, 5): guard_cell(), (6, 5): guard_cell()})
    low = magic_state(3)
    low["hero"]["hp"] = 999
    guard_trap(low, fl, 5, 5)
    assert low["hero"]["hp"] == 999
    high = magic_state(40)
    high["hero"]["hp"] = 999
    guard_trap(high, fl, 5, 5)
    assert high["hero"]["hp"] == 500


def test_weaken_monster(data):
    """49 层封印:假魔王 8000/5000/1000 ×0.1 → 800/500/100,原怪不动。"""
    boss = data["monsters"]["132"]
    weakened = weaken_monster(boss, 0.1)
    assert (weakened["hp"], weakened["attack"], weakened["defence"]) == (800, 500, 100)
    assert weakened is not boss                          # 返回副本
    assert (boss["hp"], boss["attack"], boss["defence"]) == (8000, 5000, 1000)
    assert (weakened["gold"], weakened["name"], weakened["id"]) == (500, "魔王", 132)


# ============================================================ 状态与存档

def test_new_state_structure():
    """开局 = 序章:神圣剑盾 1000/100/100 从 1 层开打(被夺装备是 3 层的
    剧情事件,事件层负责,不归开局管)。"""
    assert new_state() == {
        "floor": 1,
        "hero": {
            "hp": 1000, "attack": 100, "defence": 100, "gold": 0,
            "keys": {"yellow": 0, "blue": 0, "red": 0},
            "props": {},
            "sword": "12", "shield": "17",
            "pos": [5, 10],
        },
        "flags": {"altar_count": 0, "events_done": [], "monsters_dead": []},
        "floors_state": {},
        "visited": [1],
    }


@pytest.mark.parametrize("floor,expected", [
    (1, 1), (9, 1), (10, 1),      # 第 10 层还算 ×1
    (11, 2), (12, 2), (20, 2),    # 11 层起 ×2
    (21, 3), (40, 4),
    (41, 5), (50, 5),             # 41 层起 ×5
])
def test_area_boundaries(floor, expected):
    """区域倍率 area = (floor-1)//10+1:宝石血瓶用(祭坛是另一个公式,见上)。"""
    assert area(floor) == expected


def rich_state():
    """手工拼一个"玩过一阵子"的存档状态:字段全填,回滚测试才有代表性。"""
    state = new_state()
    state["floor"] = 12
    hero = state["hero"]
    hero.update(hp=123, attack=45, defence=67, gold=89)
    hero["keys"] = {"yellow": 3, "blue": 2, "red": 1}
    hero["props"] = {"18": 1, "27": 1}
    hero["sword"], hero["shield"], hero["pos"] = "9", "14", [5, 6]
    state["flags"] = {"altar_count": 2, "events_done": ["7:0,0"],
                      "monsters_dead": ["2:3,4", "2:5,5"]}
    state["floors_state"] = {"2": [[3, 4, None], [5, 5, None]]}
    state["visited"] = [1, 2, 3, 4]
    return state


def test_save_load_roundtrip(save_dir):
    """存档→继续改状态→读档 = 完全回滚到存档那一刻(整包快照,逐键相等)。"""
    state = rich_state()
    snapshot = copy.deepcopy(state)
    save_game(state, 1)
    # 存完继续玩:楼层变了、血被打残、又上了一层——都不该影响刚存的档
    state["floor"] = 50
    state["hero"]["hp"] = 1
    state["visited"].append(12)
    loaded = load_game(1)
    assert loaded == snapshot                    # 读档 = 回滚一致
    assert state["hero"]["hp"] == 1             # 原对象没被读档动过
    # 文件里真的是带版本号的整包 JSON
    raw = json.loads((save_dir / "save_1.json").read_text(encoding="utf-8"))
    assert raw == {"version": 1, "state": snapshot}


def test_load_returns_fresh_copy(save_dir):
    """每次读档都是全新一份,改它不会污染存档文件。"""
    state = rich_state()
    save_game(state, 2)
    loaded = load_game(2)
    loaded["hero"]["hp"] = 0
    loaded["flags"]["altar_count"] = 99
    assert load_game(2) == state                # 再读还是原样


def test_load_corrupt_json(save_dir):
    """手工把存档改成坏 JSON → SaveError,报错带文件名。"""
    (save_dir / "save_1.json").write_text("{这不是合法JSON,,,", encoding="utf-8")
    with pytest.raises(SaveError, match="save_1.json"):
        load_game(1)


def test_load_missing_fields(save_dir):
    """缺关键字段(比如 hero)→ SaveError,而且一次把缺的全报出来。"""
    (save_dir / "save_1.json").write_text(
        json.dumps({"version": 1, "state": {
            "floor": 2, "flags": new_state()["flags"],
            "floors_state": {}, "visited": [2]}}), encoding="utf-8")
    with pytest.raises(SaveError, match="save_1.json") as exc:
        load_game(1)
    assert "hero" in str(exc.value)
    # 缺一大片的更要一次报全
    (save_dir / "save_2.json").write_text(
        json.dumps({"version": 1, "state": {"floor": 2}}), encoding="utf-8")
    with pytest.raises(SaveError) as exc:
        load_game(2)
    message = str(exc.value)
    for key in ("hero", "flags", "floors_state", "visited"):
        assert key in message, f"缺 {key} 没被报出来"


def test_load_missing_file(save_dir):
    """读一个空槽 → SaveError 带文件名(不是裸 FileNotFoundError)。"""
    with pytest.raises(SaveError, match="save_3.json"):
        load_game(3)


def test_load_bad_version_or_shape(save_dir, st):
    """版本不对 / 缺 version / 缺 state / 顶层不是字典 → 全是 SaveError。"""
    good_state = rich_state()
    (save_dir / "save_1.json").write_text(
        json.dumps({"version": 2, "state": good_state}), encoding="utf-8")
    with pytest.raises(SaveError, match="版本"):
        load_game(1)
    (save_dir / "save_1.json").write_text(
        json.dumps({"state": good_state}), encoding="utf-8")        # 缺 version
    with pytest.raises(SaveError, match="版本"):
        load_game(1)
    (save_dir / "save_1.json").write_text(
        json.dumps({"version": 1}), encoding="utf-8")                # 缺 state
    with pytest.raises(SaveError, match="state"):
        load_game(1)
    (save_dir / "save_1.json").write_text("[1, 2, 3]", encoding="utf-8")  # 顶层非字典
    with pytest.raises(SaveError, match="顶层"):
        load_game(1)
    (save_dir / "save_1.json").write_text(                           # state 是字符串
        json.dumps({"version": 1, "state": "假档"}), encoding="utf-8")
    with pytest.raises(SaveError, match="state"):
        load_game(1)


def test_slot_validation(save_dir, st):
    """槽位只认 1/2/3,别的槽位号直接 ValueError。"""
    for bad in (0, 4, "1", None):
        with pytest.raises(ValueError):
            save_game(st, bad)
        with pytest.raises(ValueError):
            load_game(bad)
    save_game(st, 1)                                   # 合法槽位随便用
    save_game(st, 2)
    save_game(st, 3)


def test_core_source_pygame_free():
    """规则层红线:core/ 的源码里不许出现 pygame(SSH/无显示环境可全量测试)。"""
    core_dir = Path(__file__).resolve().parents[1] / "game" / "core"
    for py in sorted(core_dir.glob("*.py")):
        source = py.read_text(encoding="utf-8")
        assert "import pygame" not in source, f"{py.name} 里不许出现 pygame 导入"
        assert "from pygame" not in source, f"{py.name} 里不许从 pygame 导入"
