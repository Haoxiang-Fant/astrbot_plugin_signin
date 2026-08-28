# -*- coding: utf-8 -*-
# 银行贷款系统。从原 main.py 的 SignInPlugin 拆出的 Mixin，由入口类组合继承。
from .base import *  # noqa: F401,F403  常量与共享工具
from .base import _register_runtime_module  # noqa: F401
import sys as _sys

_register_runtime_module(_sys.modules[__name__])


class LoanMixin:
    def _norm_loan_entry(self, d: dict):
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
            "max_amount": int(self._f(d.get("max_amount", 0))),
            "fav_req": int(self._f(d.get("fav_req", 0))),
            "pet_req": int(self._f(d.get("pet_req", 0))),
            "farm_req": int(self._f(d.get("farm_req", 0))),
            "rate": self._f(d.get("rate", 0)),
        }

    def _load_loan_packages(self):
        """自定义贷款套餐（代码 3~10）。1.7.7：优先读 game_items.json，回退解析 贷款套餐.txt"""
        flat = self._read_items_json()
        if flat is not None:
            return [l for l in (self._norm_loan_entry(d) for d in flat["loans"]) if l]
        result = []
        for it in _parse_kv_sections(LOAN_FILE, "贷款套餐"):
            d = it["data"]
            try:
                result.append({
                    "code": int(it["name"]),
                    "max_amount": int(self._f(d.get("最大金额", 0))),
                    "fav_req": int(self._f(d.get("好感度等级要求", 0))),
                    "pet_req": int(self._f(d.get("宠物等级要求", 0))),
                    "farm_req": int(self._f(d.get("农场等级要求", 0))),
                    "rate": self._f(d.get("利息", 0)),
                })
            except Exception:
                continue
        return result

    def _loans_of(self, data, key):
        return data.get("loans", {}).get(key)

    def _ensure_loans(self, data, key):
        return data.setdefault("loans", {}).setdefault(key, {
            "loans": [], "overdue_records": [],
            "overdue_year": 0, "overdue_year_key": str(date.today().year),
            "ban": False, "daily_borrowed": 0, "daily_date": "", "daily_process_date": "",
        })

    def _loan_unlocked(self, data, key):
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

    def _loan_general_rate(self, pet_level):
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

    def _pet_level(self, data, key):
        pet = data.get("pets", {}).get(key)
        return int(pet.get("level", 0)) if pet else 0

    def _loan_package(self, data, key, code):
        """返回套餐信息：0 特别、1 一般、2 短期、3+ 自定义"""
        if code == 0:
            return {"code": 0, "max_amount": LOAN_SPECIAL_AMOUNT, "rate": LOAN_SPECIAL_RATE,
                    "fav_req": 0, "pet_req": 0, "farm_req": 0, "special": True}
        if code == 1:
            farm = data.get("farms", {}).get(key)
            return {"code": 1, "max_amount": self._loan_general_max(int(farm.get("level", 0)) if farm else 0),
                    "rate": self._loan_general_rate(self._pet_level(data, key)),
                    "fav_req": 0, "pet_req": 0, "farm_req": 0, "special": False}
        if code == 2:
            user = data.get("users", {}).get(key, {})
            return {"code": 2, "max_amount": self._loan_short_max(self._level_of(float(user.get("favorability", 0.0)))),
                    "rate": LOAN_SHORT_RATE, "fav_req": 0, "pet_req": 0, "farm_req": 0, "special": False}
        for pkg in self._load_loan_packages():
            if pkg["code"] == code:
                return {"code": code, "max_amount": pkg["max_amount"], "rate": pkg["rate"],
                        "fav_req": pkg["fav_req"], "pet_req": pkg["pet_req"], "farm_req": pkg["farm_req"], "special": False}
        return None

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

    def _loan_owed(self, loan, now_ts):
        return round(loan.get("remaining", 0) + self._loan_accrued(loan, now_ts), 2)

    @staticmethod
    def _loan_is_overdue(loan, now_ts):
        return now_ts > loan.get("due_ts", 0) and loan.get("remaining", 0) > 0

    def _has_overdue_now(self, rec, now_ts):
        return any(self._loan_is_overdue(l, now_ts) for l in rec.get("loans", []))

    def _force_unlock(self, data, key):
        """强制解锁：赠送农场（2 块地）和宠物"""
        if key not in data.get("farms", {}):
            farm = self._ensure_farm(data, key)
            for _ in range(FARM_FREE_PLOTS):
                farm["plots"].append(self._new_plot())
        if key not in data.get("pets", {}):
            today = date.today().isoformat()
            data.setdefault("pets", {})[key] = {
                "name": "宠物", "level": 1, "exp": 0.0,
                "satiety": 100.0, "thirst": 100.0, "stamina": 100.0, "health": 120.0, "mood": 80.0,
                "last_settle_date": today, "last_settle": None,
                "inventory": {}, "money_event_date": today, "money_event_count": 0,
            }

    def _special_overdue(self, data, key):
        """特别贷款逾期：重锁农场/宠物，收取仓库总价值 10%（清除数据）"""
        farm = data.get("farms", {}).get(key)
        if farm:
            wh = farm.get("warehouse", {})
            crops = self._load_crops()
            ferts = self._load_fertilizers()
            total = 0
            for nm, cnt in list(wh.get("crops", {}).items()):
                c = self._find_item(crops, nm)
                total += int(round(int(cnt) * (float(c["crop_price"]) if c else 0.0)))
            for nm, cnt in list(wh.get("seeds", {}).items()):
                c = self._find_item(crops, nm)
                total += int(round(int(cnt) * (float(c["seed_sell_price"]) if c else 0.0)))
            for nm, cnt in list(wh.get("fertilizers", {}).items()):
                f = self._find_item(ferts, nm)
                total += int(round(float(cnt) * (int(f["price"]) if f else 0)))
            take = int(total * 0.1)
            if take > 0:
                self._repay_loans(data, key, take)
            farm["warehouse"] = {"crops": {}, "seeds": {}, "fertilizers": {}}
        data.get("pets", {}).pop(key, None)
        data.get("farms", {}).pop(key, None)

    def _loan_sync(self, data, key, now_ts=None):
        """标记逾期 + 逾期处置（特别贷款重锁、逾期记录、年度计数、30 天农场回退、禁用）"""
        now_ts = now_ts or datetime.now().timestamp()
        rec = self._ensure_loans(data, key)
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
                        self._special_overdue(data, key)
                if now_ts - loan.get("due_ts", 0) > LOAN_FARM_ROLLBACK_DAYS * 86400:
                    farm = data.get("farms", {}).get(key)
                    if farm:
                        farm["level"] = 0
                        farm["exp"] = 0.0
                        farm["plots"] = [self._new_plot() for _ in range(FARM_FREE_PLOTS)]
                        farm["warehouse"] = {"crops": {}, "seeds": {}, "fertilizers": {}}
        if rec.get("overdue_year", 0) >= LOAN_OVERDUE_YEAR_LIMIT:
            rec["ban"] = True
        return changed

    def _repay_loans(self, data, key, amount, code=None):
        """还款，返回实际还款金额；优先还逾期最久 / 即将到期的账单"""
        rec = self._ensure_loans(data, key)
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
            owed = self._loan_owed(loan, now_ts)
            take = min(remaining_money, owed)
            remaining_money -= take
            repaid += take
            loan["remaining"] = round(loan["remaining"] - take, 2)
            if loan["remaining"] <= 0:
                loan["remaining"] = 0
        rec["loans"] = [l for l in rec.get("loans", []) if l.get("remaining", 0) > 0]
        return round(repaid, 2)

    def _loan_daily_process(self, data, key, now=None):
        """每日逾期处置：好感度降低 + 23:00 自动卖仓库/自动签到还款（懒执行，一天一次）"""
        now = now or datetime.now()
        rec = self._ensure_loans(data, key)
        if not self._has_overdue_now(rec, now.timestamp()):
            return False
        today = now.strftime("%Y-%m-%d")
        if rec.get("daily_process_date") == today:
            return False
        rec["daily_process_date"] = today
        user = self._ensure_user(data, key)
        special = any(l.get("special") and l.get("remaining", 0) > 0 for l in rec.get("loans", []))
        lo, hi = LOAN_FAV_DROP_SPECIAL if special else LOAN_FAV_DROP_NORMAL
        drop = random.uniform(lo, hi)
        user["favorability"] = round(max(0.0, float(user.get("favorability", 0.0)) - drop), 2)
        if (now.hour, now.minute) >= LOAN_AUTO_TIME:
            farm = data.get("farms", {}).get(key)
            if farm:
                coins = self._sell_warehouse_all(data, key, farm)
                if coins > 0:
                    self._repay_loans(data, key, coins)
            coins = self._auto_signin(data, key)
            if coins > 0:
                self._repay_loans(data, key, coins)
        return True

    def _sell_warehouse_all(self, data, key, farm):
        """卖出仓库全部物品（化肥除外——只能买和使用，不可卖），返回所得金币（不进入余额，直接用于还款）"""
        wh = farm.get("warehouse", {})
        crops = self._load_crops()
        total = 0
        for nm, cnt in list(wh.get("crops", {}).items()):
            c = self._find_item(crops, nm)
            total += int(round(int(cnt) * (float(c["crop_price"]) if c else 0.0)))
        for nm, cnt in list(wh.get("seeds", {}).items()):
            c = self._find_item(crops, nm)
            total += int(round(int(cnt) * (float(c["seed_sell_price"]) if c else 0.0)))
        # 化肥不可卖：保留在仓库
        farm["warehouse"] = {"crops": {}, "seeds": {}, "fertilizers": wh.get("fertilizers", {})}
        return total

    def _auto_signin(self, data, key):
        """逾期自动签到：只发放金币与好感度（金币用于抵债），标记当日已签到"""
        today = date.today().isoformat()
        user = data.get("users", {}).get(key)
        if user and user.get("last_date") == today:
            return 0
        if user is None:
            user = self._ensure_user(data, key)
        coins = random.randint(self.min_coins, self.max_coins)
        user["favorability"] = round(float(user.get("favorability", 0.0)) + round(random.uniform(MIN_FAV, MAX_FAV), 2), 2)
        user["last_date"] = today
        return coins

    # ---- 贷款指令 ----
    def _loan_packages_info(self, data, key):
        """所有贷款套餐信息，用于「借款」无参数时的概览"""
        info = []
        info.append({
            "code": 0, "name": "特别贷款（强制解锁）",
            "max": f"{LOAN_SPECIAL_AMOUNT}（固定，不发放金币）",
            "rate": f"{LOAN_SPECIAL_RATE}%/日",
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
        for pkg in self._load_loan_packages():
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

    def _render_loan_packages(self, data, key):
        rows = []
        rows.append([("格式：借款 <套餐代码> <金额>", (90, 90, 90), False)])
        rows.append([("每日累计贷款上限 = 2 × 套餐上限", (90, 90, 90), False)])
        rows.append([("", (0, 0, 0), False)])
        for pkg in self._loan_packages_info(data, key):
            rows.append([(f"[套餐 {pkg['code']} - {pkg['name']}]", (110, 110, 110), False)])
            rows.append([(f"最大可借：{pkg['max']}", (20, 20, 20), False)])
            rows.append([(f"日利率：{pkg['rate']}", (20, 20, 20), False)])
            if pkg.get("note"):
                rows.append([(f"说明：{pkg['note']}", (80, 80, 80), False)])
            rows.append([("", (0, 0, 0), False)])
        img = self._render_rich_image("借款（贷款套餐一览）", rows)
        if img is not None:
            return img
        lines = ["借款（贷款套餐一览）：", "格式：借款 <套餐代码> <金额>", "每日累计贷款上限 = 2 × 套餐上限"]
        for pkg in self._loan_packages_info(data, key):
            lines.append(f"[套餐 {pkg['code']} - {pkg['name']}]")
            lines.append(f"最大可借：{pkg['max']}")
            lines.append(f"日利率：{pkg['rate']}")
            if pkg.get("note"):
                lines.append(f"说明：{pkg['note']}")
        return "\n".join(lines)

    def _handle_loan_borrow(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            # 无参数：展示所有贷款套餐一览（图片）
            data = self._load()
            return self._render_loan_packages(data, key)
        args = parts[1].split()
        if len(args) < 2:
            return "格式：借款 <套餐代码> <金额>（发送「借款」查看套餐一览）"
        try:
            code = int(args[0])
            amount = int(args[1])
        except ValueError:
            return "套餐代码和金额必须是整数。格式：借款 <套餐代码> <金额>"
        if code not in (0, 1, 2) and not (3 <= code <= 10):
            return "套餐代码无效（0=特别，1=一般，2=短期，3~10=自定义）。"
        if amount <= 0:
            return "金额必须为正整数。"

        data = self._load()
        self._loan_sync(data, key)
        rec = self._ensure_loans(data, key)
        if rec.get("ban"):
            return f"{name} 已因年度逾期超限被禁用贷款功能。"
        if code == 0:
            if key in data.get("pets", {}) or key in data.get("farms", {}):
                return "你已经开通了宠物或农场，不能使用强制解锁（特别贷款）。"
            if amount != LOAN_SPECIAL_AMOUNT:
                return f"特别贷款固定金额为 {LOAN_SPECIAL_AMOUNT} 金币。"
            if any(l.get("remaining", 0) > 0 for l in rec.get("loans", [])):
                return "你有未结清的贷款（含特别贷款），还清前不能再次申请。"
        else:
            if not self._loan_unlocked(data, key):
                return "贷款功能需要先解锁宠物系统或农场（发送「解锁宠物」或「解锁农场」）。"
            if self._has_overdue_now(rec, datetime.now().timestamp()):
                return "你有逾期贷款，还清前不能新增贷款。"
        if any(l.get("special") and l.get("remaining", 0) > 0 for l in rec.get("loans", [])):
            return "你有未结清的特别贷款，还清前不能再次申请任何贷款。"

        pkg = self._loan_package(data, key, code)
        if pkg is None:
            return "该套餐未配置。"
        if amount > pkg["max_amount"]:
            return f"该套餐最大可借 {pkg['max_amount']} 金币。"
        user = data.get("users", {}).get(key, {})
        fav_level = self._level_of(float(user.get("favorability", 0.0)))
        if fav_level < pkg.get("fav_req", 0):
            return f"好感度等级不足（需要 Lv.{pkg['fav_req']}）。"
        if self._pet_level(data, key) < pkg.get("pet_req", 0):
            return f"宠物等级不足（需要 Lv.{pkg['pet_req']}）。"
        farm = data.get("farms", {}).get(key)
        if (int(farm.get("level", 0)) if farm else 0) < pkg.get("farm_req", 0):
            return f"农场等级不足（需要 Lv.{pkg['farm_req']}）。"

        today = date.today().isoformat()
        if rec.get("daily_date") != today:
            rec["daily_date"] = today
            rec["daily_borrowed"] = 0
        if rec.get("daily_borrowed", 0) + amount > int(pkg["max_amount"] * LOAN_DAILY_MULT):
            return f"今日累计贷款已达上限（{int(pkg['max_amount'] * LOAN_DAILY_MULT)}），请明天再申请。"

        now = datetime.now()
        now_ts = now.timestamp()
        if code == 0:
            self._force_unlock(data, key)
            free_until = now_ts
            due = now_ts + LOAN_SPECIAL_DAYS * 86400
            rate = LOAN_SPECIAL_RATE
            special = True
        elif code == 1:
            free_until = self._bank_unlock_time().timestamp()
            due = now_ts + LOAN_GENERAL_OVERDUE_DAYS * 86400
            rate = pkg["rate"]
            special = False
        elif code == 2:
            free_until = now_ts + LOAN_SHORT_GRACE_DAYS * 86400
            due = now_ts + LOAN_SHORT_GRACE_DAYS * 86400
            rate = LOAN_SHORT_RATE
            special = False
        else:
            free_until = self._bank_unlock_time().timestamp()
            due = now_ts + LOAN_GENERAL_OVERDUE_DAYS * 86400
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
            self._add_coins(data, key, amount, f"贷款·套餐{code}")
        self._save(data)
        if code == 0:
            extra = "\n🔓 已强制解锁农场与宠物系统（产生 2500 金币贷款，日息 1%，30 天内还清，未发放金币）"
        else:
            extra = ""
        return (f"🏦 {name} 借款成功！\n"
                f"💳 套餐 {code}，金额 {amount} 金币\n"
                f"📈 日利率：{rate}%\n"
                f"⏰ 免息至 {datetime.fromtimestamp(free_until).strftime('%m-%d %H:%M')}，逾期日 {datetime.fromtimestamp(due).strftime('%m-%d %H:%M')}{extra}\n"
                f"{self._coin_line(data, key)}")

    def _handle_loan_repay(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        data = self._load()
        self._loan_sync(data, key)
        rec = self._ensure_loans(data, key)
        if not rec.get("loans"):
            return f"{name} 名下没有贷款。"
        now_ts = datetime.now().timestamp()
        coins = self._coins_of(data, key)

        if len(parts) < 2:
            # 还所有贷款：优先还逾期最久/即将到期
            if coins <= 0:
                return f"{name} 金币余额为 0，无法还款。"
            repaid = self._repay_loans(data, key, coins)
            if repaid > 0:
                self._add_coins(data, key, -int(repaid), "偿还贷款")
            total = sum(self._loan_owed(l, now_ts) for l in rec.get("loans", []))
            self._save(data)
            return (f"🏦 已用全部金币还款 {round(repaid, 2)}，剩余待还 {round(total, 2)}。\n"
                    f"{self._coin_line(data, key)}")
        args = parts[1].split()
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
            amount = int(sum(self._loan_owed(l, now_ts) for l in targets))
        if coins <= 0:
            return f"{name} 金币余额为 0。"
        amount = min(amount, coins)
        repaid = self._repay_loans(data, key, amount, code)
        if repaid > 0:
            self._add_coins(data, key, -int(repaid), f"偿还贷款·套餐{code}")
        self._save(data)
        return (f"🏦 已对套餐 {code} 还款 {round(repaid, 2)} 金币。\n"
                f"{self._coin_line(data, key)}")

    def _handle_my_loans(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        changed = self._loan_sync(data, key)
        rec = self._ensure_loans(data, key)
        lines = [f"🏦 {name} 的贷款账单："]
        loans = rec.get("loans", [])
        if not loans:
            lines.append("暂无生效中的贷款。")
        now_ts = datetime.now().timestamp()
        for l in loans:
            days = max(0, int((now_ts - max(l.get("free_until_ts", l.get("borrow_ts", 0)), l.get("borrow_ts", 0))) // 86400))
            owed = self._loan_owed(l, now_ts)
            st = "⚠️逾期" if l.get("overdue") else "✅正常"
            lines.append(f"· 套餐{l['package']}｜借款 {l['amount']}｜利率 {l['rate']}%/日｜剩余 {l['remaining']}｜计息 {days} 天｜欠款 {owed}｜{st}")
        if changed:
            self._save(data)
        return "\n".join(lines)

    def _handle_my_credit(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        changed = self._loan_sync(data, key)
        rec = self._ensure_loans(data, key)
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
                owed = self._loan_owed(l, now_ts)
                due = datetime.fromtimestamp(l["due_ts"]).strftime("%m-%d")
                st = "⚠️逾期" if l.get("overdue") else "✅"
                lines.append(f"· 套餐{l['package']}｜借款 {l['amount']}｜利率 {l['rate']}%｜欠款 {owed}｜逾期日 {due}｜{st}")
        if rec.get("ban"):
            lines.append("🚫 已因年度逾期超限被禁用贷款功能")
        if changed:
            self._save(data)
        return "\n".join(lines)
