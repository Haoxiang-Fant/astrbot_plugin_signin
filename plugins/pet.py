# -*- coding: utf-8 -*-
"""宠物插件（3.0.0）：宠物 / 更改宠物名字 / 治疗宠物 / 宠物帮助 + 每日结算 + 对外服务。

2.3.0 宠物模块核心部分移植（modules/pet.py + base.py 状态摘要）：
  - 指令：「宠物」总览（图片优先 render_pet_status，缺失/失败回退 2.3.0 文本版）、
    「更改宠物名字」、「治疗宠物」（虚弱专属，WEAK_HEAL_COST 金币，属性恢复 40）、「宠物帮助」；
  - 每日结算：core.daily("pet_settle") 把全部宠物结算到今日（缺几天补几天，逐只容错）；
  - 对外服务 core.expose("pet", api)：pet_of / gain_exp / weak_guard / busy_until /
    attr_max / state_snippet / settle_display_lines / bring_up_to_date / apply_exp /
    pet_level_from_exp（签到插件等经 core.service("pet") 调用）；
  - 数值与字段与 2.3.0 完全一致：exp/level/health/satiety/thirst/stamina/mood/weak/
    weak_streak/busy_*/inventory/attr_log/last_settle + 属性上限档位（PET_ATTR_MAX_RANGES）。
打工 / 玩耍 / 商店 / 道具 / 自动化由 pet_work / pet_play / pet_shop / pet_item /
pet_auto_care / pet_auto_work 插件承接；治疗后自动照顾检查经 core.service("pet_auto_care") 联动。
"""
import math
import random
import re
from datetime import date, datetime, timedelta

from astrbot.api import logger

from ..core import (PET_MAX_LEVEL, PET_EXP_PER_LEVEL, PET_MAX_HEALTH,
                    PET_ATTR_MAX_RANGES, PET_SETTLE_HEALTH_EXCHANGE,
                    WEAK_HEAL_COST, PET_UNLOCK_COST, ATTR_LABELS, ATTR_SHORT)

NAME = "pet"

# 2.2.1：每只宠物的「属性变化记录」上限（超出丢弃最旧；WebUI 宠物记录详情页展示）
_ATTR_LOG_MAX = 300

# 2.2.7 统一数值管理：档位不再单独配置，由「属性最大值数据」统一推导——
# 一档下限 = 60% 上限、二档下限 = 35%、三档下限 = 15%，低于 15% 为四档。
_TIER_PCTS = (0.6, 0.35, 0.15)

_SETTLE_ATTR_MAP = {"饱食": "satiety", "口渴": "thirst", "体力": "stamina", "心情": "mood", "健康": "health"}
# 2.0.1：宠物状态结算固定四档（T1~T4，按饱食/口渴/心情最差档），每档定义全部五属性变化范围
_SETTLE_DEFAULT_RANGES = {
    "T1": {"饱食": (-15.0, -10.0), "口渴": (-15.0, -10.0), "体力": (100.0, 120.0), "心情": (3.0, 7.0), "健康": (5.0, 10.0)},
    "T2": {"饱食": (-15.0, -10.0), "口渴": (-15.0, -10.0), "体力": (100.0, 120.0), "心情": (3.0, 7.0), "健康": (0.1, 6.0)},
    "T3": {"饱食": (-20.0, -15.0), "口渴": (-20.0, -15.0), "体力": (80.0, 120.0), "心情": (1.0, 2.5), "健康": (-10.0, -4.0)},
    "T4": {"饱食": (-25.0, -20.0), "口渴": (-25.0, -20.0), "体力": (40.0, 60.0), "心情": (-5.0, -2.0), "健康": (-15.0, -8.0)},
}


# ================= 基础数值（2.3.0 PetMixin 原样移植） =================
def _parse_attr_max_ranges(raw):
    """解析 PET_ATTR_MAX_RANGES（WebUI「设置 → 宠物 → 属性」可编辑）。
    格式：健康值下限=饱食,口渴,体力,心情，竖线分隔（如 140=200,200,200,120|80=120,120,120,100）。
    返回 {健康下限: (饱食, 口渴, 体力, 心情)}；解析失败回退默认四档。"""
    raw = raw or ""
    if not isinstance(raw, str) or not raw.strip():
        return {140: (200.0, 200.0, 200.0, 120.0),
                80: (120.0, 120.0, 120.0, 100.0),
                40: (100.0, 100.0, 100.0, 100.0),
                0: (80.0, 80.0, 60.0, 80.0)}
    out = {}
    for seg in str(raw).replace("；", "|").replace(";", "|").split("|"):
        seg = seg.strip()
        if not seg or "=" not in seg:
            continue
        floor_s, body = seg.split("=", 1)
        try:
            floor = float(floor_s.strip())
        except (TypeError, ValueError):
            continue
        nums = []
        for x in body.replace("，", ",").split(","):
            try:
                nums.append(float(x.strip()))
            except (TypeError, ValueError):
                nums = []
                break
        if len(nums) == 4:
            out[floor] = (nums[0], nums[1], nums[2], nums[3])
    return out or {140: (200.0, 200.0, 200.0, 120.0),
                   80: (120.0, 120.0, 120.0, 100.0),
                   40: (100.0, 100.0, 100.0, 100.0),
                   0: (80.0, 80.0, 60.0, 80.0)}


