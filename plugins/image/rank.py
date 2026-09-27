# -*- coding: utf-8 -*-
"""图片响应模块 · 排行榜渲染器。自 2.3.0 modules/rank.py _render_rank_image 原样迁移（纯函数化）：
每行「<名次> <用户名/宠物名> [进度条] <积分(右对齐)>」。
rows: [(rank, name, score_str, is_me, ratio, masked), ...]（已按积分降序、取前 N 名）；
ratio：进度条填充比例（第一名恒为 1.0，其它 = 积分/第一名积分，0~1）；
masked：该行是否为非本群（脱敏）用户。
文字颜色三态：高亮自己 hl_color（调用方传入，来自运行参数 RANK_HIGHLIGHT_COLOR）/
脱敏行 RANK_MASKED_COLOR / 普通行 RANK_TEXT_COLOR；
进度条颜色 = 文字颜色浅 RANK_BAR_LIGHTEN；分割线颜色 RANK_SEP_COLOR，高度 = 行高 × RANK_ROW_SEP_PCT（默认 3%）。
名字显示固定 RANK_NAME_MAX_CHARS 个字符，超出部分用 ... 代替；
进度条左侧留白 = 基准 12px × RANK_BAR_GAP_LEFT_MULT（默认 200%）、右侧 = × RANK_BAR_GAP_RIGHT_MULT（默认 300%）；
每行文字行高固定 line_h、分割线紧跟行底，文字与分割线间距一致；
图片宽度 = 内容宽度 × RANK_IMAGE_SCALE（默认 2.5 = 原来的 250%），高度自适应。"""
import math

from ...core import (RANK_IMAGE_SCALE, RANK_BAR_LIGHTEN, RANK_NAME_MAX_CHARS,
                     RANK_BAR_GAP_LEFT_MULT, RANK_BAR_GAP_RIGHT_MULT, RANK_ROW_SEP_PCT,
                     RANK_TEXT_COLOR, RANK_MASKED_COLOR, RANK_SEP_COLOR)
from .common import (DS_BG, DS_SURFACE_2, DS_BORDER, DS_ACCENT, DS_TEXT, DS_MUTED,
                     ensure_pillow, load_fonts, title_font, text_measurer,
                     draw_underlined_title, dtext, save_temp_image, img_ratio_min)

__all__ = ["render_rank"]


def _parse_hex_color(s, default=(55, 86, 35)):
    """#RRGGBB → (r, g, b)；非法值返回默认色（默认 #375623）"""
    s = str(s or "").strip().lstrip("#")
    if len(s) == 6:
        try:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return default


def _lighten_color(color, pct=0.2):
    """颜色向白色方向浅化 pct（0.2 = 浅 20%）：progress = c + (255 - c) * pct"""
    pct = max(0.0, min(0.9, float(pct)))
    return tuple(int(c + (255 - int(c)) * pct) for c in color)


def _fit_name(name, max_chars=None):
    """排行榜名字显示长度固定：超过 max_chars（默认 RANK_NAME_MAX_CHARS，6）个字符时，
    无法显示的部分用 ... 代替（如「超长名字测试用」→「超长名字测...」）。"""
    if max_chars is None:
        max_chars = int(RANK_NAME_MAX_CHARS or 6)
    disp = str(name)
    if max_chars >= 1 and len(disp) > max_chars:
        disp = disp[:max_chars] + "..."
    return disp


