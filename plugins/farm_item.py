# -*- coding: utf-8 -*-
"""农场道具插件（3.0.0 · 纯服务插件，无指令）。

自 2.3.0 modules/pet.py「使用」指令的农场分支原样迁移：
农场经验球（2.0.1 起属农场特殊道具）：需开通农场，存于农场记录 tools 字典，
使用时按当前等级升级所需经验的 EXP_BALL_MIN_PCT~EXP_BALL_MAX_PCT 转化为农场经验，
每日最多使用 EXP_BALL_DAILY_LIMIT 次（farm.ball_used_date / ball_used_count）；
旧版存于宠物背包的遗留数量自动迁移到农场 tools（无农场时自动转为金币）。

其它插件（「使用」指令联动）经 core.service("farm_item") 调用：
  - use_item(key, name, qty)   使用农场道具，返回回复文本；非本插件道具返回 None
"""
import random
from datetime import date

from astrbot.api import logger

from ..core import (EXP_BALL_NAME, EXP_BALL_MIN_PCT, EXP_BALL_MAX_PCT,
                    EXP_BALL_DAILY_LIMIT, FARM_EXP_BASE, FARM_MAX_LEVEL,
                    ITEM_TO_COIN)

NAME = "farm_item"


def _farm_gain_exp(farm, amount) -> str:
    """农场加经验（2.3.0 同款）：返回升级提示行（未升级返回空串）。
    经验结构 farm.exp / farm.level 与 2.3.0 完全兼容；调用方负责 core.save()。"""
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


def _farm_state_snippet(farm: dict) -> str:
    """农场当前状态摘要（农场变更反馈末尾附加，2.3.0 同款文本）"""
    wh = farm.get("warehouse", {})
    n_plot = len(farm.get("plots", []))
    n_crop = sum(int(v) for v in wh.get("crops", {}).values())
    n_seed = sum(int(v) for v in wh.get("seeds", {}).values())
    n_fert = sum(int(v) for v in wh.get("fertilizers", {}).values())
    return (f"🌾 农场 Lv.{farm.get('level', 0)}｜土地 {n_plot} 块｜"
            f"仓库：作物 {n_crop} / 种子 {n_seed} / 肥料 {n_fert}")


def _user_display_name(core, key):
    """服务调用无 event，按数据解析用户显示名：自定义昵称 → 用户记录 name/nickname → key"""
    custom = core.custom_name_of(key)
    if custom:
        return custom
    u = (core.data.get("users") or {}).get(key) or {}
    return u.get("name") or u.get("nickname") or str(key)


class FarmItemApi:
    """农场道具服务接口（其它插件经 core.service("farm_item") 调用）"""

    def __init__(self, core):
        self._core = core

    def use_item(self, key, name, qty):
        """使用农场道具（「使用」指令联动）。返回回复文本；非本插件道具返回 None。
        qty 已由指令方解析校验（≥1）；本方法结束前统一 core.save()。"""
        if name != EXP_BALL_NAME:
            return None
        core = self._core
        data = core.data
        qty = int(qty)
        farm = (data.get("farms") or {}).get(key)
        pet0 = (data.get("pets") or {}).get(key)
        legacy = int(((pet0.get("inventory", {}) or {}) if pet0 else {}).get(EXP_BALL_NAME, 0) or 0)

        # 农场经验球（2.0.1 改属农场特殊道具：需开通农场）
        if not farm:
            if legacy <= 0:
                return f"{EXP_BALL_NAME}不足：需要 {qty} 个，当前 0 个（签到有几率获得，需开通农场后使用）。"
            # 无农场（旧数据遗留）→ 自动转为金币（每个 ITEM_TO_COIN 金币）
            gain = qty * ITEM_TO_COIN
            inv = pet0.setdefault("inventory", {})
            inv[EXP_BALL_NAME] = legacy - qty
            if inv[EXP_BALL_NAME] <= 0:
                inv.pop(EXP_BALL_NAME, None)
            core.add_coins(key, gain, "道具自动转金币")
            core.save()
            return (f"🔄 {_user_display_name(core, key)} 还没有农场，「{EXP_BALL_NAME}」×{qty} 自动转换为 {gain} 金币。\n"
                    f"{core.coin_line(key)}")

        if legacy > 0 and pet0 is not None:
            # 旧版存于宠物背包 → 迁移到农场工具
            tools = farm.setdefault("tools", {})
            tools[EXP_BALL_NAME] = int(tools.get(EXP_BALL_NAME, 0)) + legacy
            (pet0.get("inventory", {}) or {}).pop(EXP_BALL_NAME, None)

        today = date.today().isoformat()
        if farm.get("ball_used_date") != today:
            farm["ball_used_date"] = today
            farm["ball_used_count"] = 0
        used = int(farm.get("ball_used_count", 0))
        daily_limit = int(core.param("EXP_BALL_DAILY_LIMIT", EXP_BALL_DAILY_LIMIT))
        if used + qty > daily_limit:
            return f"{EXP_BALL_NAME}每天最多使用 {daily_limit} 次（今天已用 {used} 次）。"

        tools = farm.setdefault("tools", {})
        have = int(tools.get(EXP_BALL_NAME, 0))
        if have < qty:
            return f"{EXP_BALL_NAME}不足：需要 {qty} 个，当前 {have} 个（签到有几率获得）。"

        min_pct = float(core.param("EXP_BALL_MIN_PCT", EXP_BALL_MIN_PCT))
        max_pct = float(core.param("EXP_BALL_MAX_PCT", EXP_BALL_MAX_PCT))
        need = FARM_EXP_BASE * (int(farm.get("level", 0)) + 1)  # 升级所需总经验
        total = 0.0
        for _ in range(qty):
            total += round(need * random.uniform(min_pct, max_pct), 2)
        lvl_msg = _farm_gain_exp(farm, total)
        tools[EXP_BALL_NAME] = have - qty
        if tools[EXP_BALL_NAME] <= 0:
            tools.pop(EXP_BALL_NAME, None)
        farm["ball_used_count"] = used + qty
        core.save()
        logger.info(f"[农场道具] {key} 使用 {EXP_BALL_NAME}×{qty}：农场经验 +{total:.1f}")
        return (f"🏵️ {_user_display_name(core, key)} 使用了农场经验球×{qty}：农场经验 +{total:.1f}{lvl_msg}\n"
                f"（今日已用 {farm['ball_used_count']}/{daily_limit} 次）\n"
                f"{_farm_state_snippet(farm)}")


def register(core):
    core.expose("farm_item", FarmItemApi(core))
