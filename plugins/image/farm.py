# -*- coding: utf-8 -*-
"""图片响应模块 · 农场渲染器（2.3.0 modules/farm.py 渲染层移植）：
render_seed_shop 种子商店富文本 / render_farm_shop 农场商店卡片 /
render_warehouse 农场仓库富文本 / render_plot_status 土地状态（我的农场）。
布局与 2.3.0 一致：渲染环境不可用时返回 None（调用方回退纯文本）；
老 Mixin 方法（_farm_seed_mult / _plot_growth / _farm_rank_text 等）以模块级私有函数原样移植，
运行参数经 core 模块全局读取（Core.set_param 会同步覆盖值，等价老代码 globals().get 语义）。
"""
import random
import re
from datetime import date, datetime

from ... import core as _core
from .common import (DS_BG, DS_BORDER, DS_BORDER_2, DS_SURFACE_2,
                     DS_ACCENT, DS_GOLD, DS_GOLD_2, DS_TEXT, DS_TEXT_2, DS_MUTED,
                     DS_SUCCESS, DS_DANGER,
                     ensure_pillow, load_fonts, title_font, text_measurer,
                     make_wrapper, save_temp_image, dtext)
from .generic import rich

__all__ = ["render_seed_shop", "render_farm_shop", "render_warehouse", "render_plot_status"]


# ================= 业务小工具（老 farm.py / base.py / rank.py Mixin 方法原样移植） =================
def _p(key, default=None):
    """读运行参数：core 模块全局（Core.set_param 会把 WebUI 覆盖值同步进 core 全局）→ 默认值。
    等价老代码的 globals().get(KEY, default)（2.3.0 各 mixin 模块全局被运行参数同步）。"""
    v = getattr(_core, key, None)
    return default if v is None else v


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _fmt_price(v):
    """价格显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0"""
    v = float(v)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _fmt_hours(h):
    """化肥库存显示：小时数（保留最多 2 位小数去尾 0）"""
    try:
        h = float(h)
    except (TypeError, ValueError):
        h = 0.0
    if h == int(h):
        return str(int(h))
    return f"{h:.2f}".rstrip("0").rstrip(".")


def _fmt_duration(sec):
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}秒"
    minutes = sec // 60
    if minutes < 60:
        return f"{minutes}分钟"
    hours, rem_min = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}小时{rem_min}分"
    days, rem_h = divmod(hours, 24)
    return f"{days}天{rem_h}小时"


def _fmt_score(v):
    """积分显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0（老 rank._fmt_score 同款）"""
    v = float(v)
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _find_item(items, name):
    return next((x for x in items if x["name"] == name), None)


def _fert_max_accel(fert):
    """化肥可加速次数（-1 = 不限）。兼容旧字段 max_uses"""
    v = fert.get("max_accel", fert.get("max_uses", -1))
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return -1


def _farm_seed_mult(farm):
    """种子价格倍率（按农场等级分档）"""
    lv = int(farm.get("level", 0))
    if lv <= 19:
        return 1.5
    if lv <= 49:
        return 1.0
    if lv <= 99:
        return 0.8
    return 0.7


_DEFAULT_FARM_GRADES = [
    ("贫瘠土地", 0.0, 0.0),
    ("红土地", 1.0, 0.0),
    ("普通土地", 2.0, 0.10),
    ("肥沃土地", 2.5, 0.20),
    ("黑土地", 4.0, 0.35),
]


def _farm_grades():
    """土地等级表：v3 core.FARM_GRADES 默认为 [(名, 产量加成小数, 时间减免小数)] 列表；
    兼容 2.3.0「名=产量加成%,时间减免%｜…」字符串格式（WebUI 表格 set_param 同步）。
    返回 [(名称, 产量加成小数, 时间减免小数)]；解析失败回退默认。"""
    raw = _p("FARM_GRADES")
    if isinstance(raw, (list, tuple)):
        out = []
        for g in raw:
            try:
                out.append((str(g[0]), _f(g[1]), _f(g[2])))
            except Exception:
                continue
        if out:
            return out
    if isinstance(raw, str) and raw.strip():
        out = []
        for seg in raw.replace("；", "|").replace(";", "|").split("|"):
            seg = seg.strip()
            if not seg or "=" not in seg:
                continue
            name, body = seg.split("=", 1)
            nums = []
            for x in body.replace("，", ",").split(","):
                try:
                    nums.append(float(x.strip()))
                except (TypeError, ValueError):
                    nums = []
                    break
            if len(nums) == 2:
                # UI 存百分比（如 100 = 100%），内部换算为小数
                out.append((name.strip(), nums[0] / 100.0, nums[1] / 100.0))
        if out:
            return out
    return list(_DEFAULT_FARM_GRADES)


def _plot_grade(grade):
    grades = _farm_grades()
    return grades[grade] if 0 <= grade < len(grades) else grades[0]


def _crop_level_ranges():
    """作物等级划分上限（分钟）：如 (0,240,480,720,1440) → 0~240 一级 / 241~480 二级 / …"""
    raw = _p("CROP_LEVEL_RANGES", (0, 240, 480, 720, 1440))
    try:
        if isinstance(raw, str):
            vals = [float(x) for x in raw.replace("，", ",").split(",") if str(x).strip() != ""]
        else:
            vals = [float(x) for x in raw]
    except (TypeError, ValueError):
        vals = [0.0, 240.0, 480.0, 720.0, 1440.0]
    if len(vals) < 2:
        vals = [0.0, 240.0, 480.0, 720.0, 1440.0]
    return vals


