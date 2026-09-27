# -*- coding: utf-8 -*-
"""宠物看家插件（3.0.0 功能插件）。

自 2.3.0 modules/farm.py `_handle_guard`（看家 开/关，旧 1974-1989 行）与 `_do_steal`
内的宠物加护拦截判定（旧 2097-2211 行）原样迁移：
  - 「看家 开/关」：开关存于宠物记录 pets[key]["guard"]（与旧版字段名/位置一致）；
    2.0.1 起宠物加护自动生效，本开关仅作偏好标记（拦截判定不读它）。
  - intercept(data, thief_key, target_key, gain)：偷菜方在扣产量/入账前调用，
    返回 {"guard": None|"catch"|"stop", "fine": int, "stamina": (lo, hi)|None}——
    guard_effective = 宠物存在、非虚弱、空闲且状态档位 < STEAL_GUARD_TIER_MAX(3)；
    气味记忆概率 ×STEAL_SCENT_MULT、等级压制 ÷STEAL_PET_GAP_DIV（2.0.1 同款）；
    「抓到你了」→ 连续成功清零 + 气味记忆 24h，体力消耗区间 (STEAL_GUARD_CATCH_STAMINA_MIN, MAX)=(2, 5)；
    「给我站住」→ 罚款 = 本次原金额 ×STEAL_FINE_RATIO(1.1)（由偷菜方扣款并转赔农场主）、
    目标农场 guard_suppress_until = now + STEAL_SUPPRESS_HOURS(12h)、体力区间 (3, 6)；
    未触发 → 连续成功 +1（达标 STEAL_SCENT_CONSEC 留气味记忆），与旧 2226-2229 一致。
拦截产生的数据变更不存盘，由调用方（持有 core.lock 的偷菜流程）统一 core.save()。
"""
import random
from datetime import datetime

from astrbot.api import logger

from ..core import WEAK_HEAL_COST

NAME = "farm_guard"

# 2.2.7 统一数值管理：档位下限 = 属性最大值的 60% / 35% / 15%，低于 15% 为四档
_TIER_PCTS = (0.6, 0.35, 0.15)


def _attr_tier(val, max_v) -> int:
    """按属性最大值推导档位：≥60% 一档、≥35% 二档、≥15% 三档、否则四档"""
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