def pet_attr_max(core, health: float):
    """返回 (饱食上限, 口渴上限, 体力上限, 心情上限)，由健康度决定（2.0.2：阈值可在 WebUI 编辑）。
    默认规则：健康 140-200 → 200/200/200/120；80-139 → 120/120/120/100；
    40-79 → 100/100/100/100；0-39 → 80/80/60/80。健康度最大值 200（PET_MAX_HEALTH）。"""
    ranges = _parse_attr_max_ranges(core.param("PET_ATTR_MAX_RANGES", PET_ATTR_MAX_RANGES))
    # 按健康值下限从高到低匹配（健康 >= 下限 即命中）
    for floor in sorted(ranges, reverse=True):
        if health >= floor:
            return ranges[floor]
    return (80.0, 80.0, 60.0, 80.0)


def clamp_attrs(core, pet: dict) -> None:
    """按当前健康度对应的属性上限统一 clamp（2.3.0 _clamp_attrs）"""
    sat_max, thr_max, sta_max, mood_max = pet_attr_max(core, pet["health"])
    pet["satiety"] = round(core.clamp(pet["satiety"], 0, sat_max), 2)
    pet["thirst"] = round(core.clamp(pet["thirst"], 0, thr_max), 2)
    pet["stamina"] = round(core.clamp(pet["stamina"], 0, sta_max), 2)
    pet["mood"] = round(core.clamp(pet["mood"], 0, mood_max), 2)
    pet["health"] = round(core.clamp(pet["health"], 0, PET_MAX_HEALTH), 2)


def attr_tier(val: float, max_v: float) -> int:
    """按属性最大值推导档位（2.2.7 统一数值管理）：≥60% 一档、≥35% 二档、≥15% 三档、否则四档。"""
    try:
        max_v = float(max_v or 0)
        val = float(val or 0)
    except (TypeError, ValueError):
        return 1
    if max_v <= 0:
        return 1
    for i, p in enumerate(_TIER_PCTS):
        if val >= max_v * p:
            return i + 1
    return 4


def worst_tier(core, satiety: float, thirst: float, mood: float, health: float = 0.0) -> int:
    """饱食/口渴/心情对健康的影响档位（2.0.1 四档；2.2.7 起档位由属性最大值统一推导），
    每个属性单独定档后取最差档（4 最差）。health 用于确定当前属性上限。"""
    sat_max, thr_max, sta_max, mood_max = pet_attr_max(core, float(health or 0))
    return max(attr_tier(satiety, sat_max),
               attr_tier(thirst, thr_max),
               attr_tier(mood, mood_max))


def pet_attr_log(core, pet: dict, cat: str, behavior: str, changes: dict, extra: str = "", ts=None, items=None):
    """2.2.1：追加一条宠物属性变化记录（WebUI「运行记录 → 宠物记录 → 宠物详情」展示）。
    cat = 行为分类（每日结算/打工/玩耍/使用道具/治疗/自动照顾/自动打工）；
    behavior = 具体行为描述；changes = {属性key: 变化量}（仅记录非零项）；
    extra = 附加信息（金币/经验等）；
    items = [(道具名, 数量, 效果dict), ...]（2.2.7：本次使用的道具及其效果快照）。"""
    if not changes:
        return
    if ts is None:
        ts = datetime.now().timestamp()
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = datetime.now().timestamp()
    log = pet.setdefault("attr_log", [])
    sat_max, thr_max, sta_max, mood_max = pet_attr_max(core, pet["health"])
    entry = {
        "ts": ts,
        "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
        "cat": cat,
        "behavior": behavior,
        "changes": {k: round(float(v), 2) for k, v in changes.items() if abs(float(v)) > 1e-9},
        "extra": extra or "",
        # 2.2.2：变动后的属性绝对值快照（WebUI 属性条可视化：基础=变动前=after-changes）。
        # 本函数总在属性变更后立即调用，pet 当前值即为变动后状态。
        "after": {k: round(float(pet.get(k, 0)), 2) for k in ATTR_LABELS},
        # 2.2.2：变动发生时的各属性上限快照（上限随健康度动态变化，旧记录无此字段）
        "max": {"satiety": sat_max, "thirst": thr_max, "stamina": sta_max,
                "mood": mood_max, "health": PET_MAX_HEALTH},
    }
    if items:
        # 2.2.7：道具效果快照（每条记录冻结使用当时的数值，商店后续改价/改效果不影响历史展示）
        arr = []
        for n, q, eff in items:
            fx = {}
            for k, v in (eff or {}).items():
                try:
                    fv = round(float(v), 2)
                except (TypeError, ValueError):
                    continue
                if abs(fv) > 1e-9:
                    fx[str(k)] = fv
            try:
                iq = int(q)
            except (TypeError, ValueError):
                iq = 1
            arr.append({"name": str(n), "qty": max(1, iq), "effects": fx})
        if arr:
            entry["items"] = arr
    log.append(entry)
    if len(log) > _ATTR_LOG_MAX:
        del log[:len(log) - _ATTR_LOG_MAX]


