# -*- coding: utf-8 -*-
"""图片响应模块 · 渲染基础设施：字体 / 设计系统配色 / 像素换行 / 临时图片存盘。
自 2.3.0 base.py 渲染层原样迁移（温暖简约和风：米白暖底 / 白卡片 / 深林绿 / 校徽金）。"""
import math
import os
import re
from datetime import datetime

from astrbot.api import logger

from ...core import (FONT_FILE, TITLE_FONT_FILE, TEMP_IMAGE_DIR, TEMP_IMAGE_TTL,
                     MIN_IMG_RATIO, MAX_IMG_RATIO)

_FONT_CACHE = {}


def load_fonts(*sizes, font_file=FONT_FILE):
    """按字号批量加载字体（带进程内缓存）。Pillow 缺失、字体缺失或失败时返回 None。"""
    try:
        from PIL import ImageFont
    except Exception as e:
        logger.error(f"[图片] 缺少 Pillow，无法生成图片: {e}")
        return None
    if not os.path.exists(font_file):
        logger.error(f"[图片] 字体文件不存在: {font_file}")
        return None
    fonts = []
    for size in sizes:
        key = (font_file, size)
        font = _FONT_CACHE.get(key)
        if font is None:
            try:
                font = ImageFont.truetype(font_file, size)
            except Exception as e:
                logger.error(f"[图片] 加载字体 {font_file} 失败: {e}")
                return None
            _FONT_CACHE[key] = font
        fonts.append(font)
    return tuple(fonts)


# ================= 全局图片设计系统（与 WebUI style.css 同源） =================
DS_BG = (252, 252, 250)             # 页面背景 米白 #fcfcfa
DS_SURFACE = (255, 255, 255)        # 卡片/面板 白 #ffffff
DS_SURFACE_2 = (251, 250, 245)      # 次级表面 #fbfaf5
DS_TEXT = (34, 50, 42)              # 主文本 深林绿黑 #22322a
DS_TEXT_2 = (63, 74, 64)            # 次级文本 #3f4a40
DS_MUTED = (101, 113, 95)           # 说明/占位 #65715f
DS_BORDER = (230, 226, 210)         # 常规描边 米金 #e6e2d2
DS_BORDER_2 = (217, 211, 191)       # 强调描边 #d9d3bf
DS_ACCENT = (31, 95, 62)            # 深林绿 #1f5f3e
DS_ACCENT_STRONG = (22, 71, 44)     # 深林绿加深 #16472c
DS_GOLD = (214, 161, 26)            # 校徽金 #d6a11a
DS_GOLD_2 = (226, 176, 42)          # 校徽金亮 #e2b02a
DS_DANGER = (179, 57, 46)           # 危险红 #b3392e
DS_DANGER_STRONG = (143, 43, 34)    # 危险红加深 #8f2b22
DS_SUCCESS = (44, 122, 80)          # 成功绿 #2c7a50
DS_GREEN_SOFT = (128, 160, 110)     # 柔和鼠尾草绿（属性条/空闲进度填充）
DS_BLUE = (52, 88, 132)             # 提示蓝（深调）
DS_TITLE_SIZES = {"list": 42, "rich": 40, "rank": 42, "snapshot": 36, "pet": 36,
                  "shop": 36, "bag": 36, "farm": 36, "activity": 36, "work": 36}


def title_font(size=None, kind="list"):
    """标题衬线字体（思源宋体 Bold，size 缺省按 kind 取档位）；缺失时回退 OPPOSans。"""
    if size is None:
        size = int(DS_TITLE_SIZES.get(kind, 36))
    for ff in (TITLE_FONT_FILE, FONT_FILE):
        t = load_fonts(size, font_file=ff)
        if t is not None:
            return t[0]
    return None


def draw_underlined_title(d, xy, title, font, color=DS_ACCENT, width=None, gap=8, line_w=2):
    """标题 + 下方页头分隔线。返回分隔线 y 坐标。"""
    x, y = xy
    d.text((int(x), int(y)), title, font=font, fill=color)
    ly = int(y) + int(font.size * 1.25) + gap
    d.line([(int(x), ly), (int(x) + (width if width is not None else 200), ly)],
           fill=color, width=line_w)
    return ly


