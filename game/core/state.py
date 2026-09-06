"""游戏状态的起点(开局数值)和存档三槽读写。

state 就是个普通字典(结构见设计文档 §4.4-A)——所以存档不需要任何
转换:整个字典原样 dump 成 JSON,读档原样读回来。坏了的档(手改坏、
断电写一半)读档时抛 SaveError,报错一定带文件名和原因,不许崩。
"""
import json
from pathlib import Path

# 存档目录:项目根下的 save/,里面放 save_1.json ~ save_3.json 三个槽
SAVE_DIR = Path(__file__).resolve().parents[2] / "save"
SAVE_VERSION = 1          # 存档格式版本号,将来结构大改时 +1 用来拒绝旧档
_SLOTS = (1, 2, 3)


class SaveError(Exception):
    """存档文件不存在/损坏/结构不对。报错信息带文件名和原因。"""


def new_state():
    """开局状态(序章):持神圣剑盾、1000/100/100 从第 1 层开打。

    原版开场就是"全副武装打教学战":走上 3 层踩格触发事件 1 被夺装备,
    变成 400/10/10 掉进 2 层监狱——那是剧情结果(事件层负责),不是开局。
    出生坐标 pos 固定 [5, 10](1 层的出生点)。
    """
    return {
        "floor": 1,
        "hero": {
            "hp": 1000, "attack": 100, "defence": 100, "gold": 0,
            "keys": {"yellow": 0, "blue": 0, "red": 0},
            "props": {},               # 道具id(str)→数量;工具恒 1,用完删键
            "sword": "12", "shield": "17",   # 神圣剑 +100 / 神圣盾 +100
            "pos": [5, 10],            # [x, y],x 向右 y 向下
        },
        "flags": {
            "altar_count": 0,          # 祭坛全局购买次数(价格公式见 pickups)
            "events_done": [],         # 已触发过的一次性事件 id
            "monsters_dead": [],       # "楼层:x,y" 记录,恢复楼层地图用
        },
        "floors_state": {},            # 每层相对初始数据的变化(引擎维护)
        "visited": [1],                # 到过的楼层(飞行魔杖的目标列表)
    }


def _slot_path(slot):
    """把槽位号(1/2/3)换算成存档文件路径,顺带检查槽位合法。"""
    if slot not in _SLOTS:
        raise ValueError(f"存档槽位只能是 1/2/3,收到的是:{slot!r}")
    return SAVE_DIR / f"save_{slot}.json"


def save_game(state, slot):
    """把整个 state 原样写进 save/save_N.json(外面包一层版本号)。"""
    path = _slot_path(slot)
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": SAVE_VERSION, "state": state}
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    except TypeError as exc:   # state 里混进了 JSON 认不出的东西
        raise SaveError(f"存档内容转不成 JSON:{path.name} —— {exc}") from exc
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise SaveError(f"写存档失败:{path.name} —— {exc}") from exc


def load_game(slot):
    """读 save/save_N.json 还原出 state 字典;文件坏/缺字段抛 SaveError。"""
    path = _slot_path(slot)
    if not path.exists():
        raise SaveError(f"存档文件不存在:{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SaveError(f"存档损坏(不是合法 JSON):{path} —— 原因:{exc}") from exc
    except OSError as exc:
        raise SaveError(f"存档读不出来:{path} —— 原因:{exc}") from exc

    if not isinstance(payload, dict):
        raise SaveError(f"存档内容不对(顶层应该是字典):{path}")
    if payload.get("version") != SAVE_VERSION:
        raise SaveError(
            f"存档版本不认识(要 {SAVE_VERSION},文件里是 "
            f"{payload.get('version')!r}):{path}")
    if "state" not in payload:
        raise SaveError(f"存档缺 state 字段:{path}")

    problems = _structure_problems(payload["state"])
    if problems:
        raise SaveError(
            f"存档结构不对,缺了关键字段:{path} —— " + "、".join(problems))
    return payload["state"]


def _structure_problems(state):
    """检查 state 必须有的字段在不在,把缺的收集成列表(一次报全,不挤牙膏)。"""
    problems = []
    if not isinstance(state, dict):
        return [f"state 顶层不是字典(是 {type(state).__name__})"]
    for key in ("floor", "hero", "flags", "floors_state", "visited"):
        if key not in state:
            problems.append(f"缺 state.{key}")
    hero = state.get("hero")
    if isinstance(hero, dict):
        for key in ("hp", "attack", "defence", "gold", "keys",
                    "props", "sword", "shield", "pos"):
            if key not in hero:
                problems.append(f"缺 hero.{key}")
        keys = hero.get("keys")
        if isinstance(keys, dict):
            for color in ("yellow", "blue", "red"):
                if color not in keys:
                    problems.append(f"缺 hero.keys.{color}")
    flags = state.get("flags")
    if isinstance(flags, dict):
        for key in ("altar_count", "events_done", "monsters_dead"):
            if key not in flags:
                problems.append(f"缺 flags.{key}")
    return problems
