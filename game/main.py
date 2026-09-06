"""游戏入口:把 数据层(loader)→ 规则层(core)→ 事件层(events)→ 引擎 拼起来。

core / events 两个模块已交付(batch 2/4),本文件的活:
1) RealRules —— 把 game.core 的纯函数包成引擎要的 RulesAdapter(§4.4-B);
2) RealEvents —— 把 game.events 的四个组件(EventRunner/NpcFlow/AltarFlow/道具)
   包成引擎要的 EventsAdapter(§4.4-C + batch 6 扩展);
3) 开局状态接线 + 启动主循环。

用法:./run.sh(或 .venv/bin/python game/main.py)
"""

import sys
from pathlib import Path

# 允许两种跑法:python -m game.main,以及 run.sh 的 python game/main.py。
# 后者 sys.path 里只有 game/ 这一层目录,不补仓库根目录就 import 不到 game 包。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _say(msg):
    """把启动失败原因讲给玩家听,别让人看 traceback。"""
    print("=" * 46)
    print(msg)
    print("=" * 46)


def build_rules_adapter(core):
    """把 game.core 的函数包成引擎要的 RulesAdapter(§4.4-B 签名,全量)。

    名字对不上时逐个点名提示,方便一眼看出缺哪个。
    """
    from game.engine import RulesAdapter

    class RealRules(RulesAdapter):
        def new_state(self):
            return core.new_state()

        def calc_battle(self, hero, monster, flags):
            return core.calc_battle(hero, monster, flags)

        def apply_pickup(self, state, item_id, data):
            # data 是 loader.load_all() 的大字典(core 内部查 data["items"])
            return core.apply_pickup(state, item_id, data)

        def area(self, floor):
            return core.area(floor)

        def altar_price(self, n):
            return core.altar_price(n)

        def altar_buy(self, state, choice):
            return core.altar_buy(state, choice)

        def adjacent_damage(self, state, floor_doc, x, y):
            return core.adjacent_damage(state, floor_doc, x, y)

        def guard_trap(self, state, floor_doc, x, y):
            return core.guard_trap(state, floor_doc, x, y)

        def save_game(self, state, slot):
            return core.save_game(state, slot)

        def load_game(self, slot):
            return core.load_game(slot)

    missing = [name for name in (
        "new_state", "calc_battle", "apply_pickup", "area", "altar_price",
        "altar_buy", "adjacent_damage", "guard_trap", "save_game", "load_game")
        if not hasattr(core, name)]
    if missing:
        _say(f"[启动失败] 规则层 game.core 缺这些函数:{', '.join(missing)}\n"
             f"        (按设计文档 §4.4-B,它们应该能从 game.core 包直接导入)")
        return None
    return RealRules()


