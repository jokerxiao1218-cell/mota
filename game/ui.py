"""界面层:窗口布局、状态栏、地图、对话框/选择支、怪物手册、Esc 菜单。

只负责"照着传进来的数据画",不含任何游戏规则——
规则在 core、输入结算在 engine,这里纯渲染,方便单独冒烟测试。

窗口布局(总 576×472):
┌─────────┬──────────────┐
│ 状态栏   │   11×11 地图   │  高 352(格子 32px = 16×16 贴图放大 2 倍)
│ (224宽) │  (352×352)   │
├─────────┴──────────────┤
│  对话/选择/提示条(120高) │
└────────────────────────┘
"""

import pygame

# ---------------- 布局常量 ----------------
CELL = 32                 # 地图上一格的像素(16×16 贴图放大 2 倍)
GRID = 11                 # 原版地图 11×11
PANEL_W = 224             # 左侧状态栏宽
MAP_W = GRID * CELL       # 352
MAP_H = GRID * CELL       # 352
WIDTH = PANEL_W + MAP_W   # 576
DIALOG_H = 120            # 底部对话/提示条高
HEIGHT = MAP_H + DIALOG_H # 472
MAP_X = PANEL_W           # 地图区左上角 x

# ---------------- 配色 ----------------
BG = (16, 16, 24)
PANEL_BG = (30, 30, 44)
MAP_BG = (10, 10, 14)
DIALOG_BG = (24, 24, 36)
TEXT = (235, 235, 235)
GOLD = (245, 200, 90)
DIM = (150, 150, 160)
HP_RED = (235, 100, 100)
KEY_COLORS = {"yellow": (245, 210, 70), "blue": (90, 130, 240), "red": (230, 70, 70)}

DEFAULT_HINT = "方向键/WASD 移动  H 怪物手册  Esc 菜单"

# ---------------- 中文字体 ----------------
# pygame 自带字体没有汉字,找系统里的 CJK 字体;一台都找不到就退回默认字体
# (数字还能看,汉字会变方块,但游戏不崩)。
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
]
_font_cache = {}
_font_path = None


def get_font(size):
    """取指定字号的字体(缓存)。第一次调用时顺便初始化 pygame.font。"""
    global _font_path
    if not pygame.font.get_init():
        pygame.font.init()
    if _font_path is None:
        _font_path = ""                       # 空串表示用 pygame 默认字体
        for path in FONT_CANDIDATES:
            try:
                pygame.font.Font(path, 12)
                _font_path = path
                break
            except Exception:
                continue
    if size not in _font_cache:
        _font_cache[size] = pygame.font.Font(_font_path or None, size)
    return _font_cache[size]


def text_img(text, size, color=TEXT):
    return get_font(size).render(str(text), True, color)


def wrap_text(text, size, max_width):
    """中文没有空格,按字符一个个往行里塞,塞不下就换行。"""
    font = get_font(size)
    lines, current = [], ""
    for ch in str(text):
        if font.size(current + ch)[0] <= max_width:
            current += ch
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines or [""]


# ---------------- 窗口 ----------------

def create_screen():
    """创建游戏窗口(SDL dummy 环境下也能拿到 Surface,方便无头测试)。"""
    return pygame.display.set_mode((WIDTH, HEIGHT))


# ---------------- 主画面 ----------------

