"""拾取与祭坛:捡到道具怎么加属性、祭坛商店怎么扣钱,规则全在这一层。

地图上把捡过的格子清空、商店界面怎么弹,都是引擎/事件层的事;
这里只管"数值怎么算",输入输出全是普通字典。
"""

# 钥匙按"它能开哪扇门"查颜色,对照 tiles.json:1001 黄门 / 1002 蓝门 / 1003 红门
_KEY_COLOR_BY_DOOR = {1001: "yellow", 1002: "blue", 1003: "red"}


def area(floor):
    """血瓶/宝石的区域倍率:1-10 层 ×1、11-20 层 ×2 …… 41-50 层 ×5。

    公式是 (floor-1)//10+1——第 10 层还算 ×1,第 11 层才升 ×2
    (TS 源考据:value + floor((level-1)/10) + 1)。
    祭坛的加成用的是另一个公式(floor//10+1,见 altar_buy),别搞混。
    """
    return (floor - 1) // 10 + 1


def apply_pickup(state, item_id, data):
    """捡到道具 item_id,把效果结算进 state(原地修改,返回同一个 state)。

    data 是 loader.load_all() 返回的大字典(要用里面的 items 道具表)。
    规则:
      血瓶/宝石 —— 按 state["floor"] 当前楼层的区域倍率放大
      武器/盾   —— 只升不降,捡到更高级的换装并补差额,低级直接无视
      钥匙      —— yellow/blue/red 三色计数各 +1
      工具      —— 记进 hero.props(道具id 字符串 → 1,用完删键)
    """
    key = str(item_id)
    items = data["items"]
    if key not in items:
        raise ValueError(f"未知道具 id:{item_id}(items.json 里查不到)")
    item = items[key]
    hero = state["hero"]
    zone = area(state["floor"])   # 当前楼层的区域倍率

    kind = item["kind"]
    if kind == "potion":                       # 血瓶:HP + 面值 × 区域
        hero["hp"] += item["hp"] * (zone if item.get("area_scale") else 1)
    elif kind == "gem":                        # 宝石:攻/防 + 面值 × 区域
        times = zone if item.get("area_scale") else 1
        if "attack" in item:
            hero["attack"] += item["attack"] * times
        if "defence" in item:
            hero["defence"] += item["defence"] * times
    elif kind == "sword":                      # 武器:只升不降
        _equip_better(hero, "sword", "attack", item, items, key)
    elif kind == "shield":                     # 盾:只升不降(神圣盾 17 也走这里)
        _equip_better(hero, "shield", "defence", item, items, key)
    elif kind == "key":                        # 钥匙:三色计数 +1
        hero["keys"][_KEY_COLOR_BY_DOOR[item["door"]]] += 1
    elif kind == "tool":                       # 工具:进 props,恒记 1
        hero["props"][key] = 1
    else:
        raise ValueError(f"道具 {item_id} 的类型 {kind!r} 不认识")
    return state


def _equip_better(hero, slot, stat, item, items, key):
    """武器/盾"取最高级":新面值更高才换装(属性只补差额),否则什么都不发生。

    为什么是补差额而不是直接叠加?原版武器/盾是"定值"不是"加成":
    面板攻防 = 裸体攻防 + 手里装备的面值(附录 B:铁剑 +10、银剑 +20……)。
    所以捡到更好的剑只是把"新面值 − 旧面值"补进面板;捡到更差的直接无视,
    绝不回退(§4.4-A:捡到更高级自动替换)。
    """
    old = hero[slot]                                   # 旧装备的道具 id(可能 None)
    old_val = items[str(old)][stat] if old is not None else 0
    new_val = item[stat]
    if new_val > old_val:
        hero[stat] += new_val - old_val
        hero[slot] = key


def use_tool(state, item_id):
    """主动使用一件道具(规则层目前只有圣水一种;地图/传送类由引擎层处理)。

    圣水(道具 26):HP += 当前攻 + 当前防(数值越晚喝越赚),一次性,
    喝完从 hero.props 里删键。没持有就抛 ValueError,不许白嫖。
    """
    key = str(item_id)
    if key != "26":
        raise ValueError(f"道具 {item_id} 不是规则层能直接用的(地图/传送类走引擎层)")
    hero = state["hero"]
    if key not in hero["props"]:
        raise ValueError("身上没有圣水")
    hero["hp"] += hero["attack"] + hero["defence"]
    del hero["props"][key]
    return state


def altar_price(n):
    """第 n 次祭坛购买的价格:20/40/80/140/220……公式 10×(n²−n+2)。"""
    return 10 * (n * n - n + 2)


def altar_buy(state, choice):
    """在祭坛买一次账(choice 三选一,原地修改并返回同一个 state)。

    choice 可选:
      "hp"      生命 +100×n(n = 这是第几次买;全塔所有祭坛共用一个计数)
      "attack"  攻 + 2×当前楼层的区域倍率
      "defence" 防 + 4×当前楼层的区域倍率
    金币不够抛 ValueError,而且次数、金币、属性全都原地不动。
    """
    if choice not in ("hp", "attack", "defence"):
        raise ValueError(f"祭坛只能买 hp/attack/defence 三选一,收到的是 {choice!r}")
    hero = state["hero"]
    flags = state["flags"]
    n = flags["altar_count"] + 1        # 这次买的是第 n 次(之前买过几次就 +几)
    price = altar_price(n)
    if hero["gold"] < price:
        raise ValueError(f"金币不够:第 {n} 次祭坛要 {price},你只有 {hero['gold']}")
    if choice == "hp":
        hero["hp"] += 100 * n
    elif choice == "attack":
        # 祭坛区 = floor//10+1(TS 源码 zone=floor(level/10),attack=(zone+1)*2)。
        # 注意它和宝石的 area() 不是同一个公式:第 10 层宝石 ×1 但祭坛区 ×2,
        # 原版就是这么写的,别"顺手统一"。
        hero["attack"] += 2 * (state["floor"] // 10 + 1)
    else:
        hero["defence"] += 4 * (state["floor"] // 10 + 1)
    hero["gold"] -= price
    flags["altar_count"] = n
    return state
