"""batch 1 数据完整性测试:权威数值表、特殊能力字段、无悬空引用、楼层可加载。

怪物权威数值表来自设计文档附录 A(原版 TSW.exe 数据段提取,经 4 个独立复刻交叉验证),
这是"数值 1:1"需求的硬保证——任何人改动转换脚本导致数值漂移,这里立刻红。
"""
import pytest

from game import loader
from game.loader import DataError

# 附录 A 权威数值表:id -> (名字, HP, 攻, 防, 金币)
AUTHORITATIVE = {
    100: ("绿色史莱姆", 35, 18, 1, 1),
    101: ("红色史莱姆", 45, 20, 2, 2),
    102: ("小蝙蝠", 35, 38, 3, 3),
    103: ("初级法师", 60, 32, 8, 5),
    104: ("骷髅人", 50, 42, 6, 6),
    105: ("骷髅士兵", 55, 52, 12, 8),
    106: ("初级卫兵", 50, 48, 22, 12),
    107: ("骷髅队长", 100, 65, 15, 30),
    108: ("大史莱姆", 130, 60, 3, 8),
    109: ("大蝙蝠", 60, 100, 8, 12),
    110: ("高级法师", 100, 95, 30, 22),      # tacthgin 抄 18,权威 22
    111: ("兽人", 260, 85, 5, 18),          # tacthgin 抄 22,权威 18
    112: ("兽人武士", 320, 120, 15, 30),
    113: ("石头人", 20, 100, 68, 28),
    114: ("大乌贼", 1200, 180, 20, 100),
    115: ("吸血鬼", 444, 199, 66, 144),
    116: ("大法师", 4500, 560, 310, 1000),
    117: ("鬼战士", 220, 180, 30, 35),
    118: ("战士", 210, 200, 65, 45),
    119: ("幽灵", 320, 140, 20, 30),
    120: ("中级卫兵", 100, 180, 110, 100),  # tacthgin 抄 50,权威 100
    121: ("双手剑士", 100, 680, 50, 55),
    122: ("魔龙", 1500, 600, 250, 800),
    123: ("骑士", 160, 230, 105, 65),
    124: ("骑士队长", 120, 150, 50, 100),
    125: ("初级巫师", 220, 370, 110, 80),
    126: ("高级巫师", 200, 380, 130, 90),
    127: ("史莱姆王", 360, 310, 20, 40),
    128: ("吸血蝙蝠", 200, 390, 90, 50),
    129: ("黑暗骑士", 180, 430, 210, 120),
    130: ("魔法警卫", 230, 450, 100, 100),
    131: ("高级卫兵", 180, 460, 360, 200),
    132: ("魔王", 8000, 5000, 1000, 500),    # 剧情战显示值
    133: ("魔王", 5000, 1580, 190, 500),     # 汉化版真身
}


@pytest.fixture(scope="module")
def data():
    return loader.load_all()


def test_data_loads(data):
    """全部数据表与楼层能加载且通过自洽校验(load_all 内部已 validate)。"""
    assert data["monsters"] and data["items"] and data["npcs"]
    assert data["events"] and data["floors"]


def test_monsters_authoritative(data):
    """33+1 怪数值与附录 A 逐项一致(名字/HP/攻/防/金币)。"""
    mons = data["monsters"]
    assert len(mons) == len(AUTHORITATIVE)
    for mid, (name, hp, atk, df, gold) in AUTHORITATIVE.items():
        m = mons[str(mid)]
        assert (m["name"], m["hp"], m["attack"], m["defence"], m["gold"]) == \
               (name, hp, atk, df, gold), f"怪物 {mid} {name} 数值与权威表不符:{m}"


def test_monster_specials(data):
    """特殊能力 = 命名字段(设计 §4.2 选择5);3 处金币校正生效;克制道具 id。"""
    mons = data["monsters"]
    assert mons["125"]["special"]["adjacent_damage"] == 100   # 初级巫师
    assert mons["126"]["special"]["adjacent_damage"] == 200   # 高级巫师
    assert mons["130"]["special"]["halve_hp_trap"] is True    # 魔法警卫
    assert mons["110"]["gold"] == 22 and mons["111"]["gold"] == 18 \
        and mons["120"]["gold"] == 100                        # 3 处校正
    assert mons["116"]["boss"] is True                        # 大法师
    assert mons["132"]["event"] == 22 and mons["133"]["event"] is None
    # extraDamage 实为克制道具 id(考古终审):28=十字架、29=屠龙匕
    assert mons["111"]["special"]["counter_item"] == 28       # 兽人
    assert mons["115"]["special"]["counter_item"] == 28       # 吸血鬼
    assert mons["122"]["special"]["counter_item"] == 29       # 魔龙


