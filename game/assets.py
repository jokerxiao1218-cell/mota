"""像素贴图生成器:全部素材运行时程序自绘,零外部图片文件。

设计文档 §4.2 选择7:贴图 16×16 像素、pygame 现画、按需缓存。
只认格子数据里的 kind + id(怪物查名字和数值配色),完全不依赖 sprite 文件名——
将来想换真贴图,只改本文件的映射,引擎和界面层都不用动。

绘制办法有两种:
1. 字符画:16 行 × 每行 16 个字符,一个字符 = 一个像素点,配 palette(字符→颜色)。
   '.' 或空格 = 透明。适合怪物/NPC/道具这类不规则小图。
2. 程序画:用小矩形循环拼,适合墙砖/地面/门这类规则图块。
"""

import pygame

SIZE = 16          # 贴图边长(像素)
TRANSPARENT = ("." , " ")   # 这两个字符代表透明像素


# ---------------------------------------------------------------- 字符画工具

def _paint(name, rows, palette):
    """把 16×16 字符画刷成 Surface。行数/每行字符数不对会直接报错,别静默画歪。"""
    if len(rows) != SIZE:
        raise ValueError(f"贴图 {name} 行数不是 {SIZE}:{len(rows)}")
    surface = pygame.Surface((SIZE, SIZE), pygame.SRCALPHA)
    for y, row in enumerate(rows):
        if len(row) != SIZE:
            raise ValueError(f"贴图 {name} 第 {y} 行长度不是 {SIZE}:{row!r}")
        for x, ch in enumerate(row):
            if ch in TRANSPARENT:
                continue
            if ch not in palette:
                raise ValueError(f"贴图 {name} 用了未定义颜色的字符 {ch!r}")
            surface.set_at((x, y), palette[ch])
    return surface


def _shift(color, factor):
    """把颜色整体调亮(>1)或调暗(<1),用于同形状不同强度的怪。"""
    return tuple(max(0, min(255, int(c * factor))) for c in color)


# ---------------------------------------------------------------- 字符画形状库
# 约定用到的颜色字符:B=主体色 D=深色(描边/暗部) L=亮色(高光/白) A=强调色 E=眼睛

SHAPES = {}

SHAPES["slime"] = [   # 史莱姆:一坨水滴,两只眼睛
    "................",
    "......BBBB......",
    ".....BBLLBB.....",
    "....BBBBBBBB....",
    "...BBBBBBBBBB...",
    "..BBBBBBBBBBBB..",
    "..BBEBBBBBBEBB..",
    ".BBBBBBBBBBBBBB.",
    ".BBBBBBBBBBBBBB.",
    ".BBBDDDDDDDDBBB.",
    ".BBBBBBBBBBBBBB.",
    ".BBBBBBBBBBBBBB.",
    "..BBBBBBBBBBBB..",
    "..BBBBBBBBBBBB..",
    "...BBBBBBBBBB...",
    "....BBBBBBBB....",
]

