"""数据层加载器:读 game/data/ 的 JSON 并做完整性校验。

设计文档 §4.2 选择6:本模块(以及 core/、events.py)禁止 import pygame——
规则与数据层在无显示环境(SSH / CI)下也要能加载、能测试。
数据缺失 / 损坏一律抛 DataError(带文件名与原因),由调用方给出清晰报错,禁止静默吞掉。
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
FLOORS_DIR = DATA_DIR / "floors"


class DataError(Exception):
    """数据文件缺失 / 损坏 / 不自洽。"""


def _read(path: Path):
    if not path.exists():
        raise DataError(f"数据文件缺失:{path}(重跑 scripts/convert_tacthgin.py 生成)")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataError(f"数据文件损坏(不是合法 JSON):{path} —— {exc}") from exc


def load_all():
    """加载全部数据表与楼层,并校验;返回大字典。任何一处坏都抛 DataError。"""
    data = {
        "monsters": _read(DATA_DIR / "monsters.json"),
        "items": _read(DATA_DIR / "items.json"),
        "npcs": _read(DATA_DIR / "npcs.json"),
        "events": _read(DATA_DIR / "events.json"),
        "tiles": _read(DATA_DIR / "tiles.json"),
    }
    if not FLOORS_DIR.is_dir():
        raise DataError(f"楼层目录缺失:{FLOORS_DIR}")
    floors = {}
    for f in sorted(FLOORS_DIR.glob("f*.json"), key=lambda p: int(p.stem[1:])):
        floors[f.stem[1:]] = _read(f)
    if not floors:
        raise DataError(f"楼层目录为空:{FLOORS_DIR}(重跑转换脚本)")
    data["floors"] = floors
    validate(data)
    return data


def validate(data):
    """跨表自洽校验;有问题把所有问题一次性报全,不许只报第一个。"""
    errors = []
    mon_ids = {int(k) for k in data["monsters"]}
    item_ids = {int(k) for k in data["items"]}
    door_ids = {int(k) for k in data["tiles"]["doors"]}
    event_ids = {int(k) for k in data["events"]}
    kinds = set(data["tiles"]["kinds"])

    for fno, fl in data["floors"].items():
        if len(fl["grid"]) != 11 or any(len(row) != 11 for row in fl["grid"]):
            errors.append(f"楼层 {fno}:地图不是 11×11")
        for y, row in enumerate(fl["grid"]):
            for x, stack in enumerate(row):
                if stack is None:
                    continue
                if not isinstance(stack, list) or not stack:
                    errors.append(f"楼层 {fno} ({x},{y}):格子栈格式非法")
                    continue
                for cell in stack:
                    if cell["kind"] not in kinds:
                        errors.append(f"楼层 {fno} ({x},{y}):未知格子类型 {cell['kind']}")
                    elif cell["kind"] == "monster" and cell.get("id") not in mon_ids:
                        errors.append(f"楼层 {fno} ({x},{y}):怪物 {cell.get('id')} 不在怪物表")
                    elif cell["kind"] == "prop" and cell.get("id") not in item_ids:
                        errors.append(f"楼层 {fno} ({x},{y}):道具 {cell.get('id')} 不在道具表")
                    elif cell["kind"] == "door" and cell.get("id") not in door_ids:
                        errors.append(f"楼层 {fno} ({x},{y}):门 {cell.get('id')} 不在门表")
        if not fl.get("stairs") and fno not in ("0", "50"):
            # 0=序章层(开场剧情)、50=结局层(打完魔王由事件传送进入)无楼梯是正常的;
            # 其余楼层必须有楼梯,否则说明转换出了问题
            errors.append(f"楼层 {fno}:一张楼梯都没有")
        for s in fl.get("stairs", []):
            if s["dir"] not in ("up", "down") or not (0 <= s["x"] < 11 and 0 <= s["y"] < 11):
                errors.append(f"楼层 {fno}:楼梯数据非法 {s}")
        for npc in fl.get("npcs", []):
            if npc.get("npc") not in {int(k) for k in data["npcs"]}:
                errors.append(f"楼层 {fno}:NPC {npc.get('npc')} 不在 NPC 表")
            elif not (0 <= npc["x"] < 11 and 0 <= npc["y"] < 11):
                errors.append(f"楼层 {fno}:NPC 坐标非法 {npc}")
        for trig in fl.get("triggers", []):
            if trig.get("event") not in event_ids:
                errors.append(f"楼层 {fno}:触发事件 {trig.get('event')} 不在事件表")
        for kt in fl.get("kill_triggers", []):
            if kt.get("event") not in event_ids:
                errors.append(f"楼层 {fno}:杀怪触发事件 {kt.get('event')} 不在事件表")
            for p in kt.get("kill", []) + kt.get("keep_alive", []):
                if not (0 <= p[0] < 11 and 0 <= p[1] < 11):
                    errors.append(f"楼层 {fno}:杀怪触发坐标非法 {p}")
        links = fl.get("stair_links") or {}
        for key in ("up_stand", "down_stand"):
            p = links.get(key)
            if p is not None and not (0 <= p[0] < 11 and 0 <= p[1] < 11):
                errors.append(f"楼层 {fno}:楼梯落点坐标非法 {p}")
        for gd in fl.get("guard_doors", []):
            for p in gd.get("doors", []) + gd.get("guards", []):
                if not (0 <= p[0] < 11 and 0 <= p[1] < 11):
                    errors.append(f"楼层 {fno}:守卫门坐标非法 {p}")
        for p in fl.get("first_attack", []):
            if not (0 <= p[0] < 11 and 0 <= p[1] < 11):
                errors.append(f"楼层 {fno}:先攻怪坐标非法 {p}")

    for mid, mon in data["monsters"].items():
        if mon.get("event") is not None and mon["event"] not in event_ids:
            errors.append(f"怪物 {mid} 挂的事件 {mon['event']} 不存在")

    for eid, ev in data["events"].items():
        if not ev.get("actions"):
            errors.append(f"事件 {eid}:物化后动作列表为空")
        for act in ev["actions"]:
            if not isinstance(act.get("type"), str):
                errors.append(f"事件 {eid}:动作缺少 type 字段 {act}")

    if errors:
        raise DataError(
            f"数据自洽性校验失败(共 {len(errors)} 处):\n  " + "\n  ".join(errors)
        )
