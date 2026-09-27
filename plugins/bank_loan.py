# -*- coding: utf-8 -*-
"""银行贷款（3.0.0 功能插件）。自 2.3.0 modules/loans.py 原样迁移：
借款 / 还款 / 我的贷款 / 我的征信；账单数据存于 data["loans"]（结构不变）。
套餐 0=特别（强制解锁）/ 1=一般 / 2=短期 / 3~10=自定义（game_items.json loans，回退 贷款套餐.txt）。
逾期处置与自动还款由 core.at 每日任务统一执行（2.2.0 口径，前台只读结算结果）；
获得金币时的逾期自动划扣还款（LOAN_COIN_DEDUCT）改为订阅 core 的 coins.pre_add 联动事件
（原地修改 payload["amount"]，与 2.3.0 _add_coins 内联逻辑等价）。
跨插件能力一律 core.service("farm"/"affection") None-safe 降级（降级为直接操作共享 data）。"""
import random
from datetime import date, datetime

from astrbot.api import logger

from ..core import (LOAN_SPECIAL_AMOUNT, LOAN_SPECIAL_RATE, LOAN_SPECIAL_DAYS,
                    LOAN_COIN_DEDUCT, LOAN_FAV_DROP_SPECIAL, LOAN_FAV_DROP_NORMAL,
                    LOAN_OVERDUE_YEAR_LIMIT, LOAN_GENERAL_OVERDUE_DAYS, LOAN_SHORT_GRACE_DAYS,
                    LOAN_SHORT_RATE, LOAN_DAILY_MULT, LOAN_FARM_ROLLBACK_DAYS, LOAN_AUTO_TIME,
                    DAILY_SETTLE_HOUR, FARM_FREE_PLOTS, MIN_COINS, MAX_COINS, MIN_FAV, MAX_FAV,
                    LOAN_FILE)

NAME = "bank_loan"

_CORE = None  # register(core) 时注入的核心框架实例


# ================= 套餐配置 =================
def _norm_loan_entry(d: dict):
    """规范化一条贷款套餐（扁平 dict：code + 5 个数值字段）"""
    if not isinstance(d, dict):
        return None
    try:
        code = int(str(d.get("code", "")).strip())
    except (TypeError, ValueError):
        return None
    if not 3 <= code <= 10:
        return None
    return {
        "code": code,
        "desc": str(d.get("desc", "") or ""),
        "max_amount": int(_CORE.f(d.get("max_amount", 0))),
        "fav_req": int(_CORE.f(d.get("fav_req", 0))),
        "pet_req": int(_CORE.f(d.get("pet_req", 0))),
        "farm_req": int(_CORE.f(d.get("farm_req", 0))),
        "rate": _CORE.f(d.get("rate", 0)),
    }


def _load_loan_packages():
    """自定义贷款套餐（代码 3~10）。1.7.7：优先读 game_items.json，回退解析 贷款套餐.txt"""
    flat = _CORE.items()
    loans = [l for l in (_norm_loan_entry(d) for d in (flat.get("loans") or [])) if l]
    if loans:
        return loans
    result = []
    for it in _CORE.parse_kv_sections(LOAN_FILE, "贷款套餐"):
        d = it["data"]
        try:
            result.append({
                "code": int(it["name"]),
                "max_amount": int(_CORE.f(d.get("最大金额", 0))),
                "fav_req": int(_CORE.f(d.get("好感度等级要求", 0))),
                "pet_req": int(_CORE.f(d.get("宠物等级要求", 0))),
                "farm_req": int(_CORE.f(d.get("农场等级要求", 0))),
                "rate": _CORE.f(d.get("利息", 0)),
            })
        except Exception:
            continue
    return result


# ================= 账单记录（data["loans"]） =================
def _loans_of(data, key):
    return data.get("loans", {}).get(key)


def _ensure_loans(data, key):
    return data.setdefault("loans", {}).setdefault(key, {
        "loans": [], "overdue_records": [],
        "overdue_year": 0, "overdue_year_key": str(date.today().year),
        "ban": False, "daily_borrowed": 0, "daily_date": "",
        "daily_process_date": "", "daily_repay_date": "",
    })


def _loan_unlocked(data, key):
    return key in data.get("pets", {}) or key in data.get("farms", {})


@staticmethod
def _loan_general_max(farm_level):
    if farm_level <= 10:
        return 3000
    if farm_level <= 25:
        return 10000
    if farm_level <= 50:
        return 20000
    if farm_level <= 75:
        return 40000
    return 100000


@staticmethod
def _loan_short_max(fav_level):
    if fav_level <= 2:
        return 1000
    if fav_level <= 5:
        return 2000
    if fav_level <= 8:
        return 3000
    if fav_level == 9:
        return 4000
    return 6000


def _loan_general_rate(pet_level):
    """一般套餐日息 2%~5% 随机，宠物等级减免"""
    rate = random.uniform(2.0, 5.0)
    if pet_level > 0:
        if random.random() < pet_level / 100.0:
            rate -= 1.0
        if pet_level <= 25:
            rate -= 0.1
        elif pet_level <= 75:
            rate -= 0.2
        else:
            rate -= 0.5
    return max(0.1, round(rate, 2))


