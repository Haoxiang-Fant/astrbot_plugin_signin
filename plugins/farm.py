# -*- coding: utf-8 -*-
"""农场种地（3.0.0 功能插件）。自 2.3.0 modules/farm.py 农场主体原样迁移：
解锁农场 / 购买土地 / 土地升级 / 种植·种地（快捷种地）/ 收割·收获 / 取消种植 /
土地状态·我的农场 / 农场帮助。农场数据存于 data["farms"]（结构不变：
level/exp/plots[crop,planted_ts,mature_ts,grade,…]/warehouse(crops,seeds,fertilizers)/tools/
steal_infos）。种子/化肥的购买与施肥、售卖、偷菜、守护由 farm_shop / farm_sell /
farm_steal / farm_guard 插件实现；本插件向它们暴露 farm 服务（编号解析、地块读写、
经验/状态等快捷助手）。作物成熟判定按 mature_ts 时间戳，无需每日结算。"""
import random
import re
from datetime import datetime

from astrbot.api import logger

from .. import core as _core_mod
from ..core import (FARM_UNLOCK_COST, FARM_PLOT_COST, FARM_FREE_PLOTS, FARM_MAX_PLOTS,
                    FARM_MAX_LEVEL, FARM_EXP_BASE, FARM_UPGRADE_COSTS)

NAME = "farm"

_CORE = None  # register(core) 时注入的核心框架实例
_FARM_LOG_MAX = 300


# ================= 作物 / 肥料配置（game_items.json，缺省回退旧版 txt） =================
def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _parse_crop_fert(path, kind):
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


def _norm_crop_entry(d):
    """规范化一条作物配置（扁平 dict，键与 _parse_crop_fert 输出一致）"""
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
    """优先读 game_items.json（core.items()）；为空时回退解析 作物.txt"""
    flat = _CORE.items() or {}
    crops = [c for c in (_norm_crop_entry(d) for d in (flat.get("crops") or [])) if c]
    if crops:
        return crops
    return _parse_crop_fert(_core_mod.CROP_FILE, "作物")


def _load_fertilizers():
    """优先读 game_items.json（core.items()）；为空时回退解析 肥料.txt"""
    flat = _CORE.items() or {}
    ferts = [f_ for f_ in (_norm_fert_entry(d) for d in (flat.get("ferts") or [])) if f_]
    if ferts:
        return ferts
    return _parse_crop_fert(_core_mod.FERT_FILE, "肥料")


def _find_item(items, name):
    return next((x for x in items if x["name"] == name), None)


# ================= 农场数据（data["farms"]，结构不变） =================
def _farm_of(data, key):
    return data.get("farms", {}).get(key)


def _ensure_farm(data, key):
    return data.setdefault("farms", {}).setdefault(key, {
        "level": 0, "exp": 0.0, "plots": [],
        "warehouse": {"crops": {}, "seeds": {}, "fertilizers": {}},
        "tools": {},          # 农场特殊道具（如 农场经验球 2.0.1 改属农场）
        "total_profit": 0,
        "steal_infos": [], "steal_log": {}, "scent_memory": {},
    })


def _new_plot():
    return {"grade": 0, "crop": None, "seed": None, "plant_ts": 0, "mature_ts": 0,
            "base_time": 0, "yield": 0, "fert_time": 0.0, "fert_yield": 0.0, "fert": {}}


def _plot_grade(grade):
    grades = _CORE.farm_grades()  # 2.0.2：WebUI「设置 → 农场 → 土地」表格可编辑（FARM_GRADES）
    return grades[grade] if 0 <= grade < len(grades) else grades[0]


def _plot_free(plot):
    return plot is None or plot.get("crop") is None


def _farm_seed_mult(farm):
    lv = int(farm.get("level", 0))
    if lv <= 19:
        return 1.5
    if lv <= 49:
        return 1.0
    if lv <= 99:
        return 0.8
    return 0.7


def _farm_gain_exp(farm, amount) -> str:
    if int(farm.get("level", 0)) >= FARM_MAX_LEVEL:
        return ""
    farm["exp"] = float(farm.get("exp", 0.0)) + amount
    level = int(farm.get("level", 0))
    old = level
    while level < FARM_MAX_LEVEL:
        need = FARM_EXP_BASE * (level + 1)
        if farm["exp"] >= need:
            farm["exp"] = round(farm["exp"] - need, 2)
            level += 1
        else:
            break
    farm["level"] = level
    if level > old:
        return f"\n🎉 农场升级！Lv.{old} → Lv.{level}"
    return ""


def _farm_snippet(farm) -> str:
    """农场当前状态摘要（农场变更反馈末尾附加）"""
    wh = farm.get("warehouse", {})
    n_plot = len(farm.get("plots", []))
    n_crop = sum(int(v) for v in wh.get("crops", {}).values())
    n_seed = sum(int(v) for v in wh.get("seeds", {}).values())
    n_fert = sum(int(v) for v in wh.get("fertilizers", {}).values())
    return (f"🌾 农场 Lv.{farm.get('level', 0)}｜土地 {n_plot} 块｜"
            f"仓库：作物 {n_crop} / 种子 {n_seed} / 肥料 {n_fert}")


def _farm_need(data, key, name):
    if not _farm_of(data, key):
        return f"{name} 还没有农场，发送「解锁农场」（需 {FARM_UNLOCK_COST} 金币）解锁。"
    return None


