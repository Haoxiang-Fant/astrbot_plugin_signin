# -*- coding: utf-8 -*-
"""自动打工（3.0.0 功能插件）。自 2.3.0 modules/pet.py「自动打工」部分原样迁移（2.2.7 重写版）：
「自动打工 开/关」「自动化」「自动化帮助」「结算日志」指令 + 60s 巡检（core.interval(60)）。
- 触发条件：自动打工总开关 + 自动照顾开启（总开关 + 用户开关）+ 用户开启了自动打工 + 有宠物，
  且基准金币（user["work_base"]）> 0；
- 流程：宠物忙碌 → 等待打工完成进入下一轮；空闲 → 选择「报酬最接近基准金币」的可进行项目执行：
  消耗属性 → 报酬优先偿还自动化贷款（user["auto_loan"]，无余额时进入金币账户）
  → 无论去向基准金币均扣除报酬 → 记录 user["auto_work_logs"]（≤ AUTO_WORK_LOG_MAX 条）；
  执行后按「宠物活动导致属性变化」触发照顾检查（经 core.service("pet_auto_care")）。
打工执行使用本插件内置流程（2.3.0 _auto_work_execute 原样移植，含自动化记账）。
巡检由 core 统一巡检协程（core.interval）承载——interval 任务约定自行加锁，
巡检体统一 `async with core.lock` 后执行。"""
from datetime import date, datetime

from astrbot.api import logger

from ..core import (AUTO_FEED_ENABLED, AUTO_WORK_ENABLED, AUTO_WORK_LOG_MAX,
                    ATTR_LABELS, PET_MAX_HEALTH)

NAME = "pet_auto_work"

_CORE = None  # register(core) 时注入的核心框架实例

# 每只宠物的「属性变化记录」上限（与宠物插件口径一致，超出丢弃最旧）
_ATTR_LOG_MAX = 300