# ================= 经验体系（2.3.0 原样移植） =================
def pet_level_from_exp(exp: float) -> int:
    """新经验体系：所需经验 = 当前等级 × PET_EXP_PER_LEVEL（2.3.0 固定 100）。
    累计 100+200+...+（L-1）×100 升到 Lv.L。"""
    exp = max(0.0, float(exp))
    per = float(PET_EXP_PER_LEVEL) if PET_EXP_PER_LEVEL else 100.0
    # 解 per*(L-1)*L/2 <= exp → L = floor((1+sqrt(1+8*exp/per))/2)
    L = int((1 + (1 + 8 * exp / per) ** 0.5) / 2)
    return min(PET_MAX_LEVEL, L)


def pet_exp_progress(exp: float) -> tuple:
    """返回 (当前等级, 本级已得经验, 本级所需经验)，新经验体系：所需经验 = 当前等级 × PET_EXP_PER_LEVEL"""
    exp = max(0.0, float(exp))
    level = pet_level_from_exp(exp)
    per = float(PET_EXP_PER_LEVEL) if PET_EXP_PER_LEVEL else 100.0
    need_prev = per * (level - 1) * level / 2.0  # 升到当前等级的累计经验
    got = exp - need_prev
    need = float(level) * per
    return level, got, need


def apply_exp(pet: dict) -> str:
    """按累计经验刷新宠物等级；发生升级时返回提示行（否则空串）"""
    new_level = min(PET_MAX_LEVEL, pet_level_from_exp(float(pet.get("exp", 0.0))))
    old = int(pet.get("level", 1))
    pet["level"] = new_level
    if new_level > old:
        return f"\n🎊 宠物升级！Lv.{old} → Lv.{new_level}"
    return ""


# ================= 每日结算（2.3.0 原样移植） =================
def settle_ranges(core):
    """解析 PET_SETTLE_RANGES（WebUI「设置 → 签到 → 宠物结算范围」可编辑）。
    2.0.1：固定四档 T1~T4（按饱食/口渴/心情最差档），每档定义全部五属性变化范围。
    格式：T1=饱食-15~-10,口渴-15~-10,体力100~120,心情3~7,健康5~10|T2=…|T3=…|T4=…
    返回 {"T1": {属性: (lo, hi)}, …, "T4": {…}}；解析失败回退默认值。"""
    raw = core.param("PET_SETTLE_RANGES", "")
    if not isinstance(raw, str) or not str(raw).strip():
        return _SETTLE_DEFAULT_RANGES
    out = {}
    for seg in str(raw).replace("；", "|").replace(";", "|").split("|"):
        seg = seg.strip()
        if not seg or "=" not in seg:
            continue
        key, body = seg.split("=", 1)
        key = key.strip().upper()
        if key not in ("T1", "T2", "T3", "T4"):
            continue
        attrs = {}
        for item in body.split(","):
            item = item.strip()
            m = re.match(r"^(.*?)(-?\d+(?:\.\d+)?)~(-?\d+(?:\.\d+)?)$", item)
            if not m:
                continue
            name = m.group(1).strip()
            lo = float(m.group(2))
            hi = float(m.group(3))
            if name in _SETTLE_ATTR_MAP:
                attrs[name] = (min(lo, hi), max(lo, hi))
        if attrs:
            out[key] = attrs
    if not out:
        return _SETTLE_DEFAULT_RANGES
    return out