def _farm_log(data, key, act, detail="", coins=0, qty=0):
    """2.2.3：农场操作记录（种植/施肥/收割/偷菜，随 records.json 存储）+ 累计统计
    （farm_stats：各类次数 / 收获产量 / 偷菜收益 / 购种购肥支出；总盈利另见 farm.total_profit）。
    记录上限 _FARM_LOG_MAX 条（超出丢弃最旧）；统计为累计值不受上限影响。"""
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


# ================= 通用工具 =================
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


def _fmt_price(v):
    """价格显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0"""
    v = float(v)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _fmt_plot_nums(nums):
    """格式化地块编号列表（1-based）：连续 → 编号 1~8；不连续 → 编号 1、3、5；单块 → 编号 3"""
    nums = sorted(set(int(n) for n in nums))
    if not nums:
        return "编号 无"
    if len(nums) == 1:
        return f"编号 {nums[0]}"
    if all(nums[j + 1] - nums[j] == 1 for j in range(len(nums) - 1)):
        return f"编号 {nums[0]}~{nums[-1]}"
    return "编号 " + "、".join(str(n) for n in nums)


# ---- 2.2.0：土地编号统一解析（单块 / 区间 / 列表，括号逗号不分全半角） ----
def _parse_plot_numbers_impl(raw):
    """2.2.0 土地编号统一解析（括号、逗号不分全角半角）：
    - 单块土地：直接输入编号，如 土地升级 1
    - 连续多块：区间表示法（1,8）→ 1~8 号地（括号内为最小编号,最大编号，顺序不限）
    - 序号不连续：逗号分隔全部编号，如 1,3,5,7
    返回 (编号列表, 裸数字列表)：
    编号列表 = 全部解析出的土地编号（升序、去重、≥1，未校验上界，由调用方校验）；
    裸数字列表 = 以「独立数字」形式出现的编号（括号组/逗号列表内的编号不属裸数字，
    供调用方区分「数量」「时间」等语义，如 种植 的数量、施肥 的分钟数）。"""
    if raw is None:
        return [], []
    s = str(raw).strip().replace("，", ",").replace("、", ",").replace("（", "(").replace("）", ")")
    if not s:
        return [], []
    plots = []
    bare = []
    # 1) 括号组：单号或区间
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
    # 2) 剩余文本：逗号组内数字均视为土地编号；独立数字记录为裸数字
    for chunk in re.split(r"\s+", rest):
        chunk = chunk.strip().strip(",")
        if not chunk:
            continue
        if "," in chunk:
            # 逗号分隔 → 全部视为土地编号
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
    # 3) 去重、排序（仅保留 ≥1）
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


def plot_parser(raw, max_n=None):
    """土地编号统一解析（2.2.0 规则，供其他农场插件经 core.service("farm") 复用）。
    raw 支持：单块 1 / 连续区间 (1,8)（全角（1，8）亦可）/ 不连续列表 1,3,5 / 顿号 1、3。
    返回 (nums, bare, bad)：
    - nums：全部土地编号（升序、去重、≥1；未校验上界）
    - bare：以「独立数字」出现的编号（括号组/逗号列表内不属裸数字，供区分数量/时间语义）
    - bad：max_n 提供时为 nums 中 <1 或 >max_n 的编号（上界校验），未提供时为 []"""
    nums, bare = _parse_plot_numbers_impl(raw)
    bad = []
    if max_n is not None:
        bad = [n for n in nums if n < 1 or n > int(max_n)]
    return nums, bare, bad


def _parse_fert_targets(raw, max_plots):
    """解析「施肥」指令的土地编号与使用分钟数（2.2.0 复用土地编号解析）：
    - 括号区间（1,7）→ 1~7 号地；逗号/顿号列表 1，6 → 1、6 号地；单号 1 → 1 号地
    - 裸数字：≤ 最大土地数 → 土地编号；> 最大土地数 → 使用时间（分钟）
    返回 (土地编号列表, 时间分钟 or None)。"""
    if raw is None:
        return [], None
    plots, bare = _parse_plot_numbers_impl(raw)
    time_cands = [n for n in bare if n > max_plots]
    time_min = time_cands[-1] if time_cands else None
    if time_cands:
        plots = [n for n in plots if n not in time_cands]
    return plots, time_min


# ---- 2.0.0：作物成长阶段 & 生长模型 ----
def _crop_level_ranges():
    """作物等级划分上限（分钟）：如 (0,240,480,720,1440) → 0~240 一级 / 241~480 二级 / …"""
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


# ================= 种植 / 收割核心（字段赋值，不落盘） =================
def _clear_plot(plot):
    """清空一块地块（收割/取消种植后重置所有种植字段）"""
    plot["crop"] = None
    plot["seed"] = None
    plot["plant_ts"] = 0
    plot["mature_ts"] = 0
    plot["base_time"] = 0
    plot["yield"] = 0
    plot["fert_time"] = 0.0
    plot["fert_yield"] = 0.0
    plot["fert"] = {}
    plot["fert_advance"] = 0.0
    plot["fert_accel"] = {}
    plot["orig_yield"] = 0


def _plant_plot(plot, crop, now):
    """把作物种到一块空闲地块（字段赋值，不落盘）。
    2.0.0：记录 stage_count / stage_sec（按贫瘠总时间均分）与化肥推进字段。
    2.0.1：记录 orig_yield（原始产量，用于偷菜「保护地块」判定）。"""
    gname, gy, gt = _plot_grade(int(plot.get("grade", 0)))
    base_sec = crop["grow_minutes"] * 60
    plot["crop"] = crop["name"]
    plot["seed"] = crop["name"]
    plot["plant_ts"] = now
    plot["base_time"] = base_sec
    plot["yield"] = int(crop["yield"] * (1 + gy))
    plot["orig_yield"] = plot["yield"]
    plot["mature_ts"] = now + base_sec * (1 - gt)
    plot["fert_time"] = 0.0
    plot["fert_yield"] = 0.0
    plot["fert"] = {}
    plot["fert_advance"] = 0.0
    plot["fert_accel"] = {}


