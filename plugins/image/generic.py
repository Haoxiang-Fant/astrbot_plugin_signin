# -*- coding: utf-8 -*-
"""图片响应模块 · 通用渲染器：文本图 / 富文本图 / 快照瀑布卡片 / 帮助菜单。
图片格式规范：标题思源宋体 Bold（衬线）+ 页头分隔线，正文 OPPOSans；
返回 ("image", path)，渲染环境不可用时返回 None（调用方回退纯文本）。"""
import math

from .common import (DS_BG, DS_BORDER, DS_ACCENT, DS_GOLD, DS_TEXT, DS_TEXT_2, DS_MUTED,
                     ensure_pillow, load_fonts, title_font, text_measurer, make_wrapper,
                     wrap_rich_rows, draw_underlined_title, save_temp_image, dtext, img_ratio_min)


def text(title, lines, force_width=None):
    """标题 + 正文行 → PNG。正文按最大内容宽自动换行；过窄时按高度自动加宽重排。"""
    Image, ImageDraw = ensure_pillow()
    if Image is None:
        return None
    title_font_ = title_font(kind="list")
    fonts = load_fonts(24)
    if title_font_ is None or fonts is None:
        return None
    body_font = fonts[0]

    pad = 30
    title_h = 80
    line_h = 42

    tw = text_measurer()
    if tw is None:
        width = force_width or 720
        disp = list(lines)
    else:
        limit = (force_width - pad * 2) if force_width else 840
        wrap = make_wrapper(tw, limit)
        disp = []
        for line in lines:
            disp.extend(wrap(line, body_font))
        all_w = [tw(title, title_font_)] + [tw(l, body_font) for l in disp]
        width = force_width or max(480, min(int(max(all_w) + pad * 2), 900))

    height = pad * 2 + title_h + line_h * len(disp)
    rmin = img_ratio_min()
    if force_width is None and tw is not None and rmin and height > width / rmin:
        return text(title, lines, force_width=math.ceil(height * rmin))
    img = Image.new("RGB", (width, height), DS_BG)
    draw = ImageDraw.Draw(img)
    y = pad
    draw_underlined_title(draw, (pad, y), title, title_font_, color=DS_ACCENT,
                          width=width - pad * 2, gap=10)
    y += title_h
    for line in disp:
        draw.text((pad, y), line, font=body_font, fill=DS_TEXT_2)
        y += line_h
    return save_temp_image(img, "_list_", "")


def rich(title, rows, force_width=None):
    """富文本图：rows 每行是 (text, color, strike) 段列表。"""
    Image, ImageDraw = ensure_pillow()
    if Image is None:
        return None
    title_font_ = title_font(kind="rich")
    fonts = load_fonts(24)
    if title_font_ is None or fonts is None:
        return None
    body_font = fonts[0]
    pad = 26
    title_h = 76
    line_h = 40
    sw = text_measurer()
    if sw is None:
        return None

    limit = (force_width - pad * 2) if force_width else 840
    disp = wrap_rich_rows(rows, make_wrapper(sw, limit), sw, body_font, limit)
    max_w = sw(title, title_font_)
    for r in disp:
        max_w = max(max_w, sum(sw(s[0], body_font) for s in r))
    width = force_width or max(460, min(int(max_w + pad * 2), 920))
    height = pad * 2 + title_h + line_h * len(disp)
    rmin = img_ratio_min()
    if force_width is None and rmin and height > width / rmin:
        return rich(title, rows, force_width=math.ceil(height * rmin))

    img = Image.new("RGB", (width, height), DS_BG)
    d = ImageDraw.Draw(img)
    draw_underlined_title(d, (pad, pad), title, title_font_, color=DS_ACCENT,
                          width=width - pad * 2, gap=10)
    y = pad + title_h
    for r in disp:
        x = pad
        for seg in r:
            txt, color, strike = seg
            d.text((int(x), y), txt, font=body_font, fill=color)
            if strike:
                bb = d.textbbox((int(x), y), txt, font=body_font)
                midy = (int(bb[1]) + int(bb[3])) // 2
                d.line([(int(bb[0]), midy), (int(bb[2]), midy)], fill=color, width=2)
            x += sw(txt, body_font)
        y += line_h
    return save_temp_image(img, "_farm_", "农场")