def _pet_level(data, key):
    pet = data.get("pets", {}).get(key)
    return int(pet.get("level", 0)) if pet else 0


def _loan_package(data, key, code):
    """返回套餐信息：0 特别、1 一般、2 短期、3+ 自定义"""
    if code == 0:
        return {"code": 0, "max_amount": _CORE.param("LOAN_SPECIAL_AMOUNT", LOAN_SPECIAL_AMOUNT),
                "rate": _CORE.param("LOAN_SPECIAL_RATE", LOAN_SPECIAL_RATE),
                "fav_req": 0, "pet_req": 0, "farm_req": 0, "special": True}
    if code == 1:
        farm = data.get("farms", {}).get(key)
        return {"code": 1, "max_amount": _loan_general_max(int(farm.get("level", 0)) if farm else 0),
                "rate": _loan_general_rate(_pet_level(data, key)),
                "fav_req": 0, "pet_req": 0, "farm_req": 0, "special": False}
    if code == 2:
        user = data.get("users", {}).get(key, {})
        return {"code": 2, "max_amount": _loan_short_max(_CORE.level_of(float(user.get("favorability", 0.0)))),
                "rate": _CORE.param("LOAN_SHORT_RATE", LOAN_SHORT_RATE),
                "fav_req": 0, "pet_req": 0, "farm_req": 0, "special": False}
    # （2.2.7：自动化贷款已改为 auto_loan 余额制，不再有套餐代码，任何代码都无法手动借出自动化贷款）
    for pkg in _load_loan_packages():
        if pkg["code"] == code:
            return {"code": code, "max_amount": pkg["max_amount"], "rate": pkg["rate"],
                    "fav_req": pkg["fav_req"], "pet_req": pkg["pet_req"], "farm_req": pkg["farm_req"], "special": False}
    return None


# ================= 欠款计算 =================
@staticmethod
def _loan_accrued(loan, now_ts):
    if loan.get("remaining", 0) <= 0:
        return 0.0
    start = max(loan.get("free_until_ts", 0), loan.get("borrow_ts", 0))
    if now_ts <= start:
        return 0.0
    days = (now_ts - start) // 86400
    if days <= 0:
        return 0.0
    return round(loan.get("remaining", 0) * loan.get("rate", 0) / 100.0 * days, 2)


def _loan_owed(loan, now_ts):
    return round(loan.get("remaining", 0) + _loan_accrued(loan, now_ts), 2)


@staticmethod
def _loan_is_overdue(loan, now_ts):
    return now_ts > loan.get("due_ts", 0) and loan.get("remaining", 0) > 0


def _has_overdue_now(rec, now_ts):
    return any(_loan_is_overdue(l, now_ts) for l in rec.get("loans", []))


# ================= 农场/宠物/好感度（跨插件 None-safe：服务优先，降级共享 data 直改） =================
def _ensure_farm(data, key):
    """取/建农场记录：farm 服务可用时优先（结构由农场插件保证），否则按 2.3.0 字面量降级"""
    svc = _CORE.service("farm")
    fn = getattr(svc, "ensure_farm", None) if svc is not None else None
    if callable(fn):
        try:
            farm = fn(key)
            if isinstance(farm, dict):
                return farm
        except Exception as e:
            logger.warning(f"[bank_loan] farm.ensure_farm 调用失败，降级直改共享数据: {e}")
    return data.setdefault("farms", {}).setdefault(key, {
        "level": 0, "exp": 0.0, "plots": [],
        "warehouse": {"crops": {}, "seeds": {}, "fertilizers": {}},
        "tools": {},          # 农场特殊道具（如 农场经验球 2.0.1 改属农场）
        "total_profit": 0,
        "steal_infos": [], "steal_log": {}, "scent_memory": {},
    })


def _farm_of(data, key):
    """读农场记录：farm 服务可用时优先，否则读共享 data"""
    svc = _CORE.service("farm")
    fn = getattr(svc, "farm_of", None) if svc is not None else None
    if callable(fn):
        try:
            farm = fn(key)
            if farm is not None:
                return farm
        except Exception:
            pass
    return data.get("farms", {}).get(key)


def _new_plots(count):
    """新建地块：farm 服务可用时优先借用其结构，否则按 2.3.0 字面量降级"""
    svc = _CORE.service("farm")
    fn = getattr(svc, "new_plot", None) if svc is not None else None
    if callable(fn):
        try:
            plots = [fn() for _ in range(count)]
            if plots and all(isinstance(p, dict) for p in plots):
                return plots
        except Exception:
            pass
    return [{"grade": 0, "crop": None, "seed": None, "plant_ts": 0, "mature_ts": 0,
             "base_time": 0, "yield": 0, "fert_time": 0.0, "fert_yield": 0.0, "fert": {}}
            for _ in range(count)]


def _fav_adjust(data, key, delta, reason, round2=True):
    """好感度增减：affection 服务可用时优先（delta 保留 2 位），否则直接改共享 user 记录"""
    if round2:
        delta = round(delta, 2)
    svc = _CORE.service("affection")
    fn = getattr(svc, "add", None) if svc is not None else None
    if callable(fn):
        try:
            fn(key, delta, reason)
            return
        except Exception as e:
            logger.warning(f"[bank_loan] affection.add 调用失败，降级直改共享数据: {e}")
    user = _CORE.ensure_user(key)
    user["favorability"] = round(max(0.0, float(user.get("favorability", 0.0)) + delta), 2)