def _harvest_mature(farm, crops, now, targets=None):
    """收割成熟地块作物进仓库（不落盘）。targets=None = 全部成熟地块（targets 为 0-based 下标）。
    返回 (harvested 编号列表[1-based], {作物:数量}, 总经验)。"""
    plots = farm["plots"]
    if targets is None:
        idxs = [i for i, p in enumerate(plots)
                if p.get("crop") is not None and now >= p.get("mature_ts", 0)]
    else:
        idxs = [i for i in targets
                if plots[i].get("crop") is not None and now >= plots[i].get("mature_ts", 0)]
    if not idxs:
        return [], {}, 0
    wh = farm["warehouse"].setdefault("crops", {})
    amounts = {}
    total_exp = 0
    harvested = []
    for i in idxs:
        plot = plots[i]
        crop = _find_item(crops, plot["crop"])
        amount = int(plot.get("yield", 0))
        wh[plot["crop"]] = int(wh.get(plot["crop"], 0)) + amount
        amounts[plot["crop"]] = amounts.get(plot["crop"], 0) + amount
        gy = _plot_grade(int(plot.get("grade", 0)))[1]
        base_exp = int(crop["exp"]) if crop else 0
        total_exp += int(round(base_exp * (1 + gy))) if crop else 0
        harvested.append(i + 1)
        _clear_plot(plot)
    # 被偷批次：本次收割的地块若有偷菜信息，标记 harvest_ts（24h 内可见）
    now_ts = datetime.now().timestamp()
    for it in farm.get("steal_infos", []):
        if it.get("harvest_ts") is None:
            it["harvest_ts"] = now_ts
    return harvested, amounts, total_exp


def _steal_info_lines(farm, now_ts):
    """生成偷菜信息表格行（被偷方视角，仅展示；偷菜逻辑属 farm_steal 插件）：
    用户1|白菜 * 10|损失0|失败：对方等级过低
    有效期：被偷批次作物主动收割后 24 小时"""
    infos = farm.get("steal_infos", [])
    # 过滤：harvest_ts 为空（未收割）→ 显示；已收割且 24h 内 → 显示
    alive = []
    for it in infos:
        hts = it.get("harvest_ts")
        if hts is None or now_ts - hts <= 86400:
            alive.append(it)
    if not alive:
        return []
    # 按偷菜者聚合（同一偷菜者一行，多种作物换行；2.0.3：连续重复折叠）
    by_thief = {}
    for it in alive:
        tid = it["thief_uid"]
        d = by_thief.setdefault(tid, {"name": it["thief_name"], "rows": []})
        for item in it.get("items", []):
            status = item.get("status", "success")
            qty = item.get("qty", 0)
            loss = item.get("loss", 0)
            if status == "level_fail":
                st = "失败：对方等级过低"
            elif status == "protected":
                st = "保护地块"
            elif status == "pet_catch":
                st = "失败：宠物发现"
            elif status == "pet_stop":
                st = "成功（损失减半）"
            else:
                st = "成功"
            # [作物, 数量, 损失, 状态, 次数]
            d["rows"].append([item["crop"], qty, loss, st, 1])
    lines = ["🥬 偷菜记录（被偷批次收割后 24 小时内显示）："]
    for tid, d in by_thief.items():
        # 2.0.3：折叠连续重复（同一偷菜者 + 同作物 + 同成败状态）→ *N 表示次数
        merged = []
        for r in d["rows"]:
            if merged and merged[-1][0] == r[0] and merged[-1][3] == r[3]:
                merged[-1][4] += 1
            else:
                merged.append(list(r))
        for crop, qty, loss, st, count in merged:
            crop_txt = f"{crop} * {qty}" + (f"*{count}" if count > 1 else "")
            lines.append(f"{d['name']}|{crop_txt}|损失{loss}|{st}")
    return lines