def snapshot(title, modules, width=900, pad=20, title_h=64, line_h=30, mod_gap=14,
             col_gap=12, header_right=None, _depth=0):
    """信息流瀑布平铺（签到快照/帮助/活动等共用卡片渲染）。
    modules 每项为卡片 (标题, 行列表, 高亮?) 或并排行 [卡1, 卡2, (w1, w2)]；
    内容行支持 (文本, 颜色[, 边框高亮色]) 与 ("__bar__", 比例, 填充色, 标签)。"""
    Image, ImageDraw = ensure_pillow()
    if Image is None:
        return None
    fonts = load_fonts(24, 20, 18)
    if fonts is None:
        return None
    mod_font, body_font, small_font = fonts
    title_font_ = title_font(kind="snapshot")
    if title_font_ is None:
        return None
    tw = text_measurer()
    if tw is None:
        return None
    content_w = width - pad * 2
    inner = 10
    mod_title_h = 34
    bar_h = 16
    wrap = make_wrapper(tw, content_w - inner * 2)
    BG = DS_BG
    HL = DS_GOLD
    NORMAL = DS_BORDER
    TITLE_DARK = DS_ACCENT
    MOD_TITLE = DS_TEXT
    TEXT = DS_TEXT_2
    TITLE_UNDERLINE_GAP = 12

    # 规范化：modules → 行列表（并排行可带列宽权重元组）
    lines = []
    for m in modules:
        if isinstance(m, (list, tuple)) and m and isinstance(m[0], (list, tuple)):
            row = list(m)
            weights = None
            if row and isinstance(row[-1], tuple) and len(row[-1]) == len(row) - 1 \
                    and all(isinstance(w, (int, float)) for w in row[-1]):
                weights = row.pop()
            lines.append((row, weights))
        else:
            lines.append(([m], None))

    col_widths = []
    for row, weights in lines:
        n = len(row)
        avail = content_w - col_gap * (n - 1)
        if weights and len(weights) == n:
            total_w = float(sum(weights))
            ws = []
            used = 0
            for i, w in enumerate(weights):
                if i == n - 1:
                    ws.append(avail - used)
                else:
                    cw = int(avail * w / total_w)
                    ws.append(cw)
                    used += cw
            col_widths.append(ws)
        else:
            col_widths.append([avail // n] * n)

    def wrap_pipe(text_, font, max_w):
        """含「｜」按段整体换行（段内不切开）；否则字符级 fill 换行"""
        if "｜" not in text_:
            return [wl for wl in wrap(text_, font, max_w)]
        segs = text_.split("｜")
        out = []
        cur = ""
        for seg in segs:
            piece = ("｜" + seg) if cur else seg
            if tw(cur + piece, font) <= max_w:
                cur += piece
                continue
            if cur:
                out.append(cur)
            if tw(seg, font) > max_w:
                cur = ""
                for ch in seg:
                    if tw(cur + ch, font) <= max_w and cur:
                        cur += ch
                    else:
                        if cur:
                            out.append(cur)
                        cur = ch
            else:
                cur = seg
        if cur:
            out.append(cur)
        return out

    # 第一遍：计算每行卡片高度（行内等高）
    boxes = []
    total_h = pad * 2 + title_h + TITLE_UNDERLINE_GAP
    for (row, weights), col_ws in zip(lines, col_widths):
        line_plan = []
        line_h_max = 0
        for (mtitle, rows, hl), col_w in zip(row, col_ws):
            inner_w = col_w - inner * 2
            mod_rows = []
            h = inner * 2 + mod_title_h
            for item in rows:
                if isinstance(item, (list, tuple)) and len(item) >= 2 and item[0] == "__bar__":
                    ratio = max(0.0, min(1.0, float(item[1])))
                    color = item[2] if len(item) > 2 else (52, 168, 83)
                    label = str(item[3]) if len(item) > 3 else ""
                    mod_rows.append(("__bar__", ratio, color, label))
                    h += line_h
                    continue
                txt, color = item[0], item[1]
                hl_color = item[2] if len(item) > 2 else (HL if hl else NORMAL)
                font = body_font
                if tw(txt, body_font) > inner_w:
                    for wl in wrap_pipe(txt, body_font, inner_w):
                        mod_rows.append((wl, color, font, hl_color))
                        h += line_h
                else:
                    mod_rows.append((txt, color, font, hl_color))
                    h += line_h
            line_plan.append((h, mod_rows))
            line_h_max = max(line_h_max, h)
        boxes.append((line_h_max, line_plan))
        total_h += line_h_max + mod_gap
    total_h += pad

    img = Image.new("RGB", (width, total_h), BG)
    d = ImageDraw.Draw(img)
    y = pad
    dtext(d, (pad, y), title, font=title_font_, fill=TITLE_DARK)
    if header_right:
        rt_text, rt_color = header_right
        dtext(d, (int(width - pad - tw(rt_text, small_font)), y + (title_h - 20) // 2),
              rt_text, font=small_font, fill=rt_color)
    y += title_h - 6
    d.line([(pad, y), (width - pad, y)], fill=TITLE_DARK, width=2)
    y += 6 + TITLE_UNDERLINE_GAP
    for (row, weights), col_ws, (line_h_max, line_plan) in zip(lines, col_widths, boxes):
        for ci, ((mtitle, rows, hl), (h, mod_rows)) in enumerate(zip(row, line_plan)):
            x0 = pad + sum(col_ws[:ci]) + col_gap * ci
            x1 = x0 + col_ws[ci]
            border = HL if hl else NORMAL
            d.rectangle([x0, y, x1, y + line_h_max], fill=(255, 255, 255),
                        outline=border, width=(2 if hl else 1))
            yy = y + (line_h_max - h) // 2 + inner
            dtext(d, (x0 + inner, yy), mtitle, font=mod_font, fill=MOD_TITLE)
            yy += mod_title_h
            for item in mod_rows:
                if item[0] == "__bar__":
                    ratio, color, label = item[1], item[2], item[3]
                    bar_y = yy + (line_h - bar_h) // 2
                    bx0, bx1 = x0 + inner, x1 - inner
                    d.rectangle([bx0, bar_y, bx1, bar_y + bar_h], fill=(255, 255, 255),
                                outline=NORMAL, width=1)
                    fw = int((bx1 - bx0 - 2) * ratio)
                    if fw > 0:
                        d.rectangle([bx0 + 1, bar_y + 1, bx0 + 1 + fw, bar_y + bar_h - 1], fill=color)
                    if label:
                        dtext(d, (int(bx1 - tw(label, small_font)), bar_y - 4),
                              label, font=small_font, fill=color)
                    yy += line_h
                else:
                    txt, color, font, hl_color = item
                    dtext(d, (x0 + inner, yy), txt, font=font, fill=color)
                    yy += line_h
        y += line_h_max + mod_gap
    rmin = img_ratio_min()
    if _depth == 0 and rmin and total_h > width / rmin:
        return snapshot(title, modules, width=math.ceil(total_h * rmin), pad=pad,
                        title_h=title_h, line_h=line_h, mod_gap=mod_gap,
                        col_gap=col_gap, header_right=header_right, _depth=1)
    return save_temp_image(img, "_snap_", "数据快照")


def help_menu(title, sections):
    """帮助菜单：每个模块一张独立卡片（复用快照渲染器）。sections: [(标题, [(指令, 说明), ...])]"""
    modules = []
    for header, items in sections:
        if not header or not items:
            continue
        rows = [(f"{cmd}：{desc}", DS_TEXT_2) for cmd, desc in items]
        modules.append((str(header), rows, False))
    if not modules:
        return None
    return snapshot(title, modules)


def build_help(title, sections):
    """帮助图（渲染失败回退纯文本）：返回 ("image", path) 或 str"""
    img = help_menu(title, sections)
    if img is not None:
        return img
    lines = [f"{title}："]
    for header, items in sections:
        if header:
            lines.append(f"【{header}】")
        for cmd, desc in items:
            lines.append(f"{cmd}：{desc}")
    return "\n".join(lines)