def test_items(data):
    items = data["items"]
    assert items["1"]["kind"] == "key" and items["1"]["door"] == 1001
    assert items["4"]["hp"] == 50 and items["4"]["area_scale"] is True   # 红瓶
    assert items["6"]["attack"] == 1 and items["6"]["area_scale"]        # 红宝石
    assert items["12"]["attack"] == 100                                  # 神圣剑
    assert items["17"]["defence"] == 100                                 # 神圣盾
    assert items["28"]["affects"] == [111, 112, 115]                     # 十字架
    assert items["28"]["attack_multiplier"] == 2
    assert items["29"]["affects"] == [122]                                # 屠龙匕
    assert items["20"]["uses"] is None                                    # 飞行魔杖无限
    assert items["32"]["uses"] == 3                                       # 中心飞行器 3 次
    assert items["30"]["floor_delta"] == 1 and items["31"]["floor_delta"] == -1


def test_doors_and_kinds(data):
    doors = data["tiles"]["doors"]
    assert len(doors) == 6
    assert doors["1001"]["opens_with_key"] == 1
    assert doors["1002"]["opens_with_key"] == 2
    assert doors["1003"]["opens_with_key"] == 3
    # 考古终审:1004 事件门 / 1005 杀守卫门 / 1006 墙门(撞开)
    assert doors["1004"]["opens_by"] == "event"
    assert doors["1005"]["opens_by"] == "kill_guards"
    assert doors["1006"]["opens_by"] == "bump"
    for kind, meta in data["tiles"]["kinds"].items():
        assert isinstance(meta["walkable"], bool)


def test_events_materialized(data):
    """27 个事件全部物化为顺序动作列表(§4.2 选择3)。"""
    evs = data["events"]
    assert len(evs) == 27
    for eid, ev in evs.items():
        assert isinstance(ev["actions"], list) and ev["actions"], f"事件 {eid} 动作为空"
        for act in ev["actions"]:
            assert isinstance(act["type"], str)


def test_floors(data):
    """每层 11×11、至少一张楼梯;叠放栈引用的怪物/道具/门全部存在;存在同格多层机制。"""
    has_stack = False
    for fno, fl in data["floors"].items():
        assert len(fl["grid"]) == 11, f"楼层 {fno}"
        assert all(len(row) == 11 for row in fl["grid"])
        assert fl["stairs"] or fno in ("0", "50"), \
            f"楼层 {fno} 无楼梯(序章 0 / 结局 50 层除外)"
        for row in fl["grid"]:
            for stack in row:
                if not stack:
                    continue
                assert isinstance(stack, list) and stack, f"楼层 {fno} 空栈"
                if len(stack) > 1:
                    has_stack = True
                for cell in stack:
                    assert cell["kind"] in data["tiles"]["kinds"], (fno, cell)
                    if cell["kind"] == "monster":
                        assert str(cell["id"]) in data["monsters"]
                    elif cell["kind"] == "prop":
                        assert str(cell["id"]) in data["items"]
                    elif cell["kind"] == "door":
                        assert str(cell["id"]) in data["tiles"]["doors"]
    assert has_stack, "存在同格叠放(墙门下藏道具等)是原版机制,数据里应能找到"


def test_semantic_wiring(data):
    """考古终审接线字段:楼梯落点/祭坛/守卫门/杀怪触发/先攻位置。"""
    floors = data["floors"]
    # 祭坛只有 4 层(4/12/32/46)
    altar_floors = {fno for fno, fl in floors.items()
                    if any(any(c["kind"] == "altar" for c in st)
                           for row in fl["grid"] for st in row if st)}
    assert altar_floors == {"4", "12", "32", "46"}, altar_floors
    # 43 层上梯跨 2 层(43→45 绕过 44 层),45 层下梯 -2
    assert floors["43"]["stair_links"]["up_diff"] == 2
    assert floors["45"]["stair_links"]["down_diff"] == -2
    assert floors["2"]["stair_links"]["up_stand"] == [0, 9]   # location '99,11'
    # 49 层封印阵:事件21(杀16,26,28,38 且 15,17,37,39 存活)+ 事件22(杀@27 假魔王)
    kt49 = {kt["event"]: kt for kt in floors["49"]["kill_triggers"]}
    assert kt49[21]["kill"] == [[5, 1], [4, 2], [6, 2], [5, 3]]       # 16,26,28,38
    assert kt49[21]["keep_alive"] == [[4, 1], [6, 1], [4, 3], [6, 3]]  # 15,17,37,39
    assert kt49[22]["kill"] == [[5, 2]]                                # 27
    # 34 层杀 8 守卫开宝库
    kt34 = {kt["event"]: kt for kt in floors["34"]["kill_triggers"]}
    assert len(kt34[14]["kill"]) == 8
    # 40 层先攻怪 12 只(数据保留,暂未启用)
    assert len(floors["40"]["first_attack"]) == 12
    # 事件4 的 show 15 勘误为 115(10 层隐藏楼梯)
    show_acts = [a for a in data["events"]["4"]["actions"] if a["type"] == "show"]
    assert show_acts and all(a["data"] == 115 for a in show_acts)


