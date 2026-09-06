"""战斗规则:一场仗打完双方各掉多少血——纯算账,不改任何游戏状态。

设计文档 §3.1 的公式在这里一字不差地落地:
玩家先手、全程无随机数、破防判定用裸攻击力(原版怪癖,单测钉死)。
本模块(以及整个 core/)不碰 pygame——无显示的 SSH/CI 环境也要能跑测试。
"""

# 持十字架(道具 28)打这三只怪攻击 ×2,清单与 items.json 里 28 的 "affects" 一致
CROSS_AFFECTS = {111, 112, 115}    # 兽人 / 兽人武士 / 吸血鬼
# 持屠龙匕(道具 29)只对魔龙生效,与 items.json 里 29 的 "affects" 一致
DRAGON_SLAYER_AFFECTS = {122}      # 魔龙


def calc_battle(hero, monster, flags):
    """预测一场战斗:打不打得过、玩家损多少血、出手几回合、赚多少金币。

    参数:
      hero    —— state["hero"](本函数只读 hp/attack/defence 三个数)
      monster —— monsters.json 里的一条怪物数据
      flags   —— {"cross": …, "lucky_coin": …, "dragon_slayer": …,
                 "first_attack": …}
                 没写的键一律当 False 处理;first_attack 是给 40 层那批
                 "先攻怪"预留的开关(本项目默认不做,保持 False 行为不变)
    返回:{"can_fight", "hero_damage", "turns", "gold"} 四个键的普通字典。
    纯函数:不改传入的任何字典,同样的输入永远同样的输出——
    战斗结算和怪物手册的"预测伤害"面板共用这一个函数。
    """
    flags = flags or {}
    hp, atk, dfn = hero["hp"], hero["attack"], hero["defence"]
    m_hp, m_atk = monster["hp"], monster["attack"]
    m_def, m_gold = monster["defence"], monster["gold"]
    m_id = monster.get("id")

    # 巫师(125/126)在数据里标了 unfightable(不可战斗),只能绕着走吃魔伤
    if monster.get("special", {}).get("unfightable"):
        return {"can_fight": False, "hero_damage": 0, "turns": 0, "gold": 0}
    # 血 ≤ 0 的怪已经死了,不存在这场战斗
    if m_hp <= 0:
        return {"can_fight": False, "hero_damage": 0, "turns": 0, "gold": 0}

    # 破防判定:用的是【裸攻击力】atk,不是下面 ×2 之后的有效攻击——
    # 就算十字架/屠龙匕翻倍后够破防,裸攻不够照样整场打不了(原版怪癖,忠实保留)
    if atk <= m_def:
        return {"can_fight": False, "hero_damage": 0, "turns": 0, "gold": 0}

    # 有效攻击力:持十字架撞特定怪、持屠龙匕撞魔龙时 ×2,其余情况就是裸攻
    eff_atk = atk
    if flags.get("cross") and m_id in CROSS_AFFECTS:
        eff_atk = atk * 2
    elif flags.get("dragon_slayer") and m_id in DRAGON_SLAYER_AFFECTS:
        eff_atk = atk * 2

    d1 = eff_atk - m_def        # 玩家每击伤害(过了破防判定,这里一定 ≥ 1)
    d2 = max(0, m_atk - dfn)    # 怪物每击伤害,最低 0(没有"保底 1 点")
    n = (m_hp - 1) // d1 + 1    # 玩家出手次数 = ceil(怪血 / d1),整除也不多打一下

    if flags.get("first_attack"):
        # 先攻怪(40 层那批,默认不做):怪抢先出手,反击打满 n 次
        hero_damage = n * d2
    else:
        # 普通怪:玩家先手,怪只来得及反击 n-1 次
        hero_damage = (n - 1) * d2

    return {
        # 可战胜条件:总损血【严格小于】当前 HP(损血 == HP 就是同归于尽,不让打)
        "can_fight": hero_damage < hp,
        "hero_damage": hero_damage,
        "turns": n,
        # 幸运金币(道具 27):战后金币 ×2
        "gold": m_gold * 2 if flags.get("lucky_coin") else m_gold,
    }
