# -*- coding: utf-8 -*-
"""银行存款（3.0.0 功能插件）。自 2.3.0 modules/bank.py 银行部分原样迁移：
存款 / 取款 / 银行统计；存单数据存于 data["bank"]（结构不变）。
利率 = 基础利率（好感度等级）+ 加成利率（宠物等级），额度 = 好感度×100 + 宠物经验。
到期结算（解锁存单 + 发放利息）由 core.at(BANK_SETTLE_HOUR, "bank_settle", ...) 每日任务统一执行，
前台只读已结算结果（2.2.0 口径）。好感度/宠物数据经 core.service 或共享 data 读取。"""
import math
import random
from datetime import datetime, time, timedelta

from astrbot.api import logger

from ..core import BANK_SETTLE_HOUR

NAME = "bank_deposit"

_CORE = None  # register(core) 时注入的核心框架实例


# ================= 银行内部计算（与 2.3.0 完全一致） =================
def _bank_base_rate(data: dict, key: str) -> float:
    """基础利率（%），由好感度等级决定"""
    user = data.get("users", {}).get(key, {})
    fav = user.get("favorability")
    if not isinstance(fav, (int, float)):
        fav = 0.0
    lv = _CORE.level_of(float(fav))
    if lv <= 3:
        lo, hi = 0.02, 0.10
    elif lv <= 5:
        lo, hi = 0.02, 0.15
    elif lv <= 8:
        lo, hi = 0.05, 0.15
    else:
        lo, hi = 0.05, 0.18
    return round(random.uniform(lo, hi), 2)


def _bank_bonus_rate(data: dict, key: str) -> float:
    """利率加成（%），由宠物等级决定；无宠物为 0"""
    pet = data.get("pets", {}).get(key)
    if not pet:
        return 0.0
    lv = int(pet.get("level", 1))
    if lv <= 10:
        lo, hi = 0.00, 0.01
    elif lv <= 40:
        lo, hi = 0.00, 0.02
    elif lv <= 80:
        lo, hi = 0.01, 0.02
    else:
        lo, hi = 0.01, 0.03
    return round(random.uniform(lo, hi), 2)


def _bank_unlock_time():
    """下一个 4:00（凌晨 0-4 点存款则在当天 4:00 解锁，其余在次日 4:00）"""
    now = datetime.now()
    today_4am = datetime.combine(now.date(), time(4, 0))
    if now < today_4am:
        return today_4am
    return datetime.combine(now.date() + timedelta(days=1), time(4, 0))


def _bank_hours() -> int:
    """到下一个 4:00 的小时数（向上取整）"""
    unlock = _bank_unlock_time()
    sec = (unlock - datetime.now()).total_seconds()
    return int(math.ceil(sec / 3600))


def _max_bank_storage(data: dict, key: str) -> int:
    """最大存储额度 = 好感度×100 + 宠物经验"""
    user = data.get("users", {}).get(key, {})
    fav = user.get("favorability")
    if not isinstance(fav, (int, float)):
        fav = 0.0
    pet = data.get("pets", {}).get(key)
    pet_exp = 0.0
    if pet:
        pe = pet.get("exp")
        pet_exp = float(pe) if isinstance(pe, (int, float)) else 0.0
    return int(float(fav) * 100 + pet_exp)


def _bank_settle(data: dict, key: str):
    """结算：到期的存单解锁、利息自动入账。返回 (解锁笔数, 已发放利息)"""
    bank = data.get("bank", {}).get(key)
    if not bank:
        return 0, 0
    ts = datetime.now().timestamp()
    count = 0
    paid = 0
    for d in bank.get("deposits", []):
        uts = d.get("unlock_ts")
        if d.get("status") == "locked" and isinstance(uts, (int, float)) and ts >= uts:
            d["status"] = "matured"
            itr = d.get("interest")
            pay = int(itr) if isinstance(itr, (int, float)) else 0
            paid += pay
            if pay > 0:
                _CORE.add_coins(key, pay, "银行利息")
            ti = bank.get("total_interest")
            if not isinstance(ti, (int, float)):
                ti = 0.0
            bank["total_interest"] = round(float(ti) + float(itr or 0), 2)
            count += 1
    return count, paid


def _dep_amount(d) -> int:
    """安全读取存单本金，防御异常/空数据"""
    v = d.get("amount") if isinstance(d, dict) else None
    return int(v) if isinstance(v, (int, float)) else 0