def text_measurer():
    """返回像素宽度测量函数 tw(text, font)；Pillow 不可用时返回 None"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def tw(s, font):
        return probe.textlength(s, font=font)

    return tw


def ensure_pillow():
    """尝试导入 Pillow 的 Image 和 ImageDraw；不可用时返回 (None, None)"""
    try:
        from PIL import Image, ImageDraw
        return Image, ImageDraw
    except Exception as e:
        logger.error(f"[图片] 缺少 Pillow，无法生成图片: {e}")
        return None, None


def make_wrapper(tw, default_width, mode="fill"):
    """生成按像素宽度换行的函数（fill=空格断行+满宽填充；word=仅空格断行）。"""
    if mode == "word":
        def wrap(text, font, max_w=None):
            limit = default_width if max_w is None else max_w
            lines = []
            for word in text.split(" "):
                if not word:
                    continue
                if not lines:
                    lines.append(word)
                elif tw(lines[-1] + " " + word, font) <= limit:
                    lines[-1] += " " + word
                else:
                    if tw(word, font) > limit and lines:
                        cur = ""
                        for ch in word:
                            if tw(cur + ch, font) > limit and cur:
                                lines.append(cur)
                                cur = ch
                            else:
                                cur += ch
                        if cur:
                            lines.append(cur)
                    else:
                        lines.append(word)
            return [s for s in (clean_img_text(x) for x in lines) if s] or [""]

        return wrap

    def wrap(text, font, max_w=None):
        limit = default_width if max_w is None else max_w
        lines = []
        cur = ""
        for ch in text:
            if tw(cur + ch, font) <= limit:
                cur += ch
                continue
            sp = cur.rfind(" ")
            if sp > 0:
                tail = cur[sp + 1:]
                if tail and tw(cur[:sp] + " " + tail[0], font) <= limit:
                    i = 0
                    n = len(tail)
                    while i < n:
                        if tail[i].isalnum() and tail[i].isascii():
                            j = i
                            while j < n and tail[j].isalnum() and tail[j].isascii():
                                j += 1
                            seg = tail[i:j]
                        else:
                            seg = tail[i]
                            j = i + 1
                        if tw(cur[:sp] + " " + tail[:i] + seg, font) > limit:
                            break
                        i = j
                    if i > 0:
                        lines.append(cur[:sp] + " " + tail[:i])
                        cur = tail[i:] + ch
                        continue
                lines.append(cur[:sp])
                cur = tail + ch
                continue
            if cur:
                lines.append(cur)
            cur = ch
        if cur:
            lines.append(cur)
        return [s for s in (clean_img_text(x) for x in lines) if s] or [""]

    return wrap


def wrap_rich_rows(rows, wrap, sw, font, limit):
    """富文本行流式换行：rows 每行为 (text, color, strike) 段列表，颜色/删除线随段保留。"""
    out = []
    for r in rows:
        cur, cur_w = [], 0
        for text, color, strike in r:
            for piece in wrap(text, font, limit):
                w = sw(piece, font)
                if cur and cur_w + w > limit:
                    out.append(cur)
                    cur, cur_w = [], 0
                cur.append((piece, color, strike))
                cur_w += w
        out.append(cur)
    return out


def img_ratio_min():
    """输出图片最小长宽比（宽/高）；≤0 / 非法时返回 None（不限制）"""
    try:
        r = float(MIN_IMG_RATIO)
    except (TypeError, ValueError):
        return None
    return r if r > 0 else None


def apply_img_ratio(img):
    """输出图片长宽比限制在 [MIN, MAX]：过窄两侧补、过扁上下补背景色（取自身底色）。"""
    try:
        rmax = float(MAX_IMG_RATIO)
    except (TypeError, ValueError):
        rmax = 0
    rmin = img_ratio_min()
    if (rmin is None and rmax <= 0) or img.width <= 0 or img.height <= 0:
        return img
    w, h = img.width, img.height
    try:
        if rmin is not None and w / h < rmin:
            new_w = math.ceil(h * rmin)
            from PIL import Image as _Image
            canvas = _Image.new("RGB", (new_w, h), img.getpixel((0, 0)))
            canvas.paste(img, ((new_w - w) // 2, 0))
            return canvas
        if rmax > 0 and w / h > rmax:
            new_h = math.ceil(w / rmax)
            from PIL import Image as _Image
            canvas = _Image.new("RGB", (w, new_h), img.getpixel((0, 0)))
            canvas.paste(img, (0, (new_h - h) // 2))
            return canvas
    except Exception as e:
        logger.warning(f"[图片] 输出图片长宽比调整失败（按原图发送）: {e}")
    return img


def save_temp_image(img, prefix: str, kind: str):
    """保存渲染结果到临时图片目录并清理同前缀过期图片。返回 ("image", path) 或 None"""
    img = apply_img_ratio(img)
    os.makedirs(TEMP_IMAGE_DIR, exist_ok=True)
    path = os.path.join(TEMP_IMAGE_DIR, f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png")
    try:
        img.save(path)
    except Exception as e:
        logger.error(f"[图片] 保存{kind}图片失败: {e}")
        return None
    try:
        now = datetime.now().timestamp()
        for fn in os.listdir(TEMP_IMAGE_DIR):
            if fn.startswith(prefix) and fn.endswith(".png"):
                fp = os.path.join(TEMP_IMAGE_DIR, fn)
                if now - os.path.getmtime(fp) > TEMP_IMAGE_TTL:
                    os.remove(fp)
    except Exception:
        pass
    return ("image", path)


# ============ 去 Emoji：响应图片不出现 Emoji（直接删除） ============
_EMOJI_STRIP_RX = None


def clean_img_text(text):
    """移除图片文本中的各类 Emoji（OPPOSans 无 emoji 字形，避免豆腐块）。"""
    if not text:
        return text
    global _EMOJI_STRIP_RX
    if _EMOJI_STRIP_RX is None:
        _EMOJI_STRIP_RX = re.compile(
            "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2300-\u23FF]"
        )
    return _EMOJI_STRIP_RX.sub("", str(text))


def dtext(d, xy, text, **kw):
    """绘制图片文本前统一移除 Emoji。"""
    return d.text(xy, clean_img_text(text), **kw)