def _force_unlock(data, key):
    """强制解锁：赠送农场（2 块地）和宠物"""
    if key not in data.get("farms", {}):
        farm = _ensure_farm(data, key)
        for _ in range(int(_CORE.param("FARM_FREE_PLOTS", FARM_FREE_PLOTS))):
            farm["plots"].append(_new_plots(1)[0])
    if key not in data.get("pets", {}):
        today = date.today().isoformat()
        data.setdefault("pets", {})[key] = {
            "name": "宠物", "level": 1, "exp": 0.0,
            "satiety": 100.0, "thirst": 100.0, "stamina": 100.0, "health": 120.0, "mood": 80.0,
            "last_settle_date": today, "last_settle": None,
            "inventory": {}, "money_event_date": today, "money_event_count": 0,
        }


def _warehouse_value(farm) -> int:
    """仓库总价值（作物按作物价、种子按种子卖价、化肥按买价）"""
    wh = farm.get("warehouse", {})
    items = _CORE.items()
    crops = items.get("crops") or []
    ferts = items.get("ferts") or []
    total = 0
    for nm, cnt in list(wh.get("crops", {}).items()):
        c = _find_item(crops, nm)
        total += int(round(int(cnt) * (_CORE.f(c["crop_price"]) if c else 0.0)))
    for nm, cnt in list(wh.get("seeds", {}).items()):
        c = _find_item(crops, nm)
        total += int(round(int(cnt) * (_CORE.f(c["seed_sell_price"]) if c else 0.0)))
    for nm, cnt in list(wh.get("fertilizers", {}).items()):
        f = _find_item(ferts, nm)
        total += int(round(float(cnt) * (int(f["price"]) if f else 0)))
    return total


@staticmethod
def _find_item(items, name):
    return next((x for x in items if x["name"] == name), None)


def _special_overdue(data, key):
    """特别贷款逾期：重锁农场/宠物，收取仓库总价值 10%（清除数据）"""
    farm = _farm_of(data, key)
    if farm:
        total = _warehouse_value(farm)
        take = int(total * 0.1)
        if take > 0:
            _repay_loans(data, key, take)
        farm["warehouse"] = {"crops": {}, "seeds": {}, "fertilizers": {}}
    data.get("pets", {}).pop(key, None)
    data.get("farms", {}).pop(key, None)


# ================= 逾期同步与还款 =================
def _loan_sync(data, key, now_ts=None):
    """标记逾期 + 逾期处置（特别贷款重锁、逾期记录、年度计数、30 天农场回退、禁用）"""
    now_ts = now_ts or datetime.now().timestamp()
    rec = _ensure_loans(data, key)
    # 2.2.3：欠款总额（含利息）在 0~1 之间的尾数欠款永远还不清，直接免除全部账单
    owed_all = sum(_loan_owed(l, now_ts) for l in rec.get("loans", []) if l.get("remaining", 0) > 0)
    if 0 < owed_all < 1:
        rec["loans"] = []
    changed = False
    year = str(date.today().year)
    if rec.get("overdue_year_key") != year:
        rec["overdue_year_key"] = year
        rec["overdue_year"] = 0
        rec["ban"] = False  # 跨年后解除临时禁用
    for loan in rec.get("loans", []):
        if loan.get("remaining", 0) <= 0:
            continue
        if now_ts > loan.get("due_ts", 0):
            if not loan.get("overdue"):
                loan["overdue"] = True
                changed = True
                rec["overdue_year"] = int(rec.get("overdue_year", 0)) + 1
                rec["overdue_records"].append({
                    "amount": loan.get("remaining", 0),
                    "package": loan.get("package", 0),
                    "time": datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M"),
                })
                if loan.get("special"):
                    _special_overdue(data, key)
            if now_ts - loan.get("due_ts", 0) > _CORE.param("LOAN_FARM_ROLLBACK_DAYS", LOAN_FARM_ROLLBACK_DAYS) * 86400:
                farm = _farm_of(data, key)
                if farm:
                    farm["level"] = 0
                    farm["exp"] = 0.0
                    farm["plots"] = _new_plots(int(_CORE.param("FARM_FREE_PLOTS", FARM_FREE_PLOTS)))
                    farm["warehouse"] = {"crops": {}, "seeds": {}, "fertilizers": {}}
    if rec.get("overdue_year", 0) >= _CORE.param("LOAN_OVERDUE_YEAR_LIMIT", LOAN_OVERDUE_YEAR_LIMIT):
        rec["ban"] = True
    return changed


def _debt_summary_of(data: dict, key: str) -> dict:
    """生效欠款统计（2.2.3）：账单数 + 含息总额（与 `_loan_sync` 的 owed_all 同口径，无账单 → 0 张 / 0）。
    WebUI 详情页直接取用本统计，不再自行汇总。"""
    rec = data.get("loans", {}).get(key) or {}
    now_ts = datetime.now().timestamp()
    bills = [l for l in (rec.get("loans") or [])
             if isinstance(l, dict) and l.get("remaining", 0) > 0]
    return {"count": len(bills),
            "total": round(sum(_loan_owed(l, now_ts) for l in bills), 2)}