def draw_frame(screen, hud, view, assets):
    """一帧主画面:左侧状态栏 + 右侧地图 + 底部条背景。

    hud  = 引擎整理好的状态栏字段(纯 dict,见 engine._hud);
    view = 11×11 的"每格最上层可见物"(None = 空地)。
    """
    screen.fill(BG)

    # ---- 左侧状态栏 ----
    panel = pygame.Rect(0, 0, PANEL_W, MAP_H)
    pygame.draw.rect(screen, PANEL_BG, panel)
    pygame.draw.line(screen, (70, 70, 95), (PANEL_W - 1, 0), (PANEL_W - 1, MAP_H), 2)

    screen.blit(text_img("魔塔 50 层", 24, GOLD), (14, 10))
    y = 52
    for label, value, color in (
        ("生命", hud.get("hp", 0), HP_RED),
        ("攻击", hud.get("attack", 0), TEXT),
        ("防御", hud.get("defence", 0), TEXT),
        ("金币", hud.get("gold", 0), GOLD),
    ):
        screen.blit(text_img(f"{label}", 16, DIM), (14, y))
        screen.blit(text_img(value, 18, color), (70, y - 2))
        y += 28

    y += 6
    keys = hud.get("keys", {})
    for name, ch in (("yellow", "黄"), ("blue", "蓝"), ("red", "红")):
        color = KEY_COLORS[name]
        pygame.draw.rect(screen, color, (16, y + 3, 14, 14), border_radius=3)
        screen.blit(text_img(f"{ch}钥匙 x{keys.get(name, 0)}", 16), (38, y))
        y += 24

    y += 8
    screen.blit(text_img(f"楼层  第{hud.get('floor', 1)}层", 18, GOLD), (14, y))
    y += 30
    sword = hud.get("sword")
    shield = hud.get("shield")
    screen.blit(text_img(f"武器  {sword or '空手'}", 15, TEXT if sword else DIM), (14, y))
    y += 22
    screen.blit(text_img(f"盾牌  {shield or '没有'}", 15, TEXT if shield else DIM), (14, y))
    y += 28

    screen.blit(text_img("道具", 15, DIM), (14, y))
    y += 22
    for name in hud.get("props", [])[:8]:       # 最多列 8 个,多了截断
        screen.blit(text_img(name, 14), (22, y))
        y += 18

    # ---- 右侧地图 ----
    pygame.draw.rect(screen, MAP_BG, (MAP_X, 0, MAP_W, MAP_H))
    floor_tile = assets.cell_big({"kind": "floor"})
    hero_pos = hud.get("pos")
    for gy in range(GRID):
        for gx in range(GRID):
            rect = (MAP_X + gx * CELL, gy * CELL)
            screen.blit(floor_tile, rect)
            cell = view[gy][gx]
            if cell is not None:
                screen.blit(assets.cell_big(cell), rect)
    if hero_pos is not None:
        screen.blit(assets.hero_big(),
                    (MAP_X + hero_pos[0] * CELL, hero_pos[1] * CELL))

    # 地图描一圈边
    pygame.draw.rect(screen, (70, 70, 95), (MAP_X, 0, MAP_W, MAP_H), 2)

    # ---- 底部条背景 ----
    pygame.draw.rect(screen, DIALOG_BG, (0, MAP_H, WIDTH, DIALOG_H))
    pygame.draw.line(screen, (70, 70, 95), (0, MAP_H), (WIDTH, MAP_H), 2)


# ---------------- 底部对话/提示 ----------------

def draw_hint(screen, text):
    """普通状态下底部显示一条提示(捡到东西/打不开门等)。"""
    lines = wrap_text(text, 16, WIDTH - 24)[:5]
    y = MAP_H + 10
    for line in lines:
        screen.blit(text_img(line, 16), (12, y))
        y += 20
    screen.blit(text_img(DEFAULT_HINT, 13, DIM), (12, HEIGHT - 20))


def draw_chat(screen, lines):
    """对话:intent {"op":"chat","lines":[...]} 的渲染,按键一次推进整个 chat。"""
    flat = []                                    # 把嵌套的行也摊平
    for line in lines:
        if isinstance(line, list):
            flat.extend(str(x) for x in line)
        else:
            flat.append(str(line))
    shown = []
    for line in flat:
        shown.extend(wrap_text(line, 16, WIDTH - 30))
    shown = shown[-5:]                           # 只留最后 5 行
    y = MAP_H + 8
    for line in shown:
        screen.blit(text_img(line, 16), (12, y))
        y += 20
    screen.blit(text_img("▼ 空格/回车 继续", 13, DIM), (WIDTH - 130, HEIGHT - 20))