# ================= 文本状态行 & 回复渲染 =================
def _plot_status_lines(farm):
    """土地状态文本行（「土地状态」图片缺失时的富文本/纯文本回退；
    亦经 farm 服务暴露给其他插件拼装文本）。内容与 2.3.0 土地卡片一致。"""
    now = datetime.now().timestamp()
    crops = _load_crops()
    plots = farm.get("plots", [])
    level = int(farm.get("level", 0))
    exp = float(farm.get("exp", 0.0))
    need = FARM_EXP_BASE * (level + 1) if level < FARM_MAX_LEVEL else 0
    lines = []
    exp_text = f"经验 {exp:.0f}/{need:.0f}" if need > 0 else "已满级"
    lines.append(f"🌾 农场 Lv.{level}（{exp_text}）｜总盈利 {int(farm.get('total_profit', 0))} 金币")
    n_mature = sum(1 for p in plots if p.get("crop") is not None and now >= p.get("mature_ts", 0))
    n_grow = sum(1 for p in plots if p.get("crop") is not None and now < p.get("mature_ts", 0))
    n_free = sum(1 for p in plots if p.get("crop") is None)
    lines.append(f"🟫 土地共 {len(plots)} 块：已成熟 {n_mature}｜生长中 {n_grow}｜空闲 {n_free}")
    for i, plot in enumerate(plots):
        num = i + 1
        gname = _plot_grade(int(plot.get("grade", 0)))[0]
        grade = int(plot.get("grade", 0))
        if plot.get("crop") is None:
            upgrade = "🏆 已满级" if grade >= len(FARM_UPGRADE_COSTS) \
                else f"⬆️ 可升级 {int(FARM_UPGRADE_COSTS[grade])}金"
            lines.append(f"#{num} {gname}：空闲中（{upgrade}）")
        else:
            crop_name = plot.get("crop", "")
            c = _find_item(crops, crop_name)
            price = c["crop_price"] if c else 0.0
            income = int(round(int(plot.get("yield", 0)) * float(price)))
            if now >= plot.get("mature_ts", 0):
                state, remain = "已成熟", "可收割"
            else:
                state = "占用中"
                remain = _fmt_duration(plot.get("mature_ts", 0) - now)
            adv = ""
            g = _plot_growth(plot, c, now) if c else None
            if g is not None and g["advance_sec"] > 60:
                adv = f"｜加速{int(g['advance_sec'] // 60)}分"
            lines.append(f"#{num} {gname} {state}：{crop_name}｜剩余 {remain}｜预计 {income}金{adv}")
    return lines


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
            logger.error(f"[farm] 渲染土地状态图片异常: {e}")
            img = None
        if _is_image(img):
            return img
    if actions:
        return "\n".join(str(a) for a in actions)
    return "图片生成失败（缺少 Pillow 或字体），请查看日志。"


def _plot_status_view(name, key, farm, steal_lines=None):
    """「土地状态 / 我的农场」回复：专用渲染器 → core.image.rich 富文本 → 纯文本。"""
    render = getattr(_CORE.image, "render_plot_status", None)
    if render is not None:
        try:
            img = render(name, key, _CORE.data, farm, _load_crops(), _load_fertilizers(),
                         steal_lines=steal_lines)
        except Exception as e:
            logger.error(f"[farm] 渲染土地状态图片异常: {e}")
            img = None
        if _is_image(img):
            return img
    lines = _plot_status_lines(farm)
    if steal_lines:
        lines = lines + list(steal_lines)
    rich = getattr(_CORE.image, "rich", None)
    if rich is not None:
        try:
            img = rich(f"{name} 的农场", [[(ln, "#000000", False)] for ln in lines])
            if _is_image(img):
                return img
        except Exception as e:
            logger.error(f"[farm] 渲染富文本土地状态异常: {e}")
    return "\n".join(lines)


# ================= 指令处理器 =================
def _handle_farm_unlock(event):
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    data = _CORE.data
    if _farm_of(data, key):
        return f"{name} 已经拥有农场啦。"
    if _CORE.coins_of(key) < FARM_UNLOCK_COST:
        return f"解锁农场需要 {FARM_UNLOCK_COST} 金币（当前 {_CORE.coins_of(key)}）。"
    _CORE.add_coins(key, -FARM_UNLOCK_COST, "解锁农场")
    farm = _ensure_farm(data, key)
    # 盈利公式：解锁农场计入成本
    farm["total_profit"] = int(farm.get("total_profit", 0)) - FARM_UNLOCK_COST
    for _ in range(FARM_FREE_PLOTS):
        farm["plots"].append(_new_plot())
    _CORE.save()
    # 2.0.0 纯图片回复：赠送土地蓝色高亮（开垦）+ 底部大卡片
    actions = [f"🎉 花费 {FARM_UNLOCK_COST} 金币解锁农场，赠送 {FARM_FREE_PLOTS} 块土地"]
    return _plot_status_reply(
        name, key, farm,
        highlights={i + 1: "till" for i in range(FARM_FREE_PLOTS)},
        profit_delta=-FARM_UNLOCK_COST,
        actions=actions,
        coins_delta=-FARM_UNLOCK_COST)


def _handle_farm_buy_land(event):
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    data = _CORE.data
    err = _farm_need(data, key, name)
    if err:
        return err
    farm = _farm_of(data, key)
    if len(farm["plots"]) >= FARM_MAX_PLOTS:
        return f"土地数量已达上限（{FARM_MAX_PLOTS} 块）。"
    if _CORE.coins_of(key) < FARM_PLOT_COST:
        return f"购买土地需要 {FARM_PLOT_COST} 金币（当前 {_CORE.coins_of(key)}）。"
    _CORE.add_coins(key, -FARM_PLOT_COST, "购买土地")
    farm["plots"].append(_new_plot())
    # 盈利公式：购买土地计入成本
    farm["total_profit"] = int(farm.get("total_profit", 0)) - FARM_PLOT_COST
    _CORE.save()
    # 2.0.0 纯图片回复：新开垦土地蓝色高亮（开垦）+ 底部大卡片
    new_num = len(farm["plots"])
    actions = [f"🆕 花费 {FARM_PLOT_COST} 金币开垦了一块新土地（当前共 {new_num} 块）"]
    return _plot_status_reply(
        name, key, farm,
        highlights={new_num: "till"},
        profit_delta=-FARM_PLOT_COST,
        actions=actions,
        coins_delta=-FARM_PLOT_COST)