def _bank_summary_of(data, key):
    """银行累计统计（与「银行统计」指令同口径：只读已结算结果，到期结算由每日任务统一执行）：
    生效存单数 / 锁定·可取笔数与本金 / 银行内本金合计 / 预计利息合计（各存单利息之和）。
    WebUI 详情与用户卡片直接取用本统计，不再自行汇总；无银行数据返回 None。"""
    bank = data.get("bank", {}).get(key)
    if not isinstance(bank, dict):
        return None
    deposits = [d for d in (bank.get("deposits") or []) if isinstance(d, dict)]
    locked = [d for d in deposits if d.get("status") == "locked"]
    matured = [d for d in deposits if d.get("status") == "matured"]
    locked_sum = sum(_dep_amount(d) for d in locked)
    matured_sum = sum(_dep_amount(d) for d in matured)
    return {
        "total_count": len(deposits),
        "locked_count": len(locked),
        "matured_count": len(matured),
        "locked_sum": locked_sum,
        "matured_sum": matured_sum,
        "total": locked_sum + matured_sum,
        "interest_sum": sum(int(d.get("interest", 0) or 0) for d in deposits),
    }


# ================= 每日结算任务（core.at 承载，fn(data, now) → bool） =================
def _bank_settle_job(data: dict, now) -> bool:
    """银行存款结算（默认 4 点，BANK_SETTLE_HOUR 可配）：解锁全部到期存单并发放利息。
    单个用户异常只记日志不阻断其他用户，但当天返回 False 由下一巡检自动重试
    （_bank_settle 幂等：已 matured 的存单不会重复发放利息）。"""
    ok = True
    for key in list((data.get("bank") or {}).keys()):
        try:
            _bank_settle(data, key)
        except Exception as e:
            ok = False
            logger.error(f"[bank_deposit] 银行存款结算异常 uid={key}: {e}")
    core_save = _CORE.save if _CORE is not None else None
    if core_save is not None:
        core_save()
    return ok


