"""特殊机制:不走普通战斗的怪规则——巫师魔伤、警卫夹击、49 层封印削弱。

这几条在原版里都不是"撞上去打架"结算的,所以单独一个模块;
输入输出全是普通字典和整数,不碰 pygame。
"""

import copy

# 巫师相邻魔伤表(和 monsters.json 里 125/126 的 special.adjacent_damage 一致)
_WIZARD_DAMAGE = {125: 100, 126: 200}   # 初级巫师 100 / 高级巫师 200
_GUARD_ID = 130                          # 魔法警卫
_HOLY_SHIELD_ID = "17"                   # 神圣盾(道具 17):持有时免疫巫师魔伤
_MAGIC_DAMAGE_LEVEL = 40                 # 巫师魔伤/警卫夹击从第 40 层才开始算
# (tacthgin 源码的 MAGIC_DAMAGE_LEVEL=40;序章/3 层那些"挨打"是剧情演出,不走规则)


def _top_cell(floor_dict, x, y):
    """取 (x,y) 格叠放栈最顶上那个东西(被压在栈下面的不算,§4.4-D)。

    越界(走出地图)当空格处理;返回 None 表示这格没有可见物。
    注意必须先判边界再取值:Python 的 grid[y][-1] 会静默绕到行尾,是个坑。
    """
    grid = floor_dict["grid"]
    if not (0 <= y < len(grid) and 0 <= x < len(grid[y])):
        return None
    stack = grid[y][x]
    return stack[-1] if stack else None


def _has_holy_shield(hero):
    """勇士有没有神圣盾。

    捡到神圣盾后 apply_pickup 把它记在 hero.shield(道具 17 本质是盾);
    但有些调用流程可能把它记进 hero.props——两处都认,约定不一致时
    免疫不会悄悄失效。
    """
    shield = hero.get("shield")
    if shield is not None and str(shield) == _HOLY_SHIELD_ID:
        return True
    return _HOLY_SHIELD_ID in hero.get("props", {})


def adjacent_damage(state, floor_dict, x, y):
    """勇士站在 (x,y) 时,相邻的巫师一共要扣他多少血(持神圣盾则返回 0)。

    只看上下左右紧挨着的四格(斜对角不算);初级巫师(125)一只扣 100、
    高级巫师(126)一只扣 200,几只同时挨着就叠加。巫师被墙/门压在
    叠放栈下面时不喷人(只看栈顶那只)。
    只在 ≥40 层生效:40 层以下(含序章、3 层)没有魔伤。
    """
    if state["floor"] < _MAGIC_DAMAGE_LEVEL:
        return 0
    if _has_holy_shield(state["hero"]):
        return 0
    total = 0
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):   # 上 / 下 / 左 / 右
        cell = _top_cell(floor_dict, x + dx, y + dy)
        if cell is not None and cell.get("kind") == "monster":
            total += _WIZARD_DAMAGE.get(cell.get("id"), 0)
    return total


def guard_trap(state, floor_dict, x, y):
    """踏入 (x,y) 时若被两个魔法警卫夹住,生命减半(向上取整)。

    "夹住"的判定(拿 49 层"骰子5"警卫阵的实际布局考证过):
      左右两侧 (x-1,y) 和 (x+1,y) 都是警卫,或者
      上下两侧 (x,y-1) 和 (x,y+1) 都是警卫
    ——也就是两个警卫彼此相隔 2 格,勇士恰好站在它们中间那一格。
    没被夹住就原样返回,HP 不动。只在 ≥40 层生效(40 层以下警卫不夹人)。
    HP 999 → 500(奇数向上取整,原版行为)。
    """
    if state["floor"] < _MAGIC_DAMAGE_LEVEL:
        return state

    def is_guard(gx, gy):
        cell = _top_cell(floor_dict, gx, gy)
        return (cell is not None and cell.get("kind") == "monster"
                and cell.get("id") == _GUARD_ID)

    trapped = ((is_guard(x - 1, y) and is_guard(x + 1, y))    # 左右夹
               or (is_guard(x, y - 1) and is_guard(x, y + 1)))  # 上下夹
    if trapped:
        hero = state["hero"]
        hero["hp"] = (hero["hp"] + 1) // 2   # 整数的向上取整:(999+1)//2 = 500
    return state


def weaken_monster(monster, ratio):
    """返回一只被削弱过的怪物【副本】(原怪物原封不动),49 层封印用 ×0.1。

    只削 hp/attack/defence 三项(假魔王 8000/5000/1000 → 800/500/100),
    名字、金币等其余字段原样照抄。用 round 取整,免得 8000×0.1 的
    浮点尾差把 800 算成 799。
    """
    weakened = copy.deepcopy(monster)
    for field in ("hp", "attack", "defence"):
        weakened[field] = round(monster[field] * ratio)
    return weakened