def _handle_farm_upgrade(event):
    """土地升级（2.2.0 支持土地编号规则）：单块 土地升级 1 / 区间 土地升级 (1,8) /
    列表 土地升级 1,3,5（括号、逗号不分全角半角）；批量升级统一扣费、全部校验通过后执行。"""
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    parts = event.message_str.split(maxsplit=1)
    if len(parts) < 2:
        return "格式：土地升级 <土地编号>（支持 1 / (1,8) / 1,3,5）"
    nums, _ = _parse_plot_numbers_impl(parts[1])
    if not nums:
        return "土地编号必须是整数。"
    data = _CORE.data
    err = _farm_need(data, key, name)
    if err:
        return err
    farm = _farm_of(data, key)
    plots = farm["plots"]
    bad = [n for n in nums if n < 1 or n > len(plots)]
    if bad:
        return f"土地编号无效（当前共 {len(plots)} 块，范围 1~{len(plots)}）。"
    # 校验：种植中 / 已满级 / 金币充足（全部通过后才执行）
    for n in nums:
        plot = plots[n - 1]
        if plot.get("crop") is not None:
            return f"{n} 号土地正在种植中，收割后才能升级。"
        if int(plot.get("grade", 0)) >= len(FARM_UPGRADE_COSTS):
            return f"{n} 号土地已经是最高等级（黑土地）了。"
    total = sum(int(FARM_UPGRADE_COSTS[int(plots[n - 1].get("grade", 0))]) for n in nums)
    if _CORE.coins_of(key) < total:
        return f"升级需要 {total} 金币（当前 {_CORE.coins_of(key)}）。"
    actions = []
    highlights = {}
    for n in nums:
        plot = plots[n - 1]
        grade = int(plot.get("grade", 0))
        cost = int(FARM_UPGRADE_COSTS[grade])
        _CORE.add_coins(key, -cost, f"升级土地·{n}号")
        plot["grade"] = grade + 1
        # 盈利公式：升级土地计入成本
        farm["total_profit"] = int(farm.get("total_profit", 0)) - cost
        ng = _plot_grade(grade + 1)
        actions.append(f"⬆️ {n} 号土地升级为 {ng[0]}（产量 +{int(ng[1] * 100)}%，时间 -{int(ng[2] * 100)}%）")
        highlights[n] = "upgrade"
    _CORE.save()
    # 2.0.0 纯图片回复：升级土地蓝色高亮（升级）+ 底部大卡片
    return _plot_status_reply(
        name, key, farm,
        highlights=highlights,
        profit_delta=-total,
        actions=actions,
        coins_delta=-total)


def _handle_farm_plant(event):
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    parts = event.message_str.split(maxsplit=1)
    if len(parts) < 2:
        # 1.7.6 快捷种地：不指定作物 → 收割成熟 → 仓库随机种子自动种 → 缺则自动购买 → 种满
        return _farm_plant_auto(event)
    args = parts[1].split()
    crop_name = args[0]
    crops = _load_crops()
    crop = _find_item(crops, crop_name)
    if not crop:
        return f"没有「{crop_name}」这种作物，发送「种子商店」查看。"
    data = _CORE.data
    err = _farm_need(data, key, name)
    if err:
        return err
    farm = _farm_of(data, key)
    if int(farm.get("level", 0)) < crop["min_level"]:
        return f"农场等级不足（需要 Lv.{crop['min_level']}，当前 Lv.{farm['level']}）。"
    plots = farm["plots"]
    if not plots:
        return "还没有土地，发送「购买土地」开垦。"

    wh = farm["warehouse"].setdefault("seeds", {})
    have = int(wh.get(crop_name, 0))

    if len(args) == 1:
        # 种下最大数量：种子足够则种满所有空闲耕地；种子不足则把持有的种子全部种完
        free = [i for i, p in enumerate(plots) if _plot_free(p)]
        if not free:
            return "没有空闲的土地可以种植。"
        if have <= 0:
            return f"{crop_name} 种子不足，发送「购买种子」购买。"
        targets = free[:min(len(free), have)]
    else:
        # 2.2.0：全角括号/逗号归一化后再判定是否为土地编号写法（括号、逗号不分全角半角）
        spec = ("".join(args[1:]).replace("（", "(").replace("）", ")").replace("，", ",")
                .replace("、", ","))
        if "(" in spec or ")" in spec or "," in spec:
            # 2.2.0 土地编号规则：区间 (1,8) / 不连续列表 1,3,5（括号、逗号不分全角半角）
            nums, _ = _parse_plot_numbers_impl(spec)
            if not nums:
                return "土地编号必须是整数。"
            bad = [n for n in nums if n < 1 or n > len(plots)]
            if bad:
                return f"土地编号无效（当前共 {len(plots)} 块，范围 1~{len(plots)}）。"
            targets = [n - 1 for n in nums]
            for i in targets:
                if not _plot_free(plots[i]):
                    return f"{i + 1} 号土地不是空闲状态，无法种植。"
            if have < len(targets):
                return f"{crop_name} 种子不足（需要 {len(targets)}，当前 {have}），发送「购买种子」购买。"
        elif len(args) == 2:
            # 裸数字 = 数量：种下 count 块空闲土地（2.2.0 保留数量语义）
            try:
                count = int(args[1])
            except ValueError:
                return "数量必须是整数。"
            if count <= 0:
                return "数量必须为正整数。"
            free = [i for i, p in enumerate(plots) if _plot_free(p)]
            if count > len(free):
                return f"空闲土地只有 {len(free)} 块，无法种植 {count} 块。"
            targets = free[:count]
            if have < len(targets):
                return f"{crop_name} 种子不足（需要 {len(targets)}，当前 {have}），发送「购买种子」购买。"
        else:
            # 旧语法兼容：种植 <作物> <起> <止>（区间）
            try:
                start, end = int(args[1]), int(args[2])
            except ValueError:
                return "土地编号必须是整数。"
            if start < 1 or end < start or end > len(plots):
                return f"土地编号无效（当前共 {len(plots)} 块，范围 1~{len(plots)}）。"
            targets = list(range(start - 1, end))
            for i in targets:
                if not _plot_free(plots[i]):
                    return f"{i + 1} 号土地不是空闲状态，无法种植。"
            if have < len(targets):
                return f"{crop_name} 种子不足（需要 {len(targets)}，当前 {have}），发送「购买种子」购买。"

    now = datetime.now().timestamp()
    for i in targets:
        _plant_plot(plots[i], crop, now)
    wh[crop_name] = have - len(targets)
    if wh[crop_name] <= 0:
        wh.pop(crop_name, None)
    _farm_log(data, key, "plant",
              f"种下 {crop_name} ×{len(targets)}（地块 {_fmt_plot_nums([i + 1 for i in targets])}）",
              coins=0, qty=len(targets))
    _CORE.save()
    # 2.0.0：纯图片回复——新种地块黄色高亮 + 底部大卡片（不再附带文本提示）
    ferts = _load_fertilizers()
    actions = [f"🌱 在 {len(targets)} 块土地上种下 {crop_name}"
               f"（{_fmt_plot_nums([i + 1 for i in targets])}）"]
    return _plot_status_reply(
        name, key, farm, crops, ferts,
        highlights={i + 1: "plant" for i in targets},
        actions=actions)