def register(core):
    global _CORE
    _CORE = core

    # 每日结算：hour 经运行参数（默认 core.BANK_SETTLE_HOUR = 4）
    settle_hour = int(core.param("BANK_SETTLE_HOUR", BANK_SETTLE_HOUR) or 4)
    core.at(settle_hour, "bank_settle", _bank_settle_job)

    # ================= 存款 =================
    def handle_bank_deposit(event):
        name = core.user_name(event)
        key = core.user_key(event)
        parts = event.message_str.split(maxsplit=1)

        data = core.data
        # 2.2.0：存单到期结算由每日任务（BANK_SETTLE_HOUR）统一执行，此处不做懒结算

        max_store = _max_bank_storage(data, key)
        if max_store <= 0:
            return "你的最大存储额度为 0（额度 = 好感度×100 + 宠物经验），先通过签到提升好感度 / 宠物经验吧。"

        bank = data.setdefault("bank", {}).setdefault(key, {"deposits": [], "total_deposits": 0, "total_interest": 0})
        stored = sum(_dep_amount(d) for d in bank.get("deposits", []))
        coins = core.coins_of(key)
        auto = False

        if len(parts) < 2:
            # 未指定金额：默认存入还能存入的最大金额
            auto = True
            amount = min(max_store - stored, coins)
            if amount <= 0:
                if stored >= max_store:
                    return f"存款失败：存储额度已满（已存 {stored}/{max_store}），先提升好感度 / 宠物经验吧。"
                return f"存款失败：金币余额为 0，无法存款。"
        else:
            try:
                amount = int(parts[1].strip())
            except ValueError:
                return "金额必须是整数。格式：存款 <金额>（不填则存入最大可存金额）"
            if amount <= 0:
                return "存款金额必须为正整数。"
            if stored + amount > max_store:
                return f"存款失败：超出最大存储额度（已存 {stored}，额度 {max_store} = 好感度×100 + 宠物经验）。"
            if coins < amount:
                return f"金币不足（当前 {coins}，需要 {amount}）。"

        base_rate = _bank_base_rate(data, key)
        bonus_rate = _bank_bonus_rate(data, key)
        hours = _bank_hours()
        interest = int(round(amount * (base_rate + bonus_rate) / 100.0 * hours))

        now = datetime.now()
        unlock = _bank_unlock_time()

        core.add_coins(key, -amount, "银行存款")
        bank["deposits"].append({
            "amount": amount,
            "base_rate": base_rate,
            "bonus_rate": bonus_rate,
            "hours": hours,
            "interest": interest,
            "deposit_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "unlock_ts": unlock.timestamp(),
            "status": "locked",
        })
        td = bank.get("total_deposits")
        bank["total_deposits"] = (int(td) if isinstance(td, (int, float)) else 0) + 1
        core.save()

        head = f"🏦 {name} 存款成功！"
        if auto:
            head = f"🏦 {name} 存款成功（未指定金额，已自动存入最大可存金额）！"
        return (head + "\n"
                f"💰 本金：{amount}（已锁定）\n"
                f"📈 利率：{base_rate:.2f}% + {bonus_rate:.2f}% = {base_rate + bonus_rate:.2f}%/小时\n"
                f"⏰ 计息 {hours} 小时，下一个 4:00 解锁\n"
                f"🧮 预计利息：{interest} 金币\n"
                f"🔓 解锁后利息自动入账，本金发送「取款」取出。")

    # ================= 取款 =================
    def handle_bank_withdraw(event):
        name = core.user_name(event)
        key = core.user_key(event)
        parts = event.message_str.split(maxsplit=1)

        data = core.data
        # 2.2.0：存单到期结算由每日任务统一执行，取款只读取已结算的成熟存单

        bank = data.get("bank", {}).get(key)
        if not bank or not bank.get("deposits"):
            return f"{name} 银行里还没有存款，发送「存款 <金额>」存钱吧。"

        matured_sum = sum(_dep_amount(d) for d in bank["deposits"] if d.get("status") == "matured")
        locked_cnt = sum(1 for d in bank["deposits"] if d.get("status") == "locked")
        if matured_sum <= 0:
            return f"{name} 还没有已解锁的本金（锁定中的存单 {locked_cnt} 笔，4:00 解锁）。"

        if len(parts) < 2:
            withdraw = matured_sum
        else:
            try:
                withdraw = int(parts[1].strip())
            except ValueError:
                return "金额必须是整数。格式：取款 <金额>（不填则全部取出）"
            if withdraw <= 0:
                return "取款金额必须为正整数。"
            if withdraw > matured_sum:
                return f"已解锁的本金只有 {matured_sum}，无法取出 {withdraw}。"

        remaining = withdraw
        new_deposits = []
        for d in bank["deposits"]:
            if d.get("status") == "matured" and remaining > 0:
                amt = _dep_amount(d)
                take = min(amt, remaining)
                remaining -= take
                if take < amt:
                    d["amount"] = amt - take
                    new_deposits.append(d)
            else:
                new_deposits.append(d)
        bank["deposits"] = new_deposits

        core.add_coins(key, withdraw, "银行取款")
        core.save()
        return (f"🏦 {name} 取款成功：取出本金 {withdraw} 金币（已解锁本金剩余 {matured_sum - withdraw}）。\n"
                f"{core.coin_line(key)}")

    # ================= 银行统计 =================
    def handle_bank_stats(event):
        name = core.user_name(event)
        key = core.user_key(event)
        data = core.data
        # 2.2.0：存单到期结算由每日任务统一执行，统计只读取已结算结果

        max_store = _max_bank_storage(data, key)
        bank = data.get("bank", {}).get(key, {})
        deposits = bank.get("deposits", [])
        locked = [d for d in deposits if d.get("status") == "locked"]
        matured = [d for d in deposits if d.get("status") == "matured"]
        locked_sum = sum(_dep_amount(d) for d in locked)
        matured_sum = sum(_dep_amount(d) for d in matured)
        td = bank.get("total_deposits")
        ti = bank.get("total_interest")
        td = int(td) if isinstance(td, (int, float)) else 0
        ti = float(ti) if isinstance(ti, (int, float)) else 0.0

        lines = [
            f"🏦 {name} 的银行统计：",
            f"📊 累计存款次数：{td}",
            f"💳 生效中存单：{len(deposits)} 笔（锁定 {len(locked)} / 可取 {len(matured)}）",
            f"💰 银行内本金：{locked_sum + matured_sum}（锁定 {locked_sum} / 可取 {matured_sum}）",
            f"🧮 已获取利息总额：{ti:.2f} 金币",
            f"📈 最大存储额度：{max_store}（= 好感度×100 + 宠物经验）",
        ]
        if deposits:
            lines.append("📜 存单明细：")
            for d in deposits:
                st = "🔒" if d.get("status") == "locked" else "✅"
                lines.append(f"· {st} 本金 {_dep_amount(d)}｜利率 {d.get('base_rate', 0):.2f}%+{d.get('bonus_rate', 0):.2f}%×{d.get('hours', 0)}h｜利息 {d.get('interest', 0)}")
        return "\n".join(lines)

    core.command("存款", feature="bank")(handle_bank_deposit)
    core.command("取款", feature="bank")(handle_bank_withdraw)
    core.command("银行统计", feature="bank")(handle_bank_stats)

    core.add_help("金币银行", [
        ("存款 <金额>", "存钱生息（不填=存最大可存金额）"),
        ("取款 <金额>", "取出本金（不填=全部）"),
        ("银行统计", "查看存款次数 / 存单 / 利息 / 额度"),
    ])

    # ================= 服务暴露（WebUI 详情 / 其他插件查询用） =================
    class BankDepositApi:
        """存款服务：到期结算与统计"""

        def settle(self, key):
            """结算某用户到期存单（解锁 + 发放利息），返回 (解锁笔数, 已发放利息)"""
            return _bank_settle(core.data, key)

        def summary(self, key):
            """银行累计统计（与「银行统计」指令同口径）；无银行数据返回 None"""
            return _bank_summary_of(core.data, key)

        def max_storage(self, key):
            """最大存储额度（好感度×100 + 宠物经验）"""
            return _max_bank_storage(core.data, key)

    core.expose("bank_deposit", BankDepositApi())