def test_mechanism_wiring(data):
    """机关语义物化(v5,语义侦察报告):hide/appear/passive/monster_move 格子标记
    + appear_event/disappear_event/door_unlocks/wall_shows 楼层字段。"""
    floors = data["floors"]
    # f23 隐形墙迷宫:43 面隐形墙(撞一下显形,显形后永远是墙),全撞现→事件8
    f23 = floors["23"]
    assert f23["appear_event"]["event"] == 8
    assert len(f23["appear_event"]["positions"]) == 43
    assert all(any(c.get("appear") for c in f23["grid"][y][x])
               for x, y in (tuple(p) for p in f23["appear_event"]["positions"]))
    # f39 对称黄门机关:开错 7 门任一=永久作废;开齐 2 门→事件16(监狱门+中心飞行器)
    de = floors["39"]["disappear_event"]
    assert de["event"] == 16
    assert len(de["cancel"]) == 7 and len(de["complete"]) == 2
    # f39 商人勘误:tacthgin 属性位 (0,10) 与贴图位 (8,1) 错位,按贴图位摆
    npc30 = [n for n in floors["39"]["npcs"] if n["npc"] == 30][0]
    assert (npc30["x"], npc30["y"]) == (8, 1) and "fix" in npc30
    # f41 连锁:杀 (1,1) 巫师 → 解锁 (9,1) 假墙(passive);撞开假墙 → 显现 (9,1)
    f41 = floors["41"]
    assert f41["door_unlocks"] == [{"door": [9, 1], "kill": [1, 1]}]
    assert f41["wall_shows"] == [[9, 1]]
    assert any(c.get("passive") for st in f41["grid"][1] if st for c in st
               if c.get("layer") == "door")
    # f47 镜像巫师:2 只高级巫师带 monster_move
    f47 = floors["47"]
    mm = [[x, y] for y, row in enumerate(f47["grid"]) for x, st in enumerate(row)
          if st for c in st if c.get("monster_move")]
    assert mm == [[7, 1], [0, 8]]
    # hide 格:f10 (5,10) 上梯隐藏(杀骑士队长后事件18显现),不是镐破墙
    f10 = floors["10"]
    assert any(c.get("kind") == "stair" and c.get("hide")
               for st in f10["grid"][10] if st for c in st)
    # f1 无下行梯:location 的 '0' 占位归一成 None
    assert floors["1"]["stair_links"]["down_stand"] is None
    # 守卫门的门 id 混用(1004/1006 居多),引擎不能按 1005 过滤——数据侧钉死事实
    ids = {c.get("id") for fno, fl in floors.items()
           for gd in fl.get("guard_doors") or []
           for p in gd["doors"] for st in [fl["grid"][p[1]][p[0]]] if st
           for c in [st[-1]] if c.get("kind") == "door"}
    assert ids == {1004, 1005, 1006}, ids


def test_floor_wiring(data):
    """NPC 摆放与踩格触发已由层属性机械接线;引用全部有效。"""
    npc_total = trig_total = 0
    for fno, fl in data["floors"].items():
        for npc in fl.get("npcs", []):
            assert str(npc["npc"]) in data["npcs"], (fno, npc)
            assert 0 <= npc["x"] < 11 and 0 <= npc["y"] < 11
            npc_total += 1
        for trig in fl.get("triggers", []):
            assert str(trig["event"]) in data["events"], (fno, trig)
            trig_total += 1
    assert npc_total > 0, "NPC 摆放表为空(层属性解码应产生 NPC 接线)"
    assert trig_total > 0, "踩格触发器为空(event 层属性解码应产生触发器)"


def test_missing_file_error(tmp_path, monkeypatch):
    """数据缺失要报清晰错误(文件名),不许静默或裸抛底层异常。"""
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "FLOORS_DIR", tmp_path / "floors")
    with pytest.raises(DataError, match="数据文件缺失"):
        loader.load_all()


def test_corrupt_file_error(tmp_path, monkeypatch):
    """坏 JSON 报"不是合法 JSON"并带文件名。"""
    (tmp_path / "monsters.json").write_text("{不是合法JSON", encoding="utf-8")
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "FLOORS_DIR", tmp_path / "floors")
    with pytest.raises(DataError, match="monsters.json"):
        loader.load_all()
