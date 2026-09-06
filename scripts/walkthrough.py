#!/usr/bin/env python3
"""全塔无头模拟走查(batch 6 验收,设计文档 §6 batch 6 验证方式)。

从序章开局(1 层 (5,10))出发,沿楼梯逐层走到 50 层,再走完整条通关链
(26 层公主 → 24 层传送门 → 50 层小偷 → 真魔王现身),以"ending"收尾。

【真实】战斗/事件/门/机关全部走真实引擎+真实数据+真实规则层,一步一格
真实地走(BFS 找路,撞怪真打、撞门真开、事件真触发)。

【模拟声明(诚实边界)】
- 唯一作弊:序章被夺装备后,把勇士加成到 100000/2000/1000、钥匙 99×3——
  走查验的是【全塔流程连通性与机关正确性】,平衡性/数值手感归真机验证(§9);
- 44 层异空间原版只能用中心飞行器进,这里用传送(jump)模拟"用过飞行器"。

用法:env -u PYTHONPATH SDL_VIDEODRIVER=dummy .venv/bin/python scripts/walkthrough.py
退出码:0=通关;1=卡住(报告卡在哪层哪个格子)。
"""
import os
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

import game.main as main_mod  # noqa: E402
from game import core, loader  # noqa: E402
from game import events as events_mod  # noqa: E402
from game.engine import Engine  # noqa: E402

DATA = loader.load_all()
GRID = 11
KEY_DOORS = {1001, 1002, 1003}


class Blocked(Exception):
    """走查卡住:报清楚层/坐标/原因,别让人看 traceback。"""