def _farm_plant_auto(event):
    """种地/种植（无参数）快捷流程：
    1) 先收割成熟作物；2) 用仓库随机种子自动种；3) 仓库不足 → 自动购买能购买的种子；
    4) 金币不足则尽可能种满。2.0.0 回复 = 纯图片（收割红高亮 + 种植黄高亮 + 底部大卡片）。"""
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    data = _CORE.data
    err = _farm_need(data, key, name)
    if err:
        return err
    farm = _farm_of(data, key)
    if not farm or not farm.get("plots"):
        return "还没有土地，发送「购买土地」开垦。"
    crops = _load_crops()
    now = datetime.now().timestamp()
    actions = []
    highlights = {}
    spent = 0

    # 1) 先收割成熟作物（进仓库）→ 红色高亮
    harvested, _, total_exp = _harvest_mature(farm, crops, now)
    if harvested:
        lvl_msg = _farm_gain_exp(farm, total_exp)
        actions.append(f"🌾 先收割了 {len(harvested)} 块成熟作物（编号 {'、'.join(str(n) for n in harvested)}），农场经验 +{total_exp}{lvl_msg}")
        for n in harvested:
            highlights[n] = "harvest"

    plots = farm["plots"]
    free = [i for i, p in enumerate(plots) if _plot_free(p)]
    if not free and not actions:
        return "没有空闲的土地可以种植。"

    # 2) 使用仓库种子（随机名称逐个种）
    wh_seeds = farm["warehouse"].setdefault("seeds", {})
    usable = [n for n, c in wh_seeds.items() if int(c or 0) > 0 and _find_item(crops, n)]
    free_left = list(free)
    used_desc = {}
    while free_left and usable:
        nm = random.choice(usable)
        crop = _find_item(crops, nm)
        if int(farm.get("level", 0)) < crop["min_level"]:
            usable.remove(nm)  # 等级不够的种子跳过（不种）
            continue
        i = free_left.pop(0)
        _plant_plot(plots[i], crop, now)
        highlights[i + 1] = "plant"
        wh_seeds[nm] = int(wh_seeds.get(nm, 0)) - 1
        used_desc[nm] = used_desc.get(nm, 0) + 1
        if wh_seeds[nm] <= 0:
            wh_seeds.pop(nm, None)
            usable.remove(nm)
    if used_desc:
        actions.append("📦 使用仓库种子：" + "、".join(f"{n}×{c}" for n, c in used_desc.items()))

    # 3) 仓库不足 → 自动购买当前用户能购买的种子并种植
    bought = 0
    spent = 0
    if free_left:
        buyable = [c for c in crops if int(farm.get("level", 0)) >= c["min_level"]]
        if not buyable:
            actions.append("😢 当前农场等级没有可购买的种子，剩余空地未能种植。")
        else:
            missing = len(free_left)
            actions.append(f"🛒 仓库种子不足，自动购买 {missing} 颗种子补种…")
            coins = _CORE.coins_of(key)
            bought = 0
            order = sorted(buyable, key=lambda c: c["min_level"], reverse=True)
            while free_left:
                bought_any = False
                for crop in order:
                    if not free_left:
                        break
                    price = int(round(crop["seed_price"] * _farm_seed_mult(farm)))
                    if coins < price:
                        continue
                    _CORE.add_coins(key, -price, f"购买种子·{crop['name']}")
                    farm["total_profit"] = int(farm.get("total_profit", 0)) - price
                    i = free_left.pop(0)
                    _plant_plot(plots[i], crop, now)
                    highlights[i + 1] = "plant"
                    bought += 1
                    spent += price
                    coins -= price
                    bought_any = True
                    break
                if not bought_any:
                    break
            if bought:
                actions.append(f"🛒 自动购买并种下 {bought} 颗种子（花费 {spent} 金币）")
            if free_left:
                actions.append(f"🍂 金币不足，剩余 {len(free_left)} 块空地未能种植。")
    n_planted = sum(used_desc.values()) + bought
    _farm_log(data, key, "plant",
              "快捷种植：仓库种子 " + ("、".join(f"{n}×{c}" for n, c in used_desc.items()) or "无")
              + (f"，自动购买 {bought} 颗（花费 {spent} 金币）" if bought else "")
              + f"，共种 {n_planted} 块",
              coins=spent, qty=n_planted)
    _CORE.save()
    # 4) 纯图片回复：收割红高亮 + 种植黄高亮 + 底部大卡片（自动化行为 + 金币变化）
    return _plot_status_reply(
        name, key, farm, crops, _load_fertilizers(),
        highlights=highlights,
        profit_delta=-spent,
        actions=actions,
        coins_delta=-spent if spent else None)


