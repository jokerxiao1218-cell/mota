#!/usr/bin/env python3
"""一次性数据转换脚本:reference/tacthgin(MIT 协议)→ game/data/ 的 JSON。

- 数值校正依据设计文档附录 A(原版 TSW.exe 提取的权威表;tacthgin 存在 3 处金币抄录错误)
- 格子为【叠放栈】:同一格可叠多层(墙门压着道具="十字架藏墙内"、楼梯上站怪挡路、
  hide 属性隐藏整格),按 TMX 文件内图层顺序自下而上叠放,层名/层id一并保留
- 层属性(properties)语义物化(v5,上游 ParserFactory/DoorSystem/MoveSystem 考据):
  * npc 层 {位置:NPC id} = NPC 摆放表;event 层 {位置:事件 id} = 踩格触发器
  * hide(各层)= 该层该格"幽灵化":不可见、可穿越、不可交互,等事件 show 显现
  * appear/appearEvent(门层)= 隐形但【挡路】的门:撞一下显形,显形后永远是墙
    (f23 隐形墙迷宫 / f33 暗墙);appearEvent 另记楼层字段:全撞现→触发事件
  * passive(门层)= 撞不开的墙门:等小偷挖/杀怪解锁/守卫门(解除途径见语义报告)
  * monsterCondition(门层)→ 楼层字段 door_unlocks:杀掉某格的怪 → 对应门解锁
  * wallShow(门层)→ 楼层字段 wall_shows:撞开假墙 → 显现指定格藏着的元素
  * monsterMove(怪层)= 格子标记 monster_move:吃魔伤时该怪镜像瞬移(f47 巫师)
  * disappearEvent(门层)→ 楼层字段:开钥匙门记账,开错=永久作废,开齐=触发事件
- 事件按 step 物化为顺序动作列表,原样携带数据
- 重跑安全:输出每次整体重建

用法:.venv/bin/python scripts/convert_tacthgin.py
"""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "reference" / "tacthgin"
OUT = ROOT / "game" / "data"
FLOORS_OUT = OUT / "floors"

# ---- 权威金币校正(附录 A:tacthgin 抄录错误 3 处)----
GOLD_FIX = {110: 22, 111: 18, 120: 100}  # 高级法师 / 兽人 / 中级卫兵

# ---- 十字架 / 屠龙匕 生效对象(附录 A/B,原版对白确认)----
CROSS_TARGETS = [111, 112, 115]          # 兽人 / 兽人武士 / 吸血鬼
DRAGON_TARGETS = [122]                   # 魔龙

# ---- 事件触发表(踩格触发已在楼层 event 层属性接线;进层/杀怪类见楼层字段)----
TRIGGERS = {}
# ---- 楼层编号映射(tmx 编号 = 游戏楼层号,f0=序章层;考古终审确认)----
FLOOR_NO = {i: i for i in range(1, 51)} | {0: 0}

# ---- NPC 摆放勘误(语义报告:f39 商人 30 的属性位 (0,10) 与贴图位 (8,1) 错位;
#      tacthgin 里贴图格可穿行、属性格隐形可达,两头都不对——按贴图位摆,最贴原版)----
NPC_FIX = {39: {30: (8, 1)}}


def _load(name):
    return json.loads((REF / "Json" / name).read_text(encoding="utf-8"))


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# ============================================================
# 1. 图块集:gid → 语义(图片名即语义;tmx 可引用 element.tsx + big.tsx 多个图块集)
# ============================================================
def parse_all_tilesets():
    gid_map, seen = {}, set()
    for tmx in sorted(REF.glob("TiledMap/*.tmx"), key=lambda p: int(p.stem)):
        root = ET.parse(tmx).getroot()
        for ts in root.findall("tileset"):
            firstgid, src = int(ts.get("firstgid")), ts.get("source")
            if (firstgid, src) in seen:
                continue
            seen.add((firstgid, src))
            troot = ET.parse(REF / "TiledMap" / Path(src).name).getroot()
            size = int(troot.get("tilewidth")) // 32  # 32=普通格;96=3×3 大怪
            for tile in troot.findall("tile"):
                gid = firstgid + int(tile.get("id"))
                name = tile.find("image").get("source").split("/")[-1][: -len(".png")]
                info = classify_tile(name) | {"sprite": name}
                if size > 1:
                    info["size"] = size          # 3×3 大怪:大乌贼 114 / 魔龙 122
                gid_map[gid] = info
    return gid_map