def build_events_adapter(events_mod, engine, data, rules):
    """把 game.events 包成引擎要的 EventsAdapter(§4.4-C + batch 6 扩展)。

    引擎只认 intent 协议(chat/choices/done);这里负责把 events 层的
    EventRunner / NpcFlow / AltarFlow / FlyWandFlow 都说成同一种"正在演的戏":
    - sound / effect 是纯演出(没做音效系统),自动跳过;
    - pending(事件存档层≠当前层)登记到 flags,进层时重演;
    - done 带 remove_npc 的(一次性 NPC),顺手把它从地图上拿掉。
    """
    from game.engine import EventsAdapter

    if not hasattr(events_mod, "EventRunner"):
        _say("[启动失败] 事件层 game.events 还没有 EventRunner 类\n"
             "        (按设计文档 §4.4-C,应该是事件层交付物)")
        return None

    class RealEvents(EventsAdapter):
        def __init__(self):
            self.engine = engine                  # 事件/道具要拿"当前"状态,
            self.rules = rules                    # 读档后 state 会整个换掉,
            self.api = engine.build_api()         # 所以都从 engine 取,不缓存
            self.runner = None                    # 当前在演的 flow(EventRunner/NpcFlow/AltarFlow/…)
            self._npc_pos = None                  # talk 时的位置(remove_npc 用)

        # ---------- 引擎侧调用 ----------

        def talk(self, npc, state, npc_pos=None):
            self._npc_pos = npc_pos
            self.runner = events_mod.NpcFlow(
                npc["id"], state, data, self.api, rules=self.rules,
                npc_pos=npc_pos)

        def altar_flow(self, state):
            self._npc_pos = None
            self.runner = events_mod.AltarFlow(state, data, self.rules)

        def start(self, event, state):
            self._npc_pos = None
            self.runner = events_mod.EventRunner(
                event, state, self.api, rules=self.rules, data=data)

        def usable_tools(self, state):
            out = []
            items = data.get("items") or {}
            for pid in (state.get("hero", {}).get("props") or {}):
                item = items.get(str(pid)) or {}
                if item.get("tool") in events_mod.TOOL_NAMES:
                    out.append((int(pid), item.get("name", f"道具{pid}")))
            return out

        def use_tool(self, item_id, direction=None):
            state = self.engine.state
            item = (data.get("items") or {}).get(str(item_id)) or {}
            if item.get("tool") == "fly_wand":
                # 附录 B:飞行魔杖要站在楼梯旁边才能用(引擎层校验,
                # 因为只有它知道楼梯在哪;不满足就说清楚,不静默)
                if not self._near_stair(state):
                    return {"ok": False, "msg": "飞行魔杖要站在楼梯旁边才能用"}
                self.runner = events_mod.FlyWandFlow(state, self.api, data)
                return {"ok": True, "flow": True}
            out = events_mod.use_tool(state, data, self.api, item_id,
                                      direction=direction)
            if hasattr(out, "step"):              # 万一以后别的道具也做成 flow
                self.runner = out
                return {"ok": True, "flow": True}
            return out

        def enter_floor(self, floor):
            """进层钩子:登记过"到这层再演"的事件在这里开演(每次进层演一个)。"""
            flags = state_flags(self.engine.state)
            pendings = flags.setdefault("pending_events", [])
            for i, p in enumerate(pendings):
                if int(p["floor"]) == int(floor):
                    ev = (data.get("events") or {}).get(str(p["event"]))
                    pendings.pop(i)
                    if ev is None:
                        continue
                    self.start(ev, self.engine.state)
                    return True
            return False

        # ---------- intent 协议 ----------

        def step(self):
            if self.runner is None:
                return {"op": "done"}
            for _ in range(60):                  # sound/effect 连发也兜得住
                intent = self.runner.step()
                if intent is None:
                    return {"op": "done"}
                op = intent.get("op")
                if op in ("sound", "effect"):    # 纯演出:没做音效系统,自动过
                    self.runner.feed(None)
                    continue
                if op == "pending":              # 跨层挂起:登记,进层重演
                    flags = state_flags(self.engine.state)
                    flags.setdefault("pending_events", []).append(
                        {"floor": intent.get("floor"),
                         "event": intent.get("event")})
                    return {"op": "done"}
                if op == "done" and intent.get("remove_npc") and self._npc_pos:
                    # 一次性 NPC 演完离场:把它从地图上拿掉
                    x, y = self._npc_pos
                    self.api["remove_npc"](self.engine.state["floor"], x, y)
                return intent
            return {"op": "done"}                # 兜底:不该走到这

        def feed(self, response):
            if self.runner is not None:
                self.runner.feed(response)

        # ---------- 内部 ----------

        def _near_stair(self, state):
            hero = state.get("hero", {})
            x, y = hero.get("pos", [0, 0])
            grid = self.api["get_floor"](state["floor"])
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < 11 and 0 <= ny < 11:
                    stack = grid[ny][nx] or []
                    if stack and stack[-1].get("kind") == "stair" \
                            and not stack[-1].get("hide"):
                        return True
            return False

    return RealEvents()


def state_flags(state):
    """state['flags'] 的顺手取法(跟 events 层同一个约定)。"""
    return state.setdefault("flags", {})


def main(argv=None):
    # 1) 数据层
    from game import loader
    try:
        data = loader.load_all()
    except loader.DataError as exc:
        _say(f"[启动失败] 数据加载出错:\n{exc}")
        return 1

    # 2) 规则层 / 3) 事件层(缺谁点名谁)
    try:
        from game import core
        import game.events as events_mod
    except ImportError as exc:
        _say(f"[启动失败] 缺模块:{exc}\n"
             "        (规则层 batch 2 / 事件层 batch 4 应已交付,请先跑 test.sh)")
        return 1

    # 4) 都齐了:拼装 + 开跑
    import pygame  # noqa: F401  (引擎层要用)
    from game import engine as engine_mod

    rules = build_rules_adapter(core)
    if rules is None:
        return 1

    state = rules.new_state()
    # 开局接线(勘误终审):序章从第 1 层 (5,10)、1000/100/100+神圣剑盾玩起,
    # 3 层被夺装备后落到第 2 层监狱 400/10/10。new_state 给多少用多少,
    # 只在缺坐标时兜底,免得引擎没出发点。
    hero = state.setdefault("hero", {})
    hero.setdefault("pos", [5, 10])
    state.setdefault("floor", 1)
    state.setdefault("visited", [state["floor"]])
    state.setdefault("flags", {}).setdefault("events_done", [])
    state["flags"].setdefault("monsters_dead", [])

    engine = engine_mod.Engine(data, rules, None, state)     # 先建引擎(api 要用它)
    events = build_events_adapter(events_mod, engine, data, rules)
    if events is None:
        return 1
    engine.events = events

    engine.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