class Walker:
    """无头走查驱动:一个 Engine + 找路 + 走路 + 挡路时想办法。"""

    def __init__(self, eng):
        self.eng = eng
        self.visited_floors = set()
        self.avoid = set()          # 走查指定的"不许顺路杀"的怪(49 层封印阵的对角警卫)

    # ---------- 基础 ----------

    def say(self, msg):
        print(f"  [F{self.eng.state['floor']:>2}] {msg}")

    def drain(self):
        """把正在演的对话一路回车到底(选择支一律选第 0 项)。"""
        for _ in range(300):
            if not self.eng.event_active:
                return
            self.eng.handle_key(pygame.K_RETURN)
        if self.eng.event_active:
            raise Blocked("对话推了 300 下还没演完,事件疑似死循环")

    def hero_alive(self):
        if self.eng.mode == "gameover":
            raise Blocked(f"勇士死了(HP<=0),损血过程:{self.eng.message}")

    # ---------- 找路 ----------

    def cell_passable(self, x, y, target=None):
        """BFS 用的"能不能走"判定:比引擎的 _blocked 更宽(撞怪=战斗、
        撞钥匙门=开门、撞非 passive 墙门=撞开);下楼梯格一律绕开
        (免得路径穿过去掉层);隐形墙/祭坛/不可战胜怪=墙。"""
        doc = self.eng.floor_doc()
        stack = doc["grid"][y][x]
        if not stack:
            return True
        saw_passable = False
        for cell in stack:
            if cell.get("appear"):
                continue                      # 隐形墙:撞了显形(先当墙)
            if cell.get("hide"):
                # 潜伏格(隐形怪/藏梯):引擎语义=完全不存在,不挡路不交互
                # (32 层事件 11 把 (10,0) 的隐形史莱姆挪到中轴 (5,9) 当魔王
                # 走位——上游 135 数据损坏,挪的是同格暗怪;引擎里它挡不了路,
                # BFS 也不许把它当墙,否则整层误判不通)
                if cell.get("kind") == "monster" and (x, y) in self.avoid:
                    return False               # 保护名单照样算数
                saw_passable = True
                continue
            kind = cell.get("kind")
            if kind in ("floor", "star", "prop"):
                saw_passable = True
                continue
            if kind == "stair":
                if cell.get("dir") == "down":
                    return False              # 别踩下楼梯(会掉层)
                saw_passable = True           # 上楼梯可以走上去
                continue
            if kind == "door":
                did = cell.get("id")
                if did in KEY_DOORS:
                    saw_passable = True       # 有钥匙,撞开
                elif did == 1006 and not cell.get("passive"):
                    saw_passable = True       # 墙门撞开
                else:
                    return False              # 1004/1005/passive:绕
                continue
            if kind == "monster":
                if (x, y) in self.avoid:
                    return False              # 走查保护名单(49 层 keep_alive 警卫:误杀=封印永久取消)
                mon = self.eng._monster(cell.get("id"))
                if not mon:
                    return False
                if mon.get("special", {}).get("unfightable"):
                    return False              # 巫师:正面打不了,绕
                result = self.eng.rules.calc_battle(
                    self.eng.state["hero"], mon, self.eng._battle_flags(x, y))
                if not result.get("can_fight"):
                    return False              # 打不过,绕
                saw_passable = True           # 撞上去真打,赢了顺势进格
                continue
            return False                      # wall/lava/altar/npc/big_part:绕
        return saw_passable

    def bfs_path(self, targets):
        """从勇士位置到 targets(坐标集合)的一条最短路;到不了返回 None。
        战斗格可以途经(bump 真打),所以邻格计算用 cell_passable。"""
        doc = self.eng.floor_doc()
        start = tuple(self.eng.state["hero"]["pos"])
        targets = set(map(tuple, targets))
        if start in targets:
            return [start]
        prev = {start: None}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            cx, cy = cur
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < GRID and 0 <= ny < GRID) or (nx, ny) in prev:
                    continue
                if (nx, ny) in targets or self.cell_passable(nx, ny):
                    prev[(nx, ny)] = cur
                    if (nx, ny) in targets:
                        path, node = [], (nx, ny)
                        while node:
                            path.append(node)
                            node = prev[node]
                        return path[::-1]
                    queue.append((nx, ny))
        return None

    # ---------- 走路 ----------

    def _rescue_guard_doors(self):
        """walk_to 到不了时的自救:本层 guard_doors(杀光一组守卫→门自动弹开,
        引擎机制)有活守卫且够得着 → 逐只杀光,门开了路就通了。
        典型:49 层进门(杀两巫师开 (5,8) → 进中笼杀两骑士开 (5,6) → 封印阵)。"""
        doc = self.eng.floor_doc()
        killed = False
        for gd in doc.get("guard_doors") or []:
            alive = [(p[0], p[1]) for p in gd["guards"]
                     if any(c.get("kind") == "monster"
                            for c in (doc["grid"][p[1]][p[0]] or []))]
            closed = [tuple(p) for p in gd["doors"]
                      if any(c.get("kind") == "door"
                             for c in (doc["grid"][p[1]][p[0]] or []))]
            if not alive or not closed:
                continue                       # 门已开 / 守卫早没了:这组不挡事
            for p in alive:
                if p in self.avoid:           # 保护名单(封印阵 keep_alive)不杀
                    continue
                bp = self.bfs_path([p])
                if bp and self.walk_to([p], f"守卫 {p}"):
                    self.drain()
                    killed = True
        return killed

    def walk_to(self, targets, what="目标", _depth=0):
        """走到 targets 里任意一格。撞门/撞怪的那一步不进格,循环重试即可。
        踩楼梯换层了也算"到了"(调用方检查层号)。"""
        # 入口兜底:上一场对话还开着手没喝干,先喝干再动——mode≠play 时
        # try_move 全部静默失败,所有路都会"走不到"(32 层魔王埋伏曾因此
        # 卡死:踩 (5,9) 开事件 11 对话,walk_to 停手返回,调用链没喝)
        if self.eng.mode != "play":
            self.drain()
        start_floor = self.eng.state["floor"]
        for _ in range(4):
            path = self.bfs_path(targets)
            if path is None:
                break                 # 到不了:先跳出循环试守卫门自救,别急着认输
            for i in range(1, len(path)):
                for _try in range(3):          # 撞门那一下不进格,再走一次就进
                    hx, hy = self.eng.state["hero"]["pos"]
                    dx, dy = path[i][0] - hx, path[i][1] - hy
                    if (dx, dy) == (0, 0):
                        break
                    self.eng.try_move(dx, dy)
                    if self.eng.event_active:
                        break                  # 撞出对话/踩格触发事件:停手,
                                                 # 对话留给调用方喝(29 层小偷/
                                                 # 26 层公主这类"说完办事"的
                                                 # NPC 靠它跑腿,不能在这喝干)
                    self.drain()
                    self.hero_alive()
                    if self.eng.mode == "ending":
                        return True
                    if self.eng.state["floor"] != start_floor:
                        return True             # 踩上楼梯换层了
                    if self.eng.state["hero"]["pos"] == list(path[i]):
                        break
                if self.eng.event_active:
                    break
            if tuple(self.eng.state["hero"]["pos"]) in set(map(tuple, targets)):
                return True
            # 撞 NPC/触发对话的场合:人不会走进那格,对话开起来就算到了
            if self.eng.event_active:
                hx, hy = self.eng.state["hero"]["pos"]
                if any(abs(t[0] - hx) + abs(t[1] - hy) == 1 for t in targets):
                    return True
        # 自救:守卫门锁着路?杀光够得着的守卫组(引擎自动弹门)再走一次。
        # 深度保险:守卫组之间也可能互相锁(49 层进门两连门),但连环四轮
        # 还没开路基本是杀不动了,认输报诊断,别无限递归
        if self._rescue_guard_doors():
            if _depth >= 4:
                self.say(f"  [诊断] 守卫门自救 {_depth} 轮还没开路,放弃 {what}")
                return False
            return self.walk_to(targets, what, _depth + 1)
        path = self.bfs_path(targets)
        self.say(f"  [诊断] 走不到 {what}:bfs={'不通' if path is None else path},"
                 f"当前 {self.eng.state['hero']['pos']}")
        return False

    # ---------- 卡住了怎么办 ----------

    def up_stairs(self):
        """本层【已显现】的上楼梯格。"""
        doc = self.eng.floor_doc()
        out = []
        for y, row in enumerate(doc["grid"]):
            for x, stack in enumerate(row):
                if stack and any(c.get("kind") == "stair" and c.get("dir") == "up"
                                 and not c.get("hide") for c in stack):
                    out.append((x, y))
        return out

    def winnable_monsters(self):
        """本层还能打赢的怪 ((x,y), 带事件接线?)——卡关时杀怪换线索
        (10 层骷髅队长→事件4 显隐藏梯、40 层骑士队长→事件18,优先杀这种)。"""
        doc = self.eng.floor_doc()
        hero = self.eng.state["hero"]
        out = []
        for y, row in enumerate(doc["grid"]):
            for x, stack in enumerate(row):
                for cell in (stack or []):
                    if cell.get("kind") != "monster" or cell.get("hide"):
                        continue
                    mon = self.eng._monster(cell.get("id"))
                    if not mon or mon.get("special", {}).get("unfightable"):
                        continue
                    r = self.eng.rules.calc_battle(
                        hero, mon, self.eng._battle_flags(x, y))
                    if r.get("can_fight"):
                        out.append(((x, y), bool(mon.get("event"))))
        return out

    def goto_floor_up(self):
        """走本层上楼梯换层;楼梯被藏住(事件没触发)就杀怪解锁,重试。"""
        fno = self.eng.state["floor"]
        links = self.eng.floor_doc().get("stair_links") or {}
        target_floor = fno + links.get("up_diff", 1)
        for attempt in range(10):
            # 顺路踩梯/事件跳层已经离开本层:这层算到过,交还主循环按新层重新走
            # (否则 target_floor 锁死,人上了 41 还会一路踩梯爬到 49 才回头)
            if self.eng.state["floor"] != fno:
                self.on_enter(self.eng.state["floor"])
                return
            stairs = self.up_stairs()
            if stairs:
                self.walk_to(stairs, f"上楼梯{stairs}")
                if self.eng.state["floor"] == target_floor:
                    self.on_enter(target_floor)
                    return
            # 楼梯还没显现(10/40 层:杀事件怪后事件 show):先试带事件接线的怪,
            # 够不到(被埋伏门锁着)就杀最近的怪换机关线索(10 层埋伏:杀光 8 只
            # 骷髅→门开→才够得着 Boss)
            mons = self.winnable_monsters()
            if mons:
                hero = tuple(self.eng.state["hero"]["pos"])
                dist = lambda p: abs(p[0] - hero[0]) + abs(p[1] - hero[1])
                cands = sorted((p for p, ev in mons if ev), key=dist) + \
                    sorted((p for p, ev in mons if not ev), key=dist)
                for near in cands[:3]:               # 试 3 个候选,别屠全层
                    if self.walk_to([near], f"挡路的怪{near}"):
                        self.drain()
                        break
                else:
                    near = None
                if near is not None:
                    continue                         # 杀掉一只,带着新局面重试
            else:
                near = None
            # 没怪可杀/杀不掉:楼梯格上下来回踩(事件 show 后就能上了)
            if not stairs:
                hidden = [(x, y) for y, row in enumerate(self.eng.floor_doc()["grid"])
                          for x, st in enumerate(row)
                          if st and any(c.get("kind") == "stair"
                                        and c.get("dir") == "up" for c in st)]
                if hidden and self.walk_to(hidden, "还藏着的上楼梯"):
                    self.say("楼梯还藏着,来回踩一次试试")
                    continue
            raise Blocked(f"第 {fno} 层卡住:找不到上楼梯的路(尝试 {attempt + 1})")

    def on_enter(self, floor):
        self.visited_floors.add(floor)
        self.say(f"到层 {floor},已走过 {len(self.visited_floors)} 层")