def classify_tile(name):
    if name == "floor":
        return {"kind": "floor"}
    if name.startswith("stair_down"):
        return {"kind": "stair", "dir": "down"}
    if name.startswith("stair_up"):
        return {"kind": "stair", "dir": "up"}
    if name.startswith("fire"):
        return {"kind": "lava"}
    if name.startswith("star"):
        return {"kind": "star"}
    if name.startswith("door"):
        return {"kind": "door", "id": int(name[4:8])}
    if name.startswith("prop"):
        return {"kind": "prop", "id": int(name[4:])}
    if name.startswith("npc"):
        return {"kind": "npc", "sprite_id": int(name[3])}
    if name.startswith("pb_"):
        return {"kind": "big_part", "part": name}
    m = re.fullmatch(r"(\d+)_(\d+)", name)
    if m:
        return {"kind": "monster", "id": int(m.group(1))}
    return {"kind": "unknown", "raw": name}


# ============================================================
# 2. 怪物表(权威金币校正 + 特殊能力命名字段,设计文档 §4.2 选择5)
# ============================================================
def convert_monsters():
    src = _load("monster.json")
    out = {}
    for k, mon in src.items():
        mid = int(k)
        special = {}
        magic = mon.get("magicAttack")
        if magic == 0.5:
            special["halve_hp_trap"] = True          # 魔法警卫:两警卫相隔2格夹击,HP=ceil(HP/2)
        elif isinstance(magic, (int, float)):
            special["adjacent_damage"] = int(magic)  # 巫师:相邻格固定魔伤(可叠加)
            special["unfightable"] = True            # TODO(语义报告):确认是否绝对不可正面战斗
        if mon.get("big"):
            special["big"] = mon["big"]              # 3×3 大怪格偏移(元素格=贴图格+1行,考古终审)
        if mon.get("extraDamage") is not None:
            # 考古终审:extraDamage 实为【克制道具 id】(28=十字架,29=屠龙匕),不是伤害数值
            special["counter_item"] = mon["extraDamage"]
        out[k] = {
            "id": mid, "name": mon["name"],
            "hp": mon["hp"], "attack": mon["attack"], "defence": mon["defence"],
            "gold": GOLD_FIX.get(mid, mon["gold"]),
            "boss": mon["boss"], "event": mon.get("eventId"),
            "special": special,
        }
    return out


# ============================================================
# 3. 道具表(效果参数进数据;行为在代码。附录 B)
# ============================================================
TOOL_KINDS = {
    7: "monster_manual", 8: "notebook", 9: "fly_wand", 10: "pickaxe",
    11: "quake_scroll", 12: "ice_magic", 13: "bomb", 14: "magic_key",
    15: "holy_water", 16: "lucky_coin", 17: "cross", 18: "dragon_slayer",
    19: "wing", 20: "center_wing",
}
UNLIMITED_TOOLS = {"fly_wand", "ice_magic"}  # 附录 B:不限次数


def convert_items():
    src = _load("prop.json")
    out = {}
    for k, p in src.items():
        pid, typ, val = int(k), p["type"], p["value"]
        ent = {"id": pid, "name": p["name"], "desc": p["desc"], "type": typ}
        if typ == 1:
            ent.update(kind="key", door=val)
        elif typ == 2:
            ent.update(kind="potion", hp=val, area_scale=True)
        elif typ == 3:
            ent.update(kind="gem", attack=val, area_scale=True)
        elif typ == 4:
            ent.update(kind="gem", defence=val, area_scale=True)
        elif typ == 5:
            ent.update(kind="sword", attack=val)
        elif typ == 6:
            ent.update(kind="shield", defence=val)
        else:
            tool = TOOL_KINDS[typ]
            ent.update(kind="tool", tool=tool,
                       uses=(None if tool in UNLIMITED_TOOLS else p.get("initNum", 1)))
            if tool == "lucky_coin":
                ent.update(gold_multiplier=val)
            elif tool == "cross":
                ent.update(attack_multiplier=val, affects=CROSS_TARGETS)
            elif tool == "dragon_slayer":
                ent.update(attack_multiplier=val, affects=DRAGON_TARGETS)
            elif tool == "wing":
                ent.update(floor_delta=val)          # +1 上飞行器 / -1 下飞行器
        out[k] = ent
    return out


