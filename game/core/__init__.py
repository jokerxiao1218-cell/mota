"""规则层 core/:纯函数 + 纯字典,不碰 pygame,无显示环境也能全量跑测试。

模块分工:battle=战斗、pickups=拾取/祭坛、special=巫师/警卫/封印、
state=开局状态/存档。这里把对外函数再导出一遍,外部两种写法都行:
    from game.core import calc_battle          # 走本文件,写起来短
    from game.core.battle import calc_battle   # 走具体模块,语义更直白
"""
from game.core.battle import calc_battle
from game.core.pickups import (altar_buy, altar_price, apply_pickup, area,
                               use_tool)
from game.core.special import adjacent_damage, guard_trap, weaken_monster
from game.core.state import SaveError, load_game, new_state, save_game