def settle_once(core, pet: dict, settle_date: str) -> None:
    """执行一次每日结算（2.0.1：固定四档 T1~T4，按饱食/口渴/心情最差档；
    各档属性变化范围可在 WebUI「设置 → 签到 → 宠物结算范围」编辑。
    2.0.3：结算扣减的属性若超出结算前属性值，超出部分按可调比例用健康值抵扣）"""
    health = pet["health"]
    ranges = settle_ranges(core)

    # 状态档位：饱食/口渴/心情 取最差档（1 最好 ~ 4 最差；2.2.7 档位由属性最大值统一推导）
    tier = worst_tier(core, pet["satiety"], pet["thirst"], pet["mood"], pet["health"])
    tkey = f"T{tier}"

    def _roll(name, default):
        r = ranges.get(tkey, {}).get(name) or default
        return random.uniform(r[0], r[1])

    # 四档分别定义全部五属性的变化范围
    sat_d = _roll("饱食", _SETTLE_DEFAULT_RANGES[tkey]["饱食"])
    thr_d = _roll("口渴", _SETTLE_DEFAULT_RANGES[tkey]["口渴"])
    sta_d = _roll("体力", _SETTLE_DEFAULT_RANGES[tkey]["体力"])
    mood_d = _roll("心情", _SETTLE_DEFAULT_RANGES[tkey]["心情"])
    health_d = _roll("健康", _SETTLE_DEFAULT_RANGES[tkey]["健康"])

    # 2.0.3：超扣转健康抵扣 —— 扣减的属性若超出结算前属性值，
    # 超出部分按「N 属性点 = 1 健康」折算为健康额外扣减（向上取整，N = PET_SETTLE_HEALTH_EXCHANGE）
    exchange = max(1, int(float(core.param("PET_SETTLE_HEALTH_EXCHANGE", PET_SETTLE_HEALTH_EXCHANGE) or 4)))
    excess_total = 0.0
    for _attr, _chg in (("satiety", sat_d), ("thirst", thr_d), ("stamina", sta_d), ("mood", mood_d)):
        if _chg < 0:
            excess_total += max(0.0, -(pet[_attr] + _chg))
    health_extra = int(math.ceil(excess_total / exchange)) if excess_total > 0 else 0

    # 3. 应用（先按当前健康度的上限 clamp 属性，再改健康度，最后统一 clamp）
    before = {a: pet[a] for a in ATTR_LABELS}  # 2.2.2：变动前快照（记录实际生效的变化量）
    sat_max, thr_max, sta_max, mood_max = pet_attr_max(core, health)
    pet["satiety"] = round(core.clamp(pet["satiety"] + sat_d, 0, sat_max), 2)
    pet["thirst"] = round(core.clamp(pet["thirst"] + thr_d, 0, thr_max), 2)
    pet["stamina"] = round(core.clamp(pet["stamina"] + sta_d, 0, sta_max), 2)
    pet["mood"] = round(core.clamp(pet["mood"] + mood_d, 0, mood_max), 2)
    pet["health"] = round(core.clamp(pet["health"] + health_d - health_extra, 0, PET_MAX_HEALTH), 2)
    clamp_attrs(core, pet)

    pet["last_settle"] = {
        "date": settle_date,
        "satiety_d": round(sat_d, 2),
        "thirst_d": round(thr_d, 2),
        "stamina_d": round(sta_d, 2),
        "mood_d": round(mood_d, 2),
        "health_d": round(health_d - health_extra, 2),
        "health_extra": health_extra,
        "exchange": exchange,
        "rested_well": sta_d > 100.0,
        "sick": pet["health"] <= 39.0,
        "tier": tier,   # 1.7.6：1-4 档（3/4 为状态差，签到时提醒）
    }
    # 2.2.2：记录实际生效的属性变化（clamp 后 after−before；超出 0/上限的部分不虚记）
    pet_attr_log(core, pet, "每日结算", f"每日结算（T{tier}）",
                 {a: round(pet[a] - before[a], 2) for a in ATTR_LABELS
                  if abs(pet[a] - before[a]) > 1e-9})

    # 虚弱判定：连续两天结算健康均为 0 → 宠物进入「虚弱」状态（治疗宠物 可解除）
    if pet["health"] <= 0.5:
        pet["weak_streak"] = int(pet.get("weak_streak", 0)) + 1
        if pet["weak_streak"] >= 2:
            pet["weak"] = True
    else:
        pet["weak_streak"] = 0


def bring_up_to_date(core, pet: dict, today: str) -> None:
    """把宠物结算到今日（缺几天结算几天）"""
    last = pet.get("last_settle_date", "")
    if last == today:
        return
    if last:
        try:
            start = date.fromisoformat(last)
            end = date.fromisoformat(today)
            d = start
            while d < end:
                d = d + timedelta(days=1)
                settle_once(core, pet, d.isoformat())
        except ValueError:
            pass
    pet["last_settle_date"] = today
    if pet.get("money_event_date") != today:
        pet["money_event_date"] = today
        pet["money_event_count"] = 0


def settle_display_lines(pet: dict):
    """昨晚结算结果展示行（宠物总览 / 签到响应附加；无结算记录返回空列表）"""
    ls = pet.get("last_settle")
    if not ls:
        return []
    lines = ["🐾 宠物结算（昨晚）："]
    lines.append(f"🍖 饱食度 {ls['satiety_d']:+.1f}，💧 口渴值 {ls['thirst_d']:+.1f}，"
                 f"⚡ 体力 {ls['stamina_d']:+.1f}，😊 心情 {ls['mood_d']:+.1f}，❤️ 健康 {ls['health_d']:+.1f}")
    # 2.0.3：属性超扣转健康抵扣提示
    if int(ls.get("health_extra", 0) or 0) > 0:
        ex = int(ls.get("exchange", 4) or 4)
        lines.append(f"🩹 属性不足，超扣部分按 {ex} 属性点 = 1 健康用健康值抵扣（健康额外 -{int(ls['health_extra'])}）")
    if ls.get("rested_well"):
        lines.append("😴 昨晚你的宠物休息得很好！")
    if ls.get("sick"):
        lines.append("🤒 宠物生病了，快给它吃药吧！")
    tier = int(ls.get("tier", 0))
    if tier >= 4:
        lines.append("🚨 你的宠物急需你的照顾！")
    elif tier == 3:
        lines.append("⚠️ 你的宠物看起来蔫蔫的，快去照顾吧～")
    return lines