# ============================================================
# 4. 门表 + 图块登记(tiles.json)
# ============================================================
DOOR_KEYS = {1001: 1, 1002: 2, 1003: 3}        # 黄/蓝/红门 ← 钥匙道具 id
DOOR_OPEN = {1004: "event", 1005: "kill_guards", 1006: "bump"}
# 考古终审:1004 事件生成的监狱门;1005 静态杀守卫门;1006 墙门(直接撞开)

KINDS = {
    "floor":     {"walkable": True},
    "wall":      {"walkable": False},
    "lava":      {"walkable": False, "freezable": True},
    "door":      {"walkable": False},
    "stair":     {"walkable": True},
    "monster":   {"walkable": False},
    "prop":      {"walkable": True},    # 撞上拾取后可通过
    "npc":       {"walkable": False},
    "star":      {"walkable": True},
    "altar":     {"walkable": False},   # 祭坛:撞上开商店(4/12/32/46 层)
    "big_part":  {"walkable": False},
    "unknown":   {"walkable": False},   # 未解码内容:先按不可通行,转换时打印
}


def convert_doors():
    src = _load("door.json")
    out = {}
    for k, d in src.items():
        did = int(k)
        ent = {"id": did, "name": d["desc"], "sprite": d["spriteId"]}
        if did in DOOR_KEYS:
            ent["opens_with_key"] = DOOR_KEYS[did]
        else:
            ent["opens_by"] = DOOR_OPEN[did]
        out[k] = ent
    return out


# ============================================================
# 5. NPC 表(透传;wall/move 字段语义待语义报告,原样保留)
# ============================================================
NPC_TYPES = {1: "thief", 2: "elder", 3: "merchant", 4: "fairy", 5: "princess"}


def convert_npcs():
    src = _load("npc.json")
    out = {}
    for k, n in src.items():
        out[k] = {
            "id": int(k), "type": NPC_TYPES.get(n["type"], f"type{n['type']}"),
            "desc": n["desc"], "value": n["value"],
            "wall": n.get("wall"), "move": n.get("move"),
            "talk": n["talk"], "event_talk": n.get("eventTalk"),
            "event": n.get("event"), "unlimit": bool(n.get("unlimit", False)),
        }
    return out


# ============================================================
# 6. 事件表:step 脚本 → 顺序动作列表(机械物化;解释在引擎)
# ============================================================
def convert_events():
    src = _load("event.json")
    out = {}
    for k, e in src.items():
        used, actions = set(), []
        for step in e.get("step") or []:
            if step is None:
                continue
            used.add(step)
            actions.append({"type": step, "data": e.get(step)})
        meta = {f: e[f] for f in e
                if f not in used and f not in ("id", "step") and e[f] is not None}
        out[k] = {"id": int(k), "trigger": TRIGGERS.get(int(k)),
                  "actions": actions, "meta": meta}
    # 考古终审勘误:事件4 的 show 15 是 115 的笔误(10 层隐藏上行楼梯@115;
    # 原值导致"show not exist"报错、楼梯永远显不出来)——转换时直接修正
    for act in out.get("4", {}).get("actions", []):
        if act["type"] == "show" and act["data"] == 15:
            act["data"] = 115
    return out


# ============================================================
# 7. 楼层:TMX 图层 → 11×11 叠放栈网格
#    同格多层是原版机制(墙门压道具 / 楼梯上站怪 / hide 隐藏整格),
#    按 TMX 文件内图层出现顺序自下而上叠,保留层名与层id。
# ============================================================
def cell_from(layer_name, gid, gid_map):
    info = gid_map[gid]
    sprite = info["sprite"]
    if layer_name == "wall":
        if info["kind"] == "lava":
            return {"kind": "lava", "sprite": sprite}
        return {"kind": "wall", "sprite": sprite}   # 含 pb_l/pb_r 祭坛两侧装饰墙
    if layer_name == "building":
        if info["kind"] == "big_part":
            return {"kind": "altar", "sprite": sprite}  # 祭坛(pb_m 贴图,撞上开商店)
        return {"kind": info["kind"], "sprite": sprite}
    if layer_name == "door":
        return {"kind": "door", "id": info.get("id"), "sprite": sprite}
    if layer_name == "stair":
        return {"kind": "stair", "dir": info["dir"], "sprite": sprite}
    if layer_name == "monster":
        cell = {"kind": info["kind"], "sprite": sprite}
        for f in ("id", "size"):
            if f in info:
                cell[f] = info[f]
        if info["kind"] == "big_part":
            cell["part"] = info["part"]
        return cell
    if layer_name == "prop":
        return {"kind": "prop", "id": info["id"], "sprite": sprite}
    # npc / location / 未知层:按图块语义兜底
    if info["kind"] == "floor":
        return None
    cell = {"kind": info["kind"], "sprite": sprite}
    for f in ("id", "dir", "sprite_id", "size", "part"):
        if f in info:
            cell[f] = info[f]
    return cell


