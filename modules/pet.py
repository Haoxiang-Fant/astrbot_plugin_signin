# -*- coding: utf-8 -*-
# 宠物系统（养成/打工/玩耍/商店/背包/结算）。从原 main.py 的 SignInPlugin 拆出的 Mixin，由入口类组合继承。
import re

from .base import *  # noqa: F401,F403  常量与共享工具
from .base import _register_runtime_module  # noqa: F401
import sys as _sys

_register_runtime_module(_sys.modules[__name__])


class PetMixin:
    _SETTLE_ATTR_MAP = {"饱食": "satiety", "口渴": "thirst", "体力": "stamina", "心情": "mood", "健康": "health"}
    _SETTLE_DEFAULT_RANGES = {
        "H1": {"饱食": (-15.0, -10.0), "口渴": (-15.0, -10.0), "体力": (100.0, 120.0), "心情": (3.0, 7.0), "健康": (0.0, 0.0)},
        "H2": {"饱食": (-20.0, -15.0), "口渴": (-20.0, -15.0), "体力": (80.0, 120.0), "心情": (1.0, 2.5), "健康": (0.0, 0.0)},
        "H3": {"饱食": (-25.0, -20.0), "口渴": (-25.0, -20.0), "体力": (40.0, 60.0), "心情": (-5.0, -2.0), "健康": (-8.0, -1.0)},
        "T1": {"健康": (5.0, 10.0)},
        "T2": {"健康": (0.1, 6.0)},
        "T3": {"健康": (-10.0, -4.0)},
        "T4": {"健康": (-15.0, -8.0)},
    }

    def _settle_ranges(self):
        """解析 PET_SETTLE_RANGES（WebUI「设置 → 签到 → 宠物结算范围」可编辑）。
        格式：H1=饱食-15~-10,口渴-15~-10,体力100~120,心情3~7,健康0~0|H2=…|T1=健康5~10|…
        H1/H2/H3 = 按宠物健康度分档（≥100 / 40-99 / 0-39）；T1~T4 = 按饱食/口渴/心情最差档分档。
        返回 {"H1": {属性: (lo, hi)}, …, "T4": {…}}；解析失败回退默认值。"""
        raw = getattr(self, "pet_settle_ranges", None)
        if not raw or not str(raw).strip():
            raw = globals().get("PET_SETTLE_RANGES", "")
        if not isinstance(raw, str) or not str(raw).strip():
            return self._SETTLE_DEFAULT_RANGES
        out = {}
        for seg in str(raw).replace("；", "|").replace(";", "|").split("|"):
            seg = seg.strip()
            if not seg or "=" not in seg:
                continue
            key, body = seg.split("=", 1)
            key = key.strip().upper()
            if key not in ("H1", "H2", "H3", "T1", "T2", "T3", "T4"):
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
                if name in self._SETTLE_ATTR_MAP:
                    attrs[name] = (min(lo, hi), max(lo, hi))
            if attrs:
                out[key] = attrs
        if not out:
            return self._SETTLE_DEFAULT_RANGES
        return out

    @staticmethod
    def _attr_max(health: float):
        """返回 (饱食上限, 口渴上限, 体力上限, 心情上限)，由健康度决定（1.7.6 新规则）：
        健康 140-200 → 200/200/200/120；80-139 → 120/120/120/100；
        40-79 → 100/100/100/100；0-39 → 80/80/60/80。健康度最大值 200（PET_MAX_HEALTH）。"""
        if health >= 140:
            return 200.0, 200.0, 200.0, 120.0
        if health >= 80:
            return 120.0, 120.0, 120.0, 100.0
        if health >= 40:
            return 100.0, 100.0, 100.0, 100.0
        return 80.0, 80.0, 60.0, 80.0

    def _clamp_attrs(self, pet: dict) -> None:
        sat_max, thr_max, sta_max, mood_max = self._attr_max(pet["health"])
        pet["satiety"] = round(self._clamp(pet["satiety"], 0, sat_max), 2)
        pet["thirst"] = round(self._clamp(pet["thirst"], 0, thr_max), 2)
        pet["stamina"] = round(self._clamp(pet["stamina"], 0, sta_max), 2)
        pet["mood"] = round(self._clamp(pet["mood"], 0, mood_max), 2)
        pet["health"] = round(self._clamp(pet["health"], 0, PET_MAX_HEALTH), 2)

    @staticmethod
    def _worst_tier(satiety: float, thirst: float, mood: float) -> int:
        """饱食/口渴/心情对健康的影响档位（1.7.6 四档），每个属性单独定档后取最差档（4 最差）：
        一档：≥120 / ≥120 / ≥80；二档：50-119 / 70-119 / 50-79；
        三档：30-49 / 30-59 / 30-39；四档：<30 / <30 / <30。"""
        t_sat = 1 if satiety >= 120 else (2 if satiety >= 50 else (3 if satiety >= 30 else 4))
        t_thr = 1 if thirst >= 120 else (2 if thirst >= 70 else (3 if thirst >= 30 else 4))
        t_mood = 1 if mood >= 80 else (2 if mood >= 50 else (3 if mood >= 30 else 4))
        return max(t_sat, t_thr, t_mood)

    def _settle_once(self, pet: dict, settle_date: str) -> None:
        """执行一次每日结算（2.0.0：各档位属性变化范围可在 WebUI「设置 → 签到 → 宠物结算范围」编辑）"""
        health = pet["health"]
        ranges = self._settle_ranges()

        def _roll(hkey, name, default):
            r = ranges.get(hkey, {}).get(name) or default
            return random.uniform(r[0], r[1])

        # 1. 基础结算（按健康档位）
        if health >= 100:      # 100-200
            hkey = "H1"
        elif health >= 40:     # 40-99
            hkey = "H2"
        else:                  # 0-39
            hkey = "H3"
        sat_d = _roll(hkey, "饱食", self._SETTLE_DEFAULT_RANGES[hkey]["饱食"])
        thr_d = _roll(hkey, "口渴", self._SETTLE_DEFAULT_RANGES[hkey]["口渴"])
        sta_d = _roll(hkey, "体力", self._SETTLE_DEFAULT_RANGES[hkey]["体力"])
        mood_d = _roll(hkey, "心情", self._SETTLE_DEFAULT_RANGES[hkey]["心情"])
        health_base_d = _roll(hkey, "健康", self._SETTLE_DEFAULT_RANGES[hkey]["健康"])

        # 2. 饱食/口渴/心情 四档对健康的影响（取最差档）
        tier = self._worst_tier(pet["satiety"], pet["thirst"], pet["mood"])
        tkey = f"T{tier}"
        health_tier_d = _roll(tkey, "健康", self._SETTLE_DEFAULT_RANGES[tkey]["健康"])

        health_d = health_base_d + health_tier_d

        # 3. 应用（先按当前健康度的上限 clamp 属性，再改健康度，最后统一 clamp）
        sat_max, thr_max, sta_max, mood_max = self._attr_max(health)
        pet["satiety"] = round(self._clamp(pet["satiety"] + sat_d, 0, sat_max), 2)
        pet["thirst"] = round(self._clamp(pet["thirst"] + thr_d, 0, thr_max), 2)
        pet["stamina"] = round(self._clamp(pet["stamina"] + sta_d, 0, sta_max), 2)
        pet["mood"] = round(self._clamp(pet["mood"] + mood_d, 0, mood_max), 2)
        pet["health"] = round(self._clamp(pet["health"] + health_d, 0, PET_MAX_HEALTH), 2)
        self._clamp_attrs(pet)

        pet["last_settle"] = {
            "date": settle_date,
            "satiety_d": round(sat_d, 2),
            "thirst_d": round(thr_d, 2),
            "stamina_d": round(sta_d, 2),
            "mood_d": round(mood_d, 2),
            "health_d": round(health_d, 2),
            "rested_well": sta_d > 100.0,
            "sick": pet["health"] <= 39.0,
            "tier": tier,   # 1.7.6：1-4 档（3/4 为状态差，签到时提醒）
        }

        # 虚弱判定：连续两天结算健康均为 0 → 宠物进入「虚弱」状态（治疗宠物 可解除）
        if pet["health"] <= 0.5:
            pet["weak_streak"] = int(pet.get("weak_streak", 0)) + 1
            if pet["weak_streak"] >= 2:
                pet["weak"] = True
        else:
            pet["weak_streak"] = 0

    def _bring_pet_up_to_date(self, pet: dict, today: str) -> None:
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
                    self._settle_once(pet, d.isoformat())
            except ValueError:
                pass
        pet["last_settle_date"] = today
        if pet.get("money_event_date") != today:
            pet["money_event_date"] = today
            pet["money_event_count"] = 0

    def _settle_display_lines(self, pet: dict):
        ls = pet.get("last_settle")
        if not ls:
            return []
        lines = ["🐾 宠物结算（昨晚）："]
        lines.append(f"🍖 饱食度 {ls['satiety_d']:+.1f}，💧 口渴值 {ls['thirst_d']:+.1f}，"
                     f"⚡ 体力 {ls['stamina_d']:+.1f}，😊 心情 {ls['mood_d']:+.1f}，❤️ 健康 {ls['health_d']:+.1f}")
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

    @staticmethod
    def _pet_level_from_exp(exp: float) -> int:
        """新经验体系：所需经验 = 当前等级 × 100（累计 100+200+...+（L-1）×100 升到 Lv.L）"""
        exp = max(0.0, float(exp))
        # 解 100*(L-1)*L/2 <= exp → L = floor((1+sqrt(1+8*exp/100))/2)
        L = int((1 + (1 + 8 * exp / 100.0) ** 0.5) / 2)
        return min(PET_MAX_LEVEL, L)

    @staticmethod
    def _pet_exp_progress(exp: float) -> tuple:
        """返回 (当前等级, 本级已得经验, 本级所需经验)，新经验体系：所需经验 = 当前等级 × 100"""
        exp = max(0.0, float(exp))
        level = PetMixin._pet_level_from_exp(exp)
        need_prev = 100.0 * (level - 1) * level / 2.0  # 升到当前等级的累计经验
        got = exp - need_prev
        need = float(level) * 100.0
        return level, got, need

    def _apply_exp(self, pet: dict) -> str:
        new_level = min(PET_MAX_LEVEL, self._pet_level_from_exp(float(pet.get("exp", 0.0))))
        old = int(pet.get("level", 1))
        pet["level"] = new_level
        if new_level > old:
            return f"\n🎊 宠物升级！Lv.{old} → Lv.{new_level}"
        return ""

    def _fav_multipliers(self, fav_level: int):
        """返回 (正面效果倍率, 负面效果倍率)"""
        if fav_level <= 1:
            return 1.0, 1.0
        if fav_level <= 5:
            return 1.1, 1.0
        if fav_level <= 9:
            return 1.1, 0.9
        return 1.2, 0.8

    # ================= 后台配置解析 =================
    # ================= 商店/打工/玩耍数值 JSON 存储（1.7.7） =================
    # game_items.json 扁平结构：
    #   jobs:  [{name, desc, min_level, min_health, min_mood,
    #            cost_stamina, cost_satiety, cost_thirst, cost_health, cost_mood,
    #            time, coins, exp}]
    #   plays: [{name, desc, min_level, min_health, min_mood,
    #            cost_stamina, cost_satiety, cost_thirst, cost_health, cost_mood,
    #            time, exp, mood, stamina, health}]   （mood/stamina/health 为收益，可负）
    #   shop:  [{name, type, desc, price, satiety, thirst, stamina, mood, health}]
    def _handle_unlock_pet(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        if key in data.get("pets", {}):
            pet = data["pets"][key]
            return f"{name} 已经拥有一只宠物「{pet['name']}」啦，每位玩家最多只能养一只。"
        if self._coins_of(data, key) < self.pet_unlock_cost:
            return f"解锁宠物需要 {self.pet_unlock_cost} 金币（当前 {self._coins_of(data, key)}）。"

        self._add_coins(data, key, -self.pet_unlock_cost, "解锁宠物")
        today = date.today().isoformat()
        pet = {
            "name": "宠物", "level": 1, "exp": 0.0,
            "satiety": 100.0, "thirst": 100.0, "stamina": 100.0, "health": 120.0, "mood": 80.0,
            "last_settle_date": today, "last_settle": None,
            "inventory": {}, "money_event_date": today, "money_event_count": 0,
        }
        data.setdefault("pets", {})[key] = pet
        self._save(data)
        return (f"🎉 {name} 花费 {self.pet_unlock_cost} 金币解锁了一只宠物！\n"
                f"发送「更改宠物名字 <名字>」给它起名，\n"
                f"发送「宠物帮助」查看玩法。")

    def _activity_superseded_by_settle(self, pet: dict, la_ts: float) -> bool:
        """即时消息是否已被更新的每日结算覆盖：
        最近一次实际结算的日期晚于该活动发生日 → 返回 True（预留位隐藏、底部卡片切换为结算变化）。
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

    def _handle_pet_status(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」（需 {self.pet_unlock_cost} 金币）领养一只吧。"
        self._bring_pet_up_to_date(pet, date.today().isoformat())
        self._save(data)

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
            if not self._activity_superseded_by_settle(pet, la_ts):
                use_la = la
                show_slot = not bool(la.get("shown"))  # 有效显示次数 = 1
        img = self._safe_render_pet(
            name, key, data, pet,
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
        self._save(data)
        if img is not None:
            return img

        # 回退：文本版
        sat_max, thr_max, sta_max, mood_max = self._attr_max(pet["health"])
        lv, got_exp, need_exp = self._pet_exp_progress(float(pet.get("exp", 0.0)))
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
            lines.append(f"😷 宠物处于虚弱状态，发送「治疗宠物」（{WEAK_HEAL_COST} 金币）治疗！")
        if show_slot and use_la and use_la.get("msg"):
            lines.append(f"📝 {use_la['msg']}")

        # 末尾显示宠物当前活动：打工 / 玩耍（共用冷却计时器）或发呆
        now_ts = datetime.now().timestamp()
        busy_until = self._pet_busy_until(pet)
        act = pet.get("busy_activity", "")
        lines.append("")
        if now_ts < busy_until:
            icon = "💼" if act == "打工" else "🎾"
            label = "打工中" if act == "打工" else "玩耍中"
            lines.append(f"{icon} 正在{label}（剩余 {self._fmt_duration(busy_until - now_ts)}）")
        else:
            lines.append("😴 宠物正在发呆，快带它去打工或玩耍吧～")

        return "\n".join(lines)

    def _handle_weak_heal(self, event: AstrMessageEvent) -> str:
        """治疗虚弱宠物：消耗 WEAK_HEAL_COST 金币，所有数值恢复为 40 并解除虚弱。
        只有处于虚弱状态的宠物才能被治疗。"""
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"
        if not pet.get("weak"):
            return f"{name} 的宠物没有处于虚弱状态，无需治疗。"
        if self._coins_of(data, key) < WEAK_HEAL_COST:
            return (f"{name} 治疗虚弱宠物需要 {WEAK_HEAL_COST} 金币"
                    f"（当前 {self._coins_of(data, key)}），发送「签到」获取金币。")
        self._add_coins(data, key, -WEAK_HEAL_COST, "治疗虚弱宠物")
        pet["satiety"] = pet["thirst"] = pet["stamina"] = pet["mood"] = pet["health"] = 40.0
        pet["weak"] = False
        pet["weak_streak"] = 0
        self._clamp_attrs(pet)
        self._save(data)
        return (f"💊 {name} 花费 {WEAK_HEAL_COST} 金币治疗了宠物「{pet.get('name', '宠物')}」，"
                f"虚弱状态已解除！所有数值恢复至 40。\n{self._pet_state_snippet(pet)}")

    def _handle_rename_pet(self, event: AstrMessageEvent) -> str:
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：更改宠物名字 <新名字>"
        new_name = parts[1].strip()
        if not new_name:
            return "格式：更改宠物名字 <新名字>"
        if len(new_name) > 12:
            return "名字太长了（最多 12 个字）。"

        key = self._user_key(event)
        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"你还没有宠物，发送「解锁宠物」（需 {self.pet_unlock_cost} 金币）领养一只吧。"
        pet["name"] = new_name
        self._save(data)
        return f"✅ 宠物名字已改为「{new_name}」。"

    def _handle_work(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)

        if len(parts) < 2:
            return self._work_list(event)
        job_name = parts[1].strip()

        cfg = self._load_config()
        job = next((j for j in cfg["jobs"] if j["name"] == job_name), None)
        if not job:
            return f"没有名为「{job_name}」的打工，发送「打工」查看列表。"

        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"
        self._bring_pet_up_to_date(pet, date.today().isoformat())

        # 冷却检查：打工/玩耍共用一个计时器，冷却期内不能进行新的打工或玩耍
        now_ts = datetime.now().timestamp()
        busy_until = self._pet_busy_until(pet)
        if now_ts < busy_until:
            return f"{name} 的宠物还在忙碌中（冷却剩余 {self._fmt_duration(busy_until - now_ts)}），暂时不能打工或玩耍。"

        # 要求检查
        if pet["level"] < job["min_level"]:
            return f"宠物等级不足（需要 Lv.{int(job['min_level'])}，当前 Lv.{pet['level']}）。"
        if pet["health"] < job["min_health"]:
            return f"宠物健康度不足（需要 {job['min_health']:.0f}，当前 {pet['health']:.1f}）。"
        if pet["mood"] < job["min_mood"]:
            return f"宠物心情不足（需要 {job['min_mood']:.0f}，当前 {pet['mood']:.1f}）。"

        # 消耗检查
        for attr, cost in job["cost"].items():
            if cost > 0 and pet[attr] < cost:
                return f"{ATTR_LABELS[attr]}不足，无法打工（需要 {cost:.0f}，当前 {pet[attr]:.1f}）。"

        # 记录变化前属性（供底部卡片1显示本次变化）
        before = {a: pet[a] for a in ATTR_LABELS}
        # 应用消耗
        for attr, cost in job["cost"].items():
            pet[attr] = round(max(0.0, pet[attr] - cost), 2)

        # 报酬
        self._add_coins(data, key, int(job["coins"]), f"打工·{job['name']}")
        pet["exp"] = round(float(pet.get("exp", 0.0)) + job["exp"], 2)
        lvl_msg = self._apply_exp(pet)
        self._clamp_attrs(pet)
        # 进入冷却（打工/玩耍共用计时器）
        pet["busy_until"] = now_ts + int(job["time"]) * 60
        pet["busy_start"] = now_ts
        pet["busy_activity"] = "打工"
        pet["busy_item"] = job["name"]
        pet["_progress_done_notified"] = False  # 新一轮进度条开始，重置「满后第一次响应」标记
        changes = {a: round(pet[a] - before[a], 2) for a in ATTR_LABELS if abs(pet[a] - before[a]) > 1e-9}
        pet["last_activity"] = {
            "msg": f"{pet['name']} 去「{job['name']}」打工成功！",
            "changes": changes,
            "reason": f"打工「{job['name']}」",
            "coins": int(job["coins"]),
            "exp": job["exp"],
            "act": "打工",
            "ts": now_ts,
            "shown": False,   # 即时消息：本次响应显示一次后置 True
        }
        self._save(data)

        cd = f"（冷却 {int(job['time'])} 分钟）" if job["time"] > 0 else ""
        text = (f"💼 {name} 的宠物去「{job['name']}」打工完成！{cd}\n"
                f"💰 金币 +{int(job['coins'])}，🐾 经验 +{job['exp']:.1f}{lvl_msg}\n"
                f"{self._coin_line(data, key)}\n"
                f"{self._pet_state_snippet(pet)}")
        # 2.0.0 消息合并：打工结果合入宠物指令图片的预留位（即时消息显示一次）+ 底部卡片
        # 本次产生新的状态变化（卡片1高亮）且发生打工变动（卡片2高亮）
        img = self._safe_render_pet(
            name, key, data, pet,
            slot_msg=f"{pet['name']} 去「{job['name']}」打工成功！{lvl_msg}".strip(),
            changes=changes,
            reason=f"打工「{job['name']}」",
            coins_delta=int(job["coins"]),
            exp_delta=job["exp"],
            changes_fresh=True,
            card2_hl=True,
        )
        # 即时消息已显示（图片或文本回退均视为显示一次）→ 标记，宠物指令不再重复显示
        if pet.get("last_activity"):
            pet["last_activity"]["shown"] = True
            self._save(data)
        return img if img is not None else text

    def _work_list(self, event=None):
        cfg = self._load_config()
        if not cfg["jobs"]:
            return "后台还没有配置打工项目（请管理员编辑 后台.txt）。"
        pet = None
        coins = None
        if event is not None:
            data = self._load()
            key = self._user_key(event)
            pet = data.get("pets", {}).get(key)
            coins = self._coins_of(data, key)
        img = self._render_work_play_image("打工", event.get_sender_name(), pet, cfg["jobs"], coins)
        if img is not None:
            return img
        lines = ["发送「打工 <名称>」开始", ""]
        for j in cfg["jobs"]:
            lines.append(f"· {j['name']}：{j['desc']}｜要求 Lv.{int(j['min_level'])}+ / 健康 {j['min_health']:.0f}+ / 心情 {j['min_mood']:.0f}+｜耗时 {j['time']:.0f}分｜金币 +{int(j['coins'])} 经验 +{j['exp']:.0f}")
        img = self._render_text_image("打工列表", lines)
        if img is not None:
            return img
        return "\n".join(["💼 打工列表（发送「打工 <名称>」开始）："] + lines)

    def _handle_play(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)

        if len(parts) < 2:
            return self._play_list(event)
        play_name = parts[1].strip()

        cfg = self._load_config()
        play = next((p for p in cfg["plays"] if p["name"] == play_name), None)
        if not play:
            return f"没有名为「{play_name}」的玩耍项目，发送「玩耍」查看列表。"

        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"
        self._bring_pet_up_to_date(pet, date.today().isoformat())

        # 冷却检查：打工/玩耍共用一个计时器，冷却期内不能进行新的打工或玩耍
        now_ts = datetime.now().timestamp()
        busy_until = self._pet_busy_until(pet)
        if now_ts < busy_until:
            return f"{name} 的宠物还在忙碌中（冷却剩余 {self._fmt_duration(busy_until - now_ts)}），暂时不能打工或玩耍。"

        if pet["level"] < play["min_level"]:
            return f"宠物等级不足（需要 Lv.{int(play['min_level'])}，当前 Lv.{pet['level']}）。"
        if pet["health"] < play["min_health"]:
            return f"宠物健康度不足（需要 {play['min_health']:.0f}，当前 {pet['health']:.1f}）。"
        if pet["mood"] < play["min_mood"]:
            return f"宠物心情不足（需要 {play['min_mood']:.0f}，当前 {pet['mood']:.1f}）。"

        for attr, cost in play["cost"].items():
            if cost > 0 and pet[attr] < cost:
                return f"{ATTR_LABELS[attr]}不足，无法玩耍（需要 {cost:.0f}，当前 {pet[attr]:.1f}）。"

        # 记录变化前属性（供底部卡片1显示本次变化）
        before = {a: pet[a] for a in ATTR_LABELS}
        for attr, cost in play["cost"].items():
            if attr == "health":
                continue  # 健康消耗并入下方净变化计算（避免双扣）
            pet[attr] = round(max(0.0, pet[attr] - cost), 2)

        pet["exp"] = round(float(pet.get("exp", 0.0)) + play["exp"], 2)
        pet["mood"] = round(pet["mood"] + play["mood"], 2)
        if play.get("stamina", 0) > 0:
            pet["stamina"] = round(pet["stamina"] + play["stamina"], 2)
        # 健康净变化：收益《健康度》(可负) − 消耗《消耗健康度》
        pet["health"] = round(
            pet["health"] + play.get("health", 0) - play["cost"].get("health", 0), 2)
        lvl_msg = self._apply_exp(pet)
        self._clamp_attrs(pet)

        bonus = ""
        money_gain = 0
        if pet.get("money_event_count", 0) < self.money_event_max_per_day and random.random() < self.money_event_chance:
            self._add_coins(data, key, self.money_event_gain, "玩耍捡到钱")
            pet["money_event_count"] = int(pet.get("money_event_count", 0)) + 1
            money_gain = self.money_event_gain
            bonus = f"\n🍀 触发「捡到钱了」事件，金币 +{self.money_event_gain}！\n{self._coin_line(data, key)}"

        # 进入冷却（打工/玩耍共用计时器）
        pet["busy_until"] = now_ts + int(play["time"]) * 60
        pet["busy_start"] = now_ts
        pet["busy_activity"] = "玩耍"
        pet["busy_item"] = play["name"]
        pet["_progress_done_notified"] = False  # 新一轮进度条开始，重置「满后第一次响应」标记
        changes = {a: round(pet[a] - before[a], 2) for a in ATTR_LABELS if abs(pet[a] - before[a]) > 1e-9}
        pet["last_activity"] = {
            "msg": f"{pet['name']} 去「{play['name']}」玩耍成功！",
            "changes": changes,
            "reason": f"玩耍「{play['name']}」",
            "coins": money_gain,
            "exp": play["exp"],
            "act": "玩耍",
            "ts": now_ts,
            "shown": False,   # 即时消息：本次响应显示一次后置 True
        }
        self._save(data)
        cd = f"（冷却 {int(play['time'])} 分钟）" if play["time"] > 0 else ""
        gain = f"经验 +{play['exp']:.1f}，😊 心情 +{play['mood']:.1f}"
        net_stamina = play.get("stamina", 0) - play["cost"].get("stamina", 0)
        if net_stamina != 0:
            gain += f"，💪 体力 {net_stamina:+.1f}"
        net_health = play.get("health", 0) - play["cost"].get("health", 0)
        if net_health != 0:
            gain += f"，❤️ 健康 {net_health:+.1f}"
        text = (f"🎾 {name} 的宠物去「{play['name']}」玩耍完成！{cd}\n"
                f"🐾 {gain}{lvl_msg}{bonus}\n"
                f"{self._pet_state_snippet(pet)}")
        # 2.0.0 消息合并：玩耍结果合入宠物指令图片的预留位（即时消息显示一次）+ 底部卡片
        # 本次产生新的状态变化（卡片1高亮）且发生玩耍变动（卡片2高亮）
        img = self._safe_render_pet(
            name, key, data, pet,
            slot_msg=f"{pet['name']} 去「{play['name']}」玩耍成功！{lvl_msg}".strip(),
            changes=changes,
            reason=f"玩耍「{play['name']}」",
            coins_delta=money_gain if money_gain else None,
            exp_delta=play["exp"],
            changes_fresh=True,
            card2_hl=True,
        )
        # 即时消息已显示（图片或文本回退均视为显示一次）→ 标记，宠物指令不再重复显示
        if pet.get("last_activity"):
            pet["last_activity"]["shown"] = True
            self._save(data)
        return img if img is not None else text

    def _play_list(self, event=None):
        cfg = self._load_config()
        if not cfg["plays"]:
            return "后台还没有配置玩耍项目（请管理员编辑 后台.txt）。"
        pet = None
        coins = None
        if event is not None:
            data = self._load()
            key = self._user_key(event)
            pet = data.get("pets", {}).get(key)
            coins = self._coins_of(data, key)
        img = self._render_work_play_image("玩耍", event.get_sender_name(), pet, cfg["plays"], coins)
        if img is not None:
            return img
        lines = ["发送「玩耍 <名称>」开始", ""]
        for p in cfg["plays"]:
            lines.append(f"· {p['name']}：{p['desc']}｜要求 Lv.{int(p['min_level'])}+ / 健康 {p['min_health']:.0f}+ / 心情 {p['min_mood']:.0f}+｜耗时 {p['time']:.0f}分｜经验 +{p['exp']:.0f} 心情 +{p['mood']:.0f}")
        img = self._render_text_image("玩耍列表", lines)
        if img is not None:
            return img
        return "\n".join(["🎾 玩耍列表（发送「玩耍 <名称>」开始）："] + lines)

    def _safe_render_pet(self, name, uid, data, pet, **kwargs):
        """安全渲染宠物总览图：异常时返回 None（调用方回退文本），避免渲染 bug 阻断指令"""
        try:
            return self._render_pet_status_image(name, uid, data, pet, **kwargs)
        except Exception as e:
            logger.error(f"[插件] 渲染宠物图片异常: {e}")
            return None

    def _pet_rank_text(self, uid, data):
        """宠物排行榜的排行积分文本（标题右侧）"""
        try:
            entries = self._rank_entries("pet", data)
        except Exception:
            entries = []
        for i, (score, euid, _) in enumerate(entries, 1):
            if str(euid) == str(uid):
                return f"🐾 宠物榜 第{i}名 · {self._fmt_score(score)}分"
        return "🐾 宠物榜未上榜"

    def _render_pet_status_image(self, name, uid, data, pet, slot_msg=None,
                                 changes=None, reason=None, coins_delta=None, exp_delta=None,
                                 changes_fresh=False, progress_done=False, card2_hl=False):
        """「宠物」指令图片（2.0.0 新模板）：
        标题(<用户昵称>的宠物 + 右侧宠物排行数据) / 预留位(红 #C00000，无内容则忽略) / 分割线 /
        宠物状态卡片（名称/状态/等级/经验/升级进度条 + 五项属性条）/ 底部双卡：
        - 底部卡片1（宽）：最近一次五大状态属性变化 + 变化原因 + 原打工/玩耍进度条模块
        - 底部卡片2（窄）：宠物正在进行的工作/玩耍项目 + 金币/经验变化 + 预计完成时间
        高亮规则：
        - 卡片1：仅在本次响应产生了新的状态变化（changes_fresh）或 进度条满（空闲）后的第一次响应
          （progress_done，内部自动判定）时黄色高亮
        - 卡片2：仅在本次响应发生了 打工/玩耍 变动（card2_hl）时黄色高亮
        预留位消息为即时消息：有效显示次数 = 1，仅产生新变化的那次响应显示。"""
        Image, ImageDraw = _ensure_pillow()
        if Image is None:
            return None
        # 字号语义：标题 32 / 宠物名 26（大两号）/ 状态 18 / 正文 20 / 经验 16 / 属性名 18 / 提示 16 / 描述 18
        # 附加 14/12 小字号：底部卡片单行自适应（内容超宽时逐级缩小，保证不触发自动换行）
        fonts = _load_fonts(32, 26, 18, 20, 16, 18, 16, 18, 14, 12)
        if fonts is None:
            return None
        (title_font, pet_name_font, status_font, lv_font,
         exp_font, attr_font, hint_font, desc_font) = fonts[:8]
        small_font, tiny_font = fonts[8], fonts[9]

        now_ts = datetime.now().timestamp()
        busy_until = self._pet_busy_until(pet)
        busy = now_ts < busy_until
        act = pet.get("busy_activity", "")
        status = "忙碌中" if busy else "空闲中"

        sat_max, thr_max, sta_max, mood_max = self._attr_max(pet["health"])
        # 属性条数据：(标签, 当前值, 最大值, 提示词)
        attrs = [
            ("饱食度", pet["satiety"], sat_max, "宠物饿了"),
            ("口渴值", pet["thirst"], thr_max, "宠物渴了"),
            ("心情值", pet["mood"], mood_max, "宠物不开心"),
            ("体力值", pet["stamina"], sta_max, "宠物累了"),
            ("健康度", pet["health"], PET_MAX_HEALTH, "宠物生病了"),
        ]

        pad = 20
        title_h = 52
        inner = 10
        name_row_h = 34
        lv_row_h = 28
        bar_h = 26            # 普通进度条行高（条高 16 = 升级/空闲进度条）
        bar_h_px = 16
        attr_label_h = 22     # 属性名行高
        attr_bar_h = 16       # 属性条行高（条高 = 普通进度条的 30% ≈ 5px）
        attr_bar_px = max(4, int(bar_h_px * 0.3))
        rule_h = 18
        line_h = 26
        bottom_gap = 14       # 宠物状态卡片 与 底部双卡 之间的间距

        # 2.0.0：整张图片加宽为原宽度（640）的 110%，各模块布局与卡片 1/2 宽度比例不变
        width = 704
        content_w = width - pad * 2

        tw = _text_measurer()
        if tw is None:
            return None
        wrap = _make_wrapper(tw, content_w)

        # 升级进度（新经验体系）
        lv, got_exp, need_exp = self._pet_exp_progress(float(pet.get("exp", 0.0)))
        exp_ratio = min(1.0, got_exp / need_exp) if need_exp > 0 else 1.0
        exp_text = f"经验 {got_exp:.0f}/{need_exp:.0f}"

        # ---------- 预留位（红 #C00000，无内容忽略） ----------
        slot_lines = []
        slot_h = 0
        if slot_msg:
            for ln in str(slot_msg).split("\n"):
                for wl in wrap(ln, desc_font, content_w):
                    slot_lines.append(wl)
            slot_h = len(slot_lines) * line_h + 6

        # ---------- 最近变化 / 原因（底部卡片1） ----------
        # 只有显式未提供变化（None）时才回退到每日结算；显式空变化（如 经验球）保持「无变化」
        if changes is None:
            ls = pet.get("last_settle") or {}
            changes = {}
            for k, a in (("satiety_d", "satiety"), ("thirst_d", "thirst"),
                         ("stamina_d", "stamina"), ("mood_d", "mood"), ("health_d", "health")):
                if ls.get(k):
                    changes[a] = ls[k]
        if not reason:
            reason = "昨晚结算" if pet.get("last_settle") else "暂无变化记录"
        # 五属性变化片段：饱食/口渴/体力/心情/健康 同时显示；
        # 有变化用深色，无变化用灰色隐去；尽量一行（提示字号+半角空格），超宽自动换行
        chg_segs = []
        for i, a in enumerate(("satiety", "thirst", "stamina", "mood", "health")):
            try:
                v = float(changes.get(a, 0) or 0)
            except (TypeError, ValueError):
                v = 0.0
            txt = f"{ATTR_SHORT[a]}{v:+.1f}"
            if i < 4:
                txt += " "  # 半角空格分隔（节省宽度，尽量一行）
            color = (60, 60, 60) if abs(v) > 1e-9 else (190, 190, 190)
            chg_segs.append((txt, color))

        # 空闲进度条（原打工/玩耍进度条模块，移入底部卡片1）
        idle_ratio = 1.0
        if busy:
            start = float(pet.get("busy_start", 0) or 0)
            total = busy_until - start if busy_until > start else 1.0
            idle_ratio = min(0.9, max(0.05, 1.0 - (busy_until - now_ts) / total))  # 忙碌中进度不满
        idle_color = (146, 208, 80) if not busy else (255, 192, 0)

        # ---------- 底部卡片2 数据（当前项目 / 金币经验变化 / 预计完成时刻 / 状态档位） ----------
        if busy:
            item = pet.get("busy_item", "")
            cur_txt = f"{('打工' if act == '打工' else '玩耍')}「{item}」" if item else ("打工中" if act == "打工" else "玩耍中")
            try:
                _done = datetime.fromtimestamp(busy_until)
                if _done.date() == date.today():
                    eta_txt = f"预计 {_done.strftime('%H:%M')} 完成"
                else:
                    eta_txt = f"预计 {_done.strftime('%m-%d %H:%M')} 完成"
            except Exception:
                eta_txt = "预计稍后完成"
        else:
            cur_txt = "空闲中"
            eta_txt = "—"
        reward_txt = "金币/经验 无变化"
        if coins_delta or exp_delta:
            parts = []
            if coins_delta:
                parts.append(f"金币{int(coins_delta):+}")
            if exp_delta:
                parts.append(f"经验{exp_delta:+.1f}")
            reward_txt = " ".join(parts)  # 紧凑格式（保证卡片2 一行放下，不触发自动换行）
        # 进度条满（空闲）后的第一次响应：内部自动判定并标记（调用方随后 _save）
        if not progress_done:
            _bu = float(pet.get("busy_until", 0) or 0)
            if _bu > 0 and now_ts >= _bu and not pet.get("_progress_done_notified"):
                progress_done = True
                pet["_progress_done_notified"] = True
        # 当前宠物所处的状态档位（饱食/口渴/心情 取最差档，1~4）
        tier = self._worst_tier(pet["satiety"], pet["thirst"], pet["mood"])
        tier_txt = f"状态档位：{tier}/4"
        tier_color = (192, 0, 0) if tier >= 3 else (110, 110, 110)

        # ---------- 描述行（沿用原底部文案，显示在卡片1进度条下方） ----------
        if busy:
            act_label = "打工" if act == "打工" else "玩耍"
            desc = f"{pet.get('name', '宠物')}正在{act_label}"
        elif any(attrs[i][1] < (50 if i == 0 else (60 if i == 1 else (40 if i == 2 else (20 if i == 3 else 40))))
                 for i in range(5)):
            desc = random.choice([
                f"{pet.get('name', '宠物')}看起来不太舒服，快照料一下吧～",
                f"{pet.get('name', '宠物')}有点不舒服，喂食 / 饮水 / 陪伴一下吧～",
                f"{pet.get('name', '宠物')}状态不佳，需要你的照顾～",
            ])
        elif pet.get("guard"):
            desc = random.choice([
                f"闲来没事，{pet.get('name', '宠物')}正在巡逻你的农场",
                f"{pet.get('name', '宠物')}尽职尽责，正在农场周围巡视～",
            ])
        else:
            desc = random.choice([
                f"{pet.get('name', '宠物')}正在悠闲地晒太阳～",
                f"{pet.get('name', '宠物')}精神饱满，随时可以出发！",
                f"{pet.get('name', '宠物')}正在开心地打盹～",
            ])

        # ---------- 布局与高度 ----------
        pet_card_h = inner * 2 + name_row_h + lv_row_h + bar_h + len(attrs) * (attr_label_h + attr_bar_h)

        # 卡片 1/2 宽度比例与原布局保持一致（按 110% 加宽后等比换算）：
        # 原布局 content=600：卡片1=423 / 卡片2=165 / 间距=12 → 等比放大到 content=664
        card_gap = 13
        card2_w = 183                      # 卡片2 收窄（等比）
        card1_w = content_w - card2_w - card_gap  # 卡片1 加宽（等比）
        c1_inner = 8

        # 多色分段文本：按片段测量换行行数 / 逐段绘制（支持自动换行）
        def seg_lines_n(segs, max_w, font):
            n = 1
            row_w = 0.0
            for t, _ in segs:
                w = tw(t, font)
                if row_w > 0 and row_w + w > max_w:
                    n += 1
                    row_w = w
                else:
                    row_w += w
            return n

        def draw_segs(d, x0, y0, max_w, segs, font, lh):
            y = y0
            row = []
            row_w = 0.0
            for t, c in segs:
                w = tw(t, font)
                if row and row_w + w > max_w:
                    x = x0
                    for tt, cc in row:
                        d.text((int(x), y), tt, font=font, fill=cc)
                        x += tw(tt, font)
                    y += lh
                    row = []
                    row_w = 0.0
                row.append((t, c))
                row_w += w
            if row:
                x = x0
                for tt, cc in row:
                    d.text((int(x), y), tt, font=font, fill=cc)
                    x += tw(tt, font)
                y += lh
            return y

        # 剩余时间：显示在进度条右下方（进度条外部）；空闲时不显示
        remain_txt = f"剩余 {self._fmt_duration(busy_until - now_ts)}" if busy else ""
        desc_avail = card1_w - c1_inner * 2
        remain_same_row = bool(remain_txt) and (tw(desc, hint_font) + 16 + tw(remain_txt, hint_font) <= desc_avail)
        tail_rows = 1 if (not remain_txt or remain_same_row) else 2  # 描述行（+剩余时间行）

        # 单行自适应字号：内容超宽时逐级缩小字号，保证不触发自动换行；
        # 最小字号仍放不下（超长自定义名等）→ 截断加省略号，始终单行
        def fit_rows(txt, fonts_chain, avail):
            for f in fonts_chain:
                if tw(txt, f) <= avail:
                    return [(txt, f)]
            smallest = fonts_chain[-1]
            cut = txt
            while cut and tw(cut + "…", smallest) > avail:
                cut = cut[:-1]
            return [(cut + "…", smallest)] if cut else [(txt, smallest)]

        # 卡片1：变化行(多色,逐级缩字号保证一行) + 原因行(自适应) + 进度条 + 描述/剩余行
        c1_avail = card1_w - c1_inner * 2
        c1_chg_font = hint_font
        for f in (hint_font, small_font, tiny_font):
            if seg_lines_n(chg_segs, c1_avail, f) <= 1:
                c1_chg_font = f
                break
        c1_chg_n = seg_lines_n(chg_segs, c1_avail, c1_chg_font)  # 正常=1（保证不换行）
        c1_reason_rows = fit_rows(f"原因：{reason}", (hint_font, small_font, tiny_font), c1_avail)
        c1_h = c1_inner * 2 + (c1_chg_n + len(c1_reason_rows) + tail_rows) * line_h + bar_h
        # 卡片2：项目行 + 奖励行 + 预计完成行 + 状态档位行（均单行自适应字号，不自动换行）
        c2_avail = card2_w - c1_inner * 2
        c2_cur_rows = fit_rows(cur_txt, (desc_font, hint_font, small_font, tiny_font), c2_avail)
        c2_reward_rows = fit_rows(reward_txt, (hint_font, small_font, tiny_font), c2_avail)
        c2_eta_rows = fit_rows(eta_txt, (hint_font, small_font, tiny_font), c2_avail)
        c2_tier_rows = fit_rows(tier_txt, (hint_font, small_font, tiny_font), c2_avail)
        c2_h = c1_inner * 2 + (len(c2_cur_rows) + len(c2_reward_rows)
                               + len(c2_eta_rows) + len(c2_tier_rows)) * line_h
        bottom_h = max(c1_h, c2_h)

        height = pad * 2 + title_h + slot_h + rule_h + pet_card_h + bottom_gap + bottom_h

        img = Image.new("RGB", (width, height), (255, 255, 255))
        d = ImageDraw.Draw(img)
        y = pad

        # 标题：<用户昵称>的宠物 + 右侧 宠物排行数据
        rank_text = self._pet_rank_text(uid, data) if data else ""
        title_line = f"{name} 的宠物"
        if rank_text and tw(title_line, title_font) + 24 + tw(rank_text, desc_font) <= content_w:
            d.text((pad, y), title_line, font=title_font, fill=(20, 20, 20))
            d.text((int(width - pad - tw(rank_text, desc_font)), y + 14), rank_text,
                   font=desc_font, fill=(90, 90, 90))
        else:
            d.text((pad, y), title_line, font=title_font, fill=(20, 20, 20))
            if rank_text:
                d.text((pad, y + 28), rank_text, font=desc_font, fill=(90, 90, 90))
                title_h = max(title_h, 52 + 22)
        y += title_h

        # 预留位（红 #C00000）
        if slot_lines:
            for wl in slot_lines:
                d.text((pad, y), wl, font=desc_font, fill=(192, 0, 0))
                y += line_h
            y += 6

        # 分割线
        d.line([(pad, y), (width - pad, y)], fill=(200, 200, 200), width=2)
        y += rule_h

        # ---------- 宠物状态卡片（横跨整行） ----------
        d.rectangle([pad, y, width - pad, y + pet_card_h], outline=(205, 205, 205), width=1)
        yy = y + inner
        # 行1：宠物名称(大两号,左) + 状态(小一号,右)
        d.text((int(pad + inner), yy), pet.get("name", "宠物"), font=pet_name_font, fill=(20, 20, 20))
        d.text((int(width - pad - inner - tw(status, status_font)), yy + 10),
               status, font=status_font, fill=(150, 150, 150))
        yy += name_row_h
        # 行2：等级(左) + 经验(右,小两号)
        d.text((int(pad + inner), yy), f"Lv.{lv}", font=lv_font, fill=(40, 40, 40))
        d.text((int(width - pad - inner - tw(exp_text, exp_font)), yy + 6),
               exp_text, font=exp_font, fill=(110, 110, 110))
        yy += lv_row_h
        # 行3：升级进度条（普通进度条高度，含百分比）
        bar_y = yy + (bar_h - bar_h_px) // 2
        d.rectangle([pad + inner, bar_y, width - pad - inner, bar_y + bar_h_px], outline=(200, 200, 200), width=1)
        if exp_ratio > 0:
            d.rectangle([pad + inner + 1, bar_y + 1,
                         int(pad + inner + 1 + (content_w - 2 * inner - 2) * exp_ratio), bar_y + bar_h_px - 1],
                        fill=(52, 168, 83))
        pct_text = f"{int(exp_ratio * 100)}%"
        d.text((int(width - pad - inner - tw(pct_text, exp_font) - 4), bar_y - 4),
               pct_text, font=exp_font, fill=(40, 40, 40))
        yy += bar_h
        # 行4+：宠物属性条区
        attr_w = int(content_w * 0.55)  # 属性条宽度（剩余右侧放状态解释）
        for label, val, amax, hint in attrs:
            # 属性名 + 当前值/最大值
            t = f"{label} {val:.0f}/{amax:.0f}"
            d.text((int(pad + inner), yy), t, font=attr_font, fill=(60, 60, 60))
            yy += attr_label_h
            # 属性条颜色判定（1.7.6：饱/渴/心 采用第三档标准 = 状态低）
            if label == "饱食度":
                red = val < 50
            elif label == "口渴值":
                red = val < 60
            elif label == "心情值":
                red = val < 40
            elif label == "体力值":
                red = val < 20
            else:  # 健康度
                red = val < 40
            green = (amax - val) < 20 and pet["health"] >= 41
            bar_color = (192, 0, 0) if red else ((146, 208, 80) if green else (51, 51, 51))
            # 属性条（高度 = 普通进度条的 30%）
            ay = yy + (attr_bar_h - attr_bar_px) // 2
            ratio = max(0.0, min(1.0, val / amax)) if amax > 0 else 0.0
            d.rectangle([pad + inner, ay, pad + inner + attr_w, ay + attr_bar_px],
                        outline=(200, 200, 200), width=1)
            if ratio > 0:
                d.rectangle([pad + inner + 1, ay + 1,
                             int(pad + inner + 1 + (attr_w - 2) * ratio), ay + attr_bar_px - 1],
                            fill=bar_color)
            # 状态解释：仅红色时固定显示在整个属性条区域的右侧（不随填充比例移动）
            if red:
                d.text((int(pad + inner + attr_w + 6), yy + 1), hint, font=hint_font, fill=(192, 0, 0))
            yy += attr_bar_h
        y += pet_card_h
        y += bottom_gap  # 状态卡片与底部双卡保持间距

        # ---------- 底部双卡：卡片1(宽) + 卡片2(窄) ----------
        YELLOW = (255, 230, 153)  # #FFE699 变动高亮
        # 卡片1：五属性变化(多色分段,自动换行) + 原因 + 打工/玩耍进度条 + 描述/剩余时间
        # 高亮条件：本次产生了新的状态变化，或 进度条满（空闲）后的第一次响应
        c1_fill = YELLOW if (changes_fresh or progress_done) else None
        d.rectangle([pad, y, pad + card1_w, y + bottom_h],
                    fill=c1_fill, outline=(205, 205, 205), width=1)
        yy = y + c1_inner
        yy = draw_segs(d, pad + c1_inner, yy, c1_avail, chg_segs, c1_chg_font, line_h)
        for wl, f in c1_reason_rows:
            d.text((int(pad + c1_inner), yy), wl, font=f, fill=(110, 110, 110))
            yy += line_h
        # 打工/玩耍进度条（原模块）
        by = yy + (bar_h - bar_h_px) // 2
        d.rectangle([pad + c1_inner, by, pad + card1_w - c1_inner, by + bar_h_px],
                    outline=(200, 200, 200), width=1)
        if idle_ratio > 0:
            d.rectangle([pad + c1_inner + 1, by + 1,
                         int(pad + c1_inner + 1 + (card1_w - 2 * c1_inner - 2) * idle_ratio), by + bar_h_px - 1],
                        fill=idle_color)
        yy += bar_h
        # 描述（左）+ 剩余时间（进度条右下方、进度条外部，右对齐）
        if remain_txt and remain_same_row:
            d.text((int(pad + c1_inner), yy), desc, font=hint_font, fill=(90, 90, 90))
            d.text((int(pad + card1_w - c1_inner - tw(remain_txt, hint_font)), yy),
                   remain_txt, font=hint_font, fill=(110, 110, 110))
        else:
            d.text((int(pad + c1_inner), yy), desc, font=hint_font, fill=(90, 90, 90))
            if remain_txt:
                d.text((int(pad + card1_w - c1_inner - tw(remain_txt, hint_font)), yy + line_h),
                       remain_txt, font=hint_font, fill=(110, 110, 110))
        # 卡片2：当前项目 + 金币/经验 + 预计完成时刻 + 状态档位
        # 高亮条件：本次响应发生了 打工/玩耍 变动
        c2_x = pad + card1_w + card_gap
        c2_fill = YELLOW if card2_hl else None
        d.rectangle([c2_x, y, width - pad, y + bottom_h],
                    fill=c2_fill, outline=(205, 205, 205), width=1)
        yy = y + c1_inner
        for wl, f in c2_cur_rows:
            d.text((int(c2_x + c1_inner), yy), wl, font=f, fill=(60, 60, 60))
            yy += line_h
        for wl, f in c2_reward_rows:
            d.text((int(c2_x + c1_inner), yy), wl, font=f, fill=(150, 90, 0))
            yy += line_h
        for wl, f in c2_eta_rows:
            d.text((int(c2_x + c1_inner), yy), wl, font=f, fill=(110, 110, 110))
            yy += line_h
        for wl, f in c2_tier_rows:
            d.text((int(c2_x + c1_inner), yy), wl, font=f, fill=tier_color)
            yy += line_h

        return _save_temp_image(img, "_pet_", "宠物状态")

    def _render_work_play_image(self, kind, name, pet, items, coins=None):
        """打工/玩耍列表图片（1.7.1 布局）：
        标题(用户名称) + 宠物信息卡片(横跨整行) + 分割线 + 内容卡片（每行 WORK/PLAY_CARD_COLS 个）。
        宠物信息卡：宠物名称(大两号)+状态(小一号) / 等级+经验值(小两号,两端对齐) / 升级进度条(含百分比) / 属性(过低红色)。
        内容卡：名称(大三号,居左)+时间(居右) / 描述 / 条件(如有) / 消耗 / 卡片内分割线 / 报酬或变更(红 #C00000,右,大一号)。
        卡片高度自适应（按换行后行数），同行取最高；不能打工/玩耍的卡片灰(#D9D9D9)。
        pet 可为 None（无宠物）：宠物信息卡显示「还没有宠物」提示，内容卡全部灰卡。
        coins（1.7.6）：不为 None 时在标题下方显示金币余额行。"""
        try:
            return self._render_work_play_image_inner(kind, name, pet, items, coins)
        except Exception as e:
            logger.error(f"[插件] 渲染{kind}列表图片异常: {e}")
            return None

    def _render_work_play_image_inner(self, kind, name, pet, items, coins=None):
        """打工/玩耍列表图片实际渲染（异常由外层捕获并回退文本）"""
        Image, ImageDraw = _ensure_pillow()
        if Image is None:
            raise ImportError("Pillow 不可用")
        # 字号语义：标题 32 / 宠物名 26 / 状态 18 / 正文 20 / 经验 16 / 属性 18 / 内容名 28 / 描述 20 / 报酬 22
        fonts = _load_fonts(32, 26, 18, 20, 16, 18, 28, 20, 22)
        if fonts is None:
            raise RuntimeError(f"字体加载失败: {FONT_FILE}")
        (title_font, pet_name_font, status_font, lv_font, exp_font,
         attr_font, item_name_font, body_font, price_font) = fonts

        has_pet = pet is not None
        now_ts = datetime.now().timestamp()
        if has_pet:
            # 宠物忙碌状态：打工/玩耍共用冷却计时器，任一忙碌即忙碌（与灰卡判定一致）
            busy = now_ts < self._pet_busy_until(pet)
            status = "虚弱中" if pet.get("weak") else ("忙碌中" if busy else "空闲中")
            sat_max, thr_max, sta_max, mood_max = self._attr_max(pet["health"])
            # 属性展示（过低红色高亮，与「宠物」指令属性条阈值一致，1.7.6 第三档标准）：饱食<50 / 口渴<60 / 体力<20 / 心情<40 / 健康<40
            attrs = [
                ("饱食", pet["satiety"], sat_max, pet["satiety"] < 50),
                ("口渴", pet["thirst"], thr_max, pet["thirst"] < 60),
                ("体力", pet["stamina"], sta_max, pet["stamina"] < 20),
                ("心情", pet["mood"], mood_max, pet["mood"] < 40),
                ("健康", pet["health"], PET_MAX_HEALTH, pet["health"] < 40),
            ]
        else:
            status = "未解锁"

        pad = 20
        title_h = 52
        coin_h = 28  # 1.7.6：标题下方金币余额行高度
        rule_h = 22          # 宠物卡与内容卡之间的分割线
        gap = 12
        inner = 10
        cols = int(globals().get("WORK_CARD_COLS" if kind == "打工" else "PLAY_CARD_COLS", 2))
        cols = max(1, min(4, cols))
        card_w = int(globals().get("WORK_PLAY_CARD_WIDTH", 522))  # 默认 522 = 原 290 的 180%
        card_w = max(290, min(800, card_w))
        width = pad * 2 + card_w * cols + gap * (cols - 1)
        pet_w = width - pad * 2  # 宠物信息卡宽度 = 内容卡一行布局总宽度

        name_row_h = 34
        lv_row_h = 28
        bar_h = 26
        attr_row_h = 24
        line_h = 26
        item_name_h = 40
        rule_card_h = 10
        price_h = 30
        n_pad = int(globals().get("SHOP_PRICE_PAD", 4))

        tw = _text_measurer()
        if tw is None:
            raise RuntimeError("Pillow 不可用，无法测量文本宽度")

        wrap = _make_wrapper(tw, 0)

        # ---------- 宠物信息卡片 ----------
        pet_content_w = pet_w - inner * 2
        if has_pet:
            # 属性行：每行 3 个（最后一行 2 个）
            attr_rows = [attrs[:3], attrs[3:]]
            pet_card_h = inner * 2 + name_row_h + lv_row_h + bar_h + len(attr_rows) * attr_row_h
            # 升级进度：新经验体系（所需经验 = 当前等级 × 100）
            lv, got_exp, need_exp = self._pet_exp_progress(float(pet.get("exp", 0.0)))
            exp_ratio = min(1.0, got_exp / need_exp) if need_exp > 0 else 1.0
            exp_text = f"经验 {got_exp:.0f}/{need_exp:.0f}"
        else:
            attr_rows = []
            # 无宠物：名称行 + 提示行
            pet_card_h = inner * 2 + name_row_h + lv_row_h
            exp_ratio = 0.0
            exp_text = ""

        # ---------- 内容卡片预计算 ----------
        def can_do(item):
            if not has_pet:
                return False
            if now_ts < self._pet_busy_until(pet):  # 打工/玩耍共用冷却计时器
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

        def cond_text(item):
            parts = []
            if item["min_level"] > 0:
                parts.append(f"Lv.{int(item['min_level'])}+")
            if item["min_health"] > 0:
                parts.append(f"健康 {item['min_health']:.0f}+")
            if item["min_mood"] > 0:
                parts.append(f"心情 {item['min_mood']:.0f}+")
            return "条件：" + " / ".join(parts) if parts else ""

        def cost_text(item):
            parts = []
            for attr, cost in item["cost"].items():
                if cost > 0:
                    parts.append(f"{ATTR_SHORT[attr]}-{cost:.0f}")
            return "消耗：" + " ".join(parts) if parts else "消耗：无"

        def need_text(item):
            # 玩耍卡「需要」行：条件下一行显示需要的 饱食度/口渴值/体力（固定顺序中文全称）
            parts = []
            for attr in ("satiety", "thirst", "stamina", "health"):
                cost = item["cost"].get(attr, 0)
                if cost > 0:
                    parts.append(f"{ATTR_LABELS[attr]} {cost:.0f}")
            return "需要：" + " ".join(parts) if parts else "需要：无"

        def reward_text(item):
            if kind == "打工":
                s = f"金币 +{int(item['coins'])}"
                if item["exp"] > 0:
                    s += f" · 经验 +{item['exp']:.0f}"
                return s
            else:
                s = f"经验 +{item['exp']:.0f}"
                if item["mood"] > 0:
                    s += f" · 心情 +{item['mood']:.0f}"
                # 红字不含体力（体力需求在「需要」行体现）；健康按净变化显示（可负）
                net_health = item.get("health", 0) - item["cost"].get("health", 0)
                if net_health != 0:
                    s += f" · 健康 {net_health:+.0f}"
                return s

        plans = []  # (item, ok, [行], 高度)
        for it in items:
            ok = can_do(it)
            content_w = card_w - inner * 2
            rows = []
            rows.append(("pair", it["name"], f"{int(it['time'])} 分钟"))
            for wl in wrap(it["desc"], body_font, content_w):
                rows.append(("plain", wl, ""))
            ct = cond_text(it)
            if ct:
                for wl in wrap(ct, body_font, content_w):
                    rows.append(("plain", wl, ""))
            if kind == "打工":
                for wl in wrap(cost_text(it), body_font, content_w):
                    rows.append(("plain", wl, ""))
            else:
                # 玩耍：要求（条件）下一行显示需要的 饱食度/口渴值/体力（中文全称）
                for wl in wrap(need_text(it), body_font, content_w):
                    rows.append(("plain", wl, ""))
            rows.append(("rule", "", ""))
            rows.append(("price", reward_text(it), ""))
            h_before = inner * 2 + item_name_h + (len(rows) - 3) * line_h  # 去掉 rule/price 后的文本行数
            h = h_before + rule_card_h + price_h + n_pad
            plans.append((it, ok, rows, h))

        # ---------- 总高度 ----------
        rows_n = (len(plans) + cols - 1) // cols if plans else 0
        cards_h = 0
        for r in range(rows_n):
            group = plans[r * cols:(r + 1) * cols]
            cards_h += max(p[3] for p in group) + gap
        if cards_h > 0:
            cards_h -= gap
        height = pad * 2 + title_h + (coin_h if coins is not None else 0) + pet_card_h + rule_h + cards_h

        img = Image.new("RGB", (width, height), (255, 255, 255))
        d = ImageDraw.Draw(img)
        y = pad

        # 标题：<用户名称>
        d.text((pad, y), name, font=title_font, fill=(20, 20, 20))
        y += title_h
        # 1.7.6：标题下方金币余额
        if coins is not None:
            d.text((pad, y), f"💰 金币余额：{coins}", font=attr_font, fill=(140, 90, 0))
            y += coin_h

        # ---------- 宠物信息卡片（横跨整行） ----------
        d.rectangle([pad, y, pad + pet_w, y + pet_card_h], outline=(205, 205, 205), width=1)
        yy = y + inner
        # 行1：宠物名称(大两号,左) + 状态(小一号,右)
        pet_disp = pet.get("name", "宠物") if has_pet else "还没有宠物"
        d.text((int(pad + inner), yy), pet_disp, font=pet_name_font, fill=(20, 20, 20))
        d.text((int(pad + pet_w - inner - tw(status, status_font)), yy + 10),
               status, font=status_font, fill=(150, 150, 150))
        yy += name_row_h
        if has_pet:
            # 行2：等级(左) + 经验值(小两号,右)，两端对齐（本行宽 = 内容宽）
            d.text((int(pad + inner), yy), f"Lv.{pet['level']}", font=lv_font, fill=(40, 40, 40))
            d.text((int(pad + pet_w - inner - tw(exp_text, exp_font)), yy + 6),
                   exp_text, font=exp_font, fill=(110, 110, 110))
            yy += lv_row_h
            # 行3：升级进度条（宽度与上一行相等 = 内容宽，含百分比）
            bar_w = pet_content_w
            bar_y = yy + (bar_h - 14) // 2
            d.rectangle([pad + inner, bar_y, pad + inner + bar_w, bar_y + 14], outline=(200, 200, 200), width=1)
            if exp_ratio > 0:
                d.rectangle([pad + inner + 1, bar_y + 1,
                             int(pad + inner + 1 + (bar_w - 2) * exp_ratio), bar_y + 13],
                            fill=(52, 168, 83))
            pct_text = f"{int(exp_ratio * 100)}%"
            d.text((int(pad + pet_w - inner - tw(pct_text, exp_font) - 4), bar_y - 4),
                   pct_text, font=exp_font, fill=(40, 40, 40))
            yy += bar_h
            # 行4+：宠物属性（过低红色高亮）
            for group in attr_rows:
                gx = pad + inner
                for an, av, amax, low in group:
                    t = f"{an} {av:.0f}/{amax:.0f}"
                    d.text((int(gx), yy), t, font=attr_font,
                           fill=(192, 0, 0) if low else (70, 70, 70))
                    gx += tw(t, attr_font) + 22
                yy += attr_row_h
        else:
            # 无宠物：提示行
            d.text((int(pad + inner), yy), "发送「解锁宠物」领养一只吧", font=lv_font, fill=(140, 90, 0))
            yy += lv_row_h
        y += pet_card_h

        # 分割线
        d.line([(pad, y), (width - pad, y)], fill=(200, 200, 200), width=2)
        y += rule_h

        # ---------- 内容卡片 ----------
        for r in range(rows_n):
            group = plans[r * cols:(r + 1) * cols]
            gh = max(p[3] for p in group)
            for j, (it, ok, rows, _) in enumerate(group):
                x0 = pad + j * (card_w + gap)
                bg = (217, 217, 217) if not ok else (255, 255, 255)
                d.rectangle([x0, y, x0 + card_w, y + gh], fill=bg, outline=(200, 200, 200), width=1)
                yy = y + inner
                for row in rows:
                    kind_row = row[0]
                    if kind_row == "pair":
                        d.text((int(x0 + inner), yy), row[1], font=item_name_font, fill=(20, 20, 20))
                        d.text((int(x0 + card_w - inner - tw(row[2], body_font)), yy + 12),
                               row[2], font=body_font, fill=(150, 150, 150))
                        yy += item_name_h
                    elif kind_row == "plain":
                        for wl in wrap(row[1], body_font, card_w - inner * 2):
                            d.text((int(x0 + inner), yy), wl, font=body_font, fill=(70, 70, 70))
                            yy += line_h
                    elif kind_row == "rule":
                        yy += rule_card_h // 2
                        d.line([(x0 + 8, yy), (x0 + card_w - 8, yy)], fill=(200, 200, 200), width=1)
                        yy += rule_card_h // 2
                    elif kind_row == "price":
                        py = y + gh - n_pad - price_h
                        d.text((int(x0 + card_w - inner - tw(row[1], price_font)), py),
                               row[1], font=price_font, fill=(192, 0, 0))
                        break
            y += gh + gap

        return _save_temp_image(img, "_wp_", f"{kind}列表")

    def _handle_shop(self, event: AstrMessageEvent) -> str:
        cfg = self._load_config()
        if not cfg["shop"]:
            return "商店暂无商品（请管理员编辑 后台.txt）。"
        # 按类型分组（保持配置顺序），组内商品按价格升序（价格越高位置越靠后）
        categories = []
        seen = {}
        for it in cfg["shop"]:
            typ = it["type"] or "其他"
            if typ not in seen:
                seen[typ] = len(categories)
                categories.append((typ, []))
            categories[seen[typ]][1].append(it)
        for typ, items in categories:
            items.sort(key=lambda it: (int(it["price"]), it["name"]))
        # 分类之间也按「组内最低价」升序，便宜的类别靠前
        categories.sort(key=lambda cat: (min((int(it["price"]) for it in cat[1]), default=0), cat[0]))
        # 当前用户持有数量
        key = self._user_key(event)
        data = self._load()
        pet = data.get("pets", {}).get(key)
        inventory = pet.get("inventory", {}) if pet else {}
        img = self._render_shop_image(event.get_sender_name(), categories, inventory,
                                      self._coins_of(data, key))
        if img is not None:
            return img
        lines = ["🛒 宠物商店（发送「购买 <道具名> [数量]」购买，发送「使用 <道具名> [数量]」使用）："]
        for typ, items in categories:
            lines.append(f"【{typ}】")
            for it in items:
                have = int(inventory.get(it["name"], 0))
                ln = f"· {it['name']} ×{have}｜{int(it['price'])}金币"
                if it.get("desc"):
                    ln += f"（{it['desc']}）"
                ln += f"：{self._effect_desc(it['effects'])}"
                lines.append(ln)
        _no, _pill_p, _ball_p = self._signin_reward_chances()
        lines.append(f"· {PILL_NAME}（特殊）：随机 2 个属性 +5~20（每日最多 {self.pill_daily_limit} 次，签到 {_pill_p * 100:.0f}% 概率获得）")
        lines.append(f"· {EXP_BALL_NAME}（特殊）：获得升级经验 5%~20%（每日最多 {self.exp_ball_daily_limit} 次，签到 {_ball_p * 100:.0f}% 概率获得）")
        return "\n".join(lines)

    def _render_shop_image(self, name, categories, inventory, coins=None):
        """宠物商店：每行 SHOP_CARD_COLS 个卡片；名称(大三号)/持有数 / 效果(空格优先换行) / 分割线 / 价格(红、右、大一号、分割线与底边之间 N 像素)
        卡片高度自适应，同行取最高；被拉伸的低卡片忽略价格与分割线的 N 约束。
        coins（1.7.6）：不为 None 时在标题下方显示金币余额行。"""
        Image, ImageDraw = _ensure_pillow()
        if Image is None:
            return None
        # 字号语义：标题 32 / 分类 26 / 名称 26（大三号）/ 正文 18 / 价格 20（大一号）
        fonts = _load_fonts(32, 26, 26, 18, 20)
        if fonts is None:
            return None
        title_font, cat_font, name_font, body_font, price_font = fonts

        pad = 20
        title_h = 52
        coin_h = 28  # 1.7.6：标题下方金币余额行高度
        cat_h = 30
        rule_h = 18
        cols = int(globals().get("SHOP_CARD_COLS", 3))
        n_pad = int(globals().get("SHOP_PRICE_PAD", 4))  # N：价格距分割线/底边
        card_w = 246
        gap = 12
        inner = 10
        name_h = 34
        line_h = 26
        price_h = 26

        tw = _text_measurer()
        if tw is None:
            return None

        content_w = card_w - inner * 2

        wrap = _make_wrapper(tw, content_w)
        word_wrap = _make_wrapper(tw, content_w, mode="word")  # 效果描述按单元整体换行

        def _card_plan(it):
            """返回 (行列表, 高度, 分割线前高度)。行 = (kind, text, extra)"""
            have = int(inventory.get(it["name"], 0))
            cnt_text = f"×{have}"
            rows = []
            if tw(it["name"], name_font) + tw(cnt_text, body_font) + 8 <= content_w:
                rows.append(("pair", it["name"], cnt_text))
            else:
                for ln in wrap(it["name"], name_font):
                    rows.append(("plain", ln, ""))
                rows.append(("plain", cnt_text, ""))
            # 商品描述：名称下方、效果上方
            if it.get("desc"):
                for ln in wrap(it["desc"], body_font):
                    rows.append(("plain", ln, ""))
            for ln in word_wrap(f"效果：{self._effect_desc(it['effects'])}", body_font):
                rows.append(("plain", ln, ""))
            h_before = inner * 2 + sum(name_h if (i == 0 and r[0] == "pair") else line_h for i, r in enumerate(rows))
            rows.append(("rule", "", ""))
            rows.append(("price", f"{int(it['price'])} 金币", ""))
            # 总高 = 分割线前内容 + 分割线距价格上方 N + 线 1px + 价格区（价格高 + 底边 N）
            h = h_before + n_pad + 1 + price_h + n_pad
            return rows, h, h_before

        # 预计算每个类别的卡片排版
        cat_plans = []
        for typ, items in categories:
            item_plans = []
            for it in items:
                rows, h, hb = _card_plan(it)
                item_plans.append((it, rows, h, hb))
            cat_plans.append((typ, item_plans))

        width = pad * 2 + card_w * cols + gap * (cols - 1)

        # 总高度（卡片行高度取同行最大值）+ 底部特殊道具提示（自动换行防溢出）
        height = pad * 2 + title_h + (coin_h if coins is not None else 0)
        for typ, item_plans in cat_plans:
            height += cat_h + rule_h
            if item_plans:
                for g in range(0, len(item_plans), cols):
                    group = item_plans[g:g + cols]
                    height += max(p[2] for p in group) + gap
                height -= gap  # 去掉最后一组后的多余间距
        _no, _pill_p, _ball_p = self._signin_reward_chances()
        pill_txt = f"· {PILL_NAME}（特殊）：随机 2 个属性 +5~20（每日最多 {self.pill_daily_limit} 次，签到 {_pill_p * 100:.0f}% 概率获得）"
        ball_txt = f"· {EXP_BALL_NAME}（特殊）：获得升级经验 5%~20%（每日最多 {self.exp_ball_daily_limit} 次，签到 {_ball_p * 100:.0f}% 概率获得）"
        foot_max_w = width - pad * 2

        def wrap_foot(text, font):
            lines = []
            cur = ""
            for ch in text:
                if tw(cur + ch, font) <= foot_max_w:
                    cur += ch
                else:
                    if cur:
                        lines.append(cur)
                    cur = ch
            if cur:
                lines.append(cur)
            return lines or [""]

        foot_lines = wrap_foot(pill_txt, body_font) + wrap_foot(ball_txt, body_font)
        height += len(foot_lines) * 26 + 14

        img = Image.new("RGB", (width, height), (255, 255, 255))
        d = ImageDraw.Draw(img)
        y = pad
        d.text((pad, y), f"{name} 的宠物商店", font=title_font, fill=(20, 20, 20))
        y += title_h
        # 1.7.6：标题下方金币余额
        if coins is not None:
            d.text((pad, y), f"💰 金币余额：{coins}", font=body_font, fill=(140, 90, 0))
            y += coin_h

        for typ, item_plans in cat_plans:
            # 类别名（居中；坐标必须转 int）
            cx = int(pad + (width - 2 * pad - tw(typ, cat_font)) / 2)
            d.text((cx, y), typ, font=cat_font, fill=(60, 60, 60))
            y += cat_h
            # 分隔线（居中）
            d.line([(pad + 20, y), (width - pad - 20, y)], fill=(200, 200, 200), width=2)
            y += rule_h
            for g in range(0, len(item_plans), cols):
                group = item_plans[g:g + cols]
                gh = max(p[2] for p in group)
                for j, (it, rows, _, _) in enumerate(group):
                    x0 = pad + j * (card_w + gap)
                    d.rectangle([x0, y, x0 + card_w, y + gh], outline=(200, 200, 200), width=1)
                    yy = y + inner
                    rule_y = None
                    price_y = y + gh - n_pad - price_h  # 价格基线（贴底边 N）
                    for row in rows:
                        kind = row[0]
                        if kind == "pair":
                            d.text((int(x0 + inner), yy), row[1], font=name_font, fill=(20, 20, 20))
                            d.text((int(x0 + card_w - inner - tw(row[2], body_font)), yy + 4),
                                   row[2], font=body_font, fill=(140, 90, 0))
                            yy += name_h
                        elif kind == "plain":
                            for wl in wrap(row[1], body_font):
                                d.text((int(x0 + inner), yy), wl, font=body_font, fill=(70, 70, 70))
                                yy += line_h
                        elif kind == "rule":
                            rule_y = price_y - n_pad  # 分割线固定在价格上方 N 距离
                        elif kind == "price":
                            # 价格贴底边 N
                            d.text((int(x0 + card_w - inner - tw(row[1], price_font)), price_y),
                                   row[1], font=price_font, fill=(192, 0, 0))
                            break
                    if rule_y is not None:
                        d.line([(x0 + 8, rule_y), (x0 + card_w - 8, rule_y)], fill=(200, 200, 200), width=1)
                y += gh + gap

        # 底部：特殊道具提示（自动换行，不溢出图片）
        y += 8
        for ln in foot_lines:
            d.text((pad, y), ln, font=body_font, fill=(120, 120, 120))
            y += 26

        return _save_temp_image(img, "_shop_", "商店")

    def _handle_buy(self, event: AstrMessageEvent) -> str:
        """购买 <名称> [数量]：宠物商店道具 / 农场种子 / 农场化肥"""
        name = event.get_sender_name()
        key = self._user_key(event)
        item_name, qty, err = _parse_item_qty(event.message_str)
        if err:
            return f"格式：购买 <道具名> [数量]。{err}"

        # 农场种子：必须带「种子」后缀（如「白菜种子」），否则视为宠物商店商品
        crop = None
        if item_name.endswith("种子"):
            crop = self._find_item(self._load_crops(), item_name[:-2])
        if crop:
            data = self._load()
            return self._farm_buy_seed(data, key, name, crop["name"], qty)
        fert = self._find_item(self._load_fertilizers(), item_name)
        if fert:
            data = self._load()
            return self._farm_buy_fert(data, key, name, fert["name"], qty)

        cfg = self._load_config()
        item = next((it for it in cfg["shop"] if it["name"] == item_name), None)
        if not item:
            return f"没有「{item_name}」这个商品（宠物商店 / 农场商店都没有），发送「商店」或「农场商店」查看。"

        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"

        price = int(item["price"])
        total = price * qty
        coins = self._coins_of(data, key)
        if coins < total:
            return f"金币不足：{qty} × {price} = {total} 金币，当前 {coins}。"

        self._add_coins(data, key, -total, f"购买道具·{item_name}")
        inv = pet.setdefault("inventory", {})
        inv[item_name] = int(inv.get(item_name, 0)) + qty
        self._save(data)
        return (f"🛒 {name} 花费 {total} 金币购买了「{item_name}」×{qty}。发送「使用 {item_name}」使用。\n"
                f"{self._coin_line(data, key)}")

    def _handle_use_item(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        item_name, qty, err = _parse_item_qty(event.message_str)
        if err:
            return f"格式：使用 <道具名> [数量]。{err}"

        data = self._load()

        # 化肥（农场道具）：使用 <化肥> <分钟数> → 对生长中土地施肥（2.0.0 起按分钟使用）
        fert = self._find_item(self._load_fertilizers(), item_name)
        if fert:
            farm = data.get("farms", {}).get(key)
            if not farm:
                return f"{name} 还没有农场，发送「解锁农场」开通后再使用化肥。"
            return self._apply_fert_use(data, key, name, farm, fert, qty)

        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"
        self._bring_pet_up_to_date(pet, date.today().isoformat())

        user = data.get("users", {}).get(key, {})
        fav_level = self._level_of(float(user.get("favorability", 0.0)))
        pos_mult, neg_mult = self._fav_multipliers(fav_level)

        inv = pet.setdefault("inventory", {})

        # 属性丸（特殊道具：随机 2 个属性 +5~20，每日最多 3 次）
        if item_name == PILL_NAME:
            today = date.today().isoformat()
            if pet.get("pill_used_date") != today:
                pet["pill_used_date"] = today
                pet["pill_used_count"] = 0
            used = int(pet.get("pill_used_count", 0))
            if used + qty > self.pill_daily_limit:
                return f"属性丸每天最多使用 {self.pill_daily_limit} 次（今天已用 {used} 次）。"
            have = int(inv.get(PILL_NAME, 0))
            if have < qty:
                return f"属性丸不足：需要 {qty} 个，当前 {have} 个（签到有几率获得）。"
            boosts = {}
            for _ in range(qty):
                for attr in random.sample(list(ATTR_LABELS), int(self.pill_attr_count or 2)):  # 随机 N 个属性
                    v = round(random.uniform(self.pill_boost_min, self.pill_boost_max) * pos_mult, 2)
                    boosts[attr] = round(boosts.get(attr, 0) + v, 2)
                    pet[attr] = round(pet[attr] + v, 2)
            inv[PILL_NAME] = have - qty
            if inv[PILL_NAME] <= 0:
                inv.pop(PILL_NAME, None)
            pet["pill_used_count"] = used + qty
            self._clamp_attrs(pet)
            changes = dict(boosts)
            msg = f"{pet['name']} 使用「属性丸」×{qty} 成功！"
            pet["last_activity"] = {"msg": msg, "changes": changes,
                                    "reason": f"使用「属性丸」×{qty}",
                                    "coins": 0, "exp": 0, "act": "使用",
                                    "ts": datetime.now().timestamp(),
                                    "shown": False}
            self._save(data)
            desc = "，".join(f"{ATTR_SHORT[a]}+{v:.1f}" for a, v in boosts.items())
            text = (f"💊 {name} 使用了属性丸×{qty}：{desc}\n"
                    f"（今日已用 {pet['pill_used_count']}/{self.pill_daily_limit} 次）\n"
                    f"{self._pet_state_snippet(pet)}")
            # 2.0.0 消息合并：使用道具结果合入宠物指令图片的预留位（即时消息显示一次）
            # 本次产生新的状态变化 → 卡片1高亮；无工作/玩耍变动 → 卡片2不高亮
            img = self._safe_render_pet(
                name, key, data, pet,
                slot_msg=f"{msg}（今日已用 {pet['pill_used_count']}/{self.pill_daily_limit} 次）",
                changes=changes, reason=f"使用「属性丸」×{qty}",
                coins_delta=None, exp_delta=None,
                changes_fresh=bool(changes), card2_hl=False)
            if pet.get("last_activity"):
                pet["last_activity"]["shown"] = True
                self._save(data)
            return img if img is not None else text

        # 农场经验球（特殊道具：获得升级所需总经验的 5%~20%，每日最多 3 次）
        if item_name == EXP_BALL_NAME:
            farm = data.get("farms", {}).get(key)
            if not farm:
                # 未解锁农场 → 自动转换为金币（每个 10 金币）
                have = int(inv.get(EXP_BALL_NAME, 0))
                if have < qty:
                    return f"{EXP_BALL_NAME}不足：需要 {qty} 个，当前 {have} 个。"
                gain = qty * ITEM_TO_COIN
                inv[EXP_BALL_NAME] = have - qty
                if inv[EXP_BALL_NAME] <= 0:
                    inv.pop(EXP_BALL_NAME, None)
                self._add_coins(data, key, gain, "道具自动转金币")
                self._save(data)
                return (f"🔄 {name} 还没有农场，「{EXP_BALL_NAME}」×{qty} 自动转换为 {gain} 金币。\n"
                        f"{self._coin_line(data, key)}")
            today = date.today().isoformat()
            if farm.get("ball_used_date") != today:
                farm["ball_used_date"] = today
                farm["ball_used_count"] = 0
            used = int(farm.get("ball_used_count", 0))
            if used + qty > self.exp_ball_daily_limit:
                return f"{EXP_BALL_NAME}每天最多使用 {self.exp_ball_daily_limit} 次（今天已用 {used} 次）。"
            have = int(inv.get(EXP_BALL_NAME, 0))
            if have < qty:
                return f"{EXP_BALL_NAME}不足：需要 {qty} 个，当前 {have} 个（签到有几率获得）。"
            need = FARM_EXP_BASE * (int(farm.get("level", 0)) + 1)  # 升级所需总经验
            total = 0.0
            for _ in range(qty):
                total += round(need * random.uniform(self.exp_ball_min_pct, self.exp_ball_max_pct), 2)
            lvl_msg = self._farm_gain_exp(farm, total)
            inv[EXP_BALL_NAME] = have - qty
            if inv[EXP_BALL_NAME] <= 0:
                inv.pop(EXP_BALL_NAME, None)
            farm["ball_used_count"] = used + qty
            msg = f"{pet['name']} 使用「{EXP_BALL_NAME}」×{qty} 成功！农场经验 +{total:.1f}{lvl_msg}"
            pet["last_activity"] = {"msg": msg, "changes": {},
                                    "reason": f"使用「{EXP_BALL_NAME}」×{qty}",
                                    "coins": 0, "exp": 0, "act": "使用",
                                    "ts": datetime.now().timestamp(),
                                    "shown": False}
            self._save(data)
            text = (f"🏵️ {name} 使用了农场经验球×{qty}：农场经验 +{total:.1f}{lvl_msg}\n"
                    f"（今日已用 {farm['ball_used_count']}/{self.exp_ball_daily_limit} 次）\n"
                    f"{self._farm_state_snippet(farm)}")
            # 2.0.0 消息合并：使用道具结果合入宠物指令图片的预留位（即时消息显示一次）
            # 无属性变化 → 卡片1不高亮；无工作/玩耍变动 → 卡片2不高亮
            img = self._safe_render_pet(
                name, key, data, pet,
                slot_msg=f"{msg}（今日已用 {farm['ball_used_count']}/{self.exp_ball_daily_limit} 次）",
                changes={}, reason=f"使用「{EXP_BALL_NAME}」×{qty}",
                coins_delta=None, exp_delta=None,
                changes_fresh=False, card2_hl=False)
            if pet.get("last_activity"):
                pet["last_activity"]["shown"] = True
                self._save(data)
            return img if img is not None else text

        # 商店道具
        cfg = self._load_config()
        item = next((it for it in cfg["shop"] if it["name"] == item_name), None)
        if not item:
            return f"没有「{item_name}」这个道具，发送「商店」查看。"
        have = int(inv.get(item_name, 0))
        if have < qty:
            return f"你没有足够的「{item_name}」：需要 {qty} 个，当前 {have} 个。发送「购买 {item_name} {qty}」购买。"

        changes = {}
        for _ in range(qty):
            for attr in ATTR_LABELS:
                val = item["effects"].get(attr, 0)
                if val > 0:
                    applied = round(val * pos_mult, 2)
                elif val < 0:
                    applied = round(val * neg_mult, 2)
                else:
                    continue
                pet[attr] = round(pet[attr] + applied, 2)
                changes[attr] = round(changes.get(attr, 0) + applied, 2)

        inv[item_name] = have - qty
        if inv[item_name] <= 0:
            inv.pop(item_name, None)
        self._clamp_attrs(pet)
        msg = f"{pet['name']} 使用「{item_name}」×{qty} 成功！"
        if not changes:
            msg += "（无效果）"
        pet["last_activity"] = {"msg": msg, "changes": changes,
                                "reason": f"使用「{item_name}」×{qty}",
                                "coins": 0, "exp": 0, "act": "使用",
                                "ts": datetime.now().timestamp(),
                                "shown": False}
        self._save(data)

        if not changes:
            text = (f"✅ {name} 使用了「{item_name}」×{qty}（无效果）。\n"
                    f"{self._pet_state_snippet(pet)}")
        else:
            desc = "，".join(f"{ATTR_SHORT[a]}{v:+.1f}" for a, v in changes.items())
            text = (f"✅ {name} 使用了「{item_name}」×{qty}：{desc}\n"
                    f"{self._pet_state_snippet(pet)}")
        # 2.0.0 消息合并：使用道具结果合入宠物指令图片的预留位（即时消息显示一次）
        # 本次有属性变化 → 卡片1高亮；无工作/玩耍变动 → 卡片2不高亮
        img = self._safe_render_pet(
            name, key, data, pet,
            slot_msg=msg,
            changes=changes, reason=f"使用「{item_name}」×{qty}",
            coins_delta=None, exp_delta=None,
            changes_fresh=bool(changes), card2_hl=False)
        if pet.get("last_activity"):
            pet["last_activity"]["shown"] = True
            self._save(data)
        return img if img is not None else text

    def _handle_bag(self, event: AstrMessageEvent):
        """背包（1.7.7 大改）：卡片式图片——标题区（用户名/好感等级/金币/负债/宠物/农场）
        + 分类卡片（宠物道具/作物/种子/肥料）+ 页尾大卡（仓库总价值/今日净收益/三榜排名）。
        渲染失败回退为文本列表。"""
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        pet = data.get("pets", {}).get(key)
        farm = data.get("farms", {}).get(key)
        user = data.get("users", {}).get(key) or {}

        cfg = self._load_config()
        shop_items = cfg["shop"]
        crops = self._load_crops()
        ferts = self._load_fertilizers()

        # ---- 物品卡片分类：两级（1.7.7）----
        # 第一级：宠物道具 / 农场道具；
        # 第二级（宠物）：食物/饮料/玩具/药物/特殊；（农场）：种子/收获物/化肥
        # groups = [(一级名, [(二级名, [card])])]；card = {name,count,desc,effect,sell_unit}
        groups = []
        if pet:
            by_type = {"食物": [], "饮料": [], "玩具": [], "药物": [], "特殊": []}
            other = []
            for nm, cnt in pet.get("inventory", {}).items():
                cnt = int(cnt)
                if cnt <= 0:
                    continue
                it = self._find_item(shop_items, nm)
                if it:
                    card = {"name": nm, "count": cnt, "desc": it.get("desc", ""),
                            "effect": self._effect_desc(it["effects"]), "sell_unit": None}
                    typ = (it.get("type") or "").strip()
                    if typ == "食品":  # 旧类型兼容：食品 → 食物
                        typ = "食物"
                    if typ in by_type:
                        by_type[typ].append(card)
                    else:
                        other.append(card)
                elif nm == PILL_NAME:
                    by_type["特殊"].append({"name": nm, "count": cnt, "desc": "随机提升宠物属性",
                                            "effect": f"随机{int(self.pill_attr_count or 2)}属性 +{self.pill_boost_min:.0f}~{self.pill_boost_max:.0f}（每日{self.pill_daily_limit}次）",
                                            "sell_unit": None})
                elif nm == EXP_BALL_NAME:
                    by_type["特殊"].append({"name": nm, "count": cnt, "desc": "提升农场经验",
                                            "effect": f"获得升级经验 {EXP_BALL_MIN_PCT * 100:.0f}%~{EXP_BALL_MAX_PCT * 100:.0f}%（每日{self.exp_ball_daily_limit}次）",
                                            "sell_unit": None})
                else:
                    other.append({"name": nm, "count": cnt, "desc": "", "effect": "", "sell_unit": None})
            subs = [(t, by_type[t]) for t in ("食物", "饮料", "玩具", "药物", "特殊") if by_type[t]]
            if other:
                subs.append(("其它", other))
            if subs:
                groups.append(("宠物道具", subs))
        if farm:
            wh = farm.get("warehouse", {})
            seed_cards, crop_cards, fert_cards = [], [], []
            for nm, cnt in wh.get("seeds", {}).items():
                c = self._find_item(crops, nm)
                seed_cards.append({"name": nm, "count": int(cnt),
                                   "desc": c.get("desc", "") if c else "",
                                   "effect": "", "sell_unit": float(c["seed_sell_price"]) if c else 0.0})
            for nm, cnt in wh.get("crops", {}).items():
                c = self._find_item(crops, nm)
                crop_cards.append({"name": nm, "count": int(cnt),
                                   "desc": c.get("desc", "") if c else "",
                                   "effect": "", "sell_unit": float(c["crop_price"]) if c else 0.0})
            for nm, cnt in wh.get("fertilizers", {}).items():
                f = self._find_item(ferts, nm)
                maxa = "不限" if (f and self._fert_max_accel(f) < 0) else (f"{self._fert_max_accel(f)}次" if f else "？")
                effect = f"每株可加速 {maxa}"
                if f and float(f.get("yield_add", 0) or 0) > 0:
                    effect += f" 增产{f['yield_add']:.0f}%/次"
                fert_cards.append({"name": nm, "count": self._fmt_hours(cnt),
                                   "desc": f.get("desc", "") if f else "",
                                   "effect": effect,
                                   "sell_unit": None})
            subs = []
            if seed_cards:
                subs.append(("种子", seed_cards))
            if crop_cards:
                subs.append(("收获物", crop_cards))
            if fert_cards:
                subs.append(("化肥", fert_cards))
            if subs:
                groups.append(("农场道具", subs))
        if not groups:
            return f"{name} 的背包是空的。"

        # ---- 页尾：仓库总价值 / 今日净收益 / 三榜排名 ----
        wh_total = 0
        if farm:
            wh = farm.get("warehouse", {})
            for nm, cnt in wh.get("crops", {}).items():
                c = self._find_item(crops, nm)
                wh_total += int(round(int(cnt) * (float(c["crop_price"]) if c else 0.0)))
            for nm, cnt in wh.get("seeds", {}).items():
                c = self._find_item(crops, nm)
                wh_total += int(round(int(cnt) * (float(c["seed_sell_price"]) if c else 0.0)))
        # 1.7.8：今日净收益 = 当前金币排行积分 − 当日零点基线（覆盖红包/利息/存款/左轮等所有金币变化）
        base, base_new = self._ensure_bag_base(data, key)
        net = self._rank_score_coins(data, key) - base
        if base_new:
            self._save(data)  # 持久化当日零点基线

        def my_rank(kind):
            for i, e in enumerate(self._rank_entries(kind, data)):
                if e[1] == str(key):
                    return i + 1
            return None

        footer = {
            "wh_total": wh_total, "net": net,
            "coins_rank": my_rank("coins"), "pet_rank": my_rank("pet"), "farm_rank": my_rank("farm"),
        }

        # ---- 标题区：好感等级 / 金币 / 负债 / 宠物 / 农场 ----
        fav_lv = self._level_of(float(user.get("favorability", 0.0)))
        debt = 0
        rec = data.get("loans", {}).get(key)
        if rec:
            now_ts = datetime.now().timestamp()
            debt = int(sum(self._loan_owed(l, now_ts) for l in rec.get("loans", []) if l.get("remaining", 0) > 0))
        pet_line = None
        if pet:
            lv, _, _ = self._pet_exp_progress(float(pet.get("exp", 0.0)))
            abnormal = bool(pet.get("weak")) or float(pet.get("health", 100)) <= 39
            pet_line = {"name": pet.get("name", "宠物"), "level": lv, "abnormal": abnormal}
        farm_line = None
        if farm:
            now_ts = datetime.now().timestamp()
            plots = farm.get("plots", [])
            idle = sum(1 for pl in plots if self._plot_free(pl))
            mature = sum(1 for pl in plots
                         if not self._plot_free(pl) and float(pl.get("mature_ts", 0)) <= now_ts)
            occupied = len(plots) - idle - mature
            farm_line = {"level": int(farm.get("level", 0)), "idle": idle,
                         "occupied": occupied, "mature": mature}
        header = {"fav_lv": fav_lv, "coins": self._coins_of(data, key), "debt": debt,
                  "pet": pet_line, "farm": farm_line}

        img = self._render_bag_image(name, header, groups, footer)
        if img is not None:
            return img
        # ---- 文本回退 ----
        lines = [f"🎒 {name} 的背包（好感 Lv.{fav_lv}）"]
        coin_line = f"金币 {header['coins']}"
        if debt > 0:
            coin_line += f"｜负债 {debt}"
        lines.append(coin_line)
        if pet_line:
            lines.append(f"宠物 {pet_line['name']} Lv.{pet_line['level']}"
                         f"（{'异常' if pet_line['abnormal'] else '正常'}）")
        else:
            lines.append("未领养宠物")
        if farm_line:
            lines.append(f"农场 Lv.{farm_line['level']}：空闲 {farm_line['idle']} 块｜"
                         f"占用 {farm_line['occupied']} 块｜成熟 {farm_line['mature']} 块")
        else:
            lines.append("未解锁农场")
        for l1, subs in groups:
            lines.append(f"【{l1}】")
            for l2, cards in subs:
                lines.append(f"　【{l2}】")
                for c in cards:
                    ln = f"　· {c['name']} ×{c['count']}"
                    if c.get("desc"):
                        ln += f"（{c['desc']}）"
                    if c.get("effect"):
                        ln += f"：{c['effect']}"
                    if c.get("sell_unit") is not None:
                        ln += f" 可售 {self._fmt_price(c['sell_unit'])} |{self._fmt_price(c['sell_unit'] * c['count'])}"
                    lines.append(ln)
        lines.append(f"仓库总价值 {wh_total}｜今日净收益 {'+' if net >= 0 else ''}{net}")

        def _rk(r):
            return f"第{r}名" if r else "未上榜"
        lines.append(f"金币排行 {_rk(footer['coins_rank'])}｜宠物排行 {_rk(footer['pet_rank'])}｜农场排行 {_rk(footer['farm_rank'])}")
        return "\n".join(lines)

    def _render_bag_image(self, name, header, groups, footer):
        """背包图片（1.7.7）：
        标题区：「<用户名>的背包」（居左大字号）+「好感 Lv.X」（居右）；金币行（负债 #FF6D6D）；
        宠物行（名字+等级+状态：正常/异常）；农场行（等级+空闲/占用/成熟地块数）。
        内容区（两级分类）：一级分类（宠物道具/农场道具，居中+居中分割线）→
        二级分类（食物/饮料/玩具/药物/特殊 或 种子/收获物/化肥，居中+居中分割线）→ 卡片网格
        （每行 BAG_CARD_COLS 个）。
        卡片：物品名(左)+数量(右) → 描述 → 使用效果 → 卡片内分割线 → 可售卖金额(右，#C00000，
        格式「单价 |总价」）；无数据的模块自动隐藏（无描述/无效果/不可售时对应模块不显示）。
        页尾：通宽大卡片 = 内容区卡片总宽 + 行内间隙总和；第一行 仓库总价值(大字号)+今日净收益(小字号)，
        第二行 金币/宠物/农场三榜排名。"""
        Image, ImageDraw = _ensure_pillow()
        if Image is None:
            return None
        # 字号语义：标题 32 / 信息 20 / 一级分类 26 / 名称 22 / 正文 18 / 页尾大字 26 / 二级分类 20
        fonts = _load_fonts(32, 20, 26, 22, 18, 26)
        if fonts is None:
            return None
        title_font, info_font, l1_font, name_font, body_font, big_font = fonts
        l2_font = info_font

        tw = _text_measurer()
        if tw is None:
            return None

        pad = 20
        title_h = 50
        info_h = 30
        head_gap = 12      # 标题区与内容区之间的距离
        l1_h = 38
        l1_rule_h = 18
        l2_h = 28
        l2_rule_h = 14
        cols = max(3, min(7, int(globals().get("BAG_CARD_COLS", 5))))
        card_w = 200
        gap = 12
        inner = 10
        name_h = 30
        line_h = 24
        sell_h = 26
        n_pad = 4          # 卡片内分割线距可售金额上方 N

        content_w = card_w - inner * 2
        wrap = _make_wrapper(tw, content_w)
        word_wrap = _make_wrapper(tw, content_w, mode="word")

        DEBT_COLOR = (255, 109, 109)   # #FF6D6D
        SELL_COLOR = (192, 0, 0)       # #C00000

        def _card_plan(card):
            """返回 (行列表, 卡片高度)。行 = (kind, text, extra)"""
            cnt_text = f"×{card['count']}"
            rows = []
            if tw(card["name"], name_font) + tw(cnt_text, body_font) + 8 <= content_w:
                rows.append(("pair", card["name"], cnt_text))
            else:
                for ln in wrap(card["name"], name_font):
                    rows.append(("plain_name", ln, ""))
                rows.append(("plain_name", cnt_text, ""))
            if card.get("desc"):
                for ln in wrap(card["desc"], body_font):
                    rows.append(("plain", ln, ""))
            if card.get("effect"):
                for ln in word_wrap(f"效果：{card['effect']}", body_font):
                    rows.append(("plain", ln, ""))
            sellable = card.get("sell_unit") is not None
            if sellable:
                rows.append(("rule", "", ""))
                unit = self._fmt_price(card["sell_unit"])
                total = self._fmt_price(card["sell_unit"] * card["count"])
                sell_txt = f"{unit} |{total}"
                if tw(sell_txt, info_font) <= content_w:
                    rows.append(("sell", sell_txt, ""))
                else:
                    # 极端长金额 → 在「|」断行（单价一行、总价一行）
                    rows.append(("sell", f"{unit} |", ""))
                    rows.append(("sell", total, ""))
            h = inner * 2
            for r in rows:
                if r[0] == "pair":
                    h += name_h
                elif r[0] == "plain_name":
                    h += name_h - 4
                elif r[0] == "rule":
                    h += n_pad + 1
                elif r[0] == "sell":
                    h += sell_h
                else:
                    h += line_h
            return rows, h

        # 预计算排版：[(一级名, [(二级名, [(card, rows, h)])])]
        group_plans = []
        for l1, subs in groups:
            sub_plans = [(l2, [(card, *_card_plan(card)) for card in cards]) for l2, cards in subs]
            group_plans.append((l1, sub_plans))

        width = pad * 2 + card_w * cols + gap * (cols - 1)
        page_w = width - pad * 2  # 页尾大卡片宽 = 内容卡片总宽 + 间隙总和

        # ---- 高度 ----
        height = pad
        height += title_h
        height += info_h  # 金币/负债行
        height += info_h  # 宠物行
        height += info_h  # 农场行
        height += head_gap
        for l1, sub_plans in group_plans:
            height += l1_h + l1_rule_h
            for l2, plans in sub_plans:
                height += l2_h + l2_rule_h
                for g in range(0, len(plans), cols):
                    height += max(p[2] for p in plans[g:g + cols]) + gap
                height -= gap
                height += gap  # 二级分类间距
            height += gap      # 一级分类间距
        # 页尾卡片：内边距 + 大字行 + 小字行 + 排名行；底部额外留白（页尾卡片不贴底边）
        foot_h = inner * 2 + 36 + 6 + 26
        height += gap + foot_h + pad + 12

        img = Image.new("RGB", (width, height), (255, 255, 255))
        d = ImageDraw.Draw(img)
        y = pad

        # ---- 标题区 ----
        d.text((pad, y), f"{name} 的背包", font=title_font, fill=(20, 20, 20))
        fav_txt = f"好感 Lv.{header['fav_lv']}"
        d.text((int(width - pad - tw(fav_txt, info_font)), y + 10), fav_txt,
               font=info_font, fill=(140, 90, 0))
        y += title_h
        # 金币 + 负债
        x = pad
        coin_txt = f"金币 {header['coins']}"
        d.text((x, y), coin_txt, font=info_font, fill=(20, 20, 20))
        x += int(tw(coin_txt, info_font))
        if header["debt"] > 0:
            debt_txt = f"｜负债 {header['debt']}"
            d.text((x, y), debt_txt, font=info_font, fill=DEBT_COLOR)
        y += info_h
        # 宠物行
        if header["pet"]:
            p = header["pet"]
            pet_txt = f"宠物 {p['name']} Lv.{p['level']}"
            d.text((pad, y), pet_txt, font=info_font, fill=(20, 20, 20))
            st_txt = "异常" if p["abnormal"] else "正常"
            st_color = DEBT_COLOR if p["abnormal"] else (90, 160, 60)
            d.text((pad + int(tw(pet_txt, info_font)) + 10, y), st_txt, font=info_font, fill=st_color)
        else:
            d.text((pad, y), "未领养宠物", font=info_font, fill=(120, 120, 120))
        y += info_h
        # 农场行
        if header["farm"]:
            f = header["farm"]
            d.text((pad, y),
                   f"农场 Lv.{f['level']}　空闲 {f['idle']} 块｜占用 {f['occupied']} 块｜成熟 {f['mature']} 块",
                   font=info_font, fill=(20, 20, 20))
        else:
            d.text((pad, y), "未解锁农场", font=info_font, fill=(120, 120, 120))
        y += info_h + head_gap

        # ---- 内容区（两级分类） ----
        for l1, sub_plans in group_plans:
            # 一级分类名（居中）+ 分割线（居中）
            cx = int(pad + (page_w - tw(l1, l1_font)) / 2)
            d.text((cx, y), l1, font=l1_font, fill=(30, 30, 30))
            y += l1_h
            d.line([(pad, y), (width - pad, y)], fill=(120, 120, 120), width=2)
            y += l1_rule_h
            for l2, plans in sub_plans:
                # 二级分类名（居中）+ 分割线（居中，更细更浅）
                cx = int(pad + (page_w - tw(l2, l2_font)) / 2)
                d.text((cx, y), l2, font=l2_font, fill=(90, 90, 90))
                y += l2_h
                d.line([(pad + 20, y), (width - pad - 20, y)], fill=(210, 210, 210), width=1)
                y += l2_rule_h
                for g in range(0, len(plans), cols):
                    group = plans[g:g + cols]
                    gh = max(p[2] for p in group)
                    for j, (card, rows, _) in enumerate(group):
                        x0 = pad + j * (card_w + gap)
                        d.rectangle([x0, y, x0 + card_w, y + gh], outline=(200, 200, 200), width=1)
                        yy = y + inner
                        for row in rows:
                            kind = row[0]
                            if kind == "pair":
                                d.text((int(x0 + inner), yy), row[1], font=name_font, fill=(20, 20, 20))
                                d.text((int(x0 + card_w - inner - tw(row[2], body_font)), yy + 4),
                                       row[2], font=body_font, fill=(140, 90, 0))
                                yy += name_h
                            elif kind == "plain_name":
                                d.text((int(x0 + inner), yy), row[1], font=name_font, fill=(20, 20, 20))
                                yy += name_h - 4
                            elif kind == "plain":
                                d.text((int(x0 + inner), yy), row[1], font=body_font, fill=(70, 70, 70))
                                yy += line_h
                            elif kind == "rule":
                                yy += n_pad
                                d.line([(x0 + 8, yy), (x0 + card_w - 8, yy)], fill=(200, 200, 200), width=1)
                                yy += 1
                            elif kind == "sell":
                                d.text((int(x0 + card_w - inner - tw(row[1], info_font)), yy + 2),
                                       row[1], font=info_font, fill=SELL_COLOR)
                                yy += sell_h
                    y += gh + gap
                y += gap  # 二级分类间距
            y += gap      # 一级分类间距

        # ---- 页尾大卡片 ----
        d.rectangle([pad, y, pad + page_w, y + foot_h], outline=(160, 160, 160), width=2)
        yy = y + inner
        total_txt = f"仓库总价值 {footer['wh_total']}"
        d.text((pad + inner, yy), total_txt, font=big_font, fill=(20, 20, 20))
        net = footer["net"]
        net_txt = f"｜今日净收益 {'+' if net >= 0 else ''}{net}"
        net_color = DEBT_COLOR if net < 0 else (70, 70, 70)
        d.text((pad + inner + int(tw(total_txt, big_font)) + 8, yy + 6), net_txt,
               font=body_font, fill=net_color)
        yy += 36 + 6

        def _rk(r):
            return f"第{r}名" if r else "未上榜"
        rank_txt = (f"金币排行 {_rk(footer['coins_rank'])}｜宠物排行 {_rk(footer['pet_rank'])}"
                    f"｜农场排行 {_rk(footer['farm_rank'])}")
        d.text((pad + inner, yy), rank_txt, font=info_font, fill=(70, 70, 70))

        return _save_temp_image(img, "_bag_", "背包")