# ================= 状态摘要 / 工具（2.3.0 base.py 原样移植） =================
def pet_state_snippet(core, pet: dict) -> str:
    """宠物当前状态摘要（打工/玩耍/使用道具/治疗反馈末尾附加）。
    1.7.6：状态低判定采用第三档标准——饱食 <50 或 口渴 <60 或 心情 <40 时附加红色提示。"""
    sat_max, thr_max, sta_max, mood_max = pet_attr_max(core, pet["health"])
    weak = "，😷 虚弱（发送「治疗宠物」）" if pet.get("weak") else ""
    line = (f"🐾 {pet.get('name', '宠物')}：饱食 {pet['satiety']:.0f}/{sat_max:.0f}，"
            f"口渴 {pet['thirst']:.0f}/{thr_max:.0f}，体力 {pet['stamina']:.0f}/{sta_max:.0f}，"
            f"心情 {pet['mood']:.0f}/{mood_max:.0f}，健康 {pet['health']:.0f}/{PET_MAX_HEALTH:.0f}{weak}")
    # 第三档标准判定状态低
    lows = []
    if pet["satiety"] < 50:
        lows.append("饿了")
    if pet["thirst"] < 60:
        lows.append("渴了")
    if pet["mood"] < 40:
        lows.append("不开心")
    if lows:
        line += f"（{'、'.join(lows)}，状态低！）"
    return line


def pet_busy_until(pet: dict) -> float:
    """打工/玩耍共用冷却计时器：返回忙碌结束时间戳（兼容旧数据 work_until / play_until）"""
    busy = float(pet.get("busy_until", 0) or 0)
    old = max(float(pet.get("work_until", 0) or 0), float(pet.get("play_until", 0) or 0))
    return max(busy, old)


def fmt_duration(sec) -> str:
    """秒数 →「N天N小时N分 / N小时N分 / N分钟 / N秒」"""
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


def activity_superseded_by_settle(pet: dict, la_ts: float) -> bool:
    """即时消息是否已被更新的每日结算覆盖：
    最近一次实际结算的日期晚于该活动发生日 → 返回 True（预留位隐藏）。
    last_settle 仅在真正结算时写入（last_settle_date 只是惰性标记，不能作为依据）。"""
    ls = pet.get("last_settle") or {}
    sdate = ls.get("date")
    if not sdate:
        return False
    try:
        settle_day = datetime.fromisoformat(str(sdate))
    except (TypeError, ValueError):
        return False
    day_start = datetime(settle_day.year, settle_day.month, settle_day.day).timestamp()
    return la_ts < day_start


# ================= 图片渲染（图片响应模块；缺失/失败回退文本） =================
def _render_pet_status(core, name, key, data, pet, **kwargs):
    """渲染宠物总览图（图片响应模块 render_pet_status；模块未挂载或异常返回 None，
    调用方回退 2.3.0 文本版，避免渲染问题阻断指令）。"""
    fn = getattr(core.image, "render_pet_status", None)
    if fn is None:
        return None
    try:
        return fn(name, key, data, pet, **kwargs)
    except Exception as e:
        logger.error(f"[宠物] 渲染宠物图片异常: {e}")
        return None


# ================= 联动：治疗后自动照顾检查（pet_auto_care 插件） =================
def _auto_care_after_change(core, key) -> None:
    """2.2.5：宠物属性变化后（治疗后）触发自动照顾检查——
    经 core.service("pet_auto_care") 联动（care_due / care_run）；服务未挂载时静默跳过。
    照顾发生时此处再次 core.save()（与 2.3.0 _auto_care_after_change 时序一致）。"""
    svc = core.service("pet_auto_care")
    if svc is None:
        return
    try:
        due = getattr(svc, "care_due", None)
        run = getattr(svc, "care_run", None)
        if callable(due) and due(key) and callable(run):
            run(key, "属性变化")
            core.save()
    except Exception as e:
        logger.error(f"[宠物] 自动照顾联动失败: {e}")


