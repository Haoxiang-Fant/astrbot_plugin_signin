# -*- coding: utf-8 -*-
"""农场商店（3.0.0 功能插件）。自 2.3.0 modules/farm.py 商店与施肥分支原样迁移：
种子商店 / 农场商店 / 肥料商店（种子+化肥合并展示，支持 展开/全部/页码；图片渲染经
core.image.render_farm_shop，缺失或异常回退 2.3.0 同款文本）、购买种子（2.0.3 种子
每日折扣）、购买肥料（按小时购买，最小 1 小时）、施肥（完整格式 + 快捷施肥：缺化肥名
按「化肥 → 有机化肥 → 任意可用」自动选肥、缺编号默认全部生长地、缺时间施到下一阶段，
库存不足自动购买，金币不足则买多少算多少）。

其它插件经 core.service("farm_shop") 调用：
  - buy_seed(event)  购买种子（接受原始消息事件并自行解析参数；「购买 <作物>种子 <数量>」
                     由 pet_shop 插件委托，兼容「种子」后缀）
  - buy_fert(event)  购买化肥（接受原始消息事件并自行解析「<肥料名> [小时数]」）
  - fert_list()      肥料配置列表（game_items.json ferts，结构与 2.3.0 一致）

土地编号/施肥参数解析复用 farm 服务（fert_targets/plot_parser），farm 服务缺失时退化为
本地同款解析；农场记录读写经 farm 服务（farm_of 等），缺失时直接操作 core.data["farms"]
（结构不变：warehouse{crops,seeds,fertilizers}/tools）。回复文本与 2.3.0 逐字一致。
"""
import math
import random
import re
from datetime import date, datetime

from astrbot.api import logger

from .. import core as _core_mod
from ..core import (FARM_SHOP_COLS, FARM_SHOP_SHOW_BUY, FARM_SHOP_SHOW_LOCKED,
                    SEED_DISCOUNT_ENABLED, SEED_DISCOUNT_CHANCE, SEED_DISCOUNT_MIN,
                    SEED_DISCOUNT_MAX, SEED_DISCOUNT_PCT, SHOP_PRICE_PAD,
                    FARM_UNLOCK_COST)

NAME = "farm_shop"

_CORE = None  # register(core) 时注入的核心框架实例
_FARM_LOG_MAX = 300


# ================= 作物 / 肥料配置（game_items.json，缺省回退旧版 txt） =================
def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _norm_crop_entry(d):
    """规范化一条作物配置（扁平 dict，键与 2.3.0 _parse_crop_fert 输出一致）"""
    if not isinstance(d, dict):
        return None
    name = str(d.get("name", "")).strip()
    if not name:
        return None
    return {
        "name": name,
        "desc": str(d.get("desc", "") or ""),
        "seed_price": _f(d.get("seed_price", 0)),
        "seed_sell_price": _f(d.get("seed_sell_price", 0)),
        "yield": int(_f(d.get("yield", 0))),
        "crop_price": _f(d.get("crop_price", 0)),
        "exp": int(_f(d.get("exp", 0))),
        "min_level": int(_f(d.get("min_level", 0))),
        "grow_minutes": int(_f(d.get("grow_minutes", 0))),
    }


def _norm_fert_entry(d):
    """规范化一条肥料配置（2.0.0：max_accel 为可加速次数，替代 max_uses）"""
    if not isinstance(d, dict):
        return None
    name = str(d.get("name", "")).strip()
    if not name:
        return None
    max_accel = d.get("max_accel", d.get("max_uses", -1))
    try:
        max_accel = int(float(max_accel))
    except (TypeError, ValueError):
        max_accel = -1
    return {
        "name": name,
        "desc": str(d.get("desc", "") or ""),
        "price": int(_f(d.get("price", 0))),
        "time_reduce": _f(d.get("time_reduce", 0)),
        "yield_add": _f(d.get("yield_add", 0)),
        "max_uses": int(_f(d.get("max_uses", -1))),
        "max_accel": max_accel,
    }


def _load_crops():
    """作物配置：core.items()["crops"]（game_items.json）；为空时回退解析 作物.txt"""
    flat = _CORE.items() or {}
    crops = [c for c in (_norm_crop_entry(d) for d in (flat.get("crops") or [])) if c]
    if crops:
        return crops
    return _parse_crop_fert(_core_mod.CROP_FILE, "作物")


def _load_fertilizers():
    """肥料配置：core.items()["ferts"]（game_items.json）；为空时回退解析 肥料.txt"""
    flat = _CORE.items() or {}
    ferts = [f_ for f_ in (_norm_fert_entry(d) for d in (flat.get("ferts") or [])) if f_]
    if ferts:
        return ferts
    return _parse_crop_fert(_core_mod.FERT_FILE, "肥料")