class FarmGuardApi:
    """宠物看家服务接口（其它插件经 core.service("farm_guard") 调用）"""

    def __init__(self, core):
        self._core = core

    # ---- 内部工具 ----
    def _worst_tier(self, tpet) -> int:
        """饱食/口渴/心情最差档位（2.0.1 四档制；属性上限经 pet 服务 attr_max 推导）。
        宠物服务未挂载或缺 attr_max 时按 2.0.1 兼容回退：返回 1（视为看家有效）。"""
        pet_svc = self._core.service("pet")
        attr_max = getattr(pet_svc, "attr_max", None) if pet_svc is not None else None
        if attr_max is None:
            return 1
        try:
            sat_max, thr_max, _sta_max, mood_max = attr_max(float(tpet.get("health", 0) or 0))
        except Exception:
            return 1
        return max(_attr_tier(tpet.get("satiety", 0), sat_max),
                   _attr_tier(tpet.get("thirst", 0), thr_max),
                   _attr_tier(tpet.get("mood", 0), mood_max))

    @staticmethod
    def _busy_until(tpet) -> float:
        """打工/玩耍共用冷却计时器（兼容旧数据 work_until / play_until）"""
        busy = float(tpet.get("busy_until", 0) or 0)
        old = max(float(tpet.get("work_until", 0) or 0), float(tpet.get("play_until", 0) or 0))
        return max(busy, old)

    def _apply_scent(self, data, thief_key, target_key, now_ts):
        """气味记忆（记在偷菜者农场记录上、针对本农场主；有效期 STEAL_SCENT_HOURS，旧 2157-2158）"""
        thief_farm = data.setdefault("farms", {}).setdefault(thief_key, {})
        hours = float(self._core.param("STEAL_SCENT_HOURS", 24))
        thief_farm.setdefault("scent_memory", {})[target_key] = now_ts + hours * 3600

    # ---- 对外接口 ----
    def intercept(self, data, thief_key, target_key, gain=0):
        """宠物加护拦截判定（旧 2097-2211 行原样迁移；偷菜插件在取走作物前调用）。
        gain = 本次偷菜的原金额（扣减前计划收益），用于罚款与连续成功计数。
        返回 {"guard": None|"catch"|"stop", "fine": int, "stamina": (lo, hi)|None}：
        stamina 区间由调用方对农场主宠物结算（体力 -randint(lo, hi)）；
        fine 由调用方扣偷菜者余额并转赔农场主（余额不足部分作废，旧 2192-2200）。
        不存盘，由调用方统一 core.save()。"""
        core = self._core
        now_ts = datetime.now().timestamp()
        tdata_farm = data.get("farms", {}).get(target_key)
        if not isinstance(tdata_farm, dict):
            return {"guard": None, "fine": 0, "stamina": None}
        tpet = data.get("pets", {}).get(target_key)

        # ---- 宠物加护判定（农场主宠物激活 + 非虚弱 + 空闲 + 状态档位 1~2，旧 2095-2103） ----
        guard_effective = False
        if tpet is not None and not tpet.get("weak") and not (now_ts < self._busy_until(tpet)):
            pet_tier = self._worst_tier(tpet)
            guard_tier_max = int(core.param("STEAL_GUARD_TIER_MAX", 3))
            guard_effective = pet_tier < guard_tier_max

        # ---- 参数（全部 WebUI 可编辑，缺省与 2.3.0 一致） ----
        guard_catch = float(core.param("STEAL_GUARD_CATCH", 0.10))
        guard_stop = float(core.param("STEAL_GUARD_STOP", 0.20))
        pet_gap = int(core.param("STEAL_PET_GAP", 10))
        pet_gap_div = float(core.param("STEAL_PET_GAP_DIV", 2.0))
        scent_mult = float(core.param("STEAL_SCENT_MULT", 3.0))
        scent_hours = float(core.param("STEAL_SCENT_HOURS", 24))
        scent_consec = int(core.param("STEAL_SCENT_CONSEC", 3))
        suppress_hours = float(core.param("STEAL_SUPPRESS_HOURS", 12))
        fine_ratio = float(core.param("STEAL_FINE_RATIO", 1.10))

        # 气味记忆（记在偷菜者身上、针对本农场主；触发概率 ×scent_mult，旧 2105-2108）
        thief_farm = data.setdefault("farms", {}).setdefault(thief_key, {})
        thief_scent = thief_farm.setdefault("scent_memory", {})
        has_scent = float(thief_scent.get(target_key, 0) or 0) > now_ts

        # 等级压制：偷菜者宠物等级比农场主低 pet_gap 级及以上 → 概率减半
        # （「给我站住」触发后 suppress_hours 内失效，旧 2110-2116）
        thief_pet = data.get("pets", {}).get(thief_key)
        thief_pet_lv = int(thief_pet.get("level", 0)) if thief_pet else 0
        suppress_until = float(tdata_farm.get("guard_suppress_until", 0) or 0)
        suppressed = bool(tpet) and (int(tpet.get("level", 0)) - thief_pet_lv >= pet_gap) \
            and now_ts >= suppress_until

        # 加护触发（旧 2118-2133）
        guard_effect = None
        if guard_effective:
            p_catch, p_stop = guard_catch, guard_stop
            if has_scent:
                # 气味记忆：概率 ×scent_mult（不受等级压制影响）
                p_catch *= scent_mult
                p_stop *= scent_mult
            elif suppressed:
                p_catch /= pet_gap_div
                p_stop /= pet_gap_div
            rnd = random.random()
            if rnd < p_catch:
                guard_effect = "catch"
            elif rnd < p_catch + p_stop:
                guard_effect = "stop"

        # ---- 连续成功计数（被偷方记录，气味记忆来源，旧 2135-2140） ----
        consec = tdata_farm.setdefault("steal_consec", {})
        ce = consec.setdefault(thief_key, {"ts": now_ts, "count": 0})
        if now_ts - ce["ts"] > 86400:
            ce["ts"] = now_ts
            ce["count"] = 0

        # ---- 抓到你了：连续成功清零 + 气味记忆 24h（体力由调用方按区间结算，旧 2160-2174） ----
        if guard_effect == "catch":
            ce["count"] = 0  # 失败 → 连续成功清零
            c_min = int(float(core.param("STEAL_GUARD_CATCH_STAMINA_MIN", 2)))
            c_max = int(float(core.param("STEAL_GUARD_CATCH_STAMINA_MAX", 5)))
            self._apply_scent(data, thief_key, target_key, now_ts)
            return {"guard": "catch", "fine": 0, "stamina": (c_min, c_max)}

        # ---- 给我站住：罚款=原金额110% + 等级压制失效（旧 2176-2211） ----
        if guard_effect == "stop":
            fine = int(round(float(gain or 0) * fine_ratio))
            s_min = int(float(core.param("STEAL_GUARD_STOP_STAMINA_MIN", 3)))
            s_max = int(float(core.param("STEAL_GUARD_STOP_STAMINA_MAX", 6)))
            tdata_farm["guard_suppress_until"] = now_ts + suppress_hours * 3600
            if float(gain or 0) > 0:
                ce["count"] += 1
                if ce["count"] >= scent_consec:
                    self._apply_scent(data, thief_key, target_key, now_ts)
            return {"guard": "stop", "fine": fine, "stamina": (s_min, s_max)}

        # ---- 未触发：连续成功 +1（有收益时，达标留气味记忆，旧 2226-2229） ----
        if float(gain or 0) > 0:
            ce["count"] += 1
            if ce["count"] >= scent_consec:
                self._apply_scent(data, thief_key, target_key, now_ts)
        return {"guard": None, "fine": 0, "stamina": None}