# ================= 帮助菜单（与 2.3.0 文本一致） =================
def _pet_help_sections(core, brief=False):
    """宠物模块帮助段（brief=False「宠物帮助」详版；brief=True「游戏帮助」摘要版）"""
    cost = int(core.param("PET_UNLOCK_COST", PET_UNLOCK_COST))
    if not brief:
        return [
            ("宠物", [
                ("解锁宠物", f"花 {cost} 金币领养宠物（每人限一只）"),
                ("宠物", "查看宠物总览（状态/属性/排行/最近变化/当前项目）"),
                ("更改宠物名字 <名字>", "给宠物起名"),
                ("打工 / 打工 <名称>", "打工赚金币与经验（完成后返回宠物总览图）"),
                ("玩耍 / 玩耍 <名称>", "玩耍赚经验与心情（完成后返回宠物总览图）"),
                ("商店", "查看宠物商店"),
                ("购买 <道具名> [数量]", "购买道具（不填数量 = 1 个）"),
                ("使用 <道具名> [数量]", "使用道具（不填数量 = 1 个，结果合入宠物总览图）"),
                ("背包", "查看背包"),
                ("治疗宠物", "治疗虚弱宠物（花 500 金币，所有数值恢复 40；仅虚弱状态可用）"),
                ("自动照顾 开/关", "开启/关闭自动照顾（同步开启/关闭自动打工；开启时初始照顾把所有属性提升到第 1 档并记录花费；五属性每次变化（打工/玩耍/使用道具/治疗后）与每日结算时检查，任一属性处于第 3/4 档即照顾——健康补到最大健康×目标百分比（默认80%），饱食/口渴/心情/体力第 1/2 档忽略、第 3/4 档补满（体力用体力丸 50金币/个）；优先使用持有道具，没有再购买（手动价×1.1 倍，WebUI 可调）；花费计入基准金币；金币不足自动申请自动化贷款（无上限无逾期，还款指令/打工报酬偿还））"),
                ("自动打工 开/关", "自动打工开关（自动照顾开启且基准金币 > 0 时，选择报酬最接近基准金币的项目，打工完成后进入下一轮；报酬优先偿还自动化贷款，只给金币不给经验）"),
                ("自动化", "查看自动照顾/自动打工状态与指令调用方法"),
                ("自动化帮助", "自动照顾 + 自动打工 玩法说明（含固定刷新时间）"),
                ("结算日志", "查看自动照顾/自动打工记录（购买/使用带数量标记与触发来源）"),
            ]),
        ]
    return [
        ("宠物", [
            ("解锁宠物", f"花 {cost} 金币领养宠物"),
            ("宠物", "查看宠物属性 / 等级 / 经验"),
            ("更改宠物名字 <名字>", "给宠物起名"),
            ("打工 / 打工 <名称>", "打工赚金币与经验"),
            ("玩耍 / 玩耍 <名称>", "玩耍赚经验与心情"),
            ("商店", "查看宠物商店"),
            ("购买 / 使用 <道具名> [数量]", "购买 / 使用道具（不填数量 = 1 个）"),
            ("背包", "查看背包"),
            ("治疗宠物", "治疗虚弱宠物（花 500 金币，所有数值恢复 40；仅虚弱状态可用）"),
            ("自动照顾 开/关", "开启/关闭自动照顾（同步开启/关闭自动打工；开启时初始照顾把所有属性提升到第 1 档并记录花费；五属性每次变化（打工/玩耍/使用道具/治疗后）与每日结算时检查，任一属性处于第 3/4 档即照顾——健康补到最大健康×目标百分比（默认80%），饱食/口渴/心情/体力第 1/2 档忽略、第 3/4 档补满（体力用体力丸 50金币/个）；优先使用持有道具，没有再购买（手动价×1.1 倍，WebUI 可调）；花费计入基准金币；金币不足自动申请自动化贷款（无上限无逾期，还款指令/打工报酬偿还））"),
            ("自动打工 开/关", "自动打工开关（自动照顾开启且基准金币 > 0 时，选择报酬最接近基准金币的项目，打工完成后进入下一轮；报酬优先偿还自动化贷款，只给金币不给经验）"),
            ("自动化 / 自动化帮助", "查看自动化状态与玩法 / 自动照顾+自动打工说明（含固定刷新时间）"),
            ("结算日志", "查看自动照顾/自动打工记录（购买/使用带数量标记与触发来源）"),
        ]),
    ]