def _handle_farm_harvest(event):
    """收割 / 收获（2.2.0）：**仅收割成熟作物入库，不再自动售出**（售卖请用「售卖」指令）。
    土地编号规则（括号、逗号不分全角半角）：单块 1 / 连续区间 (1,8) / 不连续列表 1,3,5,7；
    不填 = 全部成熟作物。图片回复：收割地块红色高亮 + 底部大卡片（入库提示）。"""
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    parts = event.message_str.split(maxsplit=1)
    crops = _load_crops()
    data = _CORE.data
    err = _farm_need(data, key, name)
    if err:
        return err
    farm = _farm_of(data, key)
    plots = farm["plots"]
    now = datetime.now().timestamp()
    if len(parts) >= 2 and parts[1].strip():
        nums, _ = _parse_plot_numbers_impl(parts[1])
        if not nums:
            return "土地编号必须是整数。"
        bad = [n for n in nums if n < 1 or n > len(plots)]
        if bad:
            return f"土地编号无效（当前共 {len(plots)} 块）。"
        targets = [n - 1 for n in nums]
    else:
        targets = None
    harvested, amounts, total_exp = _harvest_mature(farm, crops, now, targets=targets)
    if not harvested:
        if targets is not None:
            return "所选土地没有可收割的成熟作物。"
        return "没有可收割的成熟作物。"
    lvl_msg = _farm_gain_exp(farm, total_exp)
    _farm_log(data, key, "harvest",
              f"收割 {len(harvested)} 块（{_fmt_plot_nums(harvested)}）："
              + ("、".join(f"{k}×{v}" for k, v in amounts.items()) or "无收成")
              + f"，经验 +{total_exp}",
              coins=0, qty=sum(amounts.values()))
    _CORE.save()
    # 2.2.0：仅收割入库，不再自动售出；盈利不变化、金币不变化
    actions = [f"🌾 收割 {len(harvested)} 块地（{_fmt_plot_nums(harvested)}），"
               f"农场经验 +{total_exp}{lvl_msg}",
               f"📦 收获已存入仓库（发送「售卖」可卖出）"]
    steal_lines = _steal_info_lines(farm, datetime.now().timestamp())
    return _plot_status_reply(
        name, key, farm, crops, _load_fertilizers(),
        highlights={n: "harvest" for n in harvested},
        actions=actions,
        steal_lines=steal_lines)


def _handle_farm_cancel(event):
    """取消种植（2.2.0 支持土地编号规则）：单块 取消种植 1 / 区间 (1,8) / 列表 1,3,5
    （括号、逗号不分全角半角）。"""
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    parts = event.message_str.split(maxsplit=1)
    if len(parts) < 2:
        return "格式：取消种植 <土地编号>（支持 1 / (1,8) / 1,3,5）"
    nums, _ = _parse_plot_numbers_impl(parts[1])
    if not nums:
        return "土地编号必须是整数。"
    data = _CORE.data
    err = _farm_need(data, key, name)
    if err:
        return err
    farm = _farm_of(data, key)
    plots = farm["plots"]
    bad = [n for n in nums if n < 1 or n > len(plots)]
    if bad:
        return f"土地编号无效（当前共 {len(plots)} 块）。"
    for n in nums:
        if plots[n - 1].get("crop") is None:
            return f"{n} 号土地本来就是空闲的。"
    for n in nums:
        _clear_plot(plots[n - 1])
    _CORE.save()
    # 2.0.0 纯图片回复：取消种植地块蓝色高亮 + 底部大卡片
    actions = [f"🗑️ 已取消 {len(nums)} 块土地的种植（{_fmt_plot_nums(nums)}）"]
    return _plot_status_reply(
        name, key, farm,
        highlights={n: "till" for n in nums},
        actions=actions)


def _handle_farm_plots(event):
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    data = _CORE.data
    err = _farm_need(data, key, name)
    if err:
        return err
    farm = _farm_of(data, key)
    steal_lines = _steal_info_lines(farm, datetime.now().timestamp())
    return _plot_status_view(name, key, farm, steal_lines=steal_lines)


def _handle_farm_help(event):
    sections = [
        ("农场", [
            ("解锁农场", f"花 {FARM_UNLOCK_COST} 金币解锁农场（赠 {FARM_FREE_PLOTS} 块地）"),
            ("购买土地", f"花 {FARM_PLOT_COST} 金币开垦新土地（最多 {FARM_MAX_PLOTS} 块）"),
            ("土地升级 <编号>", "升级土地等级（编号规则：单块 1 / 连续 (1,8) / 不连续 1,3,5，括号逗号不分全半角）"),
            ("种植 <作物> [数量]", "种植（不填=种子够则种满空闲地，不够则全部种完；裸数字=数量；指定土地用 (1,8) 区间或 1,3,5 列表）"),
            ("种地 / 种植（不填作物）", "快捷种地：先收割成熟 → 仓库随机种子自动种 → 缺则自动购买 → 种满"),
            ("收割 [编号] / 收获", "仅收割成熟作物入库（不再自动售出；不填=全部；编号规则同上）"),
            ("取消种植 <编号>", "取消种植（编号规则同上）"),
            ("土地状态 / 我的农场", "查看土地（农场指令回复均为纯图片：黄=种植 红=收割 蓝=开垦/施肥/升级；购买种子/施肥/售卖请看农场商店与仓库说明）"),
        ]),
    ]
    img = _CORE.image.build_help("农场帮助", sections)
    if img is not None:
        return img
    return "\n".join(f"· {c}：{d}" for c, d in sections[0][1])