def _parse_crop_fert(path, kind):
    """旧版 txt 配置回退解析（[类型:名称]+key=value）"""
    items = _CORE.parse_kv_sections(path, kind)
    result = []
    for it in items:
        d = it["data"]
        if kind == "作物":
            result.append({
                "name": it["name"],
                "desc": d.get("描述", ""),
                "seed_price": _f(d.get("种子价格", 0)),
                "seed_sell_price": _f(d.get("种子卖出价格", 0)),
                "yield": int(_f(d.get("产量", 0))),
                "crop_price": _f(d.get("成熟作物价格", 0)),
                "exp": int(_f(d.get("收获经验值", 0))),
                "min_level": int(_f(d.get("最低农场等级要求", 0))),
                "grow_minutes": int(_f(d.get("成熟时间", 0))),
            })
        else:
            result.append({
                "name": it["name"],
                "desc": d.get("描述", ""),
                "price": int(_f(d.get("肥料价格", 0))),
                "time_reduce": _f(d.get("减少时间", 0)),
                "yield_add": _f(d.get("增加产量", 0)),
                "max_uses": int(_f(d.get("最大使用次数", -1))),
                "max_accel": int(_f(d.get("可加速次数", d.get("最大使用次数", -1)))),
            })
    return result


def _find_item(items, name):
    return next((x for x in items if x["name"] == name), None)


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


# ================= farm 服务复用（None-safe，缺失时本地同款实现） =================
def _farm_api():
    try:
        return _CORE.service("farm")
    except Exception:
        return None


def _farm_of(key):
    """取用户农场 dict；无农场返回 None（优先 farm 服务）"""
    api = _farm_api()
    fn = getattr(api, "farm_of", None) if api is not None else None
    if fn is not None:
        try:
            return fn(key)
        except Exception:
            pass
    return _CORE.data.get("farms", {}).get(key)


def _farm_need(key, name):
    if not _farm_of(key):
        return f"{name} 还没有农场，发送「解锁农场」（需 {FARM_UNLOCK_COST} 金币）解锁。"
    return None


def _state_snippet(farm):
    """农场当前状态摘要一行（优先 farm 服务；缺失时本地 2.3.0 同款文本）"""
    api = _farm_api()
    fn = getattr(api, "state_snippet", None) if api is not None else None
    if fn is not None:
        try:
            return fn(farm)
        except Exception:
            pass
    wh = farm.get("warehouse", {})
    n_plot = len(farm.get("plots", []))
    n_crop = sum(int(v) for v in wh.get("crops", {}).values())
    n_seed = sum(int(v) for v in wh.get("seeds", {}).values())
    n_fert = sum(int(v) for v in wh.get("fertilizers", {}).values())
    return (f"🌾 农场 Lv.{farm.get('level', 0)}｜土地 {n_plot} 块｜"
            f"仓库：作物 {n_crop} / 种子 {n_seed} / 肥料 {n_fert}")


def _seed_mult(farm):
    """种子价格倍率（按农场等级，优先 farm 服务）"""
    api = _farm_api()
    fn = getattr(api, "seed_mult", None) if api is not None else None
    if fn is not None:
        try:
            return fn(farm)
        except Exception:
            pass
    lv = int(farm.get("level", 0))
    if lv <= 19:
        return 1.5
    if lv <= 49:
        return 1.0
    if lv <= 99:
        return 0.8
    return 0.7


def _farm_log(data, key, act, detail="", coins=0, qty=0):
    """农场操作记录（与 farm 插件同款数据格式，随 records.json 存储）+ 累计统计"""
    user = _CORE.ensure_user(key)
    logs = user.setdefault("farm_logs", [])
    now_dt = datetime.now()
    logs.append({
        "ts": now_dt.timestamp(),
        "time": now_dt.strftime("%Y-%m-%d %H:%M"),
        "act": act,
        "detail": str(detail or ""),
        "coins": int(coins or 0),
        "qty": int(qty or 0),
    })
    if len(logs) > _FARM_LOG_MAX:
        del logs[: len(logs) - _FARM_LOG_MAX]
    st = user.setdefault("farm_stats", {})
    if act == "plant":
        st["plant"] = int(st.get("plant", 0)) + 1
        st["cost"] = int(st.get("cost", 0)) + int(coins or 0)
    elif act == "fertilize":
        st["fertilize"] = int(st.get("fertilize", 0)) + 1
        st["cost"] = int(st.get("cost", 0)) + int(coins or 0)
    elif act == "harvest":
        st["harvest"] = int(st.get("harvest", 0)) + 1
        st["harvest_yield"] = int(st.get("harvest_yield", 0)) + int(qty or 0)
    elif act == "steal":
        st["steal"] = int(st.get("steal", 0)) + 1
        st["steal_gain"] = int(st.get("steal_gain", 0)) + int(coins or 0)