# ================= 对外服务（core.service("pet")） =================
class PetApi:
    """宠物服务接口（其它插件经 core.service("pet") 调用；调用方对 None 宠物自行降级）"""

    def __init__(self, core):
        self._core = core

    def pet_of(self, key):
        """取用户宠物 dict（无宠物返回 None；每日结算由固定结算循环统一执行，此处不做懒结算）"""
        return self._core.data.get("pets", {}).get(key)

    def gain_exp(self, key, exp) -> str:
        """给宠物增加经验并刷新等级，返回升级提示行（无宠物/未升级返回空串）。调用方负责 core.save()"""
        pet = self._core.data.get("pets", {}).get(key)
        if not pet:
            return ""
        pet["exp"] = round(float(pet.get("exp", 0.0)) + float(exp or 0.0), 2)
        return apply_exp(pet)

    def weak_guard(self, key, name=""):
        """虚弱宠物守卫：宠物虚弱期间宠物功能被锁定（查看/改名/治疗不受影响）。
        未虚弱返回 None；虚弱返回锁定提示（调用方直接回复）。name 可选（提示中带用户昵称）。"""
        core = self._core
        pet = core.data.get("pets", {}).get(key)
        if not pet or not pet.get("weak"):
            return None
        cost = int(core.param("WEAK_HEAL_COST", WEAK_HEAL_COST))
        who = f"{name} " if name else ""
        return (f"😷 {who}的宠物处于虚弱状态，宠物功能已锁定！\n"
                f"发送「治疗宠物」（{cost} 金币）即可重新激活宠物。")

    def busy_until(self, pet) -> float:
        """打工/玩耍共用冷却计时器：返回忙碌结束时间戳（兼容旧数据 work_until / play_until）"""
        return pet_busy_until(pet)

    def attr_max(self, health):
        """健康度 → (饱食上限, 口渴上限, 体力上限, 心情上限)（PET_ATTR_MAX_RANGES 档位）"""
        return pet_attr_max(self._core, health)

    def state_snippet(self, pet) -> str:
        """宠物当前状态摘要行（反馈末尾附加）"""
        return pet_state_snippet(self._core, pet)

    def settle_display_lines(self, pet):
        """昨晚结算结果展示行（无结算记录返回空列表）"""
        return settle_display_lines(pet)

    def bring_up_to_date(self, pet, today) -> None:
        """把宠物结算到今日（缺几天结算几天；settle_once 逐日执行）"""
        bring_up_to_date(self._core, pet, today)

    def apply_exp(self, pet) -> str:
        """按累计经验刷新等级，返回升级提示行（未升级返回空串）"""
        return apply_exp(pet)

    def pet_level_from_exp(self, exp) -> int:
        """累计经验 → 宠物等级"""
        return pet_level_from_exp(exp)