def _f(x):
    """安全转 float（失败回退 0.0）"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ================= 宠物插件能力（core.service("pet")，全部 None-safe） =================
def _pet_fn(name):
    """取宠物插件接口方法；插件未挂载/无该方法返回 None（调用方自行降级）"""
    svc = _CORE.service("pet") if _CORE is not None else None
    fn = getattr(svc, name, None)
    return fn if callable(fn) else None


def _bring_up_to_date(pet, today):
    """self._bring_pet_up_to_date → core.service("pet").bring_up_to_date（None-safe：插件未挂载时跳过）"""
    fn = _pet_fn("bring_up_to_date")
    if fn is None:
        return
    try:
        fn(pet, today)
    except Exception as e:
        logger.error(f"[pet_auto_work] bring_up_to_date 调用失败: {e}")


def _pet_busy_until(pet):
    """self._pet_busy_until → core.service("pet").busy_until；
    插件未挂载/调用失败时回退同口径本地判定（兼容旧数据 work_until / play_until）。"""
    fn = _pet_fn("busy_until")
    if fn is not None:
        try:
            return float(fn(pet))
        except Exception as e:
            logger.error(f"[pet_auto_work] busy_until 调用失败: {e}")
    busy = float(pet.get("busy_until", 0) or 0)
    old = max(float(pet.get("work_until", 0) or 0), float(pet.get("play_until", 0) or 0))
    return max(busy, old)


def _attr_max(health):
    """core.service("pet").attr_max(h)；宠物插件未挂载/调用失败返回 None"""
    fn = _pet_fn("attr_max")
    if fn is None:
        return None
    try:
        return fn(health)
    except Exception as e:
        logger.error(f"[pet_auto_work] attr_max 调用失败: {e}")
        return None


def _clamp_attrs(pet):
    """属性裁剪到当前健康度对应的各属性上限（2.3.0 _clamp_attrs 原样移植）；
    宠物插件未挂载时跳过（无上限数据可裁）。"""
    maxes = _attr_max(pet.get("health", 0))
    if maxes is None:
        return
    sat_max, thr_max, sta_max, mood_max = maxes
    pet["satiety"] = round(_CORE.clamp(pet["satiety"], 0, sat_max), 2)
    pet["thirst"] = round(_CORE.clamp(pet["thirst"], 0, thr_max), 2)
    pet["stamina"] = round(_CORE.clamp(pet["stamina"], 0, sta_max), 2)
    pet["mood"] = round(_CORE.clamp(pet["mood"], 0, mood_max), 2)
    pet["health"] = round(_CORE.clamp(pet["health"], 0, PET_MAX_HEALTH), 2)


def _pet_attr_log(pet, cat, behavior, changes, extra="", ts=None, items=None):
    """追加一条宠物属性变化记录（与 2.3.0 _pet_attr_log 同结构，WebUI 宠物详情展示用）。
    宠物插件未挂载（无属性上限数据）时跳过记录。"""
    if not changes:
        return
    if ts is None:
        ts = datetime.now().timestamp()
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = datetime.now().timestamp()
    maxes = _attr_max(pet.get("health", 0))
    if maxes is None:
        return
    sat_max, thr_max, sta_max, mood_max = maxes
    log = pet.setdefault("attr_log", [])
    entry = {
        "ts": ts,
        "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
        "cat": cat,
        "behavior": behavior,
        "changes": {k: round(float(v), 2) for k, v in changes.items() if abs(float(v)) > 1e-9},
        "extra": extra or "",
        # 变动后的属性绝对值快照 + 变动发生时的各属性上限快照
        "after": {k: round(float(pet.get(k, 0)), 2) for k in ATTR_LABELS},
        "max": {"satiety": sat_max, "thirst": thr_max, "stamina": sta_max,
                "mood": mood_max, "health": PET_MAX_HEALTH},
    }
    log.append(entry)
    if len(log) > _ATTR_LOG_MAX:
        del log[:len(log) - _ATTR_LOG_MAX]


# ================= 数值配置（core.items()["jobs"] 扁平结构 → 2.3.0 内部结构） =================
def _normalize_jobs(flat_jobs):
    """game_items.json 扁平结构 → [{name, desc, min_level, min_health, min_mood,
    cost{stamina/satiety/thirst/health/mood}, time, coins, exp}]
    （与 2.3.0 _items_flat_to_normalized 同口径）。"""
    out = []
    for j in flat_jobs or []:
        if not isinstance(j, dict):
            continue
        name = str(j.get("name", "") or "").strip()
        if not name:
            continue
        out.append({"name": name, "desc": str(j.get("desc", "") or ""),
                    "min_level": _f(j.get("min_level", 0)),
                    "min_health": _f(j.get("min_health", 0)),
                    "min_mood": _f(j.get("min_mood", 0)),
                    "cost": {"stamina": _f(j.get("cost_stamina", 0)),
                             "satiety": _f(j.get("cost_satiety", 0)),
                             "thirst": _f(j.get("cost_thirst", 0)),
                             "health": _f(j.get("cost_health", 0)),
                             "mood": _f(j.get("cost_mood", 0))},
                    "time": _f(j.get("time", 0)),
                    "coins": _f(j.get("coins", 0)),
                    "exp": _f(j.get("exp", 0))})
    return out


def _item_can_do(pet, item, now_ts, busy):
    """该项目当前是否可以进行（与列表灰卡判定一致：忙碌/等级/健康/心情/消耗不足均不可）"""
    if pet is None:
        return False
    if busy:
        return False
    if pet["level"] < item["min_level"]:
        return False
    if pet["health"] < item["min_health"]:
        return False
    if pet["mood"] < item["min_mood"]:
        return False
    for attr, cost in item["cost"].items():
        if cost > 0 and pet[attr] < cost:
            return False
    return True


# ================= 自动化贷款（单一余额 user["auto_loan"]，与 pet_auto_care 同口径） =================
def _auto_loan_balance_of(data, key):
    """自动化贷款余额"""
    u = data.get("users", {}).get(key)
    if not isinstance(u, dict):
        return 0.0
    try:
        return round(float(u.get("auto_loan", 0) or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _auto_loan_repay(data, key, amount):
    """偿还自动化贷款余额（自动打工报酬优先还贷）。返回实际偿还金额。"""
    bal = _auto_loan_balance_of(data, key)
    try:
        pay = min(float(amount or 0), bal)
    except (TypeError, ValueError):
        return 0.0
    if pay <= 0:
        return 0.0
    u = _CORE.ensure_user(key)
    left = round(bal - pay, 2)
    u["auto_loan"] = left if left >= 0.01 else 0
    return round(pay, 2)


# ================= 开关条件（2.2.7） =================
def _auto_care_enabled_for(data, key):
    """自动照顾是否对该用户生效（与 pet_auto_care 插件同口径的本地判定：
    总开关 + 主人开启 + 有宠物且未虚弱）"""
    if not bool(_CORE.param("AUTO_FEED_ENABLED", AUTO_FEED_ENABLED)):
        return False
    u = data.get("users", {}).get(key)
    if not (u and u.get("auto_feed_enabled")):
        return False
    pet = data.get("pets", {}).get(key)
    return bool(pet) and not pet.get("weak")


def _auto_work_enabled_for(data, key):
    """自动打工条件（2.2.7）：自动打工总开关 + 自动照顾开启（总开关 + 用户开关）
    + 用户/管理员开启了自动打工 + 有宠物。基准金币 > 0 在流程内判定。"""
    if not bool(_CORE.param("AUTO_WORK_ENABLED", AUTO_WORK_ENABLED)):
        return False
    if not _auto_care_enabled_for(data, key):
        return False
    u = data.get("users", {}).get(key)
    return bool(u and u.get("auto_work_enabled")) and data.get("pets", {}).get(key) is not None


# ================= 照顾联动（经 core.service("pet_auto_care")，全部 None-safe） =================
def _care_due(data, key):
    """该用户是否需要自动照顾；pet_auto_care 插件未挂载/调用失败时按「无需照顾」处理"""
    svc = _CORE.service("pet_auto_care") if _CORE is not None else None
    fn = getattr(svc, "care_due", None) if svc is not None else None
    if not callable(fn):
        return False
    try:
        return bool(fn(key))
    except Exception as e:
        logger.error(f"[pet_auto_work] care_due 调用失败: {e}")
        return False


def _care_run(data, key, trigger):
    """触发一次自动照顾（照顾插件内部完成 core.save()）；未挂载/异常只记日志不阻断。"""
    svc = _CORE.service("pet_auto_care") if _CORE is not None else None
    fn = getattr(svc, "care_run", None) if svc is not None else None
    if not callable(fn):
        return
    try:
        fn(key, trigger)
    except Exception as e:
        logger.error(f"[pet_auto_work] care_run 调用失败: {e}")


# ================= 自动打工执行（2.2.7 重写） =================
def _auto_work_pick_job(pet, base, cfg_jobs):
    """选择报酬最接近基准金币的可进行打工项目（要求达标 + 不忙碌 + 非虚弱）。返回项目 dict 或 None。"""
    now_ts = datetime.now().timestamp()
    busy = now_ts < _pet_busy_until(pet)
    best = None
    best_diff = None
    for j in cfg_jobs:
        if not _item_can_do(pet, j, now_ts, busy):
            continue
        diff = abs(int(j.get("coins", 0) or 0) - base)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best = j
    return best


def _auto_work_execute(data, key, u, pet, job, now_ts):
    """执行一次自动打工（2.2.7 原样移植）：消耗属性 → 报酬优先偿还自动化贷款余额
    （无余额时进入金币账户）→ 无论去向基准金币均扣除报酬 → 记录日志。
    只给金币不给经验；结果合入宠物总览图的预留位（不主动发消息）。"""
    before = {a: pet[a] for a in ATTR_LABELS}
    for attr, cost in job["cost"].items():
        pet[attr] = round(max(0.0, pet[attr] - cost), 2)
    wage = int(job["coins"])
    repaid = _auto_loan_repay(data, key, wage)  # 报酬优先偿还自动化贷款（还款系统）
    into_account = wage - int(repaid)
    if into_account > 0:
        _CORE.add_coins(key, into_account, f"自动打工·{job['name']}")
    _clamp_attrs(pet)
    pet["busy_until"] = now_ts + int(job["time"]) * 60
    pet["busy_start"] = now_ts
    pet["busy_activity"] = "打工"
    pet["busy_item"] = job["name"]
    pet["_progress_done_notified"] = False
    # 基准金币：自动化打工产生的报酬入账（不论进入账户还是偿还贷款）→ 减少
    base0 = int(u.get("work_base", 0) or 0)
    u["work_base"] = base0 - wage
    changes = {a: round(pet[a] - before[a], 2) for a in ATTR_LABELS if abs(pet[a] - before[a]) > 1e-9}
    extra = f"金币+{wage}" + (f"（偿还自动化贷款 {int(repaid)}）" if repaid > 0 else "")
    _pet_attr_log(pet, "自动打工", f"自动打工「{job['name']}」", changes, extra=extra)
    pet["last_activity"] = {
        "msg": f"{pet['name']} 自动去「{job['name']}」打工成功！",
        "changes": changes,
        "reason": f"自动打工「{job['name']}」",
        "coins": wage,
        "exp": 0,
        "act": "打工",
        "ts": now_ts,
        "shown": False,
    }
    logs = u.setdefault("auto_work_logs", [])
    logs.append({"date": date.today().isoformat(),
                 "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "job": job["name"], "coins": wage,
                 "exp": 0,
                 "repaid": round(float(repaid), 2),
                 "base_before": base0, "base_after": int(u.get("work_base", 0) or 0)})
    cap = int(_CORE.param("AUTO_WORK_LOG_MAX", AUTO_WORK_LOG_MAX) or 30)
    if len(logs) > cap:
        del logs[:len(logs) - cap]


# ================= 60s 巡检（core.interval 承载，2.3.0 _auto_work_loop 原样移植） =================
async def _auto_work_tick():
    """自动打工 60s 巡检入口。
    core 统一巡检协程调用 interval 任务时不持 core.lock（周期任务约定自行加锁），
    此处统一加锁；命令处理器等其他调用方亦安全（持锁期间不会与本任务并发）。"""
    core = _CORE
    if core is None:
        return
    async with core.lock:
        _auto_work_pass()


def _auto_work_pass():
    """巡检一轮（需在 core.lock 内执行）：遍历全部用户 ——
    触发条件（自动照顾开启 + 用户/管理员开启自动打工 + 基准金币 > 0）满足时：
    宠物忙碌 → 等待打工完成进入下一轮；空闲 → 选择报酬最接近基准金币的项目执行；
    执行后按「宠物活动导致属性变化」触发照顾检查。有改动时 core.save()。"""
    data = _CORE.data
    changed = False
    now_ts = datetime.now().timestamp()
    cfg_jobs = _normalize_jobs((_CORE.items() or {}).get("jobs"))
    for key, u in list((data.get("users") or {}).items()):
        if not isinstance(u, dict):
            continue
        if not _auto_work_enabled_for(data, key):
            continue
        base = int(u.get("work_base", 0) or 0)
        if base <= 0:
            continue  # 基准金币大于 0 才自动打工
        pet = data.get("pets", {}).get(key)
        if not pet or pet.get("weak"):
            continue
        _bring_up_to_date(pet, date.today().isoformat())
        if now_ts < _pet_busy_until(pet):
            continue  # 等待打工完成，进入下一轮
        job = _auto_work_pick_job(pet, base, cfg_jobs)
        if not job:
            continue  # 暂无可进行项目（属性/等级不足），下轮再查
        # 2.3.0 自动化记账（报酬先还 auto_loan、扣减 work_base、写 auto_work_logs、exp=0）
        # 完整保留在内部执行流程中，故不委托 pet_work.start（其手动打工语义与之不同）
        _auto_work_execute(data, key, u, pet, job, now_ts)
        changed = True
        # 宠物活动（打工）导致属性变化 → 触发照顾检查（照顾自身的消耗不再触发）
        if _care_due(data, key):
            _care_run(data, key, trigger="自动打工后")
    if changed:
        _CORE.save()


# ================= 服务暴露（其他插件查询自动打工开关状态） =================
class PetAutoWorkApi:
    """自动打工服务"""

    def auto_work_enabled_for(self, key):
        """自动打工条件判定（总开关 + 自动照顾开启 + 用户开关 + 有宠物）"""
        return _auto_work_enabled_for(_CORE.data, key)


def register(core):
    global _CORE
    _CORE = core

    # ================= 自动打工 开/关 =================
    @core.command("自动打工", feature="pet_auto")
    def handle_auto_work_switch(event):
        """自动打工 <开/关>（2.2.7 重写）：单独开关自动打工（需先开启自动照顾）。
        触发条件：自动照顾开启 + 基准金币 > 0 + 用户指令或管理员后台开启了自动打工。"""
        name = core.user_name(event)
        key = core.user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or parts[1].strip() not in ("开", "关"):
            return f"{name} 请指定：自动打工 开 / 自动打工 关"
        data = core.data
        if not data.get("pets", {}).get(key):
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"
        u = core.ensure_user(key)
        on = parts[1].strip() == "开"
        if on and not u.get("auto_feed_enabled"):
            return f"{name} 未开启自动照顾，不允许开启自动打工（请先发送「自动照顾 开」）。"
        u["auto_work_enabled"] = on
        # 巡检由 core 统一 ticker 的 interval 任务常驻执行（2.3.0 的懒启动计时器不再需要）
        base = int(u.get("work_base", 0) or 0)
        core.save()
        if on:
            return (f"✅ {name} 已开启自动打工：基准金币 > 0（当前 {base}）时自动打工——"
                    f"选择「报酬最接近基准金币」的可进行项目，等待打工完成后进入下一轮；"
                    f"报酬优先偿还自动化贷款，其余进入金币账户，无论去向基准金币均扣除报酬。")
        return f"⏹️ {name} 已关闭自动打工。"

    # ================= 自动化（状态总览） =================
    @core.command("自动化", feature="pet_auto")
    def handle_auto_overview(event):
        """自动化（2.2.7 重写）：查看当前用户的自动照顾/自动打工状态、基准金币、
        自动化贷款余额与最近记录，并列出可用于控制自动化的指令。"""
        name = core.user_name(event)
        key = core.user_key(event)
        data = core.data
        pet = data.get("pets", {}).get(key)
        u = data.get("users", {}).get(key) or {}
        lines = [f"🤖 {name} 的自动化（自动照顾 + 自动打工）状态：", ""]
        if not pet:
            lines.append("还没有宠物：发送「解锁宠物」领养后再开启自动化。")
            lines.append("")
            lines.append("【指令调用方法】")
            lines.append("· 自动照顾 开 / 自动照顾 关 —— 开启/关闭自动照顾（同步开启/关闭自动打工）")
            lines.append("· 自动打工 开 / 自动打工 关 —— 单独开关自动打工（需先开启自动照顾）")
            lines.append("· 结算日志 —— 查看自动照顾/自动打工记录")
            lines.append("· 自动化帮助 —— 查看更多说明")
            img = core.image.text("自动化", lines)
            return img if img is not None else "\n".join(lines)
        g_feed = bool(core.param("AUTO_FEED_ENABLED", AUTO_FEED_ENABLED))
        g_work = bool(core.param("AUTO_WORK_ENABLED", AUTO_WORK_ENABLED))
        feed_on = bool(u.get("auto_feed_enabled"))
        work_on = bool(u.get("auto_work_enabled"))
        base = int(u.get("work_base", 0) or 0)
        lines.append(f"· 自动照顾：{'✅ 已开启' if (g_feed and feed_on) else ('总开关关闭' if not g_feed else '未开启')}")
        lines.append(f"· 自动打工：{'✅ 已开启' if (g_feed and g_work and feed_on and work_on) else '未开启/未满足条件'}")
        loan_bal = _auto_loan_balance_of(data, key)
        if loan_bal > 0:
            lines.append(f"· 自动化贷款：欠 {loan_bal:.0f} 金币（无上限无逾期；可用「还款」指令或自动打工报酬偿还）")
        lines.append(f"· 基准金币：{base}（自动照顾花费为加、自动打工报酬为减；"
                     f"{'> 0 时自动打工持续填补' if base > 0 else '当前不触发自动打工'}）")
        fl = u.get("auto_feed_logs") or []
        wl = u.get("auto_work_logs") or []
        if fl:
            lg = fl[-1]
            items = "、".join(f"{'🛒' if it.get('src') == '购买' else '📦'}{it['name']}×{it['qty']}" for it in lg.get("items", []))
            lines.append(f"· 最近照顾：{lg.get('ts', lg.get('date', ''))}（{lg.get('trigger', '')}）{items}（花 {lg.get('total', 0)} 金币）")
        if wl:
            lg = wl[-1]
            lines.append(f"· 最近打工：{lg.get('ts', lg.get('date', ''))}「{lg.get('job')}」+{lg.get('coins', 0)} 金币（基准 {lg.get('base_before')} → {lg.get('base_after')}）")
        if not fl and not wl:
            lines.append("· 暂无自动照顾/自动打工记录")
        lines.append("")
        lines.append(f"⏰ 固定结算：每天 {int(core.param('DAILY_SETTLE_HOUR', 0) or 0)} 点结算插件数据并触发自动照顾检查，"
                     f"{int(core.param('BANK_SETTLE_HOUR', 4) or 4)} 点结算银行存款数据。")
        lines.append("")
        lines.append("【指令调用方法】")
        lines.append("· 自动照顾 开 / 自动照顾 关 —— 开启/关闭自动照顾（同步开启/关闭自动打工）")
        lines.append("· 自动打工 开 / 自动打工 关 —— 单独开关自动打工（需先开启自动照顾）")
        lines.append("· 结算日志 —— 查看自动照顾/自动打工记录")
        lines.append("· 自动化帮助 —— 查看更多说明")
        img = core.image.text("自动化", lines)
        return img if img is not None else "\n".join(lines)

    # ================= 自动化帮助 =================
    @core.command("自动化帮助", feature="pet_auto")
    def handle_auto_help(event):
        """自动化帮助（2.2.7 重写）"""
        lines = [
            "🤖 自动化帮助（自动照顾 + 自动打工）",
            "",
            "【自动照顾】指令：自动照顾 开 / 自动照顾 关",
            "· 开：开启自动照顾并同步开启自动打工；随后执行开启时的初始照顾——",
            "  检查宠物状态，使用道具把所有属性提升到第 1 档（健康补到目标值），记录花费；",
            "· 关：关闭自动照顾并同步关闭自动打工；",
            "· 触发检查：宠物活动（打工/玩耍）、使用道具、每日结算导致任意属性变化后自动检查；",
            "· 检查机制：任一属性处于第 3/4 档 → 立即自动照顾；",
            "· 照顾内容：健康优先补到 最大健康 × 目标百分比（WebUI 可调，默认 80%）；",
            "  随后 饱食(食物) → 口渴(饮料) → 心情(玩具) → 体力(体力丸) 逐项检查：",
            "  处于第 1/2 档 → 本次忽略；处于第 3/4 档 → 补满；",
            "· 道具来源：优先使用持有的道具（五条属性效果全部生效并套用好感加成，与手动使用一致）；",
            "  仓库没有 → 购买并立即使用（价格 = 手动购买价 × 1.1 倍，WebUI 可调）；",
            "· 体力丸：仅自动化程序可购买（不进商店，手动「购买/使用」无效），50 金币/个，直接补满体力；",
            "· 档位统一由属性最大值数据推导（一档 60% / 二档 35% / 三档 15% 上限），无需单独配置；",
            "",
            "【基准金币】",
            "· 自动照顾的金币消耗账本，也是自动打工的项目选择依据；",
            "· 增加：自动照顾购买道具的金币消耗；减少：自动打工产生的报酬入账",
            " （不论报酬进入金币账户还是偿还自动化贷款）；",
            "",
            "【自动化贷款】",
            "· 金额无上限、无逾期、无利息，仅自动照顾可贷（不能手动贷款）；",
            "· 偿还方式：「还款」指令 或 自动打工报酬自动扣除（报酬优先还贷）；",
            "",
            "【自动打工】指令：自动打工 开 / 自动打工 关（需先开启自动照顾）",
            "· 触发条件：自动照顾开启 + 基准金币 > 0 + 用户指令或管理员后台开启了自动打工；",
            "· 流程：检查基准金币 → 选择报酬最接近基准金币的可进行项目 → 等待打工完成后进入下一轮；",
            "· 启动：出现自动照顾行为导致基准金币增加时启动；默认只给金币不给经验；",
            "",
            "【查看与记录】",
            "· 自动化 —— 查看当前状态与指令调用方法；",
            "· 结算日志 —— 查看自动照顾/自动打工记录（含触发来源）。",
        ]
        img = core.image.text("自动化帮助", lines)
        return img if img is not None else "\n".join(lines)

    # ================= 结算日志 =================
    @core.command("结算日志", feature="pet_auto")
    def handle_auto_feed_log(event):
        """结算日志：查看自动照顾记录（购买/使用带数量标记）与自动打工记录
        （记录展示触发来源——档位触发/开启触发/每日结算/自动打工后）。"""
        name = core.user_name(event)
        key = core.user_key(event)
        data = core.data
        u = data.get("users", {}).get(key) or {}
        logs = u.get("auto_feed_logs") or []
        wlogs = u.get("auto_work_logs") or []
        if not logs and not wlogs:
            return f"{name} 还没有自动照顾/自动打工记录（发送「自动照顾 开」开启）。"
        lines = [f"📋 {name} 的自动照顾/打工记录（最近 {min(10, len(logs))} 条购买、{min(5, len(wlogs))} 条打工）：", ""]
        for lg in reversed(logs[-10:]):
            trig = lg.get("trigger") or "档位触发"
            if int(lg.get("total", 0) or 0) > 0:
                items = "、".join(
                    f"{'🛒' if it.get('src') == '购买' else '📦'}{it['name']}×{it['qty']}" + (f"（{it['cost']}金）" if it.get('cost') else "")
                    for it in lg.get("items", []))
                loan_txt = f"｜自动化贷款 {int(lg.get('loan', 0) or 0)}" if int(lg.get("loan", 0) or 0) > 0 else ""
                lines.append(f"购买 {lg.get('date', '')}（{trig}）：{items}，共花费 {lg['total']} 金币{loan_txt}")
            else:
                lines.append(f"购买 {lg.get('date', '')}（{trig}）：宠物状态良好，无需购买")
        for lg in reversed(wlogs[-5:]):
            _exp_txt = f"，经验 +{float(lg.get('exp', 0) or 0):.1f}" if float(lg.get("exp", 0) or 0) > 0 else ""
            lines.append(f"打工 {lg.get('date', '')}：完成「{lg['job']}」+{lg['coins']}金币{_exp_txt}，"
                         f"基准 {lg['base_before']} → {lg['base_after']}")
        img = core.image.text("自动照顾/打工记录", lines)
        if img is not None:
            return img
        return "\n".join(lines)

    # 60s 巡检：自动打工引擎（core 统一巡检协程承载；巡检体内不再重复加锁，见 _auto_work_tick）
    core.interval(60, _auto_work_tick)

    # 服务暴露
    core.expose("pet_auto_work", PetAutoWorkApi())

    # 「游戏帮助」菜单段
    core.add_help("自动化", [
        ("自动照顾 开/关", "开启/关闭自动照顾（同步开启/关闭自动打工）"),
        ("自动打工 开/关", "单独开关自动打工（需先开启自动照顾）"),
        ("自动化", "查看自动化状态、基准金币与最近记录"),
        ("自动化帮助", "自动化规则详细说明"),
        ("结算日志", "查看自动照顾/自动打工记录（含触发来源）"),
    ])