def register(core):
    api = FarmGuardApi(core)

    # ================= 看家 开/关（旧 1974-1989 原样迁移） =================
    @core.command("看家", feature="farm_guard")
    def handle_guard(event):
        """看家 <开/关>：开启/关闭宠物看家（2.0.1 宠物加护改为自动生效：
        宠物激活且空闲、状态档位1-2 时自动保护，本开关仅作偏好标记）"""
        name = core.user_name(event)
        key = core.user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or parts[1].strip() not in ("开", "关"):
            return "格式：看家 开 / 看家 关"
        pet_svc = core.service("pet")
        pet = None
        if pet_svc is not None:
            try:
                pet = pet_svc.pet_of(key)
            except Exception as e:
                logger.error(f"[farm_guard] pet.pet_of 调用异常: {e}")
                pet = None
        if not pet:
            return f"{name} 还没有宠物，无法开启看家。"
        # 虚弱宠物不能看家（pet 插件 weak_guard 提示优先，未提供时按 weak 字段兜底）
        weak_msg = None
        if hasattr(pet_svc, "weak_guard"):
            try:
                weak_msg = pet_svc.weak_guard(key)
            except Exception as e:
                logger.error(f"[farm_guard] pet.weak_guard 调用异常: {e}")
                weak_msg = None
        if weak_msg:
            return weak_msg
        if pet.get("weak"):
            return f"😷 {name} 的宠物处于虚弱状态，无法看家，发送「治疗宠物」（{WEAK_HEAL_COST} 金币）治疗。"
        pet["guard"] = parts[1].strip() == "开"
        core.save()
        return (f"✅ 看家已{'开启' if pet['guard'] else '关闭'}。"
                f"（宠物加护在宠物激活且空闲、状态档位 1-2 时自动生效，不受本开关限制）")

    core.expose("farm_guard", api)
    core.add_help("宠物看家", [("看家 开/关", "开启/关闭宠物看家（宠物空闲且状态良好时加护自动拦截偷菜者）")])