# ================= 插件挂载 =================
def register(core):
    api = PetApi(core)

    @core.command("宠物", feature="pet")
    def handle_pet_status(event):
        """「宠物」：宠物总览（2.3.0 _handle_pet_status 移植，图片优先 / 文本回退）"""
        name = core.user_name(event)
        key = core.user_key(event)
        data = core.data
        pet = data.get("pets", {}).get(key)
        if not pet:
            unlock_cost = int(core.param("PET_UNLOCK_COST", PET_UNLOCK_COST))
            return f"{name} 还没有宠物，发送「解锁宠物」（需 {unlock_cost} 金币）领养一只吧。"
        # 2.2.0：宠物每日结算由固定结算循环统一执行，查询只显示结算结果

        # 2.0.0 消息合并：预留位是「即时消息」——每条消息的有效显示次数 = 1，
        # 仅在产生新变化的那次响应显示一次（work/play/use 响应已显示 → shown=True，
        # 此处不再重复显示）；被更新的每日结算覆盖后同样隐藏。
        la = pet.get("last_activity") or {}
        use_la = None
        show_slot = False
        if la:
            try:
                la_ts = float(la.get("ts", 0) or 0)
            except (TypeError, ValueError):
                la_ts = 0.0
            if not activity_superseded_by_settle(pet, la_ts):
                use_la = la
                show_slot = not bool(la.get("shown"))  # 有效显示次数 = 1
        img = _render_pet_status(
            core, name, key, data, pet,
            slot_msg=(use_la.get("msg") if show_slot else None),
            changes=(use_la.get("changes") if use_la else None),
            reason=(use_la.get("reason") if use_la else None),
            coins_delta=(use_la.get("coins") if use_la else None),
            exp_delta=(use_la.get("exp") if use_la else None),
            changes_fresh=False,   # 查看场景：无新变化，卡片1 不高亮（除非进度条刚满）
            card2_hl=False,        # 查看场景：工作/玩耍无变动，卡片2 不高亮
        )
        if show_slot and use_la:
            use_la["shown"] = True
        # 保存：即时消息已显示标记 + 进度条满通知标记（渲染函数内更新）
        core.save()
        if img is not None:
            return img

        # 回退：文本版（2.3.0 原样）
        sat_max, thr_max, sta_max, mood_max = pet_attr_max(core, pet["health"])
        lv, got_exp, need_exp = pet_exp_progress(float(pet.get("exp", 0.0)))
        lines = [
            f"🐾 {name} 的宠物「{pet['name']}」：",
            f"⭐ 等级：Lv.{lv}（经验 {pet['exp']:.1f}）",
        ]
        if lv < PET_MAX_LEVEL:
            lines.append(f"📚 距下一级还需 {need_exp - got_exp:.0f} 经验")
        lines += [
            f"🍖 饱食度：{pet['satiety']:.1f}/{sat_max:.0f}",
            f"💧 口渴值：{pet['thirst']:.1f}/{thr_max:.0f}",
            f"⚡ 体力：{pet['stamina']:.1f}/{sta_max:.0f}",
            f"😊 心情值：{pet['mood']:.1f}/{mood_max:.0f}",
            f"❤️ 健康度：{pet['health']:.1f}/{PET_MAX_HEALTH:.0f}",
        ]
        if pet["health"] <= 39:
            lines.append("🤒 宠物生病了，快给它吃药吧！")
        if pet.get("weak"):
            heal_cost = int(core.param("WEAK_HEAL_COST", WEAK_HEAL_COST))
            lines.append(f"😷 宠物处于虚弱状态，发送「治疗宠物」（{heal_cost} 金币）治疗！")
        if show_slot and use_la and use_la.get("msg"):
            lines.append(f"📝 {use_la['msg']}")

        # 末尾显示宠物当前活动：打工 / 玩耍（共用冷却计时器）或发呆
        now_ts = datetime.now().timestamp()
        busy_until = pet_busy_until(pet)
        act = pet.get("busy_activity", "")
        lines.append("")
        if now_ts < busy_until:
            icon = "💼" if act == "打工" else "🎾"
            label = "打工中" if act == "打工" else "玩耍中"
            lines.append(f"{icon} 正在{label}（剩余 {fmt_duration(busy_until - now_ts)}）")
        else:
            lines.append("😴 宠物正在发呆，快带它去打工或玩耍吧～")

        return "\n".join(lines)

    @core.command("更改宠物名字", feature="pet")
    def handle_rename_pet(event):
        """更改宠物名字 <新名字>：给宠物起名（最多 12 个字）"""
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：更改宠物名字 <新名字>"
        new_name = parts[1].strip()
        if not new_name:
            return "格式：更改宠物名字 <新名字>"
        if len(new_name) > 12:
            return "名字太长了（最多 12 个字）。"

        key = core.user_key(event)
        data = core.data
        pet = data.get("pets", {}).get(key)
        if not pet:
            unlock_cost = int(core.param("PET_UNLOCK_COST", PET_UNLOCK_COST))
            return f"你还没有宠物，发送「解锁宠物」（需 {unlock_cost} 金币）领养一只吧。"
        pet["name"] = new_name
        core.save()
        return f"✅ 宠物名字已改为「{new_name}」。"

    @core.command("治疗宠物", feature="pet")
    def handle_weak_heal(event):
        """治疗虚弱宠物：消耗 WEAK_HEAL_COST 金币，所有数值恢复为 40 并解除虚弱。
        只有处于虚弱状态的宠物才能被治疗。"""
        name = core.user_name(event)
        key = core.user_key(event)
        data = core.data
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"
        if not pet.get("weak"):
            return f"{name} 的宠物没有处于虚弱状态，无需治疗。"
        if core.coins_of(key) < WEAK_HEAL_COST:
            return (f"{name} 治疗虚弱宠物需要 {WEAK_HEAL_COST} 金币"
                    f"（当前 {core.coins_of(key)}），发送「签到」获取金币。")
        before = {a: pet[a] for a in ATTR_LABELS}
        core.add_coins(key, -WEAK_HEAL_COST, "治疗虚弱宠物")
        pet["satiety"] = pet["thirst"] = pet["stamina"] = pet["mood"] = pet["health"] = 40.0
        pet["weak"] = False
        pet["weak_streak"] = 0
        clamp_attrs(core, pet)
        # 2.2.1：记录治疗造成的属性变化
        changes = {a: round(pet[a] - before[a], 2) for a in ATTR_LABELS if abs(pet[a] - before[a]) > 1e-9}
        pet_attr_log(core, pet, "治疗", "治疗虚弱宠物", changes, extra=f"花费 {WEAK_HEAL_COST} 金币")
        core.save()
        _auto_care_after_change(core, key)  # 2.2.5：治疗后属性变化 → 自动照顾检查
        return (f"💊 {name} 花费 {WEAK_HEAL_COST} 金币治疗了宠物「{pet.get('name', '宠物')}」，"
                f"虚弱状态已解除！所有数值恢复至 40。\n{pet_state_snippet(core, pet)}")

    @core.command("宠物帮助", feature="pet")
    def handle_help_pet(event):
        """宠物帮助：查看宠物模块指令"""
        return core.image.build_help("宠物帮助", _pet_help_sections(core))

    # ---- 每日结算（固定结算循环统一执行；单只异常只记日志不阻断，失败次日自动重试） ----
    def _pet_settle(data, now):
        ok = True
        today = date.today().isoformat()
        for key, pet in list((data.get("pets") or {}).items()):
            if not isinstance(pet, dict):
                continue
            try:
                api.bring_up_to_date(pet, today)
            except Exception as e:
                ok = False
                logger.error(f"[宠物] 宠物每日结算异常 uid={key}: {e}")
        core.save()
        return ok

    core.daily("pet_settle", _pet_settle)

    # ---- 对外服务（签到 / 打工 / 玩耍 / 商店 / 道具 / 排行榜等经 core.service("pet") 调用） ----
    core.expose("pet", api)

    # ---- 「游戏帮助」菜单段（与 2.3.0 游戏帮助宠物段一致） ----
    core.add_help("宠物", _pet_help_sections(core, brief=True))