def _crop_stage_counts():
    """各级作物成长阶段数（默认 一~四级 = 4/5/5/6）"""
    raw = _p("CROP_LEVEL_STAGES", (4, 5, 5, 6))
    try:
        if isinstance(raw, str):
            vals = [int(float(x)) for x in raw.replace("，", ",").split(",") if str(x).strip() != ""]
        else:
            vals = [int(float(x)) for x in raw]
    except (TypeError, ValueError):
        vals = [4, 5, 5, 6]
    if not vals:
        vals = [4, 5, 5, 6]
    return vals


def _crop_level_of(crop):
    """按贫瘠土地上的成熟分钟数划分作物等级（1~N 级）。
    CROP_LEVEL_RANGES 形如 (0,240,480,720,1440)：0 为一级下限，240/480/720/1440 为各等级上限。"""
    grow = int(_f(crop.get("grow_minutes", 0)))
    ranges = _crop_level_ranges()
    if len(ranges) <= 1:
        return 1
    for i in range(1, len(ranges)):
        if grow <= ranges[i]:
            return i
    return len(ranges)


def _crop_stage_count(crop):
    """该作物在总生长周期内划分的成长阶段数"""
    lv = _crop_level_of(crop)
    stages = _crop_stage_counts()
    idx = min(lv - 1, len(stages) - 1)
    return max(1, int(stages[idx]))