# ---- 2.2.0：土地编号统一解析（复用 farm 服务，缺失时本地同款实现） ----
def _parse_plot_numbers_impl(raw):
    """土地编号统一解析（括号、逗号不分全角半角）→ (编号列表, 裸数字列表)"""
    if raw is None:
        return [], []
    s = str(raw).strip().replace("，", ",").replace("、", ",").replace("（", "(").replace("）", ")")
    if not s:
        return [], []
    plots = []
    bare = []
    rest = s
    while "(" in rest:
        a = rest.find("(")
        b = rest.find(")", a)
        if b < 0:
            break
        grp = rest[a + 1:b].strip()
        rest = rest[:a] + " " + rest[b + 1:]
        if not grp:
            continue
        nums = []
        for p in grp.split(","):
            try:
                nums.append(int(float(p.strip())))
            except (TypeError, ValueError):
                nums = []
                break
        if not nums:
            continue
        if len(nums) >= 2:
            lo, hi = min(nums), max(nums)
            plots.extend(range(lo, hi + 1))
        else:
            plots.append(nums[0])
    for chunk in re.split(r"\s+", rest):
        chunk = chunk.strip().strip(",")
        if not chunk:
            continue
        if "," in chunk:
            for tok in chunk.split(","):
                try:
                    plots.append(int(float(tok.strip())))
                except (TypeError, ValueError):
                    continue
        else:
            try:
                n = int(float(chunk))
            except (TypeError, ValueError):
                continue
            plots.append(n)
            bare.append(n)
    seen = set()
    out = []
    for n in plots:
        if n >= 1 and n not in seen:
            seen.add(n)
            out.append(n)
    out.sort()
    seen_b = set()
    bare_out = []
    for n in bare:
        if n >= 1 and n not in seen_b:
            seen_b.add(n)
            bare_out.append(n)
    return out, bare_out


def _fert_targets(raw, max_plots):
    """「施肥」参数解析（优先复用 farm 服务 fert_targets）：裸数字 ≤max_plots 视为地块编号，
    >max_plots 视为分钟数 → (土地编号列表, 分钟 or None)"""
    api = _farm_api()
    fn = getattr(api, "fert_targets", None) if api is not None else None
    if fn is not None:
        try:
            return fn(raw, max_plots)
        except Exception:
            pass
    if raw is None:
        return [], None
    nums, bare = _parse_plot_numbers_impl(raw)
    time_cands = [n for n in bare if n > max_plots]
    time_min = time_cands[-1] if time_cands else None
    if time_cands:
        nums = [n for n in nums if n not in time_cands]
    return nums, time_min


# ---- 2.0.0：作物成长阶段 & 生长模型（优先 farm 服务 plot_growth，缺失时本地同款） ----
def _plot_grade(grade):
    grades = _CORE.farm_grades()
    return grades[grade] if 0 <= grade < len(grades) else grades[0]


def _crop_level_ranges():
    """作物等级划分上限（分钟）"""
    raw = _CORE.param("CROP_LEVEL_RANGES", (0, 240, 480, 720, 1440))
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
    raw = _CORE.param("CROP_LEVEL_STAGES", (4, 5, 5, 6))
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
    """按贫瘠土地上的成熟分钟数划分作物等级（1~N 级）"""
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


def _plot_growth_local(plot, crop, now):
    """生长状态计算（farm 服务缺失时的本地同款实现，2.0.0 阶段模型）"""
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


def _growth(plot, crop, now):
    """生长状态计算（优先 farm 服务 plot_growth）"""
    api = _farm_api()
    fn = getattr(api, "plot_growth", None) if api is not None else None
    if fn is not None:
        try:
            return fn(plot, crop, now)
        except Exception:
            pass
    return _plot_growth_local(plot, crop, now)


def _fert_max_accel(fert):
    """化肥可加速次数（-1 = 不限）。兼容旧字段 max_uses"""
    v = fert.get("max_accel", fert.get("max_uses", -1))
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return -1


def _fert_remaining_accel(plot, fert):
    """该化肥在这块地上的剩余可加速次数"""
    max_accel = _fert_max_accel(fert)
    if max_accel < 0:
        return 10 ** 9
    used = int(plot.get("fert_accel", {}).get(fert["name"], 0))
    return max(0, max_accel - used)


def _fert_actual_available_min(plot, crop, fert, now):
    """化肥实际可用时间（分钟）：
    理论最大使用时间 = 可加速次数 × 每阶段时间；
    实际可用时间 = 剩余可加速完整阶段次数 × 阶段时间 + 残余阶段总时间；
    且满足「当前所用时间 + 实际可用时间 ≤ 当前土地上的最大生长时间」。"""
    g = _growth(plot, crop, now)
    if g["mature"]:
        return 0.0
    stage_min = g["stage_sec"] / 60.0
    remain = _fert_remaining_accel(plot, fert)
    if remain <= 0:
        return 0.0
    residual_min = g["stage_left_sec"] / 60.0
    if residual_min >= stage_min - 1e-9:
        # 恰在阶段起点：剩余加速都作用于完整阶段
        avail = remain * stage_min
    else:
        # 残余阶段先消耗一次加速（残余阶段总时间），其余为完整阶段
        avail = (remain - 1) * stage_min + residual_min
    cap = g["total_sec"] / 60.0 - g["progress_sec"] / 60.0
    if cap <= 0:
        return 0.0
    return max(0.0, min(avail, cap))


def _fert_to_next_stage_min(plot, crop, now):
    """使用到下一个成长阶段所需的时间（分钟）= 当前阶段剩余时间"""
    g = _growth(plot, crop, now)
    if g["mature"]:
        return 0
    return max(0, int(g["stage_left_sec"] / 60.0 + 0.999))


