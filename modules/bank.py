# -*- coding: utf-8 -*-
# 银行系统（存款/取款/统计/流水）。从原 main.py 的 SignInPlugin 拆出的 Mixin，由入口类组合继承。
from .base import *  # noqa: F401,F403  常量与共享工具
from .base import _register_runtime_module  # noqa: F401
import sys as _sys

_register_runtime_module(_sys.modules[__name__])


class BankMixin:
    def _bank_base_rate(self, data: dict, key: str) -> float:
        """基础利率（%），由签到好感度等级决定"""
        user = data.get("users", {}).get(key, {})
        fav = user.get("favorability")
        if not isinstance(fav, (int, float)):
            fav = 0.0
        lv = self._level_of(float(fav))
        if lv <= 3:
            lo, hi = 0.02, 0.10
        elif lv <= 5:
            lo, hi = 0.02, 0.15
        elif lv <= 8:
            lo, hi = 0.05, 0.15
        else:
            lo, hi = 0.05, 0.18
        return round(random.uniform(lo, hi), 2)

    def _bank_bonus_rate(self, data: dict, key: str) -> float:
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

    def _bank_unlock_time(self):
        """下一个 4:00（凌晨 0-4 点存款则在当天 4:00 解锁，其余在次日 4:00）"""
        now = datetime.now()
        today_4am = datetime.combine(now.date(), time(4, 0))
        if now < today_4am:
            return today_4am
        return datetime.combine(now.date() + timedelta(days=1), time(4, 0))

    def _bank_hours(self) -> int:
        """到下一个 4:00 的小时数（向上取整）"""
        unlock = self._bank_unlock_time()
        sec = (unlock - datetime.now()).total_seconds()
        return int(math.ceil(sec / 3600))

    def _max_bank_storage(self, data: dict, key: str) -> int:
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

    def _bank_settle(self, data: dict, key: str):
        """懒结算：到期的存单解锁、利息自动入账。返回 (解锁笔数, 已发放利息)"""
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
                    self._add_coins(data, key, pay, "银行利息")
                ti = bank.get("total_interest")
                if not isinstance(ti, (int, float)):
                    ti = 0.0
                bank["total_interest"] = round(float(ti) + float(itr or 0), 2)
                count += 1
        return count, paid

    @staticmethod
    def _dep_amount(d) -> int:
        """安全读取存单本金，防御异常/空数据"""
        v = d.get("amount") if isinstance(d, dict) else None
        return int(v) if isinstance(v, (int, float)) else 0

    def _bank_summary_of(self, data, key):
        """银行累计统计（2.2.3，与「银行统计」指令同口径：只读已结算结果，到期结算由固定结算循环统一执行）：
        生效存单数 / 锁定·可取笔数与本金 / 银行内本金合计 / 预计利息合计（各存单利息之和）。
        WebUI 详情与用户卡片直接取用本统计，不再自行汇总；无银行数据返回 None。"""
        bank = data.get("bank", {}).get(key)
        if not isinstance(bank, dict):
            return None
        deposits = [d for d in (bank.get("deposits") or []) if isinstance(d, dict)]
        locked = [d for d in deposits if d.get("status") == "locked"]
        matured = [d for d in deposits if d.get("status") == "matured"]
        locked_sum = sum(self._dep_amount(d) for d in locked)
        matured_sum = sum(self._dep_amount(d) for d in matured)
        return {
            "total_count": len(deposits),
            "locked_count": len(locked),
            "matured_count": len(matured),
            "locked_sum": locked_sum,
            "matured_sum": matured_sum,
            "total": locked_sum + matured_sum,
            "interest_sum": sum(int(d.get("interest", 0) or 0) for d in deposits),
        }

    def _handle_bank_deposit(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)

        data = self._load()
        # 2.2.0：存单到期结算由固定结算循环（BANK_SETTLE_HOUR）统一执行，此处不再懒结算

        max_store = self._max_bank_storage(data, key)
        if max_store <= 0:
            return "你的最大存储额度为 0（额度 = 好感度×100 + 宠物经验），先通过签到提升好感度 / 宠物经验吧。"

        bank = data.setdefault("bank", {}).setdefault(key, {"deposits": [], "total_deposits": 0, "total_interest": 0})
        stored = sum(self._dep_amount(d) for d in bank.get("deposits", []))
        coins = self._coins_of(data, key)
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

        base_rate = self._bank_base_rate(data, key)
        bonus_rate = self._bank_bonus_rate(data, key)
        hours = self._bank_hours()
        interest = int(round(amount * (base_rate + bonus_rate) / 100.0 * hours))

        now = datetime.now()
        unlock = self._bank_unlock_time()

        self._add_coins(data, key, -amount, "银行存款")
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
        self._save(data)

        head = f"🏦 {name} 存款成功！"
        if auto:
            head = f"🏦 {name} 存款成功（未指定金额，已自动存入最大可存金额）！"
        return (head + "\n"
                f"💰 本金：{amount}（已锁定）\n"
                f"📈 利率：{base_rate:.2f}% + {bonus_rate:.2f}% = {base_rate + bonus_rate:.2f}%/小时\n"
                f"⏰ 计息 {hours} 小时，下一个 4:00 解锁\n"
                f"🧮 预计利息：{interest} 金币\n"
                f"🔓 解锁后利息自动入账，本金发送「取款」取出。")

    def _handle_bank_withdraw(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)

        data = self._load()
        # 2.2.0：存单到期结算由固定结算循环统一执行，取款只读取已结算的成熟存单

        bank = data.get("bank", {}).get(key)
        if not bank or not bank.get("deposits"):
            return f"{name} 银行里还没有存款，发送「存款 <金额>」存钱吧。"

        matured_sum = sum(self._dep_amount(d) for d in bank["deposits"] if d.get("status") == "matured")
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
                amt = self._dep_amount(d)
                take = min(amt, remaining)
                remaining -= take
                if take < amt:
                    d["amount"] = amt - take
                    new_deposits.append(d)
            else:
                new_deposits.append(d)
        bank["deposits"] = new_deposits

        self._add_coins(data, key, withdraw, "银行取款")
        self._save(data)
        return (f"🏦 {name} 取款成功：取出本金 {withdraw} 金币（已解锁本金剩余 {matured_sum - withdraw}）。\n"
                f"{self._coin_line(data, key)}")

    def _handle_bank_stats(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        # 2.2.0：存单到期结算由固定结算循环统一执行，统计只读取已结算结果

        max_store = self._max_bank_storage(data, key)
        bank = data.get("bank", {}).get(key, {})
        deposits = bank.get("deposits", [])
        locked = [d for d in deposits if d.get("status") == "locked"]
        matured = [d for d in deposits if d.get("status") == "matured"]
        locked_sum = sum(self._dep_amount(d) for d in locked)
        matured_sum = sum(self._dep_amount(d) for d in matured)
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
                lines.append(f"· {st} 本金 {self._dep_amount(d)}｜利率 {d.get('base_rate', 0):.2f}%+{d.get('bonus_rate', 0):.2f}%×{d.get('hours', 0)}h｜利息 {d.get('interest', 0)}")
        return "\n".join(lines)

    # ================= 金币账单 =================
    def _handle_ledger(self, event: AstrMessageEvent) -> str:
        """查询流水 / 流水查询 / 消费记录：图片展示金币变动流水（只记发生金额变动的操作）。
        2.0.3：连续的重复内容（同时间 + 同原因 + 同金额）折叠为一条，*N 用黄色表示。"""
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        ledger = data.get("ledger", {}).get(key, [])
        if not ledger:
            return f"{name} 还没有金币流水记录（金币发生变动时才会记录）。"
        # 2.0.3：折叠连续的重复条目（时间/原因/金额均相同）→ 保留组内最新一条（首条显示）的余额 + 计数
        merged = []  # [时间, 原因, 金额, 余额, 次数]
        for rec in reversed(ledger[-LEDGER_SHOW:]):
            delta = int(rec.get("delta", 0))
            ts = str(rec.get("ts", ""))[:16]
            reason = rec.get("reason", "")
            if merged and merged[-1][0] == ts and merged[-1][1] == reason and merged[-1][2] == delta:
                merged[-1][4] += 1
            else:
                merged.append([ts, reason, delta, int(rec.get("balance", 0) or 0), 1])
        # 渲染（*N 黄色 #FFC000）
        rows = []
        texts = []
        for ts, reason, delta, balance, count in merged:
            sign = "+" if delta >= 0 else ""
            base = f"{ts} {reason}{sign}{delta}"
            tail = f"（余额 {balance}）"
            texts.append(base + (f"*{count}" if count > 1 else "") + tail)
            segs = [(base, DS_TEXT_2, False)]
            if count > 1:
                segs.append((f"*{count}", DS_GOLD, False))
            segs.append((tail, DS_TEXT_2, False))
            rows.append(segs)
        img = self._render_rich_image(f"{name} 的金币账单（最近 {min(LEDGER_SHOW, len(ledger))} 条）", rows)
        if img is not None:
            return img
        return "\n".join(texts)