def _plot_growth(plot, crop, now):
    """计算一块种植地的生长状态（2.0.0 阶段模型）。返回 dict：
    base_sec 贫瘠总时间(秒) / total_sec 当前土地最大生长时间(秒, 含等级减时) /
    stage_count 阶段数 / stage_sec 每阶段时长(秒, 按贫瘠总时间均分) /
    elapsed_sec 已真实经过时间 / advance_sec 化肥已推进时间 /
    progress_sec 有效生长进度 = elapsed + advance /
    stage_idx 当前阶段(0 起) / stage_left_sec 当前阶段剩余时间 /
    remain_sec 距成熟剩余时间 / mature 是否已成熟"""
    base_sec = int(plot.get("base_time", 0)) or (crop["grow_minutes"] * 60 if crop else 0)
    _, _, gt = _plot_grade(int(plot.get("grade", 0)))
    total_sec = base_sec * (1 - gt)
    elapsed_sec = max(0.0, float(now) - float(plot.get("plant_ts", 0)))
    advance_sec = float(plot.get("fert_advance", 0.0))
    if not plot.get("fert_advance") and plot.get("fert_time"):
        # 旧数据（1.7.9 及以前：减时比例）→ 折算为推进秒数
        advance_sec = float(plot.get("fert_time", 0.0)) * base_sec
    progress_sec = elapsed_sec + advance_sec
    stage_count = _crop_stage_count(crop)
    stage_sec = base_sec / max(1, stage_count)
    stage_idx = min(stage_count - 1, int(progress_sec // stage_sec)) if stage_sec > 0 else 0
    stage_left_sec = stage_sec - (progress_sec % stage_sec)
    remain_sec = total_sec - progress_sec
    return {
        "base_sec": base_sec, "total_sec": total_sec,
        "stage_count": stage_count, "stage_sec": stage_sec,
        "elapsed_sec": elapsed_sec, "advance_sec": advance_sec,
        "progress_sec": progress_sec, "stage_idx": stage_idx,
        "stage_left_sec": stage_left_sec, "remain_sec": remain_sec,
        "mature": remain_sec <= 0,
    }


def _seed_discount_map(crops):
    """种子每日折扣（2.0.3，默认关闭）：每天有概率让 1~3 款种子打八折（肥料不受影响）。
    按「当天」固定随机（同一天折扣款一致）；返回 {作物名: 倍率}（无折扣为空）。"""
    out = {}
    if not bool(_p("SEED_DISCOUNT_ENABLED", False)):
        return out
    today = date.today().isoformat()
    rng = random.Random("seed_discount|" + today)
    if rng.random() >= float(_p("SEED_DISCOUNT_CHANCE", 0.5)):
        return out
    names = sorted({c["name"] for c in crops})
    if not names:
        return out
    n = rng.randint(int(_p("SEED_DISCOUNT_MIN", 1) or 1),
                    int(_p("SEED_DISCOUNT_MAX", 3) or 3))
    pct = float(_p("SEED_DISCOUNT_PCT", 0.8) or 0.8)
    for nm in rng.sample(names, min(n, len(names))):
        out[nm] = pct
    return out


def _farm_shop_seed_list(farm, crops, expanded=False, page=1, all_items=False):
    """农场商店种子选择（2.0.2）：
    全部（「农场商店 全部」）= 所有种子（可购 + 不可购）；
    展开 = 全部可购种子按价格升序分页（每页 FARM_SHOP_SHOW_BUY 款）；
    默认 = 可购等级最高的 FARM_SHOP_SHOW_BUY 款 + 不可购等级最低的 FARM_SHOP_SHOW_LOCKED 款灰卡。
    统一按价格升序返回。"""
    farm_lv = int(farm.get("level", 0))
    mult = _farm_seed_mult(farm)
    price_of = lambda c: int(round(float(c["seed_price"]) * mult))
    buy_n = int(_p("FARM_SHOP_SHOW_BUY", 9))
    lock_n = int(_p("FARM_SHOP_SHOW_LOCKED", 3))
    if all_items:
        return sorted(crops, key=lambda c: (price_of(c), c["name"]))
    buyable = [c for c in crops if c["min_level"] <= farm_lv]
    if expanded:
        sorted_buy = sorted(buyable, key=lambda c: (price_of(c), c["name"]))
        per_page = max(1, buy_n)
        total_pages = max(1, (len(sorted_buy) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        return sorted_buy[(page - 1) * per_page: page * per_page]
    topN = sorted(buyable, key=lambda c: c["min_level"], reverse=True)[:max(1, buy_n)]
    lowM = sorted((c for c in crops if c["min_level"] > farm_lv), key=lambda c: c["min_level"])[:max(0, lock_n)]
    return sorted(topN + lowM, key=lambda c: (price_of(c), c["name"]))


def _rank_plot_score_total(plots):
    """土地等级分合计：每块地块按累计升级花费计分（贫瘠 0 / 红 1000 / 普通 2500 / 肥沃 4500 / 黑 7500，
    分数表在 WebUI「排行榜 → 农场排行」可调），地块超界按最高级计分。"""
    raw = _p("RANK_PLOT_SCORES", (0, 1000, 2500, 4500, 7500))
    try:
        if isinstance(raw, str):
            scores = [float(x) for x in raw.replace("，", ",").split(",") if str(x).strip() != ""]
        else:
            scores = [float(x) for x in raw]
    except (TypeError, ValueError):
        scores = [0.0, 1000.0, 2500.0, 4500.0, 7500.0]
    total = 0.0
    for plot in plots:
        g = int(plot.get("grade", 0)) if isinstance(plot, dict) else 0
        total += scores[g] if 0 <= g < len(scores) else scores[-1]
    return total


def _farm_rank_entries(data):
    """农场排行条目（老 rank._rank_entries("farm") 分支移植）：
    积分 = 经验×权重 + (地块数-2)×权重 + 土地等级分×权重；返回按积分降序的 [(积分, uid, ""), ...]。"""
    ew = _f(_p("RANK_FARM_EXP_W", 2.0))
    pw = _f(_p("RANK_FARM_PLOT_W", 400.0))
    gw = _f(_p("RANK_FARM_GRADE_W", 0.5))
    entries = []
    for fuid, fm in (data.get("farms") or {}).items():
        if not isinstance(fm, dict):
            continue
        exp = float(fm.get("exp", 0) or 0)
        plots = fm.get("plots") or []
        score = exp * ew + max(0, len(plots) - 2) * pw + _rank_plot_score_total(plots) * gw
        entries.append((score, str(fuid), ""))
    # 积分降序；同分按用户 ID 稳定排序
    entries.sort(key=lambda e: (-e[0], e[1]))
    return entries


def _farm_rank_text(uid, data):
    """农场排行榜的排行积分文本（标题右侧）"""
    try:
        entries = _farm_rank_entries(data)
    except Exception:
        entries = []
    for i, (score, euid, _) in enumerate(entries, 1):
        if str(euid) == str(uid):
            return f"🌾 农场榜 第{i}名 · {_fmt_score(score)}分"
    return "🌾 农场榜未上榜"


def _coins_of(data, key):
    v = data.get("users", {}).get(key, {}).get("coins")
    return int(v) if isinstance(v, (int, float)) else 0


# ================= 种子商店（富文本） =================
def render_seed_shop(name, farm, crops):
    """种子商店富文本图：等级价格加成说明 + 全部种子价格（有折扣/等级差价时显示划线原价）。
    返回 ("image", path)；渲染环境不可用时返回 None。"""
    rows = []
    rows.append([(name, DS_TEXT, False)])
    lv = int(farm.get("level", 0))
    mult = _farm_seed_mult(farm)
    if mult > 1:
        bonus = f"农场等级 Lv.{lv}：种子价格 +{int(round((mult - 1) * 100))}%"
    elif mult < 1:
        bonus = f"农场等级 Lv.{lv}：种子价格 -{int(round((1 - mult) * 100))}%"
    else:
        bonus = f"农场等级 Lv.{lv}：种子价格无加成"
    rows.append([(bonus, DS_MUTED, False)])
    rows.append([("", (0, 0, 0), False)])
    for c in crops:
        base = float(c["seed_price"])
        p = round(base * mult, 2)
        lv_req = f"（需 Lv.{c['min_level']}）" if c["min_level"] > 0 else ""
        if p > base:
            rows.append([(f"{c['name']} {_fmt_price(p)} 金币{lv_req}", DS_TEXT, False)])
        elif p < base:
            rows.append([(f"{c['name']} ", DS_TEXT, False),
                         (_fmt_price(base), DS_TEXT, True),
                         (" ", (0, 0, 0), False),
                         (f"{_fmt_price(p)} 金币{lv_req}", DS_DANGER, False)])
        else:
            rows.append([(f"{c['name']} {_fmt_price(p)} 金币{lv_req}", DS_TEXT, False)])
    return rich("种子商店", rows)


# ================= 农场商店（种子 + 化肥 卡片） =================
def render_farm_shop(name, farm, crops, ferts, expanded=False, page=1, all_items=False):
    """农场商店：种子（上）+ 化肥（下）分类卡片展示，完全套用商店卡片模板。
    图片排版：用户名 / 商品种类（居中）+ 居中分割线 / 商品卡片（每行 FARM_SHOP_COLS 张）。
    商品卡片：名称(大三号)+持有数(居右) / 等级条件(如有) / 效果(成熟售价+收割经验，空格优先换行) /
    卡片内分割线 / 价格(红 #C00000、居右、大一号、贴底边 N)；不可购买为灰卡 #D9D9D9。
    卡片高度自适应（同行取最高，低卡拉伸忽略分割线侧 N）；图片高度按绘制流程计算，文字不溢出。
    默认：能买等级最大的 FARM_SHOP_SHOW_BUY 款种子 + 不能买等级最低的 FARM_SHOP_SHOW_LOCKED 款（灰卡）；化肥全部。
    展开：按等级从高到低分页显示全部能购买的种子（每页 FARM_SHOP_SHOW_BUY 款）。
    全部（2.0.2「农场商店 全部」）：展示所有商品（全部种子 + 全部化肥）。
    返回 ("image", path)；渲染环境不可用时返回 None。"""
    Image, ImageDraw = ensure_pillow()
    if Image is None:
        return None
    # 字号语义：标题 36（衬线）/ 分类 26 / 名称 26（大三号）/ 正文 18 / 价格 20（大一号）/ 原价 14（小一号）
    fonts = load_fonts(26, 26, 18, 20, 14)
    if fonts is None:
        return None
    cat_font, name_font, body_font, price_font, small_price_font = fonts
    title_font_ = title_font(kind="farm")
    if title_font_ is None:
        return None

    pad = 20
    title_h = 76
    cat_h = 30
    rule_h = 18
    gap = 12
    inner = 10
    name_h = 34  # 名称行高（大三号）
    line_h = 26
    price_h = 26  # 价格文字行高
    cols = int(_p("FARM_SHOP_COLS", 4))
    card_w = 246
    content_w = card_w - inner * 2
    n_pad = int(_p("SHOP_PRICE_PAD", 4))  # N：价格距分割线/底边

    tw = text_measurer()
    if tw is None:
        return None

    wrap = make_wrapper(tw, content_w)

    farm_lv = int(farm.get("level", 0))
    mult = _farm_seed_mult(farm)
    seeds_have = farm.get("warehouse", {}).get("seeds", {})
    ferts_have = farm.get("warehouse", {}).get("fertilizers", {})
    disc_map = _seed_discount_map(crops)  # 2.0.3：种子每日折扣（默认关闭）

    # ---- 种子选择 ----
    seed_list = _farm_shop_seed_list(farm, crops, expanded, page, all_items)

    # ---- 卡片行规划（plain 行已按宽度换行展开，保证高度自适应） ----
    # 行类型：
    #   ("pair", 左, 右)   名称(大三号,左) + 持有数(右)
    #   ("pair2", 左, 右)  等级条件(左) + 成熟时间(右) / 售价(左) + 经验(右)
    #   ("plain", 文本)    普通文本行（自动换行）
    #   ("rule", "", "")   卡片内分割线（位置固定在价格上方 N，见下）
    #   ("price", 文本)    价格（红、右、大一号、贴底边 N）
    def seed_rows(c):
        rows = []
        cnt_text = f"×{int(seeds_have.get(c['name'], 0))}"
        if tw(c["name"], name_font) + tw(cnt_text, body_font) + 8 <= content_w:
            rows.append(("pair", c["name"], cnt_text))
        else:
            for ln in wrap(c["name"], name_font):
                rows.append(("plain", ln, ""))
            rows.append(("plain", cnt_text, ""))
        # 商品描述：名称下方、要求（等级条件）上方
        if c.get("desc"):
            for ln in wrap(c["desc"], body_font):
                rows.append(("plain", ln, ""))
        # 等级条件（左）+ 成熟时间（右）
        lv_t = f"需要 Lv.{c['min_level']}" if c["min_level"] > 0 else ""
        tm_t = f"成熟 {c['grow_minutes']} 分钟"
        if lv_t:
            rows.append(("pair2", lv_t, tm_t))
        else:
            rows.append(("plain", tm_t, ""))
        # 成熟后售价（贫瘠土地 + 无肥料状态）+ 收割农场经验（仅种子）
        sell_v = int(round(float(c["yield"]) * float(c["crop_price"])))
        rows.append(("pair2", f"售价 {sell_v} 金币", f"经验 {c['exp']}"))
        rows.append(("rule", "", ""))
        # 2.0.3：种子折扣 → 原价（灰小字）+ 折后价（红正常字）
        disc = float(disc_map.get(c["name"], 1.0) or 1.0)
        base_price = int(round(float(c["seed_price"]) * mult))
        price = max(1, int(round(base_price * disc)))
        rows.append(("price", f"{price} 金币", (f"{base_price} 金币" if disc < 1.0 else "")))
        return rows

    def fert_rows(f):
        rows = []
        have_h = float(ferts_have.get(f["name"], 0) or 0)
        cnt_text = f"×{_fmt_hours(have_h)}h"
        if tw(f["name"], name_font) + tw(cnt_text, body_font) + 8 <= content_w:
            rows.append(("pair", f["name"], cnt_text))
        else:
            for ln in wrap(f["name"], name_font):
                rows.append(("plain", ln, ""))
            rows.append(("plain", cnt_text, ""))
        # 商品描述：名称下方、效果上方
        if f.get("desc"):
            for ln in wrap(f["desc"], body_font):
                rows.append(("plain", ln, ""))
        # 2.0.0：化肥效果 = 加速成长阶段 + 每加速一次的增产
        parts = []
        if float(f.get("yield_add", 0) or 0) > 0:
            parts.append(f"增产{f['yield_add']:.0f}%/次")
        if parts:
            for ln in wrap(" ".join(parts), body_font):
                rows.append(("plain", ln, ""))
        maxa = "不限" if _fert_max_accel(f) < 0 else f"{_fert_max_accel(f)}次"
        rows.append(("plain", f"每株可加速 {maxa}"))
        rows.append(("rule", "", ""))
        rows.append(("price", f"{int(f['price'])} 金币/时", ""))
        return rows

    seed_plans = [(c, seed_rows(c), c["min_level"] > farm_lv) for c in seed_list]
    fert_plans = [(f, fert_rows(f), False)
                  for f in sorted(ferts, key=lambda f: (int(f["price"]), f["name"]))]

    # 卡片高度：内容区（inner*2 + 各行）+ 分割线间隙 N + 分割线半行 + 价格区（价格高 + 底边 N）
    # 分割线固定在价格上方 N 距离（N = SHOP_PRICE_PAD）
    def card_height(rows):
        h = inner * 2
        for r in rows:
            if r[0] == "pair":
                h += name_h
            elif r[0] == "pair2":
                h += line_h
            elif r[0] == "rule":
                h += n_pad + 1  # 分割线距价格上方固定 N + 线本身 1px
            elif r[0] == "price":
                h += price_h + n_pad  # 价格区 = 价格高 + 底边 N
            else:
                h += line_h
        return h

    width = pad * 2 + card_w * cols + gap * (cols - 1)

    # 总高度：标题 + 展开提示 + 每区（类别标题 + 居中分割线 + 卡片组高和）+ 底部边距。
    # 与绘制流程完全一致（区之间无额外间距），保证最后一行卡片不溢出图片底部。
    def section_height(plans):
        h = 0
        for g in range(0, len(plans), cols):
            group = plans[g:g + cols]
            h += max(card_height(p[1]) for p in group) + gap
        return max(0, h - gap)

    subtitle = ""
    if expanded:
        per_page = max(1, int(_p("FARM_SHOP_SHOW_BUY", 9)))
        n_buy = sum(1 for c in crops if c["min_level"] <= int(farm.get("level", 0)))
        total_pages = max(1, (n_buy + per_page - 1) // per_page)
        subtitle = f"（展开模式：全部可购种子 第 {page}/{total_pages} 页，发送「农场商店 展开 {page + 1}」翻页）"
    height = pad * 2 + title_h + (26 if subtitle else 0)
    for plans in (seed_plans, fert_plans):
        height += cat_h + rule_h + section_height(plans)

    img = Image.new("RGB", (width, height), DS_BG)
    d = ImageDraw.Draw(img)
    y = pad
    dtext(d, (pad, y), f"{name} 的农场商店", font=title_font_, fill=DS_ACCENT)
    y += title_h
    if subtitle:
        dtext(d, (pad, y), subtitle, font=body_font, fill=DS_GOLD)
        y += 26

    for cat_title, plans in (("🌱 种子", seed_plans), ("🧪 化肥", fert_plans)):
        # 类别名（居中）
        cx = int(pad + (width - 2 * pad - tw(cat_title, cat_font)) / 2)
        dtext(d, (cx, y), cat_title, font=cat_font, fill=DS_TEXT_2)
        y += cat_h
        # 居中分隔线
        d.line([(pad + 20, y), (width - pad - 20, y)], fill=DS_BORDER, width=2)
        y += rule_h
        for g in range(0, len(plans), cols):
            group = plans[g:g + cols]
            gh = max(card_height(p[1]) for p in group)
            for j, (item, rows, grey) in enumerate(group):
                x0 = pad + j * (card_w + gap)
                # 灰卡（不可购买）
                if grey:
                    d.rectangle([x0, y, x0 + card_w, y + gh], fill=DS_BORDER_2, outline=DS_BORDER_2, width=1)
                else:
                    d.rectangle([x0, y, x0 + card_w, y + gh], outline=DS_BORDER, width=1)
                yy = y + inner
                rule_y = None  # 分割线 y 坐标（固定在价格上方 N 距离）
                price_y = y + gh - n_pad - price_h  # 价格基线
                for r in rows:
                    kind = r[0]
                    if kind == "pair":
                        dtext(d, (int(x0 + inner), yy), r[1], font=name_font, fill=DS_TEXT)
                        dtext(d, (int(x0 + card_w - inner - tw(r[2], body_font)), yy + 4),
                              r[2], font=body_font, fill=DS_GOLD)
                        yy += name_h
                    elif kind == "pair2":
                        # 左 + 右 两列文字（等级条件+成熟时间 / 售价+经验）
                        if r[1]:
                            dtext(d, (int(x0 + inner), yy), r[1], font=body_font, fill=DS_TEXT_2)
                        if r[2]:
                            dtext(d, (int(x0 + card_w - inner - tw(r[2], body_font)), yy + 2),
                                  r[2], font=body_font, fill=DS_MUTED)
                        yy += line_h
                    elif kind == "plain":
                        for wl in wrap(r[1], body_font):
                            dtext(d, (int(x0 + inner), yy), wl, font=body_font, fill=DS_TEXT_2)
                            yy += line_h
                    elif kind == "rule":
                        rule_y = price_y - n_pad  # 分割线固定在价格上方 N 距离
                    elif kind == "price":
                        # 价格贴底边 N（2.0.3：原价灰色小字 + 实时价红色正常字）
                        real_text = r[1]
                        orig_text = r[2] if len(r) > 2 else ""
                        right_x = int(x0 + card_w - inner - tw(real_text, price_font))
                        if orig_text:
                            dtext(d, (int(right_x - 6 - tw(orig_text, small_price_font)), price_y + 4),
                                  orig_text, font=small_price_font, fill=DS_MUTED)
                        dtext(d, (right_x, price_y), real_text, font=price_font, fill=DS_DANGER)
                        break
                if rule_y is not None:
                    d.line([(x0 + 8, rule_y), (x0 + card_w - 8, rule_y)], fill=DS_BORDER, width=1)
            y += gh + gap
        y -= gap

    return save_temp_image(img, "_farmshop_", "农场商店")


# ================= 农场仓库（富文本） =================
def render_warehouse(farm, crops, ferts):
    """农场仓库富文本图：作物 / 种子 / 肥料 三段库存清单（含可售价格）。
    返回 ("image", path)；渲染环境不可用时返回 None。"""
    wh = farm.get("warehouse", {})
    rows = []
    for key, label in [("crops", "作物"), ("seeds", "种子"), ("fertilizers", "肥料")]:
        rows.append([(f"----{label}----", DS_TEXT_2, False)])
        items = wh.get(key, {})
        if not items:
            rows.append([("（空）", DS_MUTED, False)])
            continue
        for nm, cnt in items.items():
            if key == "crops":
                c = _find_item(crops, nm)
                price = c["crop_price"] if c else 0.0
            elif key == "seeds":
                c = _find_item(crops, nm)
                price = c["seed_sell_price"] if c else 0.0
            else:
                # 化肥只能买和使用，不可卖（2.0.0 起按小时计量）
                rows.append([(f"{nm} ×{_fmt_hours(cnt)} 小时（不可售）", DS_MUTED, False)])
                continue
            rows.append([(f"{nm} ×{cnt} 可售 {_fmt_price(price)}金币", DS_TEXT, False)])
    return rich("农场仓库", rows)


# ================= 土地状态 / 我的农场 =================
def render_plot_status(name, uid, data, farm, crops, ferts, steal_lines=None,
                       highlights=None, highlight_plots=None, new_plots=None,
                       profit_delta=0, actions=None, coins_delta=None):
    """土地状态 / 我的农场（2.0.0 新模板）：
    - 标题行：<用户名>的农场（右对齐）农场排行榜排行积分
    - 盈利行：总盈利 + 本次盈利变化（支出 -，收入 +）
    - 等级展示模块（Lv / 经验 / 升级进度条）→ 分割线 → 土地卡片区
    - 土地卡片高亮：黄 #FFE699=种植 / 红 #FFC5C5=收割 / 蓝 #B4C7E7=开垦·施肥·升级
      （高亮变化涉及的提示内容不再作文本提示）
    - 底部大卡片（宽度 = 同一行所有土地卡片宽度+间距总和）：自动化行为提示 + 金币变化
    highlights：{地块编号(1-based): 'plant'|'harvest'|'till'|'fert'|'upgrade'}；
    兼容旧参数 highlight_plots（蓝=施肥/开垦）与 new_plots（黄=种植）。
    返回 ("image", path)；渲染环境不可用时返回 None。"""
    Image, ImageDraw = ensure_pillow()
    if Image is None:
        return None
    # 字号语义：标题 36（衬线）/ 等级 26 / 小字 18 / 经验 16（小两号）
    fonts = load_fonts(26, 18, 16)
    if fonts is None:
        return None
    lv_font, small_font, exp_font = fonts
    title_font_ = title_font(kind="farm")
    if title_font_ is None:
        return None
    now = datetime.now().timestamp()
    plots = farm.get("plots", [])
    level = int(farm.get("level", 0))
    exp = float(farm.get("exp", 0.0))
    farm_max_level = int(_p("FARM_MAX_LEVEL", 100))
    farm_exp_base = float(_p("FARM_EXP_BASE", 1000.0))
    upgrade_costs = _p("FARM_UPGRADE_COSTS", [1000, 1500, 2000, 3000])
    need = farm_exp_base * (level + 1) if level < farm_max_level else 0
    profit = int(farm.get("total_profit", 0))

    # 高亮：新参数优先，兼容旧参数
    hl_map = dict(highlights or {})
    for n in (highlight_plots or []):
        hl_map.setdefault(int(n), "fert")
    for n in (new_plots or []):
        hl_map.setdefault(int(n), "plant")
    HL_COLORS = {
        "plant": (255, 230, 153),    # 黄 #FFE699 种植
        "harvest": (255, 197, 197),  # 红 #FFC5C5 收割
        "till": (180, 199, 231),     # 蓝 #B4C7E7 开垦
        "fert": (180, 199, 231),     # 蓝 #B4C7E7 施肥
        "upgrade": (180, 199, 231),  # 蓝 #B4C7E7 升级
    }

    # ---------- 土地卡片内容 ----------
    cards = []  # (行列表, 高亮颜色 or None)
    for i, plot in enumerate(plots):
        num = i + 1
        gname = _plot_grade(int(plot.get("grade", 0)))[0]
        grade = int(plot.get("grade", 0))
        if grade >= len(upgrade_costs):
            upgrade = "🏆 已满级"
        else:
            upgrade = f"⬆️ 升级 {int(upgrade_costs[grade])}金"
        color = HL_COLORS.get(hl_map.get(num))
        if plot.get("crop") is None:
            lines = [f"#{num} {gname}", "空闲中", upgrade]
        else:
            crop_name = plot.get("crop", "")
            c = _find_item(crops, crop_name)
            price = c["crop_price"] if c else 0.0
            income = int(round(int(plot.get("yield", 0)) * float(price)))
            if now >= plot.get("mature_ts", 0):
                state = "已成熟"
                remain = "可收割"
            else:
                state = "占用中"
                remain = _fmt_duration(plot.get("mature_ts", 0) - now)
            # 化肥已推进时间（分钟）
            adv_min = ""
            g = _plot_growth(plot, c, now) if c else None
            if g is not None and g["advance_sec"] > 60:
                adv_min = f"加速{int(g['advance_sec'] // 60)}分"
            # 2.0.1：加速信息独立一行，位于「剩余时间 / 预计收入」之下
            lines = [
                f"#{num} {gname} {state}",
                crop_name,
                f"剩余 {remain} 预计 {income}金",
            ]
            if adv_min:
                lines.append(adv_min)
            lines.append(upgrade)
        cards.append((lines, color))

    # ---------- 布局参数 ----------
    pad = 20
    title_h = 52
    gap = 10
    inner = 8
    line_h = 26
    cols = int(_p("FARM_PLOT_COLS", 4))
    card_w = int(_p("FARM_PLOT_CARD_WIDTH", 270))
    content_w = card_w - inner * 2

    tw = text_measurer()
    if tw is None:
        return None

    wrap = make_wrapper(tw, content_w)

    # 预计算每张卡片换行后的行数与高度
    card_rows = []  # (行列表, 高度, 高亮颜色 or None)
    for lines, color in cards:
        rows = []
        for ln in lines:
            for wl in wrap(ln, small_font):
                rows.append(wl)
        card_rows.append((rows, inner * 2 + len(rows) * line_h, color))

    # 顶部属性区高度
    profit_h = 28
    lv_row_h = 38
    bar_h = 26
    rule_h = 22

    width = pad * 2 + card_w * cols + gap * (cols - 1)
    bar_w = int((width - pad * 2) * 0.5)  # 等级行 / 进度条宽度 = 内容宽 * 50%

    rows_n = (len(card_rows) + cols - 1) // cols if card_rows else 1
    cards_h = sum(max(card_rows[r * cols:(r + 1) * cols][j][1] for j in range(len(card_rows[r * cols:(r + 1) * cols])))
                  for r in range(rows_n)) + gap * max(0, rows_n - 1) if card_rows else 0

    # 偷菜信息区（底部）：表格行数（含标题行）
    steal_table = []
    steal_h = 0
    if steal_lines:
        steal_table, steal_h = _layout_steal_table(steal_lines, width - pad * 2, small_font, tw)
        steal_h += 8 + line_h  # 分割线间距 + 标题行

    # 底部大卡片（自动化行为 + 金币变化）：宽度 = 图片宽度 = 同一行土地卡片宽度+间距总和
    big_lines = []
    if actions:
        big_lines.extend(actions)
    if coins_delta is not None:
        cur_coins = _coins_of(data, uid) if data else 0
        sign = "+" if coins_delta >= 0 else ""
        big_lines.append(f"💰 金币变化：{sign}{int(coins_delta)}（当前 {cur_coins}）")
    big_h = 0
    big_pad = 10
    big_wrapped = []
    if big_lines:
        # 超长行为提示（如 收割+升级 合并文本，含 \n 与超宽）按 \n 拆分并按大卡片宽度换行，
        # 防止文字溢出/重叠（Pillow text() 不识别 \n）
        big_w = width - pad * 2 - 24
        for ln in big_lines:
            for part in str(ln).split("\n"):
                for wl in wrap(part, small_font, big_w):
                    big_wrapped.append(wl)
        big_h = big_pad * 2 + len(big_wrapped) * line_h + 6

    height = pad * 2 + title_h + profit_h + lv_row_h + bar_h + rule_h + cards_h + steal_h + big_h

    img = Image.new("RGB", (width, height), DS_BG)
    d = ImageDraw.Draw(img)
    y = pad

    # 标题：<用户名称> + 右侧 农场排行榜排行积分
    rank_text = _farm_rank_text(uid, data) if data else ""
    title_line = f"{name} 的农场"
    if rank_text and tw(title_line, title_font_) + 24 + tw(rank_text, small_font) <= width - pad * 2:
        dtext(d, (pad, y), title_line, font=title_font_, fill=DS_ACCENT)
        dtext(d, (int(width - pad - tw(rank_text, small_font)), y + 16), rank_text,
              font=small_font, fill=DS_MUTED)
    else:
        dtext(d, (pad, y), title_line, font=title_font_, fill=DS_ACCENT)
        if rank_text:
            dtext(d, (pad, y + 28), rank_text, font=small_font, fill=DS_MUTED)
            title_h = max(title_h, 52 + 22)
    y += title_h

    # 盈利行：总盈利 + 本次盈利变化（支出 -，收入 +）
    profit_txt = f"📈 总盈利：{profit} 金币"
    if profit_delta:
        psign = "+" if profit_delta > 0 else ""
        profit_txt += f"　（本次 {psign}{int(profit_delta)}）"
    dtext(d, (pad, y), profit_txt, font=small_font, fill=DS_MUTED)
    y += profit_h

    # 等级行：Lv.X（左）+ 经验 Y/Z（右，字号小两号）
    dtext(d, (pad, y), f"Lv.{level}", font=lv_font, fill=DS_TEXT)
    exp_text = f"经验 {exp:.0f}/{need:.0f}" if need > 0 else "已满级"
    dtext(d, (int(pad + bar_w - tw(exp_text, exp_font)), y + 6), exp_text, font=exp_font, fill=DS_TEXT_2)
    y += lv_row_h

    # 升级进度条（宽度与等级行相同，含百分比）
    bar_y = y
    ratio = min(1.0, exp / need) if need > 0 else 1.0
    d.rectangle([pad, bar_y, pad + bar_w, bar_y + 14], outline=DS_BORDER, width=1)
    if ratio > 0:
        d.rectangle([pad + 1, bar_y + 1, int(pad + 1 + (bar_w - 2) * ratio), bar_y + 13], fill=DS_SUCCESS)
    pct_text = f"{int(ratio * 100)}%"
    dtext(d, (int(pad + bar_w - tw(pct_text, small_font) - 4), bar_y - 3), pct_text, font=small_font, fill=DS_TEXT)
    y += bar_h

    # 分割线
    d.line([(pad, y), (width - pad, y)], fill=DS_BORDER, width=2)
    y += rule_h

    # 土地卡片（4 列，自动换行，同行取最高）
    for r in range(rows_n):
        group = card_rows[r * cols:(r + 1) * cols]
        if not group:
            break
        gh = max(h for _, h, _ in group)
        for j, (rows, _, color) in enumerate(group):
            x0 = pad + j * (card_w + gap)
            d.rectangle([x0, y, x0 + card_w, y + gh], fill=color, outline=DS_BORDER, width=1)
            yy = y + inner
            for ln in rows:
                dtext(d, (int(x0 + inner), yy), ln, font=small_font, fill=DS_TEXT)
                yy += line_h
        y += gh + gap

    # 偷菜信息区：分割线 + 标题行 + 表格
    if steal_lines:
        d.line([(pad, y), (width - pad, y)], fill=DS_BORDER, width=1)
        y += 8
        title_line = steal_lines[0] if steal_lines else ""
        dtext(d, (pad, y), title_line, font=small_font, fill=DS_MUTED)
        y += line_h
        _draw_steal_table(d, steal_table, pad, y, width - pad, small_font)
        y += steal_h - (8 + line_h)

    # 底部大卡片：自动化行为提示 + 金币变化（按换行后的行数绘制，避免重叠）
    if big_wrapped:
        big_y0 = y + 6
        big_h_real = big_pad * 2 + len(big_wrapped) * line_h
        d.rectangle([pad, big_y0, width - pad, big_y0 + big_h_real],
                    fill=DS_SURFACE_2, outline=DS_BORDER, width=1)
        yy = big_y0 + big_pad
        for ln in big_wrapped:
            dtext(d, (int(pad + 12), yy), ln, font=small_font, fill=DS_TEXT_2)
            yy += line_h

    return save_temp_image(img, "_farm_", "土地状态")


def _layout_steal_table(lines, max_w, font, tw):
    """偷菜信息无框线表格：行 = 用户|作物 * 数量|损失金额|状态。
    返回 (单元格二维列表, 总高度)。列宽按内容自适应。"""
    rows_data = []
    for ln in lines:
        if "|" not in ln:
            continue
        cells = [c.strip() for c in ln.split("|")]
        if len(cells) < 4:
            cells += [""] * (4 - len(cells))
        rows_data.append(cells[:4])
    if not rows_data:
        return [], 0
    # 计算列宽（按最长内容，含换行：同一用户多作物换行显示在作物列）
    n_cols = 4
    col_w = [0] * n_cols
    for cells in rows_data:
        for i in range(n_cols):
            col_w[i] = max(col_w[i], tw(cells[i], font))
    # 多作物换行：作物列内容含换行 → 按行拆
    line_h = 26
    total = 0
    table = []
    for cells in rows_data:
        # 作物列可能含多行（换行）
        crop_lines = cells[1].split("\n") if "\n" in cells[1] else [cells[1]]
        table.append((cells, crop_lines))
        total += line_h * len(crop_lines)
    return table, total


def _draw_steal_table(d, table, x0, y0, x1, font):
    """绘制偷菜表格：无框线，列宽自适应；失败 #7F7F7F / 成功 #C00000 / 宠物起作用 #BF9000；
    2.0.3：折叠次数 *N 用黄色 #FFC000 表示。"""
    line_h = 26
    y = y0
    YELLOW = DS_GOLD_2
    for cells, crop_lines in table:
        name = cells[0]
        loss = cells[2]
        status = cells[3]
        # 颜色由状态决定
        if "失败" in status:
            color = DS_MUTED
        elif "宠物" in status or "追回" in status:
            color = DS_GOLD
        else:
            color = DS_DANGER
        for k, crop_line in enumerate(crop_lines):
            dtext(d, (int(x0), y), name if k == 0 else "", font=font, fill=color)
            # 折叠次数 *N（行尾的 *数字）用黄色绘制
            x_crop = int(x0 + 90)
            m = re.match(r"^(.*?)(\*\d+)$", crop_line)
            if m:
                dtext(d, (x_crop, y), m.group(1), font=font, fill=color)
                star_w = d.textlength(m.group(2), font=font)
                dtext(d, (int(x_crop + d.textlength(m.group(1), font=font)), y),
                      m.group(2), font=font, fill=YELLOW)
            else:
                dtext(d, (x_crop, y), crop_line, font=font, fill=color)
            dtext(d, (int(x0 + 90 + 160), y), loss if k == 0 else "", font=font, fill=color)
            dtext(d, (int(x0 + 90 + 160 + 90), y), status if k == 0 else "", font=font, fill=color)
            y += line_h