def _pos_to_xy(p):
    p = int(p)
    return {"x": p % 11, "y": p // 11}


def _flat_pos(p):
    """一维展平索引 -> [x, y](x=p%11 向右,y=p//11 向下)"""
    p = int(p)
    return [p % 11, p // 11]


def _flat_list(s):
    """'12,34,56' -> [[x,y], ...]"""
    return [_flat_pos(p) for p in str(s).split(",") if p.strip()]


def convert_floor(tmx_path, gid_map):
    idx = int(tmx_path.stem)                 # tmx 编号 = 楼层号(考古终审确认)
    root = ET.parse(tmx_path).getroot()
    layers, layer_props, layer_ids = {}, {}, {}
    for layer in root.findall("layer"):
        name = layer.get("name")
        csv = layer.find("data").text
        vals = [int(v) for v in csv.replace("\n", "").replace(" ", "").strip().rstrip(",").split(",")]
        if len(vals) != 121:
            raise ValueError(f"{tmx_path.name} 图层 {name} {len(vals)} 格 ≠ 121")
        layers[name] = vals
        layer_ids[name] = int(layer.get("id"))
        props = layer.find("properties")
        if props is not None:
            layer_props[name] = {p.get("name"): p.get("value")
                                 for p in props.findall("property")}
    grid = [[None] * 11 for _ in range(11)]
    stack_count = 0
    for name, vals in layers.items():          # 按文件内图层顺序:先出现的在下层
        for pos, gid in enumerate(vals):       # pos=展平坐标(别叫 idx:会遮蔽楼层号)
            if not gid:
                continue
            cell = cell_from(name, gid, gid_map)
            if cell is None:
                continue
            x, y = pos % 11, pos // 11
            item = {"layer": name, "layer_id": layer_ids[name], **cell}
            if grid[y][x] is None:
                grid[y][x] = [item]
            else:
                grid[y][x].append(item)
                stack_count += 1
    stairs = []
    for y, row in enumerate(grid):
        for x, stack in enumerate(row):
            if stack:
                for it in stack:
                    if it["kind"] == "stair":
                        stairs.append({"dir": it["dir"], "x": x, "y": y})
    # ---- NPC 摆放:npc 层属性 {位置: NPC id}(f39 商人错位勘误见 NPC_FIX) ----
    npcs = []
    for k, v in layer_props.get("npc", {}).items():
        if k.isdigit():
            entry = {"npc": int(v), **_pos_to_xy(k)}
            fix = NPC_FIX.get(idx).get(entry["npc"]) if idx in NPC_FIX else None
            if fix:
                entry["x"], entry["y"] = fix
                entry["fix"] = "tacthgin 属性位与贴图位错位,按贴图位摆放"
            npcs.append(entry)
    # ---- 踩格触发:event 层属性 {位置: 事件id} ----
    triggers = []
    for k, v in layer_props.get("event", {}).items():
        if k.isdigit():
            triggers.append({"x": int(k) % 11, "y": int(k) // 11, "event": int(v)})
    # ---- 楼梯落点:stair 层 location = [上行站位, 下行站位, 上行层差(默认+1), 下行层差(默认-1)];
    #      踩梯→新层=当前层+diff;上楼落到【新层 down_stand】、下楼落到【新层 up_stand】(对面梯站位) ----
    stair_links = {}
    loc = layer_props.get("stair", {}).get("location")
    if loc:
        parts = [int(v) for v in loc.split(",")]
        # '0' 是"无此落点"的占位(f1 无下行梯),归一成 None,免得 (0,0) 假落点
        stair_links = {   # f0 地下室只有上行梯,location 仅 1 值
            "up_stand": _flat_pos(parts[0]) if parts[0] else None,
            "down_stand": _flat_pos(parts[1]) if len(parts) > 1 and parts[1] else None,
            "up_diff": parts[2] if len(parts) > 2 else 1,
            "down_diff": parts[3] if len(parts) > 3 else -1,
        }
    # ---- 杀守卫开门:door 层属性 {'门位置列表': '守卫位置列表'}(杀光守卫→门全开) ----
    guard_doors = []
    door_positions = []                     # 本层门格位置(appearEvent 要用)
    for y, row in enumerate(grid):
        for x, st in enumerate(row):
            if st and any(c.get("layer") == "door" for c in st):
                door_positions.append([x, y])
    for k, v in layer_props.get("door", {}).items():
        if k.replace(",", "").isdigit():
            guard_doors.append({"doors": _flat_list(k), "guards": _flat_list(v)})
    # ---- 杀怪触发:monster 层属性 monsterEvent="事件:须杀光位置" /
    #      disappearMonsterEvent="事件:须杀光位置:须存活位置"(先杀存活列表任一→永久取消) ----
    kill_triggers = []
    mprops = layer_props.get("monster", {})
    if mprops.get("monsterEvent"):
        ev, positions = mprops["monsterEvent"].split(":", 1)
        kill_triggers.append({"event": int(ev), "kill": _flat_list(positions)})
    if mprops.get("disappearMonsterEvent"):
        ev, rest = mprops["disappearMonsterEvent"].split(":", 1)
        kill_s, keep_s = rest.split(":", 1)
        kill_triggers.append({"event": int(ev), "kill": _flat_list(kill_s),
                              "keep_alive": _flat_list(keep_s)})
    # ---- 先攻怪位置(40 层 12 只;2026-09-06 用户拍板启用,引擎按此表传 flags)----
    first_attack = _flat_list(mprops["firstAttack"]) if mprops.get("firstAttack") else []

    # ---- 机关语义物化(v5,语义侦察报告)----
    dprops = layer_props.get("door", {})

    def mark(layer, pos, **flags):
        """给 (层, 展平位置) 定位的格子打标记;该层该格没格子就不打(数据残留无害)。"""
        x, y = int(pos) % 11, int(pos) // 11
        for cell in (grid[y][x] or []):
            if cell.get("layer") == layer:
                cell.update(flags)

    # hide(各层):该格"幽灵化"——不可见/可穿越/不可交互,等事件 show 显现
    # (f10 隐藏上梯、f32 上梯的隐形史莱姆、f33 下梯上的隐形黄门、f41 墙后巫师……)
    for lname, lprops in layer_props.items():
        if lprops.get("hide"):
            mark(lname, lprops["hide"], hide=True)
    # passive(门层):撞不开的墙门,等小偷挖/杀怪解锁/守卫门
    if dprops.get("passive"):
        mark("door", dprops["passive"], passive=True)
    # appear(门层,单坐标):隐形但挡路,撞一下显形,显形后永远是墙(f33 暗墙)
    if dprops.get("appear"):
        mark("door", dprops["appear"], appear=True)
    # appearEvent(门层,事件id):本层【全部门】隐形挡路,全撞现 → 触发事件(f23 迷宫→事件8)
    appear_event = None
    if dprops.get("appearEvent"):
        appear_event = {"event": int(dprops["appearEvent"]), "positions": door_positions}
        for p in door_positions:
            mark("door", p[1] * 11 + p[0], appear=True)
    # disappearEvent(门层,'取消位…:事件:完成位…'):开钥匙门记账,
    # 开到取消位=永久作废,开齐完成位=触发事件(f39 对称黄门机关)
    disappear_event = None
    if dprops.get("disappearEvent"):
        cancel_s, ev_s, done_s = dprops["disappearEvent"].split(":")
        disappear_event = {"event": int(ev_s), "cancel": _flat_list(cancel_s),
                            "complete": _flat_list(done_s)}
    # monsterCondition(门层,'门位:怪位'):杀掉怪位的怪 → 门位的 passive 门解锁
    door_unlocks = []
    if dprops.get("monsterCondition"):
        door_p, kill_p = dprops["monsterCondition"].split(":")
        door_unlocks.append({"door": _flat_pos(door_p), "kill": _flat_pos(kill_p)})
    # wallShow(门层,位):撞开 NORMAL 假墙后,显现该位藏着的元素(f41 连锁)
    wall_shows = _flat_list(dprops["wallShow"]) if dprops.get("wallShow") else []
    # monsterMove(怪层,位列表):吃魔伤时该怪镜像瞬移到以勇士为中心的对称格(f47 巫师)
    if mprops.get("monsterMove"):
        for p in str(mprops["monsterMove"]).split(","):
            mark("monster", p, monster_move=True)

    wiring = {
        "stair_links": stair_links,
        "guard_doors": guard_doors,
        "kill_triggers": kill_triggers,
        "first_attack": first_attack,
        "appear_event": appear_event,
        "disappear_event": disappear_event,
        "door_unlocks": door_unlocks,
        "wall_shows": wall_shows,
    }
    return grid, stairs, npcs, triggers, layer_props, stack_count, wiring


def convert_floors(gid_map):
    for old in FLOORS_OUT.glob("*.json"):
        old.unlink()
    summary, total_stacks, unknowns = [], 0, []
    for tmx in sorted(REF.glob("TiledMap/*.tmx"), key=lambda p: int(p.stem)):
        grid, stairs, npcs, triggers, layer_props, stacks, wiring = convert_floor(tmx, gid_map)
        idx = int(tmx.stem)
        doc = {
            "floor": FLOOR_NO.get(idx),          # tmx 编号=楼层号(考古终审确认)
            "tmx": tmx.name,
            "grid": grid,                          # 每格 null 或叠放栈(自下而上)
            "stairs": stairs,
            "stair_links": wiring["stair_links"],  # 楼梯落点与层差(43层上梯+2/45层下梯-2)
            "npcs": npcs,                          # 由 npc 层属性机械接线(含 f39 勘误)
            "triggers": triggers,                  # 踩格触发(由 event 层属性接线)
            "guard_doors": wiring["guard_doors"],  # 杀光守卫→门开(不限门 id)
            "kill_triggers": wiring["kill_triggers"],  # 杀光指定怪→触发事件(含49层封印阵)
            "first_attack": wiring["first_attack"],   # 先攻怪位置(40层,已启用)
            "appear_event": wiring["appear_event"],   # f23 隐形墙迷宫:全撞现→触发事件
            "disappear_event": wiring["disappear_event"],  # f39 开钥匙门记账机关
            "door_unlocks": wiring["door_unlocks"],   # 杀怪解锁 passive 门(f41)
            "wall_shows": wiring["wall_shows"],       # 撞开假墙→显现藏着的元素(f41)
            "layer_props": layer_props,            # 其余原始属性(备查)
        }
        _write(FLOORS_OUT / f"f{idx}.json", doc)
        total_stacks += stacks
        for row in grid:
            for st in row:
                if st:
                    unknowns += [f"{tmx.name} {it['sprite']}" for it in st if it["kind"] == "unknown"]
        mons = sorted({it["id"] for row in grid for st in row if st
                       for it in st if it["kind"] == "monster"})
        summary.append(f"f{idx}(第{FLOOR_NO.get(idx)}层): 怪{len(mons)}只{mons[:8]}"
                       f"{'…' if len(mons) > 8 else ''} NPC{len(npcs)} 触发{len(triggers)}"
                       f" 梯{[(s['dir'], s['x'], s['y']) for s in stairs]}")
    print("\n".join(summary))
    print(f"\n叠放格(同格多层,如墙门下藏道具):{total_stacks} 处"
          f" | 未解码图块:{len(unknowns)} 处{unknowns[:10] if unknowns else ''}")


def main():
    gid_map = parse_all_tilesets()
    _write(OUT / "monsters.json", convert_monsters())
    _write(OUT / "items.json", convert_items())
    doors = convert_doors()
    _write(OUT / "tiles.json", {"kinds": KINDS, "doors": doors})
    _write(OUT / "npcs.json", convert_npcs())
    _write(OUT / "events.json", convert_events())
    print(f"怪物 {len(_load('monster.json'))} | 道具 {len(_load('prop.json'))} | "
          f"门 {len(doors)} | NPC {len(_load('npc.json'))} | 事件 {len(_load('event.json'))}")
    convert_floors(gid_map)
    print("\n转换完成 → game/data/(door/stair/hide 等层属性语义待语义报告接线)")


if __name__ == "__main__":
    main()