def _repay_loans(data, key, amount, code=None):
    """还款，返回实际还款金额；优先还逾期最久 / 即将到期的账单"""
    rec = _ensure_loans(data, key)
    now_ts = datetime.now().timestamp()
    candidates = [l for l in rec.get("loans", []) if l.get("remaining", 0) > 0 and (code is None or l.get("package") == code)]
    if not candidates:
        return 0
    candidates.sort(key=lambda l: (0 if l.get("overdue") else 1, l.get("due_ts", 0)))
    remaining_money = amount
    repaid = 0
    for loan in candidates:
        if remaining_money <= 0:
            break
        owed = _loan_owed(loan, now_ts)
        take = min(remaining_money, owed)
        remaining_money -= take
        repaid += take
        loan["remaining"] = round(loan["remaining"] - take, 2)
        if loan["remaining"] <= 0:
            loan["remaining"] = 0
    rec["loans"] = [l for l in rec.get("loans", []) if l.get("remaining", 0) > 0]
    return round(repaid, 2)


# ================= 自动化贷款余额（2.2.7：user.auto_loan 单一余额，无上限无逾期无利息） =================
def _auto_loan_balance_of(data: dict, key: str) -> float:
    u = data.get("users", {}).get(key)
    if not isinstance(u, dict):
        return 0.0
    try:
        return round(float(u.get("auto_loan", 0) or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _auto_loan_repay(data: dict, key: str, amount) -> float:
    """偿还自动化贷款余额（还款指令 / 自动打工报酬）。返回实际偿还金额。"""
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


# ================= 每日处置（core.at 每日任务承载） =================
def _loan_daily_process(data, key, now=None):
    """每日逾期处置：好感度降低（由每日任务在 DAILY_SETTLE_HOUR 统一执行，替代原消息懒处理；
    23 点自动卖仓库/自动签到还款见 _loan_auto_repay_process）。
    幂等标记 daily_process_date 在成功执行后才写入（异常中断可在下一巡检重试）。"""
    now = now or datetime.now()
    rec = _ensure_loans(data, key)
    if not _has_overdue_now(rec, now.timestamp()):
        return False
    today = now.strftime("%Y-%m-%d")
    if rec.get("daily_process_date") == today:
        return False
    special = any(l.get("special") and l.get("remaining", 0) > 0 for l in rec.get("loans", []))
    lo, hi = (_CORE.param("LOAN_FAV_DROP_SPECIAL", LOAN_FAV_DROP_SPECIAL) if special
              else _CORE.param("LOAN_FAV_DROP_NORMAL", LOAN_FAV_DROP_NORMAL))
    drop = random.uniform(lo, hi)
    _fav_adjust(data, key, -drop, "贷款逾期")
    rec["daily_process_date"] = today
    return True


def _sell_warehouse_all(data, key, farm):
    """卖出仓库全部物品（化肥除外——只能买和使用，不可卖），返回所得金币（不进入余额，直接用于还款）"""
    wh = farm.get("warehouse", {})
    items = _CORE.items()
    crops = items.get("crops") or []
    total = 0
    for nm, cnt in list(wh.get("crops", {}).items()):
        c = _find_item(crops, nm)
        total += int(round(int(cnt) * (_CORE.f(c["crop_price"]) if c else 0.0)))
    for nm, cnt in list(wh.get("seeds", {}).items()):
        c = _find_item(crops, nm)
        total += int(round(int(cnt) * (_CORE.f(c["seed_sell_price"]) if c else 0.0)))
    # 化肥不可卖：保留在仓库
    farm["warehouse"] = {"crops": {}, "seeds": {}, "fertilizers": wh.get("fertilizers", {})}
    return total


def _auto_signin(data, key):
    """逾期自动签到：只发放金币与好感度（金币用于抵债），标记当日已签到"""
    today = date.today().isoformat()
    user = data.get("users", {}).get(key)
    if user and user.get("last_date") == today:
        return 0
    if user is None:
        user = _CORE.ensure_user(key)
    coins = random.randint(int(_CORE.param("MIN_COINS", MIN_COINS)), int(_CORE.param("MAX_COINS", MAX_COINS)))
    _fav_adjust(data, key, round(random.uniform(_CORE.param("MIN_FAV", MIN_FAV), _CORE.param("MAX_FAV", MAX_FAV)), 2),
                "逾期自动签到")
    user["last_date"] = today
    return coins


def _loan_auto_repay_process(data, key, now=None):
    """每日 LOAN_AUTO_TIME 自动还款：卖仓库全部 + 自动签到还款（由每日任务在到达 LOAN_AUTO_TIME 后
    统一执行，替代原消息懒处理）。
    幂等标记 daily_repay_date 在成功执行后才写入（异常中断可在下一巡检重试）。"""
    now = now or datetime.now()
    rec = _ensure_loans(data, key)
    if not _has_overdue_now(rec, now.timestamp()):
        return False
    today = now.strftime("%Y-%m-%d")
    if rec.get("daily_repay_date") == today:
        return False
    farm = _farm_of(data, key)
    if farm:
        coins = _sell_warehouse_all(data, key, farm)
        if coins > 0:
            _repay_loans(data, key, coins)
    coins = _auto_signin(data, key)
    if coins > 0:
        _repay_loans(data, key, coins)
    rec["daily_repay_date"] = today
    return True


# ================= 贷款指令 =================
def _loan_packages_info(data, key):
    """所有贷款套餐信息，用于「借款」无参数时的概览"""
    info = []
    special_amount = _CORE.param("LOAN_SPECIAL_AMOUNT", LOAN_SPECIAL_AMOUNT)
    special_rate = _CORE.param("LOAN_SPECIAL_RATE", LOAN_SPECIAL_RATE)
    info.append({
        "code": 0, "name": "特别贷款（强制解锁）",
        "max": f"{special_amount}（固定，不发放金币）",
        "rate": f"{special_rate}%/日",
        "note": "贷款 2500 用于强制解锁农场+宠物，不发放金币；已开通宠物/农场不可用；30 天内还清，逾期重锁并收取仓库价值 10%",
    })
    general = " / ".join([f"{lv}级:{amt}" for lv, amt in
                          [(0, 3000), (11, 10000), (26, 20000), (51, 40000), (76, 100000)]])
    info.append({
        "code": 1, "name": "一般贷款",
        "max": f"按农场等级（农场{general}）",
        "rate": "2%~5%/日随机（宠物等级减免）",
        "note": "最近 4:00 后开始计息，15 天逾期",
    })
    short = " / ".join([f"{lv}级:{amt}" for lv, amt in
                        [(0, 1000), (3, 2000), (6, 3000), (9, 4000), (10, 6000)]])
    info.append({
        "code": 2, "name": "短期贷款",
        "max": f"按好感度等级（好感{short}）",
        "rate": "6%/日",
        "note": "10 天免息期，之后每日 6%",
    })
    for pkg in _load_loan_packages():
        reqs = []
        if pkg.get("fav_req"):
            reqs.append(f"好感Lv.{pkg['fav_req']}+")
        if pkg.get("pet_req"):
            reqs.append(f"宠物Lv.{pkg['pet_req']}+")
        if pkg.get("farm_req"):
            reqs.append(f"农场Lv.{pkg['farm_req']}+")
        req_str = "，".join(reqs) if reqs else "无要求"
        info.append({
            "code": pkg["code"], "name": "自定义贷款",
            "max": f"{pkg['max_amount']}",
            "rate": f"{pkg['rate']}%/日",
            "note": f"要求：{req_str}",
        })
    return info


def _bank_unlock_time():
    """下一个 4:00（与银行存款解锁时刻一致；凌晨 0-4 点借款在当天 4:00 计息，其余在次日 4:00）"""
    from datetime import time, timedelta
    now = datetime.now()
    today_4am = datetime.combine(now.date(), time(4, 0))
    if now < today_4am:
        return today_4am
    return datetime.combine(now.date() + timedelta(days=1), time(4, 0))


def register(core):
    global _CORE
    _CORE = core

    # ================= 联动事件：获得金币时逾期自动划扣还款（coins.pre_add） =================
    def on_coins_pre_add(payload):
        """payload 为可变 dict {"key", "amount", "reason", "skip_repay"}：原地修改 payload["amount"]。
        与 2.3.0 _add_coins 内联逻辑等价：amount>0 且未跳过还款且有逾期账单时，
        划扣 int(amount × LOAN_COIN_DEDUCT) 优先偿还逾期最久的账单，扣减后金额再入账。"""
        key = payload.get("key")
        amount = payload.get("amount", 0)
        if not isinstance(amount, (int, float)) or amount <= 0 or payload.get("skip_repay"):
            return
        rec = (core.data.get("loans") or {}).get(key)
        if not rec:
            return
        if _has_overdue_now(rec, datetime.now().timestamp()):
            take = int(amount * float(core.param("LOAN_COIN_DEDUCT", LOAN_COIN_DEDUCT)))
            if take > 0:
                repaid = _repay_loans(core.data, key, take)
                payload["amount"] -= int(repaid)

    core.on("coins.pre_add", on_coins_pre_add)

    # ================= 每日任务：逾期标记 + 每日好感度处置（DAILY_SETTLE_HOUR） =================
    def loan_daily_job(data, now):
        ok = True
        for key in list((data.get("loans") or {}).keys()):
            if not isinstance(data["loans"].get(key), dict):
                continue
            try:
                _loan_sync(data, key)
                _loan_daily_process(data, key, now)
            except Exception as e:
                ok = False
                logger.error(f"[bank_loan] 贷款每日处置异常 uid={key}: {e}")
        core.save()
        return ok

    daily_hour = int(core.param("DAILY_SETTLE_HOUR", DAILY_SETTLE_HOUR) or 0)
    core.at(daily_hour, "loan_daily", loan_daily_job)

    # ================= 每日任务：逾期自动还款（LOAN_AUTO_TIME，默认 23 点） =================
    def loan_repay_job(data, now):
        ok = True
        today = now.strftime("%Y-%m-%d")
        for key in list((data.get("loans") or {}).keys()):
            rec = data["loans"].get(key)
            if not isinstance(rec, dict) or rec.get("daily_repay_date") == today:
                continue
            try:
                _loan_auto_repay_process(data, key, now)
            except Exception as e:
                ok = False
                logger.error(f"[bank_loan] 贷款自动还款异常 uid={key}: {e}")
        core.save()
        return ok

    la = core.param("LOAN_AUTO_TIME", LOAN_AUTO_TIME)
    try:
        la_h = int(la[0]) if isinstance(la, (tuple, list)) and len(la) >= 1 else 23
    except (TypeError, ValueError):
        la_h = 23
    core.at(la_h, "loan_repay", loan_repay_job)

    def _loan_packages_text(data, key):
        """「借款」无参数时的纯文本套餐一览（图片渲染模块缺失时的回退，2.3.0 同款文案）"""
        lines = ["借款（贷款套餐一览）：", "格式：借款 <套餐代码> <金额>", "每日累计贷款上限 = 2 × 套餐上限"]
        for pkg in _loan_packages_info(data, key):
            lines.append(f"[套餐 {pkg['code']} - {pkg['name']}]")
            lines.append(f"最大可借：{pkg['max']}")
            lines.append(f"日利率：{pkg['rate']}")
            if pkg.get("note"):
                lines.append(f"说明：{pkg['note']}")
        return "\n".join(lines)

    # ================= 借款 =================
    def handle_loan_borrow(event):
        name = core.user_name(event)
        key = core.user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            # 无参数：展示所有贷款套餐一览（图片；渲染模块缺失时回退文本一览）
            data = core.data
            render = getattr(core.image, "render_loan_packages", None)
            if render is not None:
                return render(data, key)
            return _loan_packages_text(data, key)
        args = parts[1].split()
        if len(args) < 2:
            return "格式：借款 <套餐代码> <金额>（发送「借款」查看套餐一览）"
        try:
            code = int(args[0])
            amount = int(args[1])
        except ValueError:
            return "套餐代码和金额必须是整数。格式：借款 <套餐代码> <金额>"
        # （2.2.7：自动化贷款为 auto_loan 余额制，仅自动照顾可贷；任何套餐代码都无法借出自动化贷款）
        if code not in (0, 1, 2) and not (3 <= code <= 10):
            return "套餐代码无效（0=特别，1=一般，2=短期，3~10=自定义）。"
        if amount <= 0:
            return "金额必须为正整数。"

        data = core.data
        _loan_sync(data, key)
        rec = _ensure_loans(data, key)
        if rec.get("ban"):
            return f"{name} 已因年度逾期超限被禁用贷款功能。"
        if code == 0:
            if key in data.get("pets", {}) or key in data.get("farms", {}):
                return "你已经开通了宠物或农场，不能使用强制解锁（特别贷款）。"
            if amount != _CORE.param("LOAN_SPECIAL_AMOUNT", LOAN_SPECIAL_AMOUNT):
                return f"特别贷款固定金额为 {_CORE.param('LOAN_SPECIAL_AMOUNT', LOAN_SPECIAL_AMOUNT)} 金币。"
            if any(l.get("remaining", 0) > 0 for l in rec.get("loans", [])):
                return "你有未结清的贷款（含特别贷款），还清前不能再次申请。"
        else:
            if not _loan_unlocked(data, key):
                return "贷款功能需要先解锁宠物系统或农场（发送「解锁宠物」或「解锁农场」）。"
            if _has_overdue_now(rec, datetime.now().timestamp()):
                return "你有逾期贷款，还清前不能新增贷款。"
        if any(l.get("special") and l.get("remaining", 0) > 0 for l in rec.get("loans", [])):
            return "你有未结清的特别贷款，还清前不能再次申请任何贷款。"

        pkg = _loan_package(data, key, code)
        if pkg is None:
            return "该套餐未配置。"
        if amount > pkg["max_amount"]:
            return f"该套餐最大可借 {pkg['max_amount']} 金币。"
        user = data.get("users", {}).get(key, {})
        fav_level = core.level_of(float(user.get("favorability", 0.0)))
        if fav_level < pkg.get("fav_req", 0):
            return f"好感度等级不足（需要 Lv.{pkg['fav_req']}）。"
        if _pet_level(data, key) < pkg.get("pet_req", 0):
            return f"宠物等级不足（需要 Lv.{pkg['pet_req']}）。"
        farm = data.get("farms", {}).get(key)
        if (int(farm.get("level", 0)) if farm else 0) < pkg.get("farm_req", 0):
            return f"农场等级不足（需要 Lv.{pkg['farm_req']}）。"

        today = date.today().isoformat()
        if rec.get("daily_date") != today:
            rec["daily_date"] = today
            rec["daily_borrowed"] = 0
        daily_mult = float(core.param("LOAN_DAILY_MULT", LOAN_DAILY_MULT))
        if rec.get("daily_borrowed", 0) + amount > int(pkg["max_amount"] * daily_mult):
            return f"今日累计贷款已达上限（{int(pkg['max_amount'] * daily_mult)}），请明天再申请。"

        now = datetime.now()
        now_ts = now.timestamp()
        special_days = _CORE.param("LOAN_SPECIAL_DAYS", LOAN_SPECIAL_DAYS)
        general_days = _CORE.param("LOAN_GENERAL_OVERDUE_DAYS", LOAN_GENERAL_OVERDUE_DAYS)
        grace_days = _CORE.param("LOAN_SHORT_GRACE_DAYS", LOAN_SHORT_GRACE_DAYS)
        if code == 0:
            _force_unlock(data, key)
            free_until = now_ts
            due = now_ts + special_days * 86400
            rate = _CORE.param("LOAN_SPECIAL_RATE", LOAN_SPECIAL_RATE)
            special = True
        elif code == 1:
            free_until = _bank_unlock_time().timestamp()
            due = now_ts + general_days * 86400
            rate = pkg["rate"]
            special = False
        elif code == 2:
            free_until = now_ts + grace_days * 86400
            due = now_ts + grace_days * 86400
            rate = _CORE.param("LOAN_SHORT_RATE", LOAN_SHORT_RATE)
            special = False
        else:
            free_until = _bank_unlock_time().timestamp()
            due = now_ts + general_days * 86400
            rate = pkg["rate"]
            special = False
        rec["loans"].append({
            "package": code, "amount": amount, "rate": rate,
            "borrow_ts": now_ts, "free_until_ts": free_until, "due_ts": due,
            "remaining": amount, "overdue": False, "special": special,
        })
        rec["daily_borrowed"] = int(rec.get("daily_borrowed", 0)) + amount
        if code != 0:
            # 只有普通/短期/自定义套餐才发放现金；特别贷款（0）的 2500 是解锁服务费，不发放金币
            core.add_coins(key, amount, f"贷款·套餐{code}")
        core.save()
        if code == 0:
            extra = "\n🔓 已强制解锁农场与宠物系统（产生 2500 金币贷款，日息 1%，30 天内还清，未发放金币）"
        else:
            extra = ""
        return (f"🏦 {name} 借款成功！\n"
                f"💳 套餐 {code}，金额 {amount} 金币\n"
                f"📈 日利率：{rate}%\n"
                f"⏰ 免息至 {datetime.fromtimestamp(free_until).strftime('%m-%d %H:%M')}，逾期日 {datetime.fromtimestamp(due).strftime('%m-%d %H:%M')}{extra}\n"
                f"{core.coin_line(key)}")

    def special_amount_default():
        return _CORE.param("LOAN_SPECIAL_AMOUNT", LOAN_SPECIAL_AMOUNT)

    # ================= 还款 =================
    def handle_loan_repay(event):
        name = core.user_name(event)
        key = core.user_key(event)
        parts = event.message_str.split(maxsplit=1)
        data = core.data
        _loan_sync(data, key)
        rec = _ensure_loans(data, key)
        auto_bal = _auto_loan_balance_of(data, key)
        if not rec.get("loans") and auto_bal <= 0:
            return f"{name} 名下没有贷款。"
        now_ts = datetime.now().timestamp()
        coins = core.coins_of(key)

        if len(parts) < 2:
            # 还所有贷款：先还自动化贷款余额，再还普通贷款（优先还逾期最久/即将到期）
            if coins <= 0:
                return f"{name} 金币余额为 0，无法还款。"
            repaid_total = 0.0
            if auto_bal > 0:
                pay = min(coins, auto_bal)
                got = _auto_loan_repay(data, key, pay)
                if got > 0:
                    core.add_coins(key, -int(got), "偿还自动化贷款")
                    repaid_total += got
                coins = core.coins_of(key)
            if coins > 0 and rec.get("loans"):
                repaid = _repay_loans(data, key, coins)
                if repaid > 0:
                    core.add_coins(key, -int(repaid), "偿还贷款")
                    repaid_total += repaid
            total = _auto_loan_balance_of(data, key) \
                + sum(_loan_owed(l, now_ts) for l in rec.get("loans", []))
            core.save()
            return (f"🏦 已用全部金币还款 {round(repaid_total, 2)}，剩余待还 {round(total, 2)}。\n"
                    f"{core.coin_line(key)}")
        args = parts[1].split()
        if args[0].lower() == "auto":
            # 只还自动化贷款：还款 auto [金额]（不填金额 = 还清全部余额）
            amount = auto_bal
            if len(args) >= 2:
                try:
                    amount = min(float(int(args[1])), auto_bal)
                except ValueError:
                    return "金额必须是整数。格式：还款 auto [金额]"
            if amount <= 0:
                return f"{name} 没有未还清的自动化贷款。"
            if coins <= 0:
                return f"{name} 金币余额为 0，无法还款。"
            got = _auto_loan_repay(data, key, min(amount, coins))
            if got > 0:
                core.add_coins(key, -int(got), "偿还自动化贷款")
            core.save()
            return (f"🏦 已偿还自动化贷款 {round(got, 2)} 金币，剩余 {_auto_loan_balance_of(data, key):.2f}。\n"
                    f"{core.coin_line(key)}")
        try:
            code = int(args[0])
        except ValueError:
            return "套餐代码必须是整数。"
        targets = [l for l in rec.get("loans", []) if l.get("package") == code and l.get("remaining", 0) > 0]
        if not targets:
            return f"没有套餐 {code} 的未结清贷款。"
        if len(args) >= 2:
            try:
                amount = int(args[1])
            except ValueError:
                return "金额必须是整数。"
            if amount <= 0:
                return "金额必须为正整数。"
        else:
            amount = int(sum(_loan_owed(l, now_ts) for l in targets))
        if coins <= 0:
            return f"{name} 金币余额为 0。"
        amount = min(amount, coins)
        repaid = _repay_loans(data, key, amount, code)
        if repaid > 0:
            core.add_coins(key, -int(repaid), f"偿还贷款·套餐{code}")
        core.save()
        return (f"🏦 已对套餐 {code} 还款 {round(repaid, 2)} 金币。\n"
                f"{core.coin_line(key)}")

    # ================= 我的贷款 / 我的征信 =================
    def handle_my_loans(event):
        name = core.user_name(event)
        key = core.user_key(event)
        data = core.data
        # 2.2.0：逾期标记由每日任务统一执行，查询只显示已结算结果
        rec = _ensure_loans(data, key)
        lines = [f"🏦 {name} 的贷款账单："]
        auto_bal = _auto_loan_balance_of(data, key)
        if auto_bal > 0:
            lines.append(f"· 自动化贷款｜欠款 {auto_bal}｜无上限无逾期（仅自动照顾可贷；「还款」指令或自动打工报酬偿还）")
        loans = rec.get("loans", [])
        if not loans:
            if auto_bal <= 0:
                lines.append("暂无生效中的贷款。")
        now_ts = datetime.now().timestamp()
        for l in loans:
            days = max(0, int((now_ts - max(l.get("free_until_ts", l.get("borrow_ts", 0)), l.get("borrow_ts", 0))) // 86400))
            owed = _loan_owed(l, now_ts)
            # 2.2.0：逾期指示按到期时间只读计算（结算/记录仍由每日任务统一处理）
            st = "⚠️逾期" if _loan_is_overdue(l, now_ts) else "✅正常"
            pkg_txt = "自动化贷款" if l.get("auto") else f"套餐{l['package']}"
            lines.append(f"· {pkg_txt}｜借款 {l['amount']}｜利率 {l['rate']}%/日｜剩余 {l['remaining']}｜计息 {days} 天｜欠款 {owed}｜{st}")
        return "\n".join(lines)

    def handle_my_credit(event):
        name = core.user_name(event)
        key = core.user_key(event)
        data = core.data
        # 2.2.0：逾期标记由每日任务统一执行，查询只显示已结算结果
        rec = _ensure_loans(data, key)
        lines = [f"🏦 {name} 的征信报告："]
        overdue = rec.get("overdue_records", [])
        if overdue:
            for r in overdue[-10:]:
                lines.append(f"· 逾期 {r['amount']} 金币（套餐 {r['package']}，{r['time']}）")
        else:
            lines.append("· 暂无逾期记录")
        lines.append("---")
        loans = rec.get("loans", [])
        if not loans:
            lines.append("暂无生效中的贷款账单。")
        else:
            now_ts = datetime.now().timestamp()
            for l in loans:
                owed = _loan_owed(l, now_ts)
                due = datetime.fromtimestamp(l["due_ts"]).strftime("%m-%d")
                # 2.2.0：逾期指示按到期时间只读计算（结算/记录仍由每日任务统一处理）
                st = "⚠️逾期" if _loan_is_overdue(l, now_ts) else "✅"
                pkg_txt = "自动化贷款" if l.get("auto") else f"套餐{l['package']}"
                lines.append(f"· {pkg_txt}｜借款 {l['amount']}｜利率 {l['rate']}%｜欠款 {owed}｜逾期日 {due}｜{st}")
        if rec.get("ban"):
            lines.append("🚫 已因年度逾期超限被禁用贷款功能")
        return "\n".join(lines)

    core.command("借款", feature="loan")(handle_loan_borrow)
    core.command("还款", feature="loan")(handle_loan_repay)
    core.command("我的贷款", feature="loan")(handle_my_loans)
    core.command("我的征信", feature="loan")(handle_my_credit)

    core.add_help("银行贷款", [
        ("借款 <套餐> <金额>", "贷款（0=特别 / 1=一般 / 2=短期 / 3~10=自定义）"),
        ("还款 <套餐> [金额]", "还款（不填套餐=还全部）"),
        ("我的贷款 / 我的征信", "查看贷款账单 / 征信"),
    ])

    # ================= 服务暴露（金币入账划扣 / WebUI / 其他插件查询用） =================
    class BankLoanApi:
        """贷款服务：逾期判定与还款（core 金币入账联动 / 自动打工报酬偿还等复用）"""

        def has_overdue(self, key, ts=None):
            """用户当前是否有逾期账单（ts 缺省取当前时间）"""
            rec = (core.data.get("loans") or {}).get(key)
            if not isinstance(rec, dict):
                return False
            return _has_overdue_now(rec, datetime.now().timestamp() if ts is None else ts)

        def repay_slice(self, key, amount):
            """按 LOAN_COIN_DEDUCT 比例从 amount 划扣还款（不改余额），返回实际还款金额"""
            take = int(float(amount or 0) * float(core.param("LOAN_COIN_DEDUCT", LOAN_COIN_DEDUCT)))
            if take <= 0:
                return 0.0
            return _repay_loans(core.data, key, take)

        def repay_loans(self, key, amount):
            """直接还款指定金额（不改余额），返回实际还款金额"""
            return _repay_loans(core.data, key, float(amount or 0))

        def debt_summary(self, key):
            """生效欠款统计（账单数 + 含息总额）"""
            return _debt_summary_of(core.data, key)

    core.expose("bank_loan", BankLoanApi())
