"""游戏入口:把 数据层(loader)→ 规则层(core)→ 事件层(events)→ 引擎 拼起来。

core / events 由另外两个智能体并行开发(设计文档 §4.4),import 必须写在
main() 函数体里【延迟执行】——保证现在它们还不存在时:
1) 引擎自己的测试完全不受影响(测试只 import engine/ui/assets);
2) 直接运行本文件只会打印一条清晰的提示,不会甩裸 traceback。

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
    """把 game.core 的函数包成引擎要的 RulesAdapter(§4.4-B 签名)。

    core 的模块布局由规则智能体决定(battle/pickups/special/state),
    这里按"函数直接挂在 game.core 包上"的最常见拼法走;名字对不上时
    逐个点名提示,方便集成时一眼看出缺哪个。
    """
    from game.engine import RulesAdapter

    class RealRules(RulesAdapter):
        def new_state(self):
            return core.new_state()

        def calc_battle(self, hero, monster, flags):
            return core.calc_battle(hero, monster, flags)

        def apply_pickup(self, state, item_id, data):
            # data 是 loader.load_all() 的大字典(引擎按 §4.4-B 传整包,core 内部查表)
            return core.apply_pickup(state, item_id, data)

        def adjacent_damage(self, state, floor_doc, x, y):
            return core.adjacent_damage(state, floor_doc, x, y)

        def guard_trap(self, state, floor_doc, x, y):
            return core.guard_trap(state, floor_doc, x, y)

        def save_game(self, state, slot):
            return core.save_game(state, slot)

        def load_game(self, slot):
            return core.load_game(slot)

    missing = [name for name in (
        "new_state", "calc_battle", "apply_pickup", "adjacent_damage",
        "guard_trap", "save_game", "load_game") if not hasattr(core, name)]
    if missing:
        _say(f"[启动失败] 规则层 game.core 缺这些函数:{', '.join(missing)}\n"
             f"        (按设计文档 §4.4-B,它们应该能从 game.core 包直接导入)")
        return None
    return RealRules()


def build_events_adapter(events_mod, api, data):
    """把 game.events 包成引擎要的 EventsAdapter(§4.4-C intent 协议)。

    事件层至少要有 EventRunner(event, state, api),step()/feed() 按
    chat/choices/done 协议走;NPC 对话/祭坛商店的入口名字由事件智能体定,
    对不上时这里给出点名提示。
    """
    from game.engine import EventsAdapter

    if not hasattr(events_mod, "EventRunner"):
        _say("[启动失败] 事件层 game.events 还没有 EventRunner 类\n"
             "        (按设计文档 §4.4-C:EventRunner(event, state, api),"
             "step() -> intent, feed(response))")
        return None

    class RealEvents(EventsAdapter):
        def __init__(self):
            self.runner = None
            self.api = api
            self.npc_chat = None        # 没有 event 的 NPC:适配器自己出对话

        def _flatten(self, lines):
            out = []
            for line in lines or []:
                if isinstance(line, list):
                    out.extend(self._flatten(line))
                else:
                    out.append(line)
            return out

        def talk(self, npc, state):
            # NPC 有挂事件的走事件,纯聊天的(npc["talk"])由这里直接出 intent
            event_id = npc.get("event")
            if event_id is not None and str(event_id) in data["events"]:
                self.start(data["events"][str(event_id)], state)
            else:
                self.npc_chat = self._flatten(npc.get("talk") or [])

        def altar_flow(self, state):
            # TODO(集成):祭坛商店入口由 batch 4 提供(名字未定);
            # 对齐前先给玩家一句明确提示,不静默也不崩。
            self.npc_chat = ["(祭坛商店将在事件层就绪后开放)"]

        def start(self, event, state):
            self.npc_chat = None
            self.runner = events_mod.EventRunner(event, state, self.api)

        def step(self):
            if self.npc_chat is not None:
                lines, self.npc_chat = self.npc_chat, None
                return {"op": "chat", "lines": lines}
            if self.runner is None:
                return {"op": "done"}
            return self.runner.step()

        def feed(self, response):
            if self.runner is not None:
                self.runner.feed(response)

    return RealEvents()


def main(argv=None):
    # 1) 数据层(已就绪,batch 1 交付)
    from game import loader
    try:
        data = loader.load_all()
    except loader.DataError as exc:
        _say(f"[启动失败] 数据加载出错:\n{exc}")
        return 1

    # 2) 规则层(并行开发中,可能还没有)——延迟 import,失败给清晰提示
    try:
        from game import core
    except ImportError as exc:
        _say("[启动失败] 规则层 game/core 还没有就绪(并行开发中,batch 2 负责交付)。\n"
             f"        缺少:{exc}\n"
             "        引擎与界面已就绪,可用 tests/test_engine.py 验证引擎行为。")
        return 1

    # 3) 事件层(并行开发中,可能还没有)
    try:
        import game.events as events_mod
    except ImportError as exc:
        _say("[启动失败] 事件层 game/events.py 还没有就绪(并行开发中,batch 4 负责交付)。\n"
             f"        缺少:{exc}")
        return 1

    # 4) 都齐了:拼装 + 开跑
    import pygame
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
    events = build_events_adapter(events_mod, engine.build_api(), data)
    if events is None:
        return 1
    engine.events = events

    engine.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
