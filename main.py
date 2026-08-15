import asyncio
import json
import os
import random
from datetime import date, timedelta, datetime

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

# ============ 签到 / 好感度 ============
MIN_COINS = 30          # 每次签到最少获得的金币
MAX_COINS = 300         # 每次签到最多获得的金币
MIN_FAV = 0.01          # 每次签到最少增加的好感度
MAX_FAV = 1.0           # 每次签到最多增加的好感度
MAX_LEVEL = 10          # 好感度最高等级（初始为 0）
LEVEL_STEP = 10.0       # 好感度每满 10 点提升一级

# ============ 左轮手枪 ============
ROULETTE_MAGAZINES = 7      # 弹匣数量
ROULETTE_MAX_BULLETS = 6    # 子弹数量上限
ROULETTE_MIN_PLAYERS = 2    # 最少玩家人数
ROULETTE_MAX_PLAYERS = 3    # 最多玩家人数
ROULETTE_JOIN_TIMEOUT = 30  # 加入等待秒数
ROULETTE_FEE_RATE = 0.1     # 手续费比例（10%）

# ============ 宠物 ============
PET_UNLOCK_COST = 1000      # 解锁宠物所需金币
PET_MAX_LEVEL = 100         # 宠物最大等级
PET_EXP_PER_LEVEL = 100.0   # 每 100 经验升一级
PET_MAX_HEALTH = 200.0      # 健康度最大值
PET_SIGNIN_EXP_MIN = 10.0   # 签到宠物经验下限
PET_SIGNIN_EXP_MAX = 60.0   # 签到宠物经验上限
PILL_NAME = "属性丸"        # 属性丸道具名
PILL_DROP_CHANCE = 0.5      # 签到掉落属性丸概率
PILL_DROP_MIN = 1           # 属性丸最少掉落数量
PILL_DROP_MAX = 5           # 属性丸最多掉落数量
MONEY_EVENT_CHANCE = 0.01   # 玩耍捡到钱概率（1%）
MONEY_EVENT_GAIN = 100      # 捡到钱的金币
MONEY_EVENT_MAX_PER_DAY = 2  # 每个周期最多触发次数
# =============================================

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "后台.txt")
PLUGIN_NAME = "astrbot_plugin_signin"

ATTR_LABELS = {"satiety": "饱食度", "thirst": "口渴值", "stamina": "体力", "mood": "心情值", "health": "健康度"}
ATTR_SHORT = {"satiety": "饱食", "thirst": "口渴", "stamina": "体力", "mood": "心情", "health": "健康"}


class RouletteGame:
    """一局左轮手枪游戏的内存状态"""

    def __init__(self, group_id, starter_id, starter_name, bullets, stake):
        self.group_id = group_id
        self.bullets = bullets
        self.stake = stake
        self.players = [{"id": starter_id, "name": starter_name}]
        self.status = "waiting"      # waiting -> playing -> finished
        self.magazines = []          # bool 列表，True 表示该弹匣有子弹
        self.shot_index = 0          # 下一个要用的弹匣下标
        self.turn_index = 0          # 当前回合玩家下标
        self.event = None            # 发起时的 AstrMessageEvent，供定时器主动发消息
        self.timer_task = None       # 30 秒超时任务

    @property
    def starter_id(self):
        return self.players[0]["id"]

    def player_names(self):
        return "、".join(p["name"] for p in self.players)


