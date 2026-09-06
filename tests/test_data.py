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
    """特殊能力 = 命名字段(设计 §4.2 选择5);3 处金币校正生效。"""
    mons = data["monsters"]
    assert mons["125"]["special"]["adjacent_damage"] == 100   # 初级巫师
    assert mons["126"]["special"]["adjacent_damage"] == 200   # 高级巫师
    assert mons["130"]["special"]["halve_hp_trap"] is True    # 魔法警卫
    assert mons["110"]["gold"] == 22 and mons["111"]["gold"] == 18 \
        and mons["120"]["gold"] == 100                        # 3 处校正
    assert mons["116"]["boss"] is True                        # 大法师
    assert mons["132"]["event"] == 22 and mons["133"]["event"] is None


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
    for did in ("1004", "1005", "1006"):                                 # 监狱/怪物/墙门
        assert doors[did]["opens_by"] == "event"
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