def draw_choices(screen, options, sel):
    """选择支:intent {"op":"choices","options":[{"label":...},...]}。"""
    y = MAP_H + 6
    screen.blit(text_img("请选择:", 16, GOLD), (12, y))
    y += 24
    for i, opt in enumerate(options):
        label = opt.get("label", f"选项{i + 1}") if isinstance(opt, dict) else str(opt)
        color = TEXT if i == sel else DIM
        cursor = "▶" if i == sel else "  "
        screen.blit(text_img(f"{cursor} {i + 1}. {label}", 16, color), (16, y))
        y += 22
    screen.blit(text_img("↑↓ 选择,回车/空格 确认", 13, DIM), (12, HEIGHT - 20))


# ---------------- 覆盖层:菜单 / 手册 / 死亡 ----------------

def _overlay(screen, rect, alpha=225):
    """半透明黑底,把菜单垫在地图上面。"""
    veil = pygame.Surface(rect.size, pygame.SRCALPHA)
    veil.fill((0, 0, 0, alpha))
    screen.blit(veil, rect.topleft)
    pygame.draw.rect(screen, (120, 120, 150), rect, 2)


def draw_menu(screen, menu):
    """通用列表菜单(Esc 菜单、存档槽位都用它)。

    menu = {"title": str, "options": [str...], "sel": int}
    """
    n = len(menu.get("options", []))
    w, h = 280, 46 + n * 30
    x = MAP_X + (MAP_W - w) // 2
    y = (MAP_H - h) // 2
    _overlay(screen, pygame.Rect(x, y, w, h))
    screen.blit(text_img(menu.get("title", ""), 18, GOLD), (x + 16, y + 10))
    for i, opt in enumerate(menu.get("options", [])):
        color = TEXT if i == menu.get("sel", 0) else DIM
        cursor = "▶" if i == menu.get("sel", 0) else "  "
        screen.blit(text_img(f"{cursor}{opt}", 17, color), (x + 18, y + 44 + i * 30))
    screen.blit(text_img("↑↓ 选择,回车 确认,Esc 返回", 12, DIM), (x + 10, y + h - 22))


def draw_manual(screen, rows, sel):
    """怪物手册面板。rows 由引擎算好(含预测损血),这里只管画。

    row = {"name","hp","attack","defence","gold","verdict","turns"}
    """
    rect = pygame.Rect(MAP_X + 8, 8, MAP_W - 16, MAP_H - 16)
    _overlay(screen, rect)
    screen.blit(text_img("怪物手册", 18, GOLD), (rect.x + 12, rect.y + 8))
    screen.blit(text_img("↑↓ 查看,Esc/H 关闭", 12, DIM), (rect.x + 130, rect.y + 13))
    header = f"{'名字':<6}{'生命':>5}{'攻':>5}{'防':>5}{'金币':>5}  预测"
    screen.blit(text_img(header, 14, DIM), (rect.x + 12, rect.y + 38))

    if not rows:
        screen.blit(text_img("本层没有怪物", 15), (rect.x + 12, rect.y + 64))
        return
    page = 11                                    # 一屏最多 11 行
    first = max(0, min(sel - page + 1, len(rows) - page)) if len(rows) > page else 0
    visible = rows[first:first + page]
    for i, row in enumerate(visible):
        idx = first + i
        color = TEXT if idx == sel else DIM
        line = (f"{row['name']:<6}{row['hp']:>6}{row['attack']:>5}"
                f"{row['defence']:>5}{row['gold']:>6}  {row['verdict']}")
        screen.blit(text_img(line, 14, color), (rect.x + 12, rect.y + 62 + i * 24))


def draw_gameover(screen):
    """死亡画面:红罩 + 提示。"""
    veil = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    veil.fill((120, 0, 0, 140))
    screen.blit(veil, (0, 0))
    img = text_img("你死了……", 42, (255, 220, 220))
    screen.blit(img, img.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 20)))
    img2 = text_img("按 Esc 退出游戏", 16, (255, 200, 200))
    screen.blit(img2, img2.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 30)))