def _apply_fert_minutes(plot, crop, fert, minutes, now):
    """对一块种植地使用化肥 minutes 分钟（不落盘）：
    推进成熟时间、记录该化肥的加速次数、按加速次数增加产量。
    返回 (加速次数, 增产百分比)。"""
    g = _growth(plot, crop, now)
    stage_sec = g["stage_sec"]
    stage_before = int(g["progress_sec"] // stage_sec) if stage_sec > 0 else 0
    advance = float(minutes) * 60.0
    plot["fert_advance"] = float(plot.get("fert_advance", 0.0)) + advance
    # 兼容旧字段（减时比例）
    base_sec = g["base_sec"]
    plot["fert_time"] = min(0.95, float(plot.get("fert_time", 0.0)) + advance / base_sec)
    progress_after = g["progress_sec"] + advance
    stage_after = int(progress_after // stage_sec) if stage_sec > 0 else 0
    crossed = max(0, stage_after - stage_before)
    acc = plot.setdefault("fert_accel", {})
    acc[fert["name"]] = int(acc.get(fert["name"], 0)) + crossed
    add_pct = 0.0
    if crossed > 0:
        ya = float(fert.get("yield_add", 0) or 0)
        if ya > 0:
            add_pct = crossed * ya
            plot["fert_yield"] = float(plot.get("fert_yield", 0.0)) + add_pct
    # 重算成熟时间与产量（成熟时间 = 种植时间 + 土地最大生长时间 - 化肥累计推进）
    _, gy, gt = _plot_grade(int(plot.get("grade", 0)))
    total_sec = base_sec * (1 - gt)
    plot["mature_ts"] = float(plot.get("plant_ts", 0)) + total_sec - float(plot.get("fert_advance", 0.0))
    plot["yield"] = int((crop["yield"] if crop else 0) * (1 + gy + float(plot.get("fert_yield", 0.0))))
    return crossed, add_pct


def _apply_fert_to_plots(plots, use_plan, fert, now, crops=None):
    """对 use_plan={地块下标: 使用分钟} 中的地块施肥（不落盘）。
    返回 (总加速次数, 使用地块数)。"""
    if crops is None:
        crops = _load_crops()
    total_accel = 0
    n_used = 0
    for i, minutes in use_plan.items():
        plot = plots[i]
        crop = _find_item(crops, plot.get("crop", ""))
        if not crop or minutes <= 0:
            continue
        crossed, _ = _apply_fert_minutes(plot, crop, fert, minutes, now)
        total_accel += crossed
        n_used += 1
    return total_accel, n_used


# ================= 图片回复（土地状态；渲染器缺失/异常回退文本） =================
def _is_image(img):
    return isinstance(img, tuple) and len(img) >= 2 and img[0] == "image"


def _plot_status_reply(name, key, farm, crops=None, ferts=None, steal_lines=None,
                       highlights=None, actions=None, profit_delta=0, coins_delta=None):
    """土地状态图片回复（纯图片）；render_plot_status 缺失/异常时回退 2.3.0 同款文本。"""
    if crops is None:
        crops = _load_crops()
    if ferts is None:
        ferts = _load_fertilizers()
    render = getattr(_CORE.image, "render_plot_status", None)
    if render is not None:
        try:
            img = render(name, key, _CORE.data, farm, crops, ferts,
                         steal_lines=steal_lines, highlights=highlights,
                         actions=actions, profit_delta=profit_delta,
                         coins_delta=coins_delta)
        except Exception as e:
            logger.error(f"[farm_shop] 渲染土地状态图片异常: {e}")
            img = None
        if _is_image(img):
            return img
    if actions:
        return "\n".join(str(a) for a in actions)
    return "图片生成失败（缺少 Pillow 或字体），请查看日志。"


# ================= 商店展示 =================
def _farm_shop_seed_list(farm, crops, expanded=False, page=1, all_items=False):
    """农场商店种子选择（2.0.2）：
    全部（「农场商店 全部」）= 所有种子（可购 + 不可购）；
    展开 = 全部可购种子按价格升序分页（每页 FARM_SHOP_SHOW_BUY 款）；
    默认 = 可购等级最高的 FARM_SHOP_SHOW_BUY 款 + 不可购等级最低的 FARM_SHOP_SHOW_LOCKED 款灰卡。
    统一按价格升序返回。"""
    farm_lv = int(farm.get("level", 0))
    mult = _seed_mult(farm)
    price_of = lambda c: int(round(float(c["seed_price"]) * mult))
    buy_n = int(_CORE.param("FARM_SHOP_SHOW_BUY", FARM_SHOP_SHOW_BUY))
    lock_n = int(_CORE.param("FARM_SHOP_SHOW_LOCKED", FARM_SHOP_SHOW_LOCKED))
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


def _seed_discount_map(crops):
    """种子每日折扣（2.0.3，默认关闭）：每天有概率让 1~3 款种子打八折（肥料不受影响）。
    按「当天」固定随机（同一天折扣款一致）；返回 {作物名: 倍率}（无折扣为空）。"""
    out = {}
    if not bool(_CORE.param("SEED_DISCOUNT_ENABLED", SEED_DISCOUNT_ENABLED)):
        return out
    today = date.today().isoformat()
    rng = random.Random("seed_discount|" + today)
    if rng.random() >= float(_CORE.param("SEED_DISCOUNT_CHANCE", SEED_DISCOUNT_CHANCE)):
        return out
    names = sorted({c["name"] for c in crops})
    if not names:
        return out
    n = rng.randint(int(_CORE.param("SEED_DISCOUNT_MIN", SEED_DISCOUNT_MIN) or 1),
                    int(_CORE.param("SEED_DISCOUNT_MAX", SEED_DISCOUNT_MAX) or 3))
    pct = float(_CORE.param("SEED_DISCOUNT_PCT", SEED_DISCOUNT_PCT) or 0.8)
    for nm in rng.sample(names, min(n, len(names))):
        out[nm] = pct
    return out


def _handle_shop(event):
    """农场商店：种子（上）+ 化肥（下）合并展示。
    农场商店 [展开] [页码]：默认按规则展示（可购 N 款 + 不可购 M 款灰卡 + 全部化肥）；
    农场商店 全部：展示所有商品（全部种子 + 全部化肥）。"""
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    parts = event.message_str.split()
    expanded = False
    all_items = False
    page = 1
    if len(parts) >= 2:
        if parts[1] == "全部":
            all_items = True
        elif parts[1] == "展开":
            expanded = True
            if len(parts) >= 3:
                try:
                    page = max(1, int(parts[2]))
                except ValueError:
                    page = 1
    err = _farm_need(key, name)
    if err:
        return err
    farm = _farm_of(key)
    crops = _load_crops()
    ferts = _load_fertilizers()
    if not crops and not ferts:
        return "作物与肥料配置为空（请管理员在 WebUI 编辑 作物.txt / 肥料.txt）。"
    render = getattr(_CORE.image, "render_farm_shop", None)
    if render is not None:
        try:
            img = render(name, farm, crops, ferts, expanded, page, all_items)
        except Exception as e:
            logger.error(f"[farm_shop] 渲染农场商店图片异常: {e}")
            img = None
        if _is_image(img):
            return img
    # 文本回退（同样按价格升序）
    lines = [f"{name} 的农场商店（发送「农场商店 展开」查看全部种子，发送「农场商店 全部」查看全部商品）"]
    mult = _seed_mult(farm)
    seed_list = _farm_shop_seed_list(farm, crops, expanded, page, all_items)
    for c in sorted(seed_list, key=lambda c: (int(round(c["seed_price"] * mult)), c["name"])):
        p = int(round(c["seed_price"] * mult))
        lv = f"需Lv.{c['min_level']}" if c["min_level"] > 0 else "无等级"
        desc = f"（{c['desc']}）" if c.get("desc") else ""
        lines.append(f"🌱 {c['name']}（{lv}）{p}金币{desc} 售价{int(round(c['yield']*c['crop_price']))}金 经验{c['exp']}")
    for f_ in sorted(ferts, key=lambda f_: (int(f_["price"]), f_["name"])):
        desc = f"（{f_['desc']}）" if f_.get("desc") else ""
        maxa = "不限" if _fert_max_accel(f_) < 0 else f"{_fert_max_accel(f_)}次"
        ya = float(f_.get("yield_add", 0) or 0)
        extra = f" 增产{ya:.0f}%/次" if ya > 0 else ""
        lines.append(f"🧪 {f_['name']} {int(f_['price'])}金币/时{desc} 每株可加速 {maxa}{extra}")
    return "\n".join(lines)


# ================= 购买种子 / 购买肥料 =================
def _buy_seed_core(key, name, crop_name, count):
    """购买种子核心逻辑（供「购买」「购买种子」使用），盈利即时扣减成本；
    2.0.3：种子每日折扣（打八折款按折后价结算）"""
    crops = _load_crops()
    crop = _find_item(crops, crop_name)
    if not crop:
        return f"没有「{crop_name}」这种作物，发送「农场商店」查看。"
    farm = _farm_of(key)
    if not farm:
        return f"{name} 还没有农场，发送「解锁农场」（需 {FARM_UNLOCK_COST} 金币）解锁。"
    if int(farm.get("level", 0)) < crop["min_level"]:
        return f"农场等级不足（需要 Lv.{crop['min_level']}，当前 Lv.{farm['level']}）。"
    disc = _seed_discount_map(crops).get(crop_name, 1.0)
    p = round(crop["seed_price"] * _seed_mult(farm) * disc, 2)
    total = int(round(p * count))
    if _CORE.coins_of(key) < total:
        return f"金币不足（需要 {total}，当前 {_CORE.coins_of(key)}）。"
    _CORE.add_coins(key, -total, f"购买种子·{crop_name}")
    wh = farm["warehouse"].setdefault("seeds", {})
    wh[crop_name] = int(wh.get(crop_name, 0)) + count
    # 盈利即时扣减种子成本（允许为负）
    farm["total_profit"] = int(farm.get("total_profit", 0)) - total
    _CORE.save()
    price_note = f"（折后价，原价 {_fmt_price(crop['seed_price'] * _seed_mult(farm))}）" if disc < 1.0 else ""
    return (f"✅ 购买 {crop_name} 种子 ×{count}，花费 {total} 金币（单价 {_fmt_price(p)}）{price_note}。\n"
            f"{_CORE.coin_line(key)}\n"
            f"{_state_snippet(farm)}")


def _buy_fert_core(key, name, fert_name, hours):
    """购买化肥核心逻辑（2.0.0 起按「小时」购买，最小单位为 1 小时），盈利即时扣减成本"""
    fert = _find_item(_load_fertilizers(), fert_name)
    if not fert:
        return f"没有「{fert_name}」这种肥料，发送「农场商店」查看。"
    farm = _farm_of(key)
    if not farm:
        return f"{name} 还没有农场，发送「解锁农场」（需 {FARM_UNLOCK_COST} 金币）解锁。"
    try:
        hours = max(1, int(float(hours)))
    except (TypeError, ValueError):
        return "小时数必须是整数。"
    total = int(fert["price"]) * hours
    if _CORE.coins_of(key) < total:
        return f"金币不足（需要 {total}，当前 {_CORE.coins_of(key)}）。"
    _CORE.add_coins(key, -total, f"购买肥料·{fert_name}")
    wh = farm["warehouse"].setdefault("fertilizers", {})
    wh[fert_name] = float(wh.get(fert_name, 0) or 0) + hours
    # 盈利即时扣减肥料成本（允许为负）
    farm["total_profit"] = int(farm.get("total_profit", 0)) - total
    _CORE.save()
    return (f"✅ 购买 {fert_name} {hours} 小时（单价 {int(fert['price'])} 金币/小时），花费 {total} 金币。\n"
            f"{_CORE.coin_line(key)}\n"
            f"{_state_snippet(farm)}")


def _resolve_crop_token(crop_name):
    """作物名解析：精确名优先；「购买 <作物>种子 <数量>」（pet_shop 委托）兼容去「种子」后缀。
    返回用于购买/报错的作物名。"""
    crops = _load_crops()
    if _find_item(crops, crop_name) is not None:
        return crop_name
    if crop_name.endswith("种子") and _find_item(crops, crop_name[:-2]) is not None:
        return crop_name[:-2]
    return crop_name


def _handle_buy_seed(event):
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    parts = event.message_str.split(maxsplit=1)
    if len(parts) < 2:
        return "格式：购买种子 <作物名> <数量>（或直接「购买 <作物名>种子 <数量>」）"
    args = parts[1].split()
    crop_name = args[0]
    count = 1
    if len(args) >= 2:
        try:
            count = int(args[1])
        except ValueError:
            return "数量必须是整数。"
    if count <= 0:
        return "数量必须为正整数。"
    return _buy_seed_core(key, name, _resolve_crop_token(crop_name), count)


def _handle_buy_fert(event):
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    parts = event.message_str.split(maxsplit=1)
    if len(parts) < 2:
        return "格式：购买肥料 <肥料名> <小时数>（最小购买单位为 1 小时，如「购买肥料 化肥 2」= 2 小时）"
    args = parts[1].split()
    fert_name = args[0]
    hours = 1
    if len(args) >= 2:
        try:
            hours = int(args[1])
        except ValueError:
            return "小时数必须是整数。"
    if hours <= 0:
        return "小时数必须为正整数（最小购买单位为 1 小时）。"
    return _buy_fert_core(key, name, fert_name, hours)


# ================= 施肥 =================
def _handle_fertilize(event):
    """施肥（2.0.0）：
    完整格式：施肥 <化肥名称> <土地编号> <时间（分钟）>，例：施肥 化肥 1,3 60
    快捷（结构不全）：
    - 缺化肥名称 → 按顺序先用「化肥」，达到可加速上限再用「有机化肥」
    - 缺土地编号 → 默认所有可用土地
    - 缺使用时间 → 使用到下一个成长阶段所需的化肥时间
    - 全缺失 → 按缺化肥名称的顺序，对所有可用土地使用到下一阶段所需时间
    土地编号规则（不分全半角）：逗号分隔（1，6 → 1号、6号）；括号（（20）→ 20号）；
    括号逗号分隔（（1，7）→ 1~7号区间）；裸数字 ≤ 最大地块数（3 → 3号地）。
    时间：裸数字 > 最大地块数 视为使用分钟数。"""
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    data = _CORE.data
    err = _farm_need(key, name)
    if err:
        return err
    farm = _farm_of(key)
    parts = event.message_str.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        # 全缺失 → 快捷施肥
        return _auto_fertilize(event)
    raw = parts[1].strip()
    ferts = _load_fertilizers()
    crops = _load_crops()
    plots = farm["plots"]
    now = datetime.now().timestamp()
    max_plots = len(plots)

    # 化肥名：第一个 token 是已知化肥名则取出
    tokens = raw.split()
    fert = None
    if tokens:
        cand = _find_item(ferts, tokens[0])
        if cand is not None:
            fert = cand
            raw = raw[len(tokens[0]):].strip()
    if fert is None:
        # 缺少化肥名称 → 快捷路径（自动选择化肥）
        return _auto_fertilize(event, raw=raw)

    # 解析土地编号与时间
    target_plots, time_min = _fert_targets(raw, max_plots) if raw else ([], None)
    bad = [n for n in target_plots if n < 1 or n > max_plots]
    if bad:
        return f"土地编号超出范围：{'、'.join(str(n) for n in bad)}（当前共 {max_plots} 块地）。"
    growing = [i for i, p in enumerate(plots)
               if p.get("crop") is not None and now < p.get("mature_ts", 0)]
    if target_plots:
        tset = set(target_plots)
        targets = [i for i in growing if (i + 1) in tset]
    else:
        targets = growing
    if not targets:
        return "没有正在生长中的作物可以施肥（所选土地未种植或已成熟）。"

    wh = farm["warehouse"].setdefault("fertilizers", {})
    have = float(wh.get(fert["name"], 0) or 0)
    # 每块地实际可用时间
    use_plan = {}
    for i in targets:
        plot = plots[i]
        crop = _find_item(crops, plot.get("crop", ""))
        if not crop:
            continue
        avail = _fert_actual_available_min(plot, crop, fert, now)
        if avail <= 0:
            continue
        want = time_min if time_min is not None else _fert_to_next_stage_min(plot, crop, now)
        use_plan[i] = min(want, avail)
    if not use_plan:
        return "目标土地都无法再使用该化肥（已达最大可加速次数）。"
    total_need_h = sum(use_plan.values()) / 60.0
    if have + 1e-9 < total_need_h:
        return (f"{fert['name']} 库存不足（需要 {_fmt_hours(total_need_h)} 小时，"
                f"当前 {_fmt_hours(have)} 小时）。发送「购买肥料 {fert['name']} {math.ceil(total_need_h)}」购买。")
    total_accel, n_plots = _apply_fert_to_plots(plots, use_plan, fert, now, crops)
    wh[fert["name"]] = have - total_need_h
    if wh[fert["name"]] <= 1e-9:
        wh.pop(fert["name"], None)
    _farm_log(data, key, "fertilize",
              f"{fert['name']} {_fmt_hours(total_need_h)} → {n_plots} 块地（加速 {total_accel} 次）",
              coins=0, qty=n_plots)
    _CORE.save()
    used_min = sum(use_plan.values())
    actions = [f"🧪 使用 {fert['name']} {_fmt_hours(used_min / 60.0)} 小时"
               f"（{used_min:.0f} 分钟，作用于 {n_plots} 块地，加速 {total_accel} 次）"]
    # 纯图片回复：施肥地块蓝色高亮（#B4C7E7）+ 底部大卡片
    return _plot_status_reply(
        name, key, farm, crops=crops, ferts=ferts,
        highlights={i + 1: "fert" for i in use_plan},
        actions=actions)


def _auto_fertilize(event, raw=None):
    """施肥快捷流程（缺化肥名 / 全缺失）：
    按顺序先使用「化肥」，某块地达到可加速上限则改用「有机化肥」，再不行任意可用化肥；
    raw 指定则解析其中的土地编号 / 时间（缺省全部土地 + 用到下一阶段所需时间）；
    库存不足的化肥自动购买（金币不足则买多少算多少）。"""
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    data = _CORE.data
    err = _farm_need(key, name)
    if err:
        return err
    farm = _farm_of(key)
    if not farm or not farm.get("plots"):
        return "还没有土地，发送「购买土地」开垦。"
    now = datetime.now().timestamp()
    ferts = _load_fertilizers()
    if not ferts:
        return "还没有配置任何化肥，发送「农场商店」查看。"
    crops = _load_crops()
    plots = farm["plots"]
    max_plots = len(plots)
    target_plots, time_min = _fert_targets(raw, max_plots) if raw else ([], None)
    bad = [n for n in target_plots if n < 1 or n > max_plots]
    if bad:
        return f"土地编号超出范围：{'、'.join(str(n) for n in bad)}（当前共 {max_plots} 块地）。"
    growing = [i for i, p in enumerate(plots)
               if p.get("crop") is not None and now < p.get("mature_ts", 0)]
    if target_plots:
        tset = set(target_plots)
        growing = [i for i in growing if (i + 1) in tset]
    if not growing:
        return "没有正在生长中的作物可以施肥。"

    def pick_fert(plot):
        # 优先「化肥」；不可用则「有机化肥」；再不行则任意一种可用化肥
        for f in ferts:
            if f["name"] == "化肥" and _fert_remaining_accel(plot, f) > 0:
                return f
        for f in ferts:
            if "有机" in f["name"] and _fert_remaining_accel(plot, f) > 0:
                return f
        for f in ferts:
            if _fert_remaining_accel(plot, f) > 0:
                return f
        return None

    plan = {}  # plot_idx -> (fert, minutes)
    for i in growing:
        plot = plots[i]
        crop = _find_item(crops, plot.get("crop", ""))
        if not crop:
            continue
        f = pick_fert(plot)
        if f is None:
            continue
        if time_min is not None:
            use = min(float(time_min), _fert_actual_available_min(plot, crop, f, now))
        else:
            use = min(float(_fert_to_next_stage_min(plot, crop, now)),
                      _fert_actual_available_min(plot, crop, f, now))
        if use > 0:
            plan[i] = (f, use)
    if not plan:
        return "生长中的土地都已达到各化肥的最大可加速次数。"
    # 统计需要购买的化肥（小时）
    need = {}
    for _, (f, minutes) in plan.items():
        need[f["name"]] = need.get(f["name"], 0) + minutes / 60.0
    wh = farm["warehouse"].setdefault("fertilizers", {})
    actions = []
    coins_spent = 0
    for fname, need_h in need.items():
        have = float(wh.get(fname, 0) or 0)
        if have + 1e-9 >= need_h:
            continue
        fert = _find_item(ferts, fname)
        unit = int(fert["price"]) if fert else 0
        buy_h = need_h - have
        coins = _CORE.coins_of(key)
        if unit > 0:
            buy_h = min(buy_h, coins // unit)
        if buy_h <= 0:
            continue
        spent = int(round(buy_h * unit))
        _CORE.add_coins(key, -spent, f"购买肥料·{fname}")
        farm["total_profit"] = int(farm.get("total_profit", 0)) - spent
        wh[fname] = float(wh.get(fname, 0) or 0) + buy_h
        coins_spent += spent
        actions.append(f"🛒 自动购买 {fname} ×{_fmt_hours(buy_h)} 小时（花费 {spent} 金币）")
    # 执行施肥（按实际库存扣减）
    used_plots = []
    total_min = 0.0
    total_accel = 0
    for i, (f, minutes) in plan.items():
        fname = f["name"]
        have = float(wh.get(fname, 0) or 0)
        need_this = minutes / 60.0
        if have + 1e-9 < need_this:
            minutes = have * 60.0
        if minutes <= 0:
            continue
        plot = plots[i]
        crop = _find_item(crops, plot.get("crop", ""))
        crossed, _ = _apply_fert_minutes(plot, crop, f, minutes, now)
        total_accel += crossed
        total_min += minutes
        wh[fname] = float(wh.get(fname, 0) or 0) - minutes / 60.0
        if wh[fname] <= 1e-9:
            wh.pop(fname, None)
        used_plots.append(i + 1)
    if used_plots:
        _farm_log(data, key, "fertilize",
                  f"快捷施肥：{total_min:.0f} 分钟 → {len(used_plots)} 块地（加速 {total_accel} 次）"
                  + (f"，自动购买化肥花费 {coins_spent} 金币" if coins_spent else ""),
                  coins=coins_spent, qty=len(used_plots))
    _CORE.save()
    if not used_plots:
        return "化肥库存不足且金币不足，无法自动购买施肥。"
    actions.insert(0, f"🧪 施肥完成：对 {len(used_plots)} 块地使用化肥 {total_min:.0f} 分钟（加速 {total_accel} 次）")
    return _plot_status_reply(
        name, key, farm, crops=crops, ferts=ferts,
        highlights={n: "fert" for n in used_plots},
        profit_delta=-coins_spent,
        actions=actions,
        coins_delta=-coins_spent if coins_spent else None)


# ================= 挂载入口 =================
def register(core):
    global _CORE
    _CORE = core

    core.command("种子商店", "农场商店", "肥料商店", feature="farm_shop")(_handle_shop)
    core.command("购买种子", feature="farm_shop")(_handle_buy_seed)
    core.command("购买肥料", feature="farm_shop")(_handle_buy_fert)
    core.command("施肥", feature="farm_shop")(_handle_fertilize)

    core.add_help("农场商店", [
        ("农场商店 [展开|全部] [页码]", "种子+化肥合并展示（展开=可购种子分页；全部=所有商品；种子商店/肥料商店同款）"),
        ("购买种子 <作物名> [数量]", "购买种子（或「购买 <作物名>种子 <数量>」）"),
        ("购买肥料 <肥料名> [小时数]", "按小时购买化肥（最小 1 小时，如「购买肥料 化肥 2」）"),
        ("施肥 [化肥] [编号] [分钟]", "施肥（缺省=快捷施肥：自动选肥/全部生长地/施到下一阶段，缺料自动购买）"),
    ])

    # ================= 服务暴露（其他插件经 core.service("farm_shop") 调用） =================
    class FarmShopApi:
        """农场商店服务：种子/化肥购买（「购买」指令委托，自行解析事件参数）与肥料配置"""

        def buy_seed(self, event):
            """购买种子（接受原始消息事件并自行解析「<作物名> [数量]」，兼容「<作物>种子」后缀）。
            返回回复文本（2.3.0 同款）。"""
            return _handle_buy_seed(event)

        def buy_fert(self, event):
            """购买化肥（接受原始消息事件并自行解析「<肥料名> [小时数]」）。返回回复文本。"""
            return _handle_buy_fert(event)

        def fert_list(self):
            """肥料配置列表（core.items()["ferts"] 规范化，结构与 2.3.0 一致）"""
            return _load_fertilizers()

    core.expose("farm_shop", FarmShopApi())