# ================================================================ 走查主体

def main():
    print("=" * 60)
    print("魔塔 50 层 全塔无头走查(真实引擎/数据/规则,仅数值加成)")
    print("=" * 60)
    rules = main_mod.build_rules_adapter(core)
    state = rules.new_state()
    eng = Engine(DATA, rules, None, state)
    eng.events = main_mod.build_events_adapter(events_mod, eng, DATA, rules)
    w = Walker(eng)
    w.on_enter(1)

    # ---- 楼梯爬塔:1 → 43 →(43 上梯 +2)→ 45 → … → 50 ----
    robbed = False
    while eng.state["floor"] < 50:
        fno = eng.state["floor"]
        if fno == 2 and tuple(state["hero"]["pos"]) == (2, 7) \
                and 1 not in state["flags"].get("npcs_done", []):
            # 序章越狱:隔壁牢房的仙子(npc1)挖好了暗道——对话后她拆掉
            # (1,6) 的 passive 墙门、自己走掉,勇士从暗道出狱(NpcFlow 小偷腿)
            if not w.walk_to([(2, 6)], "隔壁牢房的仙子(挖了暗道)"):
                raise Blocked("2 层监狱走不到仙子")
            w.drain()
            w.say(f"仙子开暗道离场,暗道已通:{state['flags'].get('npcs_done')}")
        if fno == 23:
            # 隐形墙迷宫:43 面隐形墙逐面撞显,全显 → 事件8(清 29 层小偷的
            # "还在挖"台词;不撞完,29 层小偷不开暗道,29 层就锁死)。
            # 上梯本身沿主开放带(x0/x5/x10 列、y1/y5 行)绕行,不走迷宫;
            # 撞墙用贪心:每轮撞一面"当前 BFS 够得着"的墙,不会把自己锁死。
            walls = set(map(tuple,
                            (eng.floor_doc().get("appear_event") or {}).get("positions") or []))
            w.say(f"隐形墙迷宫:撞 {len(walls)} 面(为触发事件8)")
            while walls:
                doc = eng.floor_doc()

                def open_adj(p):
                    """墙 p 的可站立邻格:用 BFS 通行判定(隐形墙/已显形墙/
                    真墙都不算,门和打得过的怪算)。"""
                    out = []
                    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                        qx, qy = p[0] + dx, p[1] + dy
                        if 0 <= qx < 11 and 0 <= qy < 11 and w.cell_passable(qx, qy):
                            out.append((qx, qy))
                    return out

                cands = []
                for p in walls:
                    st = doc["grid"][p[1]][p[0]] or []
                    if not any(c.get("appear") for c in st):
                        walls.discard(p)          # 已显形(理论上不会,撞完才出循环)
                        continue
                    adj = open_adj(p)
                    if adj and w.bfs_path(adj):   # 有够得着、走得进的相邻格
                        cands.append(p)
                if not cands:
                    raise Blocked(f"23 层迷宫剩下的墙够不着了:{sorted(walls)}")
                hero = tuple(eng.state["hero"]["pos"])
                target = min(cands, key=lambda q: abs(q[0] - hero[0]) + abs(q[1] - hero[1]))
                adj = open_adj(target)
                if not adj:
                    walls.discard(target)
                    continue
                if not w.walk_to(adj, f"隐形墙 @{target}"):
                    raise Blocked(f"23 层隐形墙 @{target} 走不过去")
                hx, hy = eng.state["hero"]["pos"]
                eng.try_move(target[0] - hx, target[1] - hy)   # 朝墙撞:显形
                w.drain()
                walls.discard(target)
            w.say(f"迷宫全显完成,挂起事件:{state['flags'].get('pending_events')}")
        if fno == 29 and 21 not in state["flags"].get("npcs_done", []):
            # 小偷剧情三段接力(原版数据链):29 层对话小偷21(开暗道+事件9挂
            # 起→2层)→ 飞回 2 层对话小偷22("我现在就去35楼",事件10挂起→35层)
            # → 35 层进层时事件10重演(小偷26 出现+拆魔龙前的暗道墙)。不走这
            # 趟,35 层上梯区(唯一入口 x=1 墙门竖廊)被 (3,8) 墙锁死。
            if not w.walk_to([(5, 1)], "29 层小偷(挖好暗道)"):
                raise Blocked("29 层走不到小偷")
            w.drain()
            w.say(f"小偷开了暗道 (5,2),事件9挂起:{state['flags'].get('pending_events')}")
            # 模拟原版玩家飞回 2 层赴约(飞行魔杖);进层钩子重演事件9(小偷22 出现)
            eng.api_jump_floor(2)
            w.on_enter(2)
            w.drain()
            # 2 层 (9,10) 小偷22 在 1005 卫兵门后:杀两只中级卫兵(埋伏在监牢
            # (5,1)(7,1)),门自动弹开(引擎 guard_doors),才够得着
            w.walk_to([(9, 10)], "2 层小偷22 (9,10)")
            w.drain()
            w.say(f"小偷22 说去 35 层,事件10挂起:{state['flags'].get('pending_events')}")
            eng.api_jump_floor(29)                 # 飞回 29 层继续爬塔
        if fno == 49:
            # 封印阵(原版机关):踩 (5,5) 触发事件20——8 只魔法警卫围住假魔王
            # 132。原版按序杀"十字位"4 警卫触发事件21(魔王弱化为 1/10),
            # 再杀假魔王触发事件22(宝库);对角的 4 只 keep_alive 警卫杀不得
            # (任一死=封印永久取消)。49 层没有上梯:50 层靠通关链,爬塔到此为止。
            w.avoid = {(4, 1), (6, 1), (4, 3), (6, 3)}   # 封印的对角警卫,保住
            if not w.walk_to([(5, 5)], "49 层封印阵 (5,5)"):
                raise Blocked("49 层走不到封印阵")
            w.drain()
            for guard in ((5, 1), (4, 2), (6, 2), (5, 3)):
                if not w.walk_to([guard], f"封印警卫 {guard}"):
                    raise Blocked(f"49 层杀不到警卫 {guard}")
                w.drain()
            if eng._weakened.get("132") != 0.1:
                raise Blocked(f"杀光 4 警卫后假魔王没弱化(封印没成:{eng._weakened})")
            w.say(f"封印成,假魔王弱化:{eng._weakened}")
            if not w.walk_to([(5, 2)], "假魔王 132"):
                raise Blocked("49 层打不到假魔王")
            w.drain()
            w.avoid = set()
            w.say("假魔王倒下,宝库事件触发(事件22)")
            # 进宝库捡 11 件宝物(原版通关链的补血环节,跳过它 50 层决斗
            # 必然打不过):事件22 在 8 个警卫环位摆下 3 红宝石+3 蓝宝石+
            # 3 蓝血瓶(回血 200×层) +骑士盾+神圣盾(后者免疫巫师魔伤)。
            # 摆位与事件22 appear 的 [flat, 道具id] 一一对应。
            hero = state["hero"]
            for flat, pid in ((34, 6), (35, 6), (36, 6), (40, 7), (41, 7),
                              (42, 7), (48, 5), (49, 5), (50, 5), (15, 15), (17, 17)):
                p = (flat % 11, flat // 11)
                if not w.walk_to([p], f"宝库宝物{pid}@{p}"):
                    raise Blocked(f"49 层捡不到宝物 {pid}@{p}")
                w.drain()
            w.say(f"宝库搬空:HP {hero['hp']},攻 {hero['attack']},防 {hero['defence']}")
            break            # 49 层无上梯:50 层走通关链(26 公主 → 24 传送门)
        if fno == 3 and not robbed:
            # 序章剧情:3 层 (4,8) 踩上去被魔王夺装备 → 400/10/10 → 传回 2 层监狱
            w.walk_to([(4, 8)], "3 层剧情格 (4,8)")
            w.drain()
            hero = state["hero"]
            assert (hero["hp"], hero["attack"], hero["defence"]) == (400, 10, 10), \
                f"序章被夺后数值不对:{(hero['hp'], hero['attack'], hero['defence'])}"
            w.say(f"序章被夺装备完成:400/10/10,落到 {eng.state['floor']} 层监狱")
            # 唯一作弊:数值加成(走查验流程,不验平衡)
            hero.update(hp=100000, attack=2000, defence=1000)
            hero["keys"] = {"yellow": 99, "blue": 99, "red": 99}
            w.say("走查数值加成:100000/2000/1000,钥匙 99×3")
            robbed = True
            continue        # 被夺后已身在 2 层监狱,重进循环走越狱流程
        if fno == 49:
            # 49 层封印阵:踩 (5,5) 触发事件 20(刷 8 警卫环 + 假魔王)
            w.walk_to([(5, 5)], "49 层封印阵 (5,5)")
            w.drain()
        w.goto_floor_up()
        if eng.state["floor"] == 45 and 44 not in w.visited_floors:
            # 44 层异空间:原版只能中心飞行器进,这里用传送模拟
            w.say("用中心飞行器进入 44 层异空间(模拟)")
            eng.api_jump_floor(44)
            w.on_enter(44)
            w.goto_floor_up()                  # 44 层上梯回 45

    # ---- 通关链:26 公主 → 24 传送门 → 50 小偷 → 真魔王 ----
    print("  ---- 通关链 ----")
    eng.api_jump_floor(26)
    w.on_enter(26)
    # 公主房被岩浆海围着,红门开到岩浆口后,要用冰冻魔法(道具 23,原版在
    # 44 层异空间所得,走查直接给——与"44 层传送模拟"同一份模拟声明)逐格
    # 冻开路:先走进两扇红门,再"走到岩浆格前→冻四邻→进一格"推进到公主面前
    state["hero"]["props"]["23"] = 1
    for gate in ((5, 9), (5, 8)):          # 两扇纯红门:撞开走进去
        if not w.walk_to([gate], f"26 层红门 {gate}"):
            raise Blocked(f"26 层开不进红门 {gate}")
    for lava_cell in ((5, 7), (5, 6)):     # 门叠岩浆/纯岩浆:先开门,再边冻边进
        w.walk_to([lava_cell], f"26 层岩浆口 {lava_cell}")   # 停在格子前
        r = eng.events.use_tool(23)
        w.say(f"冰冻魔法:{r.get('msg')}")
        if not w.walk_to([lava_cell], f"26 层 {lava_cell}"):
            raise Blocked(f"26 层冻不开 {lava_cell}")
    if not w.walk_to([(5, 5)], "26 层公主 (5,5)"):     # 撞上去=对话
        raise Blocked("26 层走不到公主")
    w.drain()
    w.say(f"公主对话完,挂起事件:{state['flags'].get('pending_events')}")

    eng.api_jump_floor(24)
    w.on_enter(24)
    w.drain()                                   # 进层钩子重演事件 26(放传送门触发器)
    w.walk_to([(5, 0)], "24 层传送门 (5,0)")
    w.drain()
    assert eng.state["floor"] == 50, f"踩传送门没到 50 层,却在 {eng.state['floor']}"
    w.say("传送门生效,直上 50 层")

    # 50 层:小偷(5,4)说真话 → 真魔王原地现身 → 决斗
    if not w.walk_to([(5, 4)], "50 层小偷 (5,4)"):     # 撞上去=对话
        raise Blocked("50 层走不到小偷")
    w.drain()
    grid = eng.floor_doc()["grid"]
    assert any(c.get("id") == 133 for c in (grid[4][5] or [])), "小偷说完话真魔王没现身"
    w.say("真魔王现身,决斗!")
    if not w.walk_to([(5, 4)], "真魔王"):             # 撞上去=决斗
        raise Blocked("50 层决斗走不过去")
    w.drain()

    if eng.mode != "ending":
        raise Blocked(f"打完真魔王没进通关画面(mode={eng.mode})")

    hero = state["hero"]
    print("=" * 60)
    print(f"✅ 全塔走查通关!50 层真魔王被击败")
    print(f"   走过的楼层:{sorted(w.visited_floors)}")
    print(f"   终局数值:HP {hero['hp']},攻 {hero['attack']},防 {hero['defence']},"
          f"金币 {hero['gold']},黄/蓝/红钥匙 {hero['keys']}")
    print(f"   触发过的事件:{len(state['flags'].get('events_done', []))} 个,"
          f"杀过的怪:{len(state['flags'].get('monsters_dead', []))} 只")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Blocked as exc:
        print(f"\n❌ 走查卡住:{exc}")
        sys.exit(1)