# ================= 挂载入口 =================
def register(core):
    global _CORE
    _CORE = core

    core.command("解锁农场", feature="farm")(_handle_farm_unlock)
    core.command("购买土地", feature="farm")(_handle_farm_buy_land)
    core.command("土地升级", feature="farm")(_handle_farm_upgrade)
    core.command("种植", "种地", feature="farm")(_handle_farm_plant)
    core.command("收割", "收获", feature="farm")(_handle_farm_harvest)
    core.command("取消种植", feature="farm")(_handle_farm_cancel)
    core.command("土地状态", "我的农场", feature="farm")(_handle_farm_plots)
    core.command("农场帮助", feature="farm")(_handle_farm_help)

    core.add_help("农场", [
        ("解锁农场", f"花 {FARM_UNLOCK_COST} 金币解锁农场（赠 {FARM_FREE_PLOTS} 块地）"),
        ("购买土地", f"花 {FARM_PLOT_COST} 金币开垦新土地（最多 {FARM_MAX_PLOTS} 块）"),
        ("土地升级 <编号>", "升级土地等级（编号规则：单块 1 / 连续 (1,8) / 不连续 1,3,5）"),
        ("种植 <作物> [数量]", "种植（不填=种满空闲地；裸数字=数量；(1,8)/1,3,5=指定土地）"),
        ("种地 / 种植（不填作物）", "快捷种地：先收割成熟 → 仓库随机种子自动种 → 缺则自动购买 → 种满"),
        ("收割 [编号] / 收获", "仅收割成熟作物入库（不填=全部）"),
        ("取消种植 <编号>", "取消种植（编号规则同上）"),
        ("土地状态 / 我的农场", "查看土地与仓库"),
        ("农场帮助", "查看农场模块指令"),
    ])

    # ================= 服务暴露（其他插件经 core.service("farm") 调用） =================
    class FarmApi:
        """农场服务：农场数据读写、土地编号解析、地块/经验/状态助手"""

        def farm_of(self, key):
            """取用户农场 dict；无农场返回 None"""
            return _farm_of(core.data, key)

        def ensure_farm(self, key):
            """取/建用户农场 dict（只改内存不落盘，调用方自行 core.save()）"""
            return _ensure_farm(core.data, key)

        def add_exp(self, key, exp):
            """农场加经验（自动升级，含存盘）。返回升级提示行（无升级为 ""）"""
            farm = _farm_of(core.data, key)
            if not farm:
                return ""
            msg = _farm_gain_exp(farm, exp)
            core.save()
            return msg

        def plot_status_lines(self, farm):
            """土地状态文本行（与 2.3.0 土地卡片内容同口径），供文本回复拼装"""
            return _plot_status_lines(farm)

        def state_snippet(self, farm):
            """农场状态摘要一行（Lv/土地/仓库计数）"""
            return _farm_snippet(farm)

        def plot_free(self, plot):
            """地块是否空闲（无 crop）"""
            return _plot_free(plot)

        def plot_grade(self, grade):
            """土地等级 → (名称, 产量加成小数, 时间减免小数)"""
            return _plot_grade(grade)

        def plot_growth(self, plot, crop, now):
            """地块生长状态计算（2.0.0 阶段模型），详见 _plot_growth"""
            return _plot_growth(plot, crop, now)

        def seed_mult(self, farm):
            """种子价格倍率（按农场等级）"""
            return _farm_seed_mult(farm)

        def new_plot(self):
            """新建一块空地块 dict"""
            return _new_plot()

        def plant_plot(self, plot, crop, now):
            """把作物种到空闲地块（字段赋值，不落盘）"""
            _plant_plot(plot, crop, now)

        def clear_plot(self, plot):
            """清空地块种植字段（不落盘）"""
            _clear_plot(plot)

        def harvest_mature(self, key, targets=None, save=True):
            """收割成熟作物进仓库（targets=None=全部；targets 为 0-based 下标列表）。
            返回 (harvested 编号列表[1-based], {作物:数量}, 总经验)；默认自动存盘。"""
            farm = _farm_of(core.data, key)
            if not farm:
                return [], {}, 0
            res = _harvest_mature(farm, _load_crops(), datetime.now().timestamp(), targets=targets)
            if save and res[0]:
                core.save()
            return res

        def plot_parser(self, raw, max_n=None):
            """土地编号统一解析（2.2.0 规则）→ (编号列表, 裸数字列表, 越界编号列表)；
            编号支持 1 / (1,8) / 1,3,5（括号、逗号不分全角半角）。farm_shop/farm_steal 等复用。"""
            return plot_parser(raw, max_n)

        def fert_targets(self, raw, max_plots):
            """「施肥」参数解析：裸数字 ≤max_plots 视为地块编号，>max_plots 视为分钟数
            → (土地编号列表, 分钟 or None)"""
            return _parse_fert_targets(raw, max_plots)

    core.expose("farm", FarmApi())