SHAPES["bat"] = [     # 蝙蝠:左右翅膀 + 中间小身体
    "................",
    "..B..........B..",
    ".BBB........BBB.",
    ".BBBB..BB..BBBB.",
    ".BBBBBBBBBBBBBB.",
    "..BBBBBBBBBBBB..",
    "...BBEBBBBEBB...",
    "....BBBBBBBB....",
    "....BBB..BBB....",
    ".....BB..BB.....",
    "....EE....EE....",
    "................",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["skull"] = [   # 骷髅:大头骨 + 黑眼窝 + 牙
    "................",
    "................",
    "....BBBBBBB.....",
    "...BBBBBBBBB....",
    "..BBBBBBBBBBB...",
    "..BEEBBBBBEEB...",
    "..BEEBBBBBEEB...",
    "..BBBBBBBBBBB...",
    "...BBEBBEBBB....",
    "...BBBBBBBBB....",
    "....B.B.B.B.....",
    "....B.B.B.B.....",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["mage"] = [    # 法师/巫师:尖帽子 + 白胡子 + 长袍
    ".......A........",
    "......AAA.......",
    ".....AAAAA......",
    "....AAAAAAA.....",
    "...AAAAAAAAA....",
    ".BBBBBBBBBBBBBB.",
    "...BEBBBBBEB....",
    "...BBBBBBBBB....",
    "...LLLLLLLLL....",
    "..BLLLLLLLLLB...",
    "..BBBBBBBBBBB...",
    "..BBBBBBBBBBB...",
    "..BBBBBBBBBBB...",
    "..BBBBBBBBBBB...",
    "...BBBBBBBBB....",
    "................",
]

SHAPES["guard"] = [   # 卫兵/警卫:头盔 + 方盾 + 盾徽
    "................",
    "..BBBBBBBBBBB...",
    "..BDDDDDDDDDB...",
    "..BBBBBBBBBBB...",
    "..AAAAAAAAAAA...",
    "..AAAAALAAAAA...",
    "..AAAALLLAAAA...",
    "..AAAAALAAAAA...",
    "..AAAAAAAAAAA...",
    "..AAAAAAAAAAA...",
    "..BBBBBBBBBBB...",
    "..BBB.....BBB...",
    "..DDD.....DDD...",
    "................",
    "................",
    "................",
]

SHAPES["knight"] = [  # 骑士:盔上红缨 + 长盾
    ".......A........",
    "......AA........",
    "..BBBBBBBBBBB...",
    "..BDDDDDDDDDB...",
    "..BBBBBBBBBBB...",
    "..BBBBBBBBBBB...",
    "..BBAAAAAAABB...",
    "..BBBBBBBBBBB...",
    "..BBBLLLLBBBB...",
    "..BBBLLLLBBBB...",
    "..BBBLLLLBBBB...",
    "..BBBBBBBBBBB...",
    "..BBB.....BBB...",
    "..DDD.....DDD...",
    "................",
    "................",
]

SHAPES["soldier"] = [  # 战士/剑士:盔 + 手里竖着一柄剑(右侧)
    "..........LL....",
    "..........LL....",
    "..BBBBBBB.LL....",
    "..BDDDDDB.LL....",
    "..BBBBBBB.LL....",
    "..BBBBBBBAAB....",
    "..BBBBBBBB......",
    "..BBAAAAABB.....",
    "..BBBBBBBB......",
    "..BBBBBBBB......",
    "..BBBBBBBB......",
    "..BBB..BBBB.....",
    "..DDD.....DD....",
    "................",
    "................",
    "................",
]

SHAPES["orc"] = [     # 兽人:绿脸 + 两颗獠牙
    "................",
    "..BBBBBBBBBBB...",
    "..BDDDDDDDDDB...",
    "..BBEBBBBBBEB...",
    "..BBBBBBBBBBB...",
    "..BBLBBBBLBBB...",
    "..BBLLBBBLLBB...",   # 獠牙
    "..BBBBBBBBBBB...",
    "...BBBBBBBBB....",
    "..AAAAAAAAAAA...",
    "..AAAAAAAAAAA...",
    "..AAADAAAADAAA..",   # 深色护腕
    "..AAAAAAAAAAA...",
    "..DDA.....ADD...",
    "................",
    "................",
]

SHAPES["vampire"] = [  # 吸血鬼:黑披风 + 白脸 + 红眼睛 + 尖牙
    "................",
    "...AAAAAAAAAA...",
    "..AAAAAAAAAAAA..",
    "..AABBBBBBBBAA..",
    "..AABEBBBBEBAA..",
    "..AABBBBBBBBAA..",
    "..AABLBBBBLBAA..",
    "..AABLLBBLLBAA..",   # 尖牙
    "..AABBBBBBBBAA..",
    "..AABBBBBBBBAA..",
    "..AAAABBBBAAAA..",
    "..AA.AAAAAA.AA..",
    "..A...AAAA...A..",
    "................",
    "................",
    "................",
]

SHAPES["ghost"] = [   # 幽灵:飘着的白影,下摆波浪
    "................",
    ".....BBBBB......",
    "....BBBBBBB.....",
    "...BBBBBBBBB....",
    "..BBBBBBBBBBB...",
    "..BEEBBBBBEEB...",
    "..BBBBBBBBBBB...",
    "..BBBBBBBBBBB...",
    "..BBBBBBBBBBB...",
    "..BBBBBBBBBBB...",
    "..BBBBBBBBBBB...",
    "..BBBBBBBBBBB...",
    "..B.BB.BB.BB.B..",
    "................",
    "................",
    "................",
]

SHAPES["golem"] = [   # 石头人:一大块岩石身子 + 裂纹
    "................",
    "...DBBBBBBD.....",
    "..DBBBBBBBBD....",
    ".DBBBBBBBBBBD...",
    ".DBBEEBBBEEBBD..",
    ".DBBBBBBBBBBBD..",
    "DBBBBBLBBBBBBBD.",
    "DBBBBBBBBLBBBBD.",
    "DBBLBBBBBBBBBBBD",
    "DBBBBBLBBBBBBBBD",
    "DBBBBBBBBBLBBBBD",
    ".DBBBBBBBBBBBBD.",
    ".DDBBBBBBBBBDD..",
    "...DDDBBBDDD....",
    "................",
    "................",
]

SHAPES["squid"] = [   # 大乌贼:圆脑袋 + 往下伸的触手
    "................",
    ".....BBBBB......",
    "....BBBBBBB.....",
    "...BBBBBBBBB....",
    "..BBBBBBBBBBB...",
    "..BEBBBBBBEBB...",
    "..BBBBBBBBBBB...",
    "..BBLBBBBLBBB...",
    "..BBBBBBBBBBB...",
    "...BBBBBBBBB....",
    "..B.BB.BB.BB.B..",
    "..B.BB.BB.BB.B..",
    "..B..B..B..B..B.",
    "................",
    "................",
    "................",
]

SHAPES["dragon"] = [  # 魔龙:两只角 + 绿鳞 + 尖牙
    "................",
    ".LL..........LL.",
    ".LLL........LLL.",
    "..BBBBBBBBBBBB..",
    "..BBEBBBBBBEBB..",
    "..BBBBBBBBBBBB..",
    "..BBBDBBBDBBB...",
    "..BBBBBBBBBBBB..",
    "..BBLBBBBLBBBB..",
    "..BBLLBBBLLBBB..",
    "..BBBBBBBBBBBB..",
    "...BBBBBBBBBB...",
    "...B.BB..BB.B...",
    "................",
    "................",
    "................",
]

SHAPES["demon"] = [   # 魔王:大弯角 + 红眼 + 深色身躯
    "..LL........LL..",
    ".LLL........LLL.",
    ".LL..........LL.",
    ".LLBBBBBBBBBBLL.",
    "..BBEEBBBBEEBB..",
    "..BBBBBBBBBBBB..",
    "..BBBDBBBBDBBB..",
    "..BBBLLLLLLBBB..",
    "..BBLLBBBBLLBB..",
    "..BBBBBBBBBBBB..",
    "..DBBBBBBBBBBD..",
    "..DDBBBBBBBBDD..",
    "...DDDBBBBDDD...",
    "................",
    "................",
    "................",
]

SHAPES["elder"] = [   # 老人:白头发白胡子,拄拐
    "................",
    "...LLLLLLLLL....",
    "..LLLLLLLLLLL...",
    "..LLBBBBBBBLL...",
    "..LLBEBBEBBLL...",
    "..LLBBBBBBBLL...",
    "..LLLLLLLLLLL...",
    "..LLLLLLLLLLL...",
    "..LLBAAAAABLL...",
    "...BAAAAAAAB....",
    "...BAAAAAAAB.A..",
    "...BAAAAAAAB.A..",
    "...BAAAAAAAB.A..",
    "...BAAAAAAA..A..",
    "....DDDDDD...A..",
    "................",
]

SHAPES["merchant"] = [  # 商人:戴帽子的胖商人,手里一枚金币
    "................",
    "....AAAAAAAAA...",
    "..AAAAAAAAAAAA..",
    "..AAAAAAAAAAAA..",
    "...BBBBBBBBB....",
    "...BEBBBBBEB....",
    "...BBBBBBBBB....",
    "...BBBAAABBB..L.",
    "...BBBAAABBB.LL.",
    "...BBBBBBBBB.LL.",
    "...BBAAAAABB.LL.",
    "...BBBBBBBBB.L..",
    "...DDD...DDD....",
    "................",
    "................",
    "................",
]

SHAPES["thief"] = [   # 小偷:深色兜帽,只露眼睛
    "................",
    "....BBBBBBB.....",
    "...BBBBBBBBB....",
    "..BBBBBBBBBBB...",
    "..BBDDDDDDDBB...",
    "..BBDEBBBEDBB...",
    "..BBDDDDDDDBB...",
    "..BBBDDDDDBBB...",
    "..BBBBBBBBBBB...",
    "..BBBLLLLLBBB...",
    "..BBBBBBBBBBB...",
    "..BBBBBBBBBBB...",
    "..DDD.....DDD...",
    "................",
    "................",
    "................",
]

SHAPES["princess"] = [  # 公主:金色皇冠 + 粉裙
    "................",
    "..A.A.A.A.A.A...",
    "..AAAAAAAAAAA...",
    "..AAAAAAAAAAA...",
    "...BBBBBBBBB....",
    "...BEBBBBBEB....",
    "...BBBBBBBBB....",
    "...LBBBBBBBL....",
    "...LLLLLLLLL....",
    "..LLLLLLLLLLL...",
    "..LLLLLLLLLLL...",
    "..LLLLLLLLLLL...",
    "..LLL.LLL.LLL...",
    "................",
    "................",
    "................",
]

SHAPES["hero"] = [    # 勇士:蓝盔蓝甲,腰上一把剑
    "................",
    "..BBBBBBBBBBB...",
    "..BDDDDDDDDDB...",
    "..BBBBBBBBBBB...",
    "...LBBEBBEBL....",
    "...LBBBBBBBL....",
    "..LLBBAABBBLL...",
    "..LLBBAABBBLL...",
    "...LAAAAAAAL....",
    "...LAAAAAAAL.A..",
    "...LAAAAAAALLA..",
    "...LAAAAAAA.LA..",
    "...BBB..BBB..A..",
    "...DDD..DDD.....",
    "................",
    "................",
]

# ---------------- 道具形状 ----------------

SHAPES["key"] = [     # 钥匙:圆环头 + 长柄 + 两个齿
    "................",
    "................",
    "....BBBB........",
    "...BB..BB.......",
    "...BB..BB.......",
    "....BBBB........",
    "......BB........",
    "......BB........",
    "......BB........",
    "......BB........",
    "......BBB.......",
    "......BBBB......",
    "......BBB.......",
    "................",
    "................",
    "................",
]

SHAPES["potion"] = [  # 药水瓶:软木塞 + 圆瓶身 + 高光
    "................",
    "......LL........",
    "......DD........",
    "....BBBBBB......",
    "...BBBBBBBB.....",
    "..BBBBBBBBBB....",
    "..BBBLLBBBBB....",
    "..BBBLBBBBBB....",
    "..BBBBBBBBBB....",
    "..BBBBBBBBBB....",
    "...BBBBBBBB.....",
    "....BBBBBB......",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["gem"] = [     # 宝石:菱形 + 顶部高光
    "................",
    "................",
    "......BB........",
    ".....BLBB.......",
    "....BLBBBB......",
    "...BBBBBBBB.....",
    "..BBBBBBBBBB....",
    "..BBBBBBBBBB....",
    "...BBBBBBBB.....",
    "....BBBBBB......",
    ".....BBBB.......",
    "......BB........",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["sword"] = [   # 剑:斜刃 + 十字护手 + 柄
    "................",
    "..........LL....",
    ".........LLL....",
    "........LLL.....",
    ".......LLL......",
    "......LLL.......",
    ".....LLL........",
    "...ALLL.........",
    "..AALL..........",
    "...AA...........",
    "................",
    "................",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["shield"] = [  # 盾:鸢形盾 + 中央徽记
    "................",
    "..AAAAAAAAAAA...",
    "..AABBBBBBBAA...",
    "..ABBBLLLBBBA...",
    "..ABBBLLLBBBA...",
    "..ABBBLLLBBBA...",
    "..AABBBBBBBAA...",
    "...AABBBBBAA....",
    "....ABBBBBA.....",
    ".....ABBBBA.....",
    "......ABBA......",
    ".......AA.......",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["book"] = [    # 手册/记事本:封面 + 书页线
    "................",
    "..DDDDDDDDDDD...",
    "..DAAAAAAAAAD...",
    "..DAALLLLAAAD...",
    "..DAALLLLAAAD...",
    "..DAAAAAAAAAD...",
    "..DAALLLAAAD....",
    "..DAALLLAAAD....",
    "..DAAAAAAAAAD...",
    "..DAAAAAAAAAD...",
    "..DDDDDDDDDDD...",
    "................",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["wand"] = [    # 飞行魔杖:斜杖 + 杖头星星
    "...........LL...",
    "..........LLL...",
    ".........LLL....",
    "........LLL.....",
    ".......LL.......",
    "......BB........",
    ".....BB.........",
    "....BB..........",
    "...BB...........",
    "..BB............",
    ".BB.............",
    "................",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["pickaxe"] = [  # 镐:弯头 + 木柄
    "................",
    "....LLLLLL......",
    "...LLAAAALLL....",
    "..LLAAAABBBLL...",
    "..LL..BBBBBLL...",
    "..L....BBBB.L...",
    ".......BBBB.....",
    ".......BBBB.....",
    "......BBBB......",
    ".....BBBB.......",
    "....BBBB........",
    "...BBBB.........",
    "..BBB...........",
    "................",
    "................",
    "................",
]

SHAPES["scroll"] = [  # 卷轴:卷起来的纸 + 封绳
    "................",
    "...LLLLLLLLLL...",
    "..LAAAAAAAAAAL..",
    "..LAAAAAAAAAAL..",
    "..LAADDADDADAL..",
    "..LAAAAAAAAAAL..",
    "..LAAAAAAAAAAL..",
    "..LAAAAAAAAAAL..",
    "..LAAAAAAAAAAL..",
    "..LAAAAAAAAAAL..",
    "..LAAAAAAAAAAL..",
    "...LLLLLLLLLL...",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["snowflake"] = [  # 冰冻魔法:六芒雪花
    "................",
    ".......L........",
    ".......L........",
    "..L....L....L...",
    "....L..L..L.....",
    ".....LLLLL......",
    "..LLLLLLLLLLL...",
    ".....LLLLL......",
    "....L..L..L.....",
    "..L....L....L...",
    ".......L........",
    ".......L........",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["bomb"] = [    # 炸弹:黑球 + 引线 + 火花
    "................",
    "..........A.....",
    ".........ALA....",
    ".......LL.......",
    "......LL........",
    "....DDDDDD......",
    "...DDDDDDDD.....",
    "..DDDDDDDDDD....",
    "..DDLDDDDDDD....",
    "..DDDDDDDDDD....",
    "..DDDDDDDDDD....",
    "...DDDDDDDD.....",
    "....DDDDDD......",
    "................",
    "................",
    "................",
]

SHAPES["flask"] = [   # 圣水:高瓶 + 光环
    "................",
    "....LLLLLLL.....",
    "......LL........",
    "......DD........",
    "......DD........",
    "....BBBBBB......",
    "...BBBBBBBB.....",
    "..BBBBBBBBBB....",
    "..BBLBBBBBBB....",
    "..BBLBBBBBBB....",
    "..BBBBBBBBBB....",
    "...BBBBBBBB.....",
    "....BBBBBB......",
    "................",
    "................",
    "................",
]

SHAPES["coin"] = [    # 幸运金币:圆金币 + 星纹
    "................",
    "................",
    ".....BBBBB......",
    "....BBBBBBB.....",
    "...BBBBBBBBB....",
    "...BBBBLBBBB....",
    "..BBBBBLBBBBBB..",
    "..BBLLLLLLLLBB..",
    "..BBBBBLBBBBBB..",
    "...BBBBLBBBB....",
    "...BBBBBBBBB....",
    "....BBBBBBB.....",
    ".....BBBBB......",
    "................",
    "................",
    "................",
]

SHAPES["cross"] = [   # 十字架
    "................",
    "......AAA.......",
    "......AAA.......",
    "......AAA.......",
    "..AAAAAAAAAAA...",
    "..AAAAAAAAAAA...",
    "..AAAAAAAAAAA...",
    "......AAA.......",
    "......AAA.......",
    "......AAA.......",
    "......AAA.......",
    "......AAA.......",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["dagger"] = [  # 屠龙匕:短刃匕首
    "................",
    "........LL......",
    ".......LLL......",
    "......LLL.......",
    ".....LLL........",
    "....LLL.........",
    "....LL..........",
    "...AA...........",
    "..AAAA..........",
    "...AA...........",
    "................",
    "................",
    "................",
    "................",
    "................",
    "................",
]

SHAPES["wing"] = [    # 飞行器:一对小翅膀 + 中间箭头(A)
    "................",
    "..LL........LL..",
    ".LLLL......LLLL.",
    ".LLLLL....LLLLL.",
    "..LLLLAAAAALLL..",
    "...LLAAAAAALL...",
    "....LAAAAAAL....",
    "......AAAA......",
    "......AAAA......",
    "....LAAAAAAL....",
    "...LLAAAAAALL...",
    "..LLLLAAAAALLL..",
    ".LLLLL....LLLLL.",
    ".LLLL......LLLL.",
    "..LL........LL..",
    "................",
]

SHAPES["box"] = [     # 兜底:未知道具画个小箱子
    "................",
    "..DDDDDDDDDDD...",
    "..DLLLLLLLLLD...",
    "..DLDDDDDDDLLD..",
    "..DLDDDDDDDLLD..",
    "..DLLLLLLLLLD...",
    "..DLDDDDDDDLB...",
    "..DLDDDDDDDLB...",
    "..DLLLLLLLLLD...",
    "..DLDDDDDDDLB...",
    "..DLDDDDDDDLB...",
    "..DLLLLLLLLLD...",
    "..DDDDDDDDDDD...",
    "................",
    "................",
    "................",
]

# ---------------------------------------------------------------- 程序画的图块


def _brick(main, mortar, edge=None):
    """砖墙:4 行砖,每行错缝。edge 不为 None 时再描一圈边。"""
    surface = pygame.Surface((SIZE, SIZE), pygame.SRCALPHA)
    surface.fill(main)
    for y in range(SIZE):
        if y % 4 == 3:                       # 横向灰浆缝
            for x in range(SIZE):
                surface.set_at((x, y), mortar)
    for row in range(4):                     # 纵向灰浆缝,隔行错开
        offset = 4 if row % 2 == 0 else 0
        for y in range(row * 4, row * 4 + 3):
            for x in range(offset, SIZE, 8):
                surface.set_at((x, y), mortar)
    if edge is not None:
        for i in range(SIZE):
            surface.set_at((i, 0), edge)
            surface.set_at((i, SIZE - 1), edge)
            surface.set_at((0, i), edge)
            surface.set_at((SIZE - 1, i), edge)
    return surface


def _ground(base, dot):
    """地面格:纯色底 + 固定几个暗点(用坐标写死,不引入随机)。"""
    surface = pygame.Surface((SIZE, SIZE), pygame.SRCALPHA)
    surface.fill(base)
    for x, y in ((3, 4), (11, 7), (6, 12), (13, 13), (1, 9)):
        surface.set_at((x, y), dot)
    return surface


def _lava():
    """岩浆:橙红底 + 更亮的斑块 + 暗壳。"""
    surface = pygame.Surface((SIZE, SIZE), pygame.SRCALPHA)
    surface.fill((200, 70, 30))
    for x in range(SIZE):
        for y in range(SIZE):
            if (x * 7 + y * 5) % 11 == 0:        # 亮斑
                surface.set_at((x, y), (255, 150, 40))
            elif (x * 3 + y * 9) % 13 == 0:      # 暗壳
                surface.set_at((x, y), (120, 30, 15))
    return surface


def _door(body, frame, hole):
    """门:门板 + 门框 + 钥匙孔。"""
    surface = pygame.Surface((SIZE, SIZE), pygame.SRCALPHA)
    surface.fill(frame)
    for y in range(1, SIZE - 1):                 # 门板(四边留框)
        for x in range(1, SIZE - 1):
            surface.set_at((x, y), body)
    for y in range(1, SIZE - 1):                 # 门板竖纹
        for x in (4, 8, 12):
            surface.set_at((x, y), _shift(body, 0.8))
    for y in range(5, 7):                        # 钥匙孔
        for x in range(7, 9):
            surface.set_at((x, y), hole)
    for x in range(7, 9):
        for y in range(7, 9):
            surface.set_at((x, y), hole)
    return surface


def _jail_door():
    """监狱门:深底 + 竖铁栏杆。"""
    surface = pygame.Surface((SIZE, SIZE), pygame.SRCALPHA)
    surface.fill((40, 40, 48))
    for x in (1, 3, 5, 7, 9, 11, 13, 15):
        for y in range(SIZE):
            surface.set_at((x, y), (110, 110, 130))
    for y in (3, 12):                            # 上下横梁
        for x in range(SIZE):
            surface.set_at((x, y), (90, 90, 110))
    return surface


def _monster_door():
    """怪物门(打倒守卫才开):铁门 + 怪物脸记号。"""
    surface = _door((90, 80, 70), (50, 45, 40), (30, 25, 20))
    for y in (6, 9):                             # 两个"眼睛"
        for x in (5, 6, 9, 10):
            surface.set_at((x, y), (240, 230, 200))
    for x in range(6, 10):                       # 嘴
        surface.set_at((x, 12), (240, 230, 200))
        surface.set_at((x, 13), (240, 230, 200))
    return surface


def _stair(direction):
    """楼梯格:石板 + 一个上/下箭头。"""
    surface = _ground((150, 140, 120), (110, 100, 85))
    arrow = (255, 255, 255) if direction == "up" else (250, 210, 60)
    cx = 8                                        # 箭头中心线
    for i in range(6):                            # 箭杆
        surface.set_at((cx, 4 + i), arrow)
    if direction == "up":
        tips = ((cx - 1, 5), (cx + 1, 5), (cx - 2, 6), (cx + 2, 6), (cx - 3, 7), (cx + 3, 7))
    else:
        tips = ((cx - 1, 9), (cx + 1, 9), (cx - 2, 8), (cx + 2, 8), (cx - 3, 7), (cx + 3, 7))
    for x, y in tips:
        surface.set_at((x, y), arrow)
    for i in range(6):                            # 底下的台阶横线
        surface.set_at((cx - 2 + i, 12), (120, 110, 90))
    return surface


def _star_tile():
    """结局层星空:深底 + 固定几颗小星星。"""
    surface = pygame.Surface((SIZE, SIZE), pygame.SRCALPHA)
    surface.fill((15, 15, 35))
    for x, y in ((2, 2), (8, 4), (13, 3), (4, 9), (11, 11), (6, 14), (14, 13), (1, 12)):
        surface.set_at((x, y), (255, 250, 200))
        surface.set_at((x + 1, y), (255, 250, 200))
        surface.set_at((x, y + 1), (200, 200, 160))
    return surface


def _big_part():
    """大体型怪的身体部件(大乌贼/魔龙占的格子):一团深色 + 吸盘点。"""
    surface = pygame.Surface((SIZE, SIZE), pygame.SRCALPHA)
    surface.fill((70, 40, 80))
    for x, y in ((3, 3), (9, 5), (13, 10), (5, 11), (7, 7)):
        surface.set_at((x, y), (150, 90, 160))
        surface.set_at((x + 1, y), (150, 90, 160))
    for i in range(SIZE):
        surface.set_at((i, 0), (45, 25, 55))
        surface.set_at((i, SIZE - 1), (45, 25, 55))
        surface.set_at((0, i), (45, 25, 55))
        surface.set_at((SIZE - 1, i), (45, 25, 55))
    return surface


def _altar():
    """祭坛(4/12/32/46 层):石头台子 + 中央金盆。"""
    surface = pygame.Surface((SIZE, SIZE), pygame.SRCALPHA)
    surface.fill((92, 88, 104))                      # 石台
    for i in range(SIZE):                            # 描一圈边
        surface.set_at((i, 0), (135, 130, 150))
        surface.set_at((i, SIZE - 1), (58, 56, 68))
        surface.set_at((0, i), (135, 130, 150))
        surface.set_at((SIZE - 1, i), (58, 56, 68))
    for y in range(6, 11):                           # 金盆
        for x in range(4, 12):
            if (x - 7) ** 2 + (y - 7) ** 2 <= 13:
                surface.set_at((x, y), (235, 190, 70))
    for y in range(4, 6):                            # 盆口
        for x in range(5, 11):
            surface.set_at((x, y), (250, 225, 130))
    for x, y in ((7, 3), (10, 6), (4, 6)):          # 高光点
        surface.set_at((x, y), (255, 255, 225))
    return surface


# ---------------------------------------------------------------- 怪物配色
# 名字关键词 → 形状(顺序敏感:长词/特例在前)
MONSTER_SHAPES = [
    ("魔法警卫", "mage"),    # 警卫但走法师样子,先于"警卫/卫兵"匹配
    ("吸血鬼", "vampire"),
    ("吸血蝙蝠", "bat"),
    ("史莱姆", "slime"),
    ("蝙蝠", "bat"),
    ("骷髅", "skull"),
    ("巫师", "mage"),
    ("法师", "mage"),
    ("警卫", "guard"),
    ("卫兵", "guard"),
    ("骑士", "knight"),
    ("剑士", "soldier"),
    ("兽人武士", "soldier"),  # 武士拿剑,但配色走"兽人"
    ("兽人", "orc"),
    ("战士", "soldier"),
    ("幽灵", "ghost"),
    ("石头人", "golem"),
    ("乌贼", "squid"),
    ("魔龙", "dragon"),
    ("魔王", "demon"),
]

# 名字颜色关键词 → 身体基色
NAME_COLORS = [
    ("绿", (90, 200, 90)),
    ("红", (225, 70, 60)),
    ("蓝", (80, 120, 225)),
    ("紫", (160, 90, 210)),
    ("黄", (235, 200, 70)),
    ("金", (240, 200, 60)),
    ("白", (235, 235, 235)),
    ("黑暗", (80, 70, 95)),
    ("黑", (80, 70, 95)),
    ("大", (170, 110, 60)),   # 大字号默认土棕,再被数值段加深
]

# 数值段:按攻击力分 4 档,档位越高颜色越深、强调色越凶
def _tier(attack):
    if attack <= 50:
        return 0
    if attack <= 150:
        return 1
    if attack <= 400:
        return 2
    return 3

TIER_SHADE = (1.15, 1.0, 0.8, 0.6)         # 同色系,越强越暗
TIER_ACCENT = [
    (120, 130, 150),
    (210, 160, 60),
    (200, 90, 140),
    (235, 70, 60),
]

DEFAULT_MONSTER_COLOR = (150, 150, 160)    # 名字里没颜色词的默认灰


def monster_palette(name, attack):
    """由怪物名字 + 攻击数值段决定整套配色。"""
    body = DEFAULT_MONSTER_COLOR
    for word, color in NAME_COLORS:
        if word in name:
            body = color
            break
    tier = _tier(attack)
    body = _shift(body, TIER_SHADE[tier])
    return {
        "B": body,
        "D": _shift(body, 0.55),
        "L": (240, 240, 240),
        "A": TIER_ACCENT[tier],
        "E": (20, 20, 25),
    }


# ---------------------------------------------------------------- 道具配色

KEY_COLORS = {1001: (240, 210, 60), 1002: (90, 130, 240), 1003: (230, 70, 70)}
POTION_COLORS = {4: (230, 60, 60), 5: (80, 110, 240)}
GEM_COLORS = {6: (235, 70, 70), 7: (80, 120, 235)}
# 武器盾按数值取强调色(档位越高柄色越亮)
TIER_COLORS = [
    (150, 150, 160), (200, 170, 70), (120, 190, 120), (200, 120, 60), (250, 200, 60),
]


def _tier_color(value, step):
    idx = min(len(TIER_COLORS) - 1, value // step)
    return TIER_COLORS[idx]


# ---------------------------------------------------------------- 主类


class Assets:
    """贴图仓库:生成 + 缓存。对外只要 cell()/cell_big()/hero()。"""

    def __init__(self, data):
        # 只留引用不修改:data 是 loader.load_all() 的大字典
        self.data = data
        self._small = {}     # 缓存键 -> 16×16 Surface
        self._big = {}       # 缓存键 -> 32×32 Surface(渲染用)
        self._hero = None

    # ---- 基础缓存 ----

    def _get(self, key, builder):
        if key not in self._small:
            self._small[key] = builder()
        return self._small[key]

    def _scaled(self, key):
        """取 32×32 放大版(渲染用),只放大一次。"""
        if key not in self._big:
            self._big[key] = pygame.transform.scale(self._small[key], (32, 32))
        return self._big[key]

    # ---- 对外接口 ----

    def cell(self, cell):
        """格子数据(栈里的一项 dict)→ 16×16 贴图。"""
        key, builder = self._plan(cell)
        return self._get(key, builder)

    def cell_big(self, cell):
        """同上,但返回 32×32 放大版(地图渲染用这个)。"""
        key, builder = self._plan(cell)
        self._get(key, builder)          # 先保证 16×16 原图已生成并缓存
        return self._scaled(key)

    def hero(self):
        if self._hero is None:
            self._hero = _paint("hero", SHAPES["hero"], {
                "B": (70, 110, 220), "D": (30, 50, 120), "L": (230, 230, 240),
                "A": (240, 200, 60), "E": (20, 20, 25),
            })
        return self._hero

    def hero_big(self):
        if not hasattr(self, "_hero_big"):
            self._hero_big = pygame.transform.scale(self.hero(), (32, 32))
        return self._hero_big

    # ---- 具体每一类怎么画 ----

    def _plan(self, cell):
        """根据 kind(+id)决定贴图:返回(缓存键, 构造函数)。"""
        kind = cell["kind"]

        if kind == "floor":
            return ("tile:floor", lambda: _ground((58, 62, 74), (44, 48, 58)))
        if kind == "wall":
            return ("tile:wall", lambda: _brick((105, 110, 125), (60, 62, 72)))
        if kind == "lava":
            return ("tile:lava", _lava)
        if kind == "stair":
            direction = cell.get("dir", "up")
            return (f"tile:stair:{direction}", lambda: _stair(direction))
        if kind == "star":
            return ("tile:star", _star_tile)
        if kind == "big_part":
            return ("tile:big_part", _big_part)
        if kind == "altar":                       # 祭坛(4/12/32/46 层)
            return ("tile:altar", _altar)
        if kind == "unknown":
            return ("tile:unknown", lambda: _ground((70, 60, 60), (50, 40, 40)))

        if kind == "door":
            did = cell.get("id")
            if did == 1004:
                return ("door:1004", _jail_door)
            if did == 1005:
                return ("door:1005", _monster_door)
            if did == 1006:                     # 墙门:和墙一样是砖,但配木门色
                return ("door:1006", lambda: _brick((140, 100, 60), (85, 60, 35)))
            color = KEY_COLORS.get(did, (200, 200, 80))
            return (f"door:{did}", lambda: _door(
                color, _shift(color, 0.45), (25, 22, 18)))

        if kind == "monster":
            mid = cell.get("id")
            entry = self.data["monsters"].get(str(mid))
            if entry is None:                    # 数据里没有的怪,画骷髅兜底
                entry = {"name": "未知怪", "attack": 0}
            shape = "skull"
            for word, name_shape in MONSTER_SHAPES:
                if word in entry["name"]:
                    shape = name_shape
                    break
            palette = monster_palette(entry["name"], entry.get("attack", 0))
            return (f"monster:{mid}",
                    lambda: _paint(f"monster:{mid}", SHAPES[shape], palette))

        if kind == "prop":
            pid = cell.get("id")
            entry = self.data["items"].get(str(pid), {})
            return (f"prop:{pid}", lambda: self._paint_item(pid, entry))

        if kind == "npc":
            ntype = self._npc_type(cell)
            palettes = {
                "elder": {"B": (110, 90, 160), "D": (60, 50, 90), "L": (235, 235, 235),
                          "A": (200, 170, 90), "E": (20, 20, 25)},
                "merchant": {"B": (170, 120, 60), "D": (100, 70, 35), "L": (240, 210, 80),
                             "A": (220, 60, 60), "E": (20, 20, 25)},
                "thief": {"B": (70, 70, 90), "D": (40, 40, 55), "L": (180, 170, 150),
                          "A": (150, 140, 120), "E": (230, 210, 120)},
                "princess": {"B": (235, 160, 190), "D": (160, 90, 120), "L": (250, 230, 240),
                             "A": (240, 200, 60), "E": (20, 20, 25)},
            }
            palette = palettes.get(ntype, palettes["elder"])
            return (f"npc:{ntype}",
                    lambda: _paint(f"npc:{ntype}", SHAPES.get(ntype, SHAPES["elder"]), palette))

        # 没见过的 kind:画个箱子兜底,保证渲染永不崩
        return ("tile:fallback", lambda: _paint("fallback", SHAPES["box"], {
            "B": (120, 120, 130), "D": (60, 60, 70), "L": (200, 200, 210),
            "A": (150, 150, 160), "E": (20, 20, 25),
        }))

    def _npc_type(self, cell):
        """NPC 格子只有 sprite_id(视觉编号),拿它去 NPC 表查类型。"""
        sid = cell.get("sprite_id")
        if sid is not None:
            entry = self.data["npcs"].get(str(sid))
            if entry:
                return entry.get("type", "elder")
        return "elder"

    def _paint_item(self, pid, entry):
        """道具按 kind + 数值选形状和配色。"""
        kind = entry.get("kind", "tool")
        gray = {"B": (180, 180, 195), "D": (90, 90, 105), "L": (245, 245, 250),
                "A": (150, 150, 165), "E": (20, 20, 25)}

        if kind == "key":
            color = KEY_COLORS.get(entry.get("door"), (240, 210, 60))
            return _paint(f"prop:{pid}", SHAPES["key"],
                          {"B": color, "D": _shift(color, 0.55), "L": _shift(color, 1.2),
                           "A": color, "E": (20, 20, 25)})
        if kind == "potion":
            color = POTION_COLORS.get(pid, (230, 60, 60))
            return _paint(f"prop:{pid}", SHAPES["potion"],
                          {"B": color, "D": _shift(color, 0.5), "L": (235, 220, 180),
                           "A": color, "E": (20, 20, 25)})
        if kind == "gem":
            color = GEM_COLORS.get(pid, (235, 70, 70))
            return _paint(f"prop:{pid}", SHAPES["gem"],
                          {"B": color, "D": _shift(color, 0.5), "L": (250, 250, 255),
                           "A": color, "E": (20, 20, 25)})
        if kind == "sword":
            tier = _tier_color(entry.get("attack", 10), 10)
            palette = dict(gray)
            palette["A"] = tier                     # 护手/柄用档位色
            return _paint(f"prop:{pid}", SHAPES["sword"], palette)
        if kind == "shield":
            tier = _tier_color(entry.get("defence", 10), 10)
            palette = {"A": tier, "B": (200, 200, 210), "D": (100, 100, 115),
                       "L": (250, 250, 255), "E": (20, 20, 25)}
            return _paint(f"prop:{pid}", SHAPES["shield"], palette)

        # 工具类:按 tool 字段挑形状
        tool = entry.get("tool", "")
        shapes = {
            "monster_manual": "book", "notebook": "book", "fly_wand": "wand",
            "pickaxe": "pickaxe", "quake_scroll": "scroll", "ice_magic": "snowflake",
            "bomb": "bomb", "magic_key": "key", "holy_water": "flask",
            "lucky_coin": "coin", "cross": "cross", "dragon_slayer": "dagger",
            "wing": "wing", "center_wing": "wing",
        }
        shape = shapes.get(tool, "box")
        if shape == "key":                         # 魔法黄钥:金钥匙
            palette = {"B": (250, 210, 70), "D": (170, 130, 30), "L": (255, 240, 160),
                       "A": (250, 210, 70), "E": (20, 20, 25)}
        elif shape == "book":
            palette = {"B": (235, 90, 90), "D": (150, 40, 40), "L": (245, 240, 220),
                       "A": (255, 255, 255), "E": (20, 20, 25)}
        elif shape == "flask":
            palette = {"B": (120, 220, 240), "D": (60, 140, 170), "L": (250, 250, 220),
                       "A": (120, 220, 240), "E": (20, 20, 25)}
        elif shape == "wing":
            color = (120, 200, 120) if tool != "center_wing" else (240, 200, 60)
            palette = {"B": color, "D": _shift(color, 0.6), "L": (240, 250, 255),
                       "A": (250, 210, 60), "E": (20, 20, 25)}
        else:
            palette = dict(gray)
        return _paint(f"prop:{pid}", SHAPES[shape], palette)