@register("astrbot_plugin_signin", "sishijiu", "群签到 + 左轮手枪 + 宠物系统", "1.2.0")
class SignInPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        def _get(key, default, cast=int):
            v = self.config.get(key, default)
            try:
                return cast(v)
            except (TypeError, ValueError):
                return default

        # 从 WebUI 配置页（_conf_schema.json）读取设置，未配置时用默认值
        self.min_coins = _get("min_coins", MIN_COINS)
        self.max_coins = _get("max_coins", MAX_COINS)
        self.pet_unlock_cost = _get("pet_unlock_cost", PET_UNLOCK_COST)
        self.pet_signin_exp_min = _get("pet_signin_exp_min", PET_SIGNIN_EXP_MIN, float)
        self.pet_signin_exp_max = _get("pet_signin_exp_max", PET_SIGNIN_EXP_MAX, float)
        self.pill_drop_chance = _get("pill_drop_chance", PILL_DROP_CHANCE, float)
        self.pill_drop_min = _get("pill_drop_min", PILL_DROP_MIN)
        self.pill_drop_max = _get("pill_drop_max", PILL_DROP_MAX)
        self.money_event_chance = _get("money_event_chance", MONEY_EVENT_CHANCE, float)
        self.money_event_gain = _get("money_event_gain", MONEY_EVENT_GAIN)
        self.money_event_max_per_day = _get("money_event_max_per_day", MONEY_EVENT_MAX_PER_DAY)

        self._lock = asyncio.Lock()       # 保护数据文件 + 游戏内存状态
        self._games = {}                  # group_id -> RouletteGame

        # 注册 WebUI Pages 的后端 API
        context.register_web_api(
            f"/{PLUGIN_NAME}/backend/config", self.web_get_backend_config, ["GET"], "读取后台.txt"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/backend/config", self.web_save_backend_config, ["POST"], "保存后台.txt"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/data/export", self.web_export_data, ["GET"], "导出 data.json"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/data/import", self.web_import_data, ["POST"], "导入 data.json"
        )

    # ================= 数据存取 =================
    def _load(self) -> dict:
        if not os.path.exists(DATA_FILE):
            return {"users": {}, "roulette": {}, "pets": {}}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"users": {}, "roulette": {}, "pets": {}}
            data.setdefault("users", {})
            data.setdefault("roulette", {})
            data.setdefault("pets", {})
            return data
        except Exception as e:
            logger.error(f"[插件] 读取数据失败: {e}")
            return {"users": {}, "roulette": {}, "pets": {}}

    def _save(self, data: dict) -> None:
        try:
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, DATA_FILE)
        except Exception as e:
            logger.error(f"[插件] 保存数据失败: {e}")

    @staticmethod
    def _user_key(event: AstrMessageEvent) -> str:
        gid = event.get_group_id()
        uid = event.get_sender_id()
        return f"{gid}:{uid}" if gid else f"private:{uid}"

    @staticmethod
    def _level_of(favorability: float) -> int:
        return min(MAX_LEVEL, int(favorability // LEVEL_STEP))

    def _ensure_user(self, data: dict, key: str) -> dict:
        return data.setdefault("users", {}).setdefault(key, {"coins": 0, "favorability": 0.0})

    def _coins_of(self, data: dict, key: str) -> int:
        return int(data.get("users", {}).get(key, {}).get("coins", 0))

    def _add_coins(self, data: dict, key: str, amount: int) -> None:
        user = self._ensure_user(data, key)
        user["coins"] = max(0, int(user.get("coins", 0)) + amount)

    def _ensure_stat(self, data: dict, key: str) -> dict:
        return data.setdefault("roulette", {}).setdefault(key, {
            "wins": 0, "losses": 0, "net": 0, "lost_to": {}, "won_from": {},
        })

    def _record(self, data: dict, key: str, side: str, opp_id: str, opp_name: str, amount: int) -> None:
        stat = self._ensure_stat(data, key)
        bucket = stat.setdefault(side, {})
        entry = bucket.setdefault(opp_id, {"name": opp_name, "amount": 0})
        entry["amount"] += amount
        entry["name"] = opp_name

    # ================= 通用工具 =================
    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    @staticmethod
    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    # ================= 签到 =================
    @filter.command("签到")
    async def sign_in(self, event: AstrMessageEvent):
        name = event.get_sender_name()
        key = self._user_key(event)
        today = date.today().isoformat()

        async with self._lock:
            data = self._load()
            user = data.get("users", {}).get(key)

            if user and user.get("last_date") == today:
                coins = user.get("coins", 0)
                fav = float(user.get("favorability", 0.0))
                lv = self._level_of(fav)
                reply = (f"{name}，你今天已经签到过啦～\n"
                         f"💰 当前金币：{coins}\n"
                         f"💗 当前好感度：{fav:.2f}（Lv.{lv}）")
            else:
                if user is None:
                    user = self._ensure_user(data, key)

                coins_got = random.randint(self.min_coins, self.max_coins)
                fav_got = round(random.uniform(MIN_FAV, MAX_FAV), 2)

                old_fav = float(user.get("favorability", 0.0))
                old_lv = self._level_of(old_fav)

                user["coins"] = int(user.get("coins", 0)) + coins_got
                new_fav = round(old_fav + fav_got, 2)
                user["favorability"] = new_fav
                user["last_date"] = today

                new_lv = self._level_of(new_fav)

                lines = [
                    f"✅ {name} 签到成功！",
                    f"💰 获得金币：+{coins_got}（当前 {user['coins']}）",
                    f"💗 好感度：+{fav_got:.2f}（当前 {new_fav:.2f}）",
                ]
                if new_lv > old_lv:
                    lines.append(f"🎉 好感度突破 {int(new_lv * LEVEL_STEP)}，等级提升至 Lv.{new_lv}！")
                else:
                    lines.append(f"🏅 当前好感等级：Lv.{new_lv}")

                # ---- 宠物 ----
                pet = data.get("pets", {}).get(key)
                if pet:
                    self._bring_pet_up_to_date(pet, today)

                    exp_got = round(random.uniform(self.pet_signin_exp_min, self.pet_signin_exp_max), 2)
                    pet["exp"] = round(float(pet.get("exp", 0.0)) + exp_got, 2)
                    lvl_msg = self._apply_exp(pet)
                    lines.append(f"🐾 宠物经验：+{exp_got:.1f}{lvl_msg}")

                    if random.random() < self.pill_drop_chance:
                        pills = random.randint(self.pill_drop_min, self.pill_drop_max)
                        inv = pet.setdefault("inventory", {})
                        inv[PILL_NAME] = int(inv.get(PILL_NAME, 0)) + pills
                        lines.append(f"💊 运气不错，获得 {pills} 个属性丸（发送「使用 属性丸」使用）！")

                    settle_lines = self._settle_display_lines(pet)
                    if settle_lines:
                        lines.append("")
                        lines.extend(settle_lines)

                self._save(data)
                reply = "\n".join(lines)

        yield event.plain_result(reply)

    @filter.command("我的签到")
    async def my_info(self, event: AstrMessageEvent):
        name = event.get_sender_name()
        key = self._user_key(event)

        async with self._lock:
            data = self._load()
            user = data.get("users", {}).get(key)
            if not user:
                reply = f"{name} 还没有签到记录，发送「签到」开始吧～"
            else:
                coins = user.get("coins", 0)
                fav = float(user.get("favorability", 0.0))
                lv = self._level_of(fav)
                last = user.get("last_date", "无")
                lines = [
                    f"{name} 的签到信息：",
                    f"💰 金币：{coins}",
                    f"💗 好感度：{fav:.2f}",
                    f"🏅 好感等级：Lv.{lv}",
                    f"📅 上次签到：{last}",
                ]
                reply = "\n".join(lines)
        yield event.plain_result(reply)

    # ================= 宠物：结算逻辑 =================
    @staticmethod
    def _attr_max(health: float):
        """返回 (饱食上限, 口渴上限, 体力上限, 心情上限)，由健康度决定"""
        if health >= 100:
            return 120.0, 120.0, 200.0, 100.0
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
        """每个属性单独定档，取最差档（3 最差）"""
        tiers = []
        tiers.append(1 if satiety >= 100 else (2 if satiety >= 50 else 3))
        tiers.append(1 if thirst >= 100 else (2 if thirst >= 70 else 3))
        tiers.append(1 if mood >= 80 else (2 if mood >= 50 else 3))
        return max(tiers)

    def _settle_once(self, pet: dict, settle_date: str) -> None:
        """执行一次每日结算"""
        health = pet["health"]

        # 1. 基础结算（按健康档位）
        if health >= 100:
            sat_d = -random.uniform(10.0, 15.0)
            thr_d = -random.uniform(10.0, 15.0)
            sta_d = random.uniform(100.0, 120.0)
            mood_d = random.uniform(1.0, 5.0)
            health_base_d = 0.0
        elif health >= 40:
            sat_d = -random.uniform(15.0, 20.0)
            thr_d = -random.uniform(15.0, 20.0)
            if random.random() < 0.8:
                sta_d = random.uniform(80.0, 100.0)
            else:
                sta_d = random.uniform(100.0, 120.0)
            mood_d = random.uniform(1.0, 2.5)
            health_base_d = 0.0
        else:
            sat_d = -random.uniform(20.0, 25.0)
            thr_d = -random.uniform(20.0, 25.0)
            sta_d = random.uniform(40.0, 60.0)
            mood_d = -random.uniform(2.0, 5.0)
            health_base_d = -random.uniform(1.0, 8.0)

        # 2. 饱食/口渴/心情 三档对健康的影响
        tier = self._worst_tier(pet["satiety"], pet["thirst"], pet["mood"])
        if tier == 1:
            health_tier_d = random.uniform(5.0, 10.0)
        elif tier == 2:
            health_tier_d = random.uniform(0.1, 6.0)
        else:
            health_tier_d = -random.uniform(0.1, 10.0)

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
            "tier3": tier == 3,
        }

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
        if ls.get("tier3"):
            lines.append("⚠️ 有属性处于第三档（太差），注意喂食 / 饮水 / 陪伴！")
        return lines

    def _apply_exp(self, pet: dict) -> str:
        new_level = min(PET_MAX_LEVEL, int(float(pet.get("exp", 0.0)) // PET_EXP_PER_LEVEL) + 1)
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
    def _load_config(self) -> dict:
        cfg = {"jobs": [], "plays": [], "shop": []}
        if not os.path.exists(CONFIG_FILE):
            return cfg
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw_lines = f.readlines()
        except Exception as e:
            logger.error(f"[插件] 读取后台配置失败: {e}")
            return cfg

        cur = None
        for raw in raw_lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                inner = line[1:-1]
                if ":" in inner:
                    typ, nm = inner.split(":", 1)
                    typ, nm = typ.strip(), nm.strip()
                    cur = {"type": typ, "name": nm, "data": {}}
                    if typ == "打工":
                        cfg["jobs"].append(cur)
                    elif typ == "玩耍":
                        cfg["plays"].append(cur)
                    elif typ == "商店":
                        cfg["shop"].append(cur)
                    else:
                        cur = None
                continue
            if cur is None:
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                cur["data"][k.strip()] = v.strip()

        return self._normalize_config(cfg)

    def _normalize_config(self, cfg: dict) -> dict:
        jobs = []
        for j in cfg["jobs"]:
            d = j["data"]
            jobs.append({
                "name": j["name"],
                "desc": d.get("描述", ""),
                "min_level": self._f(d.get("最低等级", 0)),
                "min_health": self._f(d.get("最低健康度", 0)),
                "min_mood": self._f(d.get("最低心情值", 0)),
                "cost": {
                    "stamina": self._f(d.get("消耗体力", 0)),
                    "satiety": self._f(d.get("消耗饱食度", 0)),
                    "thirst": self._f(d.get("消耗口渴值", 0)),
                    "health": self._f(d.get("消耗健康度", 0)),
                    "mood": self._f(d.get("消耗心情值", 0)),
                },
                "time": self._f(d.get("需要时间", 0)),
                "coins": self._f(d.get("金币", 0)),
                "exp": self._f(d.get("经验", 0)),
            })

        plays = []
        for p in cfg["plays"]:
            d = p["data"]
            plays.append({
                "name": p["name"],
                "desc": d.get("描述", ""),
                "min_level": self._f(d.get("最低等级", 0)),
                "min_health": self._f(d.get("最低健康度", 0)),
                "min_mood": self._f(d.get("最低心情值", 0)),
                "cost": {
                    "stamina": self._f(d.get("消耗体力", 0)),
                    "satiety": self._f(d.get("消耗饱食度", 0)),
                    "thirst": self._f(d.get("消耗口渴值", 0)),
                    "health": self._f(d.get("消耗健康度", 0)),
                },
                "time": self._f(d.get("需要时间", 0)),
                "exp": self._f(d.get("经验", 0)),
                "mood": self._f(d.get("心情值", 0)),
            })

        shop = []
        for s in cfg["shop"]:
            d = s["data"]
            shop.append({
                "name": s["name"],
                "type": d.get("类型", ""),
                "price": self._f(d.get("价格", 0)),
                "effects": {
                    "satiety": self._f(d.get("饱食度", 0)),
                    "thirst": self._f(d.get("口渴值", 0)),
                    "stamina": self._f(d.get("体力", 0)),
                    "mood": self._f(d.get("心情值", 0)),
                    "health": self._f(d.get("健康度", 0)),
                },
            })

        return {"jobs": jobs, "plays": plays, "shop": shop}

    # ================= WebUI 后端数据接口 =================
    def _read_config_text(self) -> str:
        """读取 后台.txt 原文，供 WebUI 编辑器使用"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            logger.error(f"[插件] 读取后台配置失败: {e}")
        return ""

    def _write_config_text(self, text: str):
        """保存 后台.txt 原文，返回 (ok, msg)"""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                f.write(text)
            return True, "保存成功"
        except Exception as e:
            return False, f"保存失败: {e}"

    def _read_data_text(self) -> str:
        """读取 data.json 原文，供 WebUI 导入导出使用"""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            logger.error(f"[插件] 读取数据失败: {e}")
        return "{}"

    def _write_data_text(self, text: str):
        """导入 data.json（覆盖），先做 JSON 校验，返回 (ok, msg)"""
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                return False, "数据必须是 JSON 对象（{...}）"
        except Exception as e:
            return False, f"JSON 格式错误: {e}"
        try:
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, DATA_FILE)
            return True, "导入成功"
        except Exception as e:
            return False, f"写入失败: {e}"

    # ================= WebUI Pages 后端 API =================
    async def web_get_backend_config(self):
        """读取 后台.txt 内容"""
        return json_response({"content": self._read_config_text()})

    async def web_save_backend_config(self):
        """保存 后台.txt 内容"""
        payload = await request.json(default={})
        content = payload.get("content")
        if not isinstance(content, str):
            return error_response("content 必须是字符串", status_code=400)
        ok, msg = self._write_config_text(content)
        if not ok:
            return error_response(msg, status_code=400)
        return json_response({"saved": True})

    async def web_export_data(self):
        """导出 data.json：返回 JSON 内容，由前端用 Blob 触发下载"""
        if not os.path.exists(DATA_FILE):
            return error_response("data.json 不存在", status_code=404)
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return error_response(f"读取失败: {e}", status_code=500)
        return json_response({"content": content})

    async def web_import_data(self):
        """导入 data.json：JSON 请求体携带 content，覆盖写入"""
        payload = await request.json(default={})
        content = payload.get("content")
        if not isinstance(content, str):
            return error_response("content 必须是字符串", status_code=400)
        ok, msg = self._write_data_text(content)
        if not ok:
            return error_response(msg, status_code=400)
        return json_response({"imported": True})

    # ================= 宠物：指令 =================
    @filter.command("解锁宠物")
    async def unlock_pet(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_unlock_pet(event)
        yield event.plain_result(text)

    def _handle_unlock_pet(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        if key in data.get("pets", {}):
            pet = data["pets"][key]
            return f"{name} 已经拥有一只宠物「{pet['name']}」啦，每位玩家最多只能养一只。"
        if self._coins_of(data, key) < self.pet_unlock_cost:
            return f"解锁宠物需要 {self.pet_unlock_cost} 金币（当前 {self._coins_of(data, key)}）。"

        self._add_coins(data, key, -self.pet_unlock_cost)
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

    @filter.command("宠物")
    async def pet_status(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_pet_status(event)
        yield event.plain_result(text)

    def _handle_pet_status(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」（需 {self.pet_unlock_cost} 金币）领养一只吧。"
        self._bring_pet_up_to_date(pet, date.today().isoformat())
        self._save(data)

        sat_max, thr_max, sta_max, mood_max = self._attr_max(pet["health"])
        need_exp = PET_EXP_PER_LEVEL - (pet["exp"] % PET_EXP_PER_LEVEL)
        lines = [
            f"🐾 {name} 的宠物「{pet['name']}」：",
            f"⭐ 等级：Lv.{pet['level']}（经验 {pet['exp']:.1f}）",
        ]
        if pet["level"] < PET_MAX_LEVEL:
            lines.append(f"📚 距下一级还需 {need_exp:.0f} 经验")
        lines += [
            f"🍖 饱食度：{pet['satiety']:.1f}/{sat_max:.0f}",
            f"💧 口渴值：{pet['thirst']:.1f}/{thr_max:.0f}",
            f"⚡ 体力：{pet['stamina']:.1f}/{sta_max:.0f}",
            f"😊 心情值：{pet['mood']:.1f}/{mood_max:.0f}",
            f"❤️ 健康度：{pet['health']:.1f}/{PET_MAX_HEALTH:.0f}",
        ]
        if pet["health"] <= 39:
            lines.append("🤒 宠物生病了，快给它吃药吧！")
        return "\n".join(lines)

    @filter.command("更改宠物名字")
    async def rename_pet(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_rename_pet(event)
        yield event.plain_result(text)

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

    @filter.command("打工")
    async def work(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_work(event)
        yield event.plain_result(text)

    def _handle_work(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)

        if len(parts) < 2:
            return self._work_list()
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

        # 应用消耗
        for attr, cost in job["cost"].items():
            pet[attr] = round(max(0.0, pet[attr] - cost), 2)

        # 报酬
        user = self._ensure_user(data, key)
        user["coins"] = int(user.get("coins", 0)) + int(job["coins"])
        pet["exp"] = round(float(pet.get("exp", 0.0)) + job["exp"], 2)
        lvl_msg = self._apply_exp(pet)
        self._clamp_attrs(pet)
        self._save(data)

        return (f"💼 {name} 的宠物去「{job['name']}」打工完成！\n"
                f"💰 金币 +{int(job['coins'])}，🐾 经验 +{job['exp']:.1f}{lvl_msg}")

    def _work_list(self) -> str:
        cfg = self._load_config()
        if not cfg["jobs"]:
            return "后台还没有配置打工项目（请管理员编辑 后台.txt）。"
        lines = ["💼 打工列表（发送「打工 <名称>」开始）："]
        for j in cfg["jobs"]:
            lines.append(f"· {j['name']}：{j['desc']}｜要求 Lv.{int(j['min_level'])}+ / 健康 {j['min_health']:.0f}+ / 心情 {j['min_mood']:.0f}+｜耗时 {j['time']:.0f}分｜金币 +{int(j['coins'])} 经验 +{j['exp']:.0f}")
        return "\n".join(lines)

    @filter.command("玩耍")
    async def play(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_play(event)
        yield event.plain_result(text)

    def _handle_play(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)

        if len(parts) < 2:
            return self._play_list()
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

        if pet["level"] < play["min_level"]:
            return f"宠物等级不足（需要 Lv.{int(play['min_level'])}，当前 Lv.{pet['level']}）。"
        if pet["health"] < play["min_health"]:
            return f"宠物健康度不足（需要 {play['min_health']:.0f}，当前 {pet['health']:.1f}）。"
        if pet["mood"] < play["min_mood"]:
            return f"宠物心情不足（需要 {play['min_mood']:.0f}，当前 {pet['mood']:.1f}）。"

        for attr, cost in play["cost"].items():
            if cost > 0 and pet[attr] < cost:
                return f"{ATTR_LABELS[attr]}不足，无法玩耍（需要 {cost:.0f}，当前 {pet[attr]:.1f}）。"

        for attr, cost in play["cost"].items():
            pet[attr] = round(max(0.0, pet[attr] - cost), 2)

        pet["exp"] = round(float(pet.get("exp", 0.0)) + play["exp"], 2)
        pet["mood"] = round(pet["mood"] + play["mood"], 2)
        lvl_msg = self._apply_exp(pet)
        self._clamp_attrs(pet)

        bonus = ""
        if pet.get("money_event_count", 0) < self.money_event_max_per_day and random.random() < self.money_event_chance:
            user = self._ensure_user(data, key)
            user["coins"] = int(user.get("coins", 0)) + self.money_event_gain
            pet["money_event_count"] = int(pet.get("money_event_count", 0)) + 1
            bonus = f"\n🍀 触发「捡到钱了」事件，金币 +{self.money_event_gain}！"

        self._save(data)
        return (f"🎾 {name} 的宠物去「{play['name']}」玩耍完成！\n"
                f"🐾 经验 +{play['exp']:.1f}，😊 心情 +{play['mood']:.1f}{lvl_msg}{bonus}")

    def _play_list(self) -> str:
        cfg = self._load_config()
        if not cfg["plays"]:
            return "后台还没有配置玩耍项目（请管理员编辑 后台.txt）。"
        lines = ["🎾 玩耍列表（发送「玩耍 <名称>」开始）："]
        for p in cfg["plays"]:
            lines.append(f"· {p['name']}：{p['desc']}｜要求 Lv.{int(p['min_level'])}+ / 健康 {p['min_health']:.0f}+ / 心情 {p['min_mood']:.0f}+｜耗时 {p['time']:.0f}分｜经验 +{p['exp']:.0f} 心情 +{p['mood']:.0f}")
        return "\n".join(lines)

    @filter.command("商店")
    async def shop(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_shop(event)
        yield event.plain_result(text)

    def _handle_shop(self, event: AstrMessageEvent) -> str:
        cfg = self._load_config()
        if not cfg["shop"]:
            return "商店暂无商品（请管理员编辑 后台.txt）。"
        lines = ["🛒 宠物商店（发送「购买 <道具名>」购买，发送「使用 <道具名>」使用）："]
        for it in cfg["shop"]:
            lines.append(f"· {it['name']}（{it['type']}）{int(it['price'])}金币：{self._effect_desc(it['effects'])}")
        lines.append(f"· {PILL_NAME}（特殊）：随机提升全属性 1.0~5.0（签到有几率获得）")
        return "\n".join(lines)

    def _effect_desc(self, effects: dict) -> str:
        parts = []
        for k, short in ATTR_SHORT.items():
            v = effects.get(k, 0)
            if v > 0:
                parts.append(f"{short}+{v:.0f}")
            elif v < 0:
                parts.append(f"{short}{v:.0f}")
        return " ".join(parts) if parts else "无效果"

    @filter.command("购买")
    async def buy(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_buy(event)
        yield event.plain_result(text)

    def _handle_buy(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：购买 <道具名>"

        item_name = parts[1].strip()
        cfg = self._load_config()
        item = next((it for it in cfg["shop"] if it["name"] == item_name), None)
        if not item:
            return f"商店没有「{item_name}」这个道具，发送「商店」查看。"

        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"

        price = int(item["price"])
        if self._coins_of(data, key) < price:
            return f"金币不足（需要 {price}，当前 {self._coins_of(data, key)}）。"

        self._add_coins(data, key, -price)
        inv = pet.setdefault("inventory", {})
        inv[item_name] = int(inv.get(item_name, 0)) + 1
        self._save(data)
        return f"🛒 {name} 花费 {price} 金币购买了「{item_name}」×1。发送「使用 {item_name}」使用。"

    @filter.command("使用")
    async def use_item(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_use_item(event)
        yield event.plain_result(text)

    def _handle_use_item(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：使用 <道具名>"

        item_name = parts[1].strip()
        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"
        self._bring_pet_up_to_date(pet, date.today().isoformat())

        user = data.get("users", {}).get(key, {})
        fav_level = self._level_of(float(user.get("favorability", 0.0)))
        pos_mult, neg_mult = self._fav_multipliers(fav_level)

        inv = pet.setdefault("inventory", {})

        # 属性丸（特殊道具）
        if item_name == PILL_NAME:
            if int(inv.get(PILL_NAME, 0)) < 1:
                return f"你没有属性丸（签到有几率获得）。"
            boosts = {}
            for attr in ATTR_LABELS:
                boosts[attr] = round(random.uniform(1.0, 5.0) * pos_mult, 2)
                pet[attr] = round(pet[attr] + boosts[attr], 2)
            inv[PILL_NAME] = int(inv.get(PILL_NAME, 0)) - 1
            if inv[PILL_NAME] <= 0:
                inv.pop(PILL_NAME, None)
            self._clamp_attrs(pet)
            self._save(data)
            desc = "，".join(f"{ATTR_SHORT[a]}+{v:.1f}" for a, v in boosts.items())
            return f"💊 {name} 使用了属性丸：{desc}"

        # 商店道具
        cfg = self._load_config()
        item = next((it for it in cfg["shop"] if it["name"] == item_name), None)
        if not item:
            return f"没有「{item_name}」这个道具，发送「商店」查看。"
        if int(inv.get(item_name, 0)) < 1:
            return f"你没有「{item_name}」，发送「购买 {item_name}」购买。"

        changes = []
        for attr in ATTR_LABELS:
            val = item["effects"].get(attr, 0)
            if val > 0:
                applied = round(val * pos_mult, 2)
            elif val < 0:
                applied = round(val * neg_mult, 2)
            else:
                continue
            pet[attr] = round(pet[attr] + applied, 2)
            changes.append(f"{ATTR_SHORT[attr]}{applied:+.1f}")

        inv[item_name] = int(inv.get(item_name, 0)) - 1
        if inv[item_name] <= 0:
            inv.pop(item_name, None)
        self._clamp_attrs(pet)
        self._save(data)

        return f"✅ {name} 使用了「{item_name}」：{'，'.join(changes)}"

    @filter.command("背包")
    async def bag(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_bag(event)
        yield event.plain_result(text)

    def _handle_bag(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物。"
        inv = pet.get("inventory", {})
        if not inv:
            return f"{name} 的背包是空的。"
        lines = ["🎒 背包："]
        for item, cnt in inv.items():
            lines.append(f"· {item} ×{cnt}")
        return "\n".join(lines)

    @filter.command("宠物帮助")
    async def pet_help(self, event: AstrMessageEvent):
        text = (f"🐾 宠物系统玩法：\n"
                f"· 解锁宠物：解锁宠物（{self.pet_unlock_cost} 金币，每人限一只）\n"
                f"· 查看：宠物 / 背包\n"
                f"· 起名：更改宠物名字 <名字>\n"
                f"· 打工：打工（列表）/ 打工 <名称>\n"
                f"· 玩耍：玩耍（列表）/ 玩耍 <名称>\n"
                f"· 商店：商店 / 购买 <道具名> / 使用 <道具名>\n"
                f"· 每日签到可获宠物经验，并概率获得属性丸\n"
                f"· 每日零点结算宠物状态（饱食/口渴/体力/心情/健康）\n"
                f"· 管理员可编辑 后台.txt 自定义打工/玩耍/商店")
        yield event.plain_result(text)

    @filter.command("签到帮助")
    async def help_all(self, event: AstrMessageEvent):
        text = (f"📖 本插件全部功能指令：\n"
                f"\n"
                f"【签到】\n"
                f"· 签到：每日签到，获得金币 / 好感度（有宠物时额外获得经验与属性丸）\n"
                f"· 我的签到：查看金币与好感度\n"
                f"· 签到帮助：查看本帮助\n"
                f"\n"
                f"【左轮手枪】\n"
                f"· 装弹 <子弹数 1~6> <金币>：发起一局游戏\n"
                f"· 加入：加入当前这局游戏\n"
                f"· 开始：发起人提前开始（≥2 人）\n"
                f"· 开枪：轮到你的回合时开枪\n"
                f"· 我的战绩：查看胜率 / 净收益 / 金币往来\n"
                f"\n"
                f"【宠物】\n"
                f"· 解锁宠物：花 {self.pet_unlock_cost} 金币领养宠物（每人限一只）\n"
                f"· 宠物：查看宠物属性 / 等级 / 经验\n"
                f"· 更改宠物名字 <名字>：给宠物起名\n"
                f"· 打工：查看打工列表\n"
                f"· 打工 <名称>：开始打工\n"
                f"· 玩耍：查看玩耍列表\n"
                f"· 玩耍 <名称>：开始玩耍\n"
                f"· 商店：查看宠物商店\n"
                f"· 购买 <道具名>：购买道具\n"
                f"· 使用 <道具名>：使用道具\n"
                f"· 背包：查看背包\n"
                f"· 宠物帮助：查看宠物玩法详情\n"
                f"\n"
                f"【数据管理（管理员）】\n"
                f"· 查看后台配置：查看 后台.txt 内容\n"
                f"· 保存后台配置 <内容>：覆盖 后台.txt\n"
                f"· 导出数据：导出 data.json 到文件\n"
                f"· 导入数据 <JSON>：导入 data.json")
        yield event.plain_result(text)

    # ================= 数据管理（后台.txt 编辑 / 数据导入导出） =================
    @filter.command("查看后台配置")
    async def view_backend_config(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._read_config_text()
        if not text.strip():
            yield event.plain_result("后台配置为空。")
        else:
            yield event.plain_result(f"当前 后台.txt 内容：\n{text}")

    @filter.command("保存后台配置")
    async def save_backend_config(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_save_backend_config(event)
        yield event.plain_result(text)

    def _handle_save_backend_config(self, event: AstrMessageEvent) -> str:
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return "格式：保存后台配置 <内容>（先「查看后台配置」复制全文，改好后粘贴到指令后）"
        ok, msg = self._write_config_text(parts[1].strip() + "\n")
        return f"✅ {msg}" if ok else f"❌ {msg}"

    @filter.command("导出数据")
    async def export_data(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_export_data()
        yield event.plain_result(text)

    def _handle_export_data(self) -> str:
        data = self._load()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = os.path.join(os.path.dirname(DATA_FILE), f"data_export_{ts}.json")
        try:
            with open(bak, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"❌ 导出失败: {e}"
        s = json.dumps(data, ensure_ascii=False)
        if len(s) <= 3500:
            return f"✅ 数据已导出到：{bak}\n内容：\n{s}"
        return f"✅ 数据已导出到：{bak}\n数据较大（{len(s)} 字符），请直接到上述路径取文件。"

    @filter.command("导入数据")
    async def import_data(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_import_data(event)
        yield event.plain_result(text)

    def _handle_import_data(self, event: AstrMessageEvent) -> str:
        parts = event.message_str.split(maxsplit=1)
        if len(parts) >= 2 and parts[1].strip():
            raw = parts[1].strip()
        else:
            imp = os.path.join(os.path.dirname(DATA_FILE), "data_import.json")
            if not os.path.exists(imp):
                return "请把要导入的数据保存为插件目录下的 data_import.json，或直接发送「导入数据 <JSON内容>」。"
            try:
                with open(imp, "r", encoding="utf-8") as f:
                    raw = f.read()
            except Exception as e:
                return f"❌ 读取 data_import.json 失败: {e}"
        ok, msg = self._write_data_text(raw)
        return "✅ 导入成功！" if ok else f"❌ {msg}"

    # ================= 左轮手枪：内部逻辑 =================
    def _start_game(self, game: RouletteGame) -> None:
        if game.timer_task and game.timer_task is not asyncio.current_task():
            game.timer_task.cancel()
        game.timer_task = None
        game.status = "playing"
        game.magazines = [False] * ROULETTE_MAGAZINES
        for idx in random.sample(range(ROULETTE_MAGAZINES), game.bullets):
            game.magazines[idx] = True
        game.shot_index = 0
        game.turn_index = 0

    def _start_announcement(self, game: RouletteGame) -> str:
        cur = game.players[0]
        return (f"🔫 左轮手枪游戏开始！\n"
                f"👥 玩家：{game.player_names()}\n"
                f"🔹 子弹：{game.bullets} 发（共 {ROULETTE_MAGAZINES} 个弹匣，随机排列）\n"
                f"💰 赌注：{game.stake} 金币/人\n"
                f"🎯 当前回合：{cur['name']}（发送「开枪」）")

    def _finish_game(self, data: dict, game: RouletteGame, loser_index: int) -> str:
        loser = game.players[loser_index]
        winners = [p for i, p in enumerate(game.players) if i != loser_index]

        loser_key = f"{game.group_id}:{loser['id']}"
        actual_loss = min(game.stake, self._coins_of(data, loser_key))
        self._add_coins(data, loser_key, -actual_loss)

        payout_total = int(actual_loss * (1 - ROULETTE_FEE_RATE))
        share = payout_total // len(winners)

        loser_stat = self._ensure_stat(data, loser_key)
        loser_stat["losses"] += 1
        loser_stat["net"] -= actual_loss

        lines = [f"💥 {loser['name']} 开枪：第 {game.shot_index + 1} 个弹匣——砰！有子弹！"]
        lines.append(f"😵 {loser['name']} 判负，扣除 {actual_loss} 金币。")

        winner_names = []
        for w in winners:
            wkey = f"{game.group_id}:{w['id']}"
            self._add_coins(data, wkey, share)
            wstat = self._ensure_stat(data, wkey)
            wstat["wins"] += 1
            wstat["net"] += share
            self._record(data, wkey, "won_from", loser["id"], loser["name"], share)
            self._record(data, loser_key, "lost_to", w["id"], w["name"], share)
            winner_names.append(w["name"])

        if len(winner_names) == 1:
            lines.append(f"🏆 {winner_names[0]} 获得 {share} 金币（已扣除 10% 手续费）。")
        else:
            lines.append(f"🏆 {('、'.join(winner_names))} 各获得 {share} 金币（已扣除 10% 手续费）。")
        return "\n".join(lines)

    def _schedule_timeout(self, game: RouletteGame) -> None:
        async def _timeout():
            try:
                await asyncio.sleep(ROULETTE_JOIN_TIMEOUT)
            except asyncio.CancelledError:
                return
            async with self._lock:
                g = self._games.get(game.group_id)
                if g is not game or g.status != "waiting":
                    return
                if len(g.players) >= ROULETTE_MIN_PLAYERS:
                    self._start_game(g)
                    text = self._start_announcement(g)
                else:
                    self._games.pop(g.group_id, None)
                    return
            try:
                await game.event.send(text)
            except Exception as e:
                logger.error(f"[左轮] 自动开始公告发送失败: {e}")

        game.timer_task = asyncio.create_task(_timeout())

    # ================= 左轮手枪：指令 =================
    @filter.command("装弹")
    async def roulette_load(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_load(event)
        yield event.plain_result(text)

    def _handle_load(self, event: AstrMessageEvent) -> str:
        gid = event.get_group_id()
        if not gid:
            return "左轮手枪游戏只能在群里玩哦～"
        if self._games.get(gid):
            g = self._games[gid]
            return f"本群已有一局左轮手枪游戏进行中（{g.player_names()}），请等它结束。"

        uid = event.get_sender_id()
        name = event.get_sender_name()
        parts = event.message_str.split()
        if len(parts) != 3:
            return f"格式：装弹 <子弹数量 1~{ROULETTE_MAX_BULLETS}> <金币>，例如：装弹 3 100"

        try:
            bullets = int(parts[1])
            stake = int(parts[2])
        except ValueError:
            return "子弹数量和金币必须是整数。格式：装弹 <子弹数量 1~6> <金币>"

        if not (1 <= bullets <= ROULETTE_MAX_BULLETS):
            return f"子弹数量必须在 1~{ROULETTE_MAX_BULLETS} 之间。"
        if stake < 1:
            return "金币必须为正整数。"

        key = f"{gid}:{uid}"
        data = self._load()
        if self._coins_of(data, key) < stake:
            return f"你的金币不足（当前 {self._coins_of(data, key)}，需要 {stake}）。"

        game = RouletteGame(gid, uid, name, bullets, stake)
        game.event = event
        self._games[gid] = game
        self._schedule_timeout(game)

        return (f"🔫 {name} 发起了一局左轮手枪游戏！\n"
                f"🔹 子弹：{bullets} 发（共 {ROULETTE_MAGAZINES} 个弹匣，随机排列）\n"
                f"💰 赌注：{stake} 金币/人\n"
                f"👥 发送「加入」参与（至少 {ROULETTE_MIN_PLAYERS} 人，最多 {ROULETTE_MAX_PLAYERS} 人）\n"
                f"⏳ {ROULETTE_JOIN_TIMEOUT} 秒内无人加入则自动取消")

    @filter.command("加入")
    async def roulette_join(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_join(event)
        yield event.plain_result(text)

    def _handle_join(self, event: AstrMessageEvent) -> str:
        gid = event.get_group_id()
        if not gid:
            return "左轮手枪游戏只能在群里玩哦～"
        uid = event.get_sender_id()
        name = event.get_sender_name()
        game = self._games.get(gid)
        if not game or game.status != "waiting":
            return "当前没有正在等待加入的左轮手枪游戏。"
        if any(p["id"] == uid for p in game.players):
            return f"{name}，你已经在这局游戏里了。"
        if len(game.players) >= ROULETTE_MAX_PLAYERS:
            return "本局人数已满。"

        key = f"{gid}:{uid}"
        data = self._load()
        if self._coins_of(data, key) < game.stake:
            return f"{name} 金币不足，无法加入（需要 {game.stake} 金币）。"

        game.players.append({"id": uid, "name": name})
        if len(game.players) >= ROULETTE_MAX_PLAYERS:
            self._start_game(game)
            return self._start_announcement(game)

        return (f"✅ {name} 加入了游戏！\n"
                f"👥 当前玩家（{len(game.players)}/{ROULETTE_MAX_PLAYERS}）：{game.player_names()}\n"
                f"发起人可发送「开始」立即开始，或等待更多玩家加入。")

    @filter.command("开始")
    async def roulette_start(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_start(event)
        yield event.plain_result(text)

    def _handle_start(self, event: AstrMessageEvent) -> str:
        gid = event.get_group_id()
        uid = event.get_sender_id()
        game = self._games.get(gid)
        if not game or game.status != "waiting":
            return "当前没有待开始的左轮手枪游戏。"
        if uid != game.starter_id:
            return "只有发起人可以开始游戏。"
        if len(game.players) < ROULETTE_MIN_PLAYERS:
            return f"至少需要 {ROULETTE_MIN_PLAYERS} 名玩家才能开始。"
        self._start_game(game)
        return self._start_announcement(game)

    @filter.command("开枪")
    async def roulette_shoot(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_shoot(event)
        yield event.plain_result(text)

    def _handle_shoot(self, event: AstrMessageEvent) -> str:
        gid = event.get_group_id()
        uid = event.get_sender_id()
        name = event.get_sender_name()
        game = self._games.get(gid)
        if not game:
            return "当前没有进行中的左轮手枪游戏。"
        if game.status != "playing":
            return "游戏还没开始，等待玩家加入或发送「开始」。"

        cur = game.players[game.turn_index]
        if cur["id"] != uid:
            return f"还没轮到你开枪，当前是 {cur['name']} 的回合。"

        shot_num = game.shot_index + 1
        if not game.magazines[game.shot_index]:
            game.shot_index += 1
            game.turn_index = (game.turn_index + 1) % len(game.players)
            nxt = game.players[game.turn_index]
            return (f"🔫 {name} 开枪：第 {shot_num} 个弹匣——咔嚓，是空膛！\n"
                    f"🎯 轮到 {nxt['name']}（发送「开枪」）")

        data = self._load()
        text = self._finish_game(data, game, game.turn_index)
        self._save(data)
        self._games.pop(gid, None)
        return text

    @filter.command("我的战绩")
    async def roulette_stats(self, event: AstrMessageEvent):
        async with self._lock:
            text = self._handle_stats(event)
        yield event.plain_result(text)

    def _handle_stats(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        stat = data.get("roulette", {}).get(key)

        if not stat or (stat.get("wins", 0) + stat.get("losses", 0)) == 0:
            return (f"{name} 还没有左轮手枪战绩，\n"
                    f"发送「装弹 <子弹数量 1~6> <金币>」开局吧～")

        wins = stat.get("wins", 0)
        losses = stat.get("losses", 0)
        total = wins + losses
        rate = wins / total * 100
        net = stat.get("net", 0)
        net_str = f"+{net}" if net >= 0 else str(net)

        lines = [
            f"📊 {name} 的左轮手枪战绩：",
            f"🎮 局数：{total}（胜 {wins} / 负 {losses}）",
            f"📈 胜率：{rate:.1f}%",
            f"💰 净收益：{net_str} 金币",
        ]
        lost_to = stat.get("lost_to", {})
        won_from = stat.get("won_from", {})
        if lost_to:
            worst = max(lost_to.items(), key=lambda kv: kv[1]["amount"])
            lines.append(f"👊 拿走你最多金币的人：{worst[1]['name']}（{worst[1]['amount']} 金币）")
        else:
            lines.append("👊 拿走你最多金币的人：无")
        if won_from:
            best = max(won_from.items(), key=lambda kv: kv[1]["amount"])
            lines.append(f"💸 你拿走最多金币的人：{best[1]['name']}（{best[1]['amount']} 金币）")
        else:
            lines.append("💸 你拿走最多金币的人：无")
        return "\n".join(lines)