def render_rank(title, rows, hl_color, force_width=None):
    """排行榜图片（渲染环境不可用时返回 None，调用方回退文本）。"""
    Image, ImageDraw = ensure_pillow()
    if Image is None:
        return None
    # 标题 42（衬线）/ 正文 24
    title_font_ = title_font(kind="rank")
    fonts = load_fonts(24)
    if title_font_ is None or fonts is None:
        return None
    body_font = fonts[0]
    tw = text_measurer()
    if tw is None:
        return None

    pad = 24
    title_h = 82
    line_h = 40
    bar_h = 12          # 进度条高度
    rank_col = 64       # 名次列宽
    gap_base = 12.0     # 进度条留白基准（左右各 12px 为“原先的”）
    scale = float(RANK_IMAGE_SCALE or 2.5)
    lighten = float(RANK_BAR_LIGHTEN or 0.2)
    name_max_chars = int(RANK_NAME_MAX_CHARS or 6)
    gap_left = gap_base * float(RANK_BAR_GAP_LEFT_MULT or 2.0)
    gap_right = gap_base * float(RANK_BAR_GAP_RIGHT_MULT or 3.0)
    sep_pct = float(RANK_ROW_SEP_PCT or 0.03)
    text_color_s = _parse_hex_color(RANK_TEXT_COLOR, DS_TEXT)
    masked_color_s = _parse_hex_color(RANK_MASKED_COLOR, DS_MUTED)
    sep_color = _parse_hex_color(RANK_SEP_COLOR, DS_BORDER)

    prepared = []
    score_w_max = 0
    name_w_max = 0
    for rank, name, score_str, is_me, ratio, masked in rows:
        # 名字显示长度固定：超过 name_max_chars 个字符的部分用 ... 代替
        disp = _fit_name(name, name_max_chars)
        sw = tw(score_str, body_font)
        score_w_max = max(score_w_max, sw)
        name_w_max = max(name_w_max, tw(disp, body_font))
        prepared.append((rank, disp, score_str, is_me, ratio, masked))

    # 宽度 = 内容宽度 × 倍数（默认 250%）；高度 = 标题 + 行 × 行高 + 行间分割线高度（间距恒定）
    base_w = pad * 2 + rank_col + 8 + name_w_max + gap_left + score_w_max
    width = force_width or int(base_w * scale)
    sep_h = int(line_h * max(0.0, min(0.5, sep_pct)))
    n = len(prepared)
    height = int(pad * 2 + title_h + line_h * n + sep_h * max(0, n - 1))
    # 2.2.6：过窄图片（低于比例下限，如展示名次较多时）按高度自动加宽重排，
    # 进度条与分割线随之拉长（高度不随宽度变化，一轮收敛）
    rmin = img_ratio_min()
    if force_width is None and rmin and height > width / rmin:
        return render_rank(title, rows, hl_color, force_width=math.ceil(height * rmin))

    score_x = width - pad                  # 积分右对齐的右边缘
    name_x = pad + rank_col + 8            # 名字列起点（分割线也从这里开始）
    bar_x0 = int(name_x + name_w_max + gap_left)
    bar_x1 = int(score_x - score_w_max - gap_right)
    if bar_x1 - bar_x0 < 12:
        bar_x1 = bar_x0 + 12              # 极小图兜底：进度条至少 12px
    sep_x1 = int(score_x)                  # 分割线右端 = 积分右边缘

    img = Image.new("RGB", (width, height), DS_BG)
    d = ImageDraw.Draw(img)
    draw_underlined_title(d, (int(pad), int(pad)), title, title_font_, color=DS_ACCENT,
                          width=width - pad * 2, gap=10)
    y = pad + title_h
    hl = _parse_hex_color(hl_color)
    bar_w = bar_x1 - bar_x0
    for idx, (rank, disp, score_str, is_me, ratio, masked) in enumerate(prepared):
        # 文字颜色三态：高亮自己 / 脱敏行 / 普通行
        if is_me:
            text_color = hl
        elif masked:
            text_color = masked_color_s
        else:
            text_color = text_color_s
        bar_color = _lighten_color(text_color, lighten)
        dtext(d, (int(pad), int(y)), str(rank), font=body_font, fill=text_color)
        dtext(d, (int(name_x), int(y)), disp, font=body_font, fill=text_color)
        # 进度条：轨道米金描边 + 填充色（文字浅 20%，高亮行即高亮色浅 20%）
        bar_y = y + (line_h - bar_h) // 2
        d.rectangle([bar_x0, bar_y, bar_x1, bar_y + bar_h], fill=DS_SURFACE_2, outline=DS_BORDER)
        fill_w = int(bar_w * max(0.0, min(1.0, ratio)))
        if fill_w > 0:
            d.rectangle([bar_x0 + 1, bar_y + 1, bar_x0 + fill_w, bar_y + bar_h - 1], fill=bar_color)
        dtext(d, (int(score_x - tw(score_str, body_font)), int(y)), score_str, font=body_font, fill=text_color)
        y += line_h
        # 行间分割线（仅 用户名 → 积分 范围）：高度 = 行高 × RANK_ROW_SEP_PCT（默认 3%），
        # 紧跟文字行底，文字与分割线间距对所有行一致
        # 注意 Pillow rectangle 坐标含端点，绘制 [y, y+sep_h-1] 使实际高度精确 = sep_h
        if idx < n - 1 and sep_h > 0:
            d.rectangle([int(name_x), int(y), sep_x1, int(y + sep_h - 1)], fill=sep_color)
            y += sep_h
    return save_temp_image(img, "_rank_", "排行榜")
