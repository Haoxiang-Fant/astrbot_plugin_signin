# -*- coding: utf-8 -*-
"""好感度插件（3.0.0 · 纯服务插件，无指令）。

好感度数据仍存于用户记录（core.data["users"][key]["favorability"]，与 2.x 存档完全兼容），
本插件只把「加好感 / 查总值 / 查等级」封装为 core.service("affection") 供其它插件调用：
  - add(key, delta, reason="")   增加好感度（负数=减少；round 2 写回），返回新总值
  - total_of(key)                查询用户好感度总值
  - level_of(key)                好感度等级（共用核心 core.level_of 计算，Lv.0~10）
"""
from astrbot.api import logger

NAME = "affection"


class AffectionApi:
    """好感度服务接口（其它插件经 core.service("affection") 调用）"""

    def __init__(self, core):
        self._core = core

    def add(self, key, delta, reason=""):
        """增加好感度（round 2 后写回用户记录），返回新总值"""
        core = self._core
        try:
            delta = float(delta)
        except (TypeError, ValueError):
            delta = 0.0
        user = core.ensure_user(key)
        new = round(self.total_of(key) + delta, 2)
        user["favorability"] = new
        if reason:
            logger.info(f"[好感度] {key} {reason}：{delta:+.2f}（当前 {new:.2f}）")
        return new

    def total_of(self, key):
        """用户好感度总值（无记录返回 0.0）"""
        u = self._core.data.get("users", {}).get(key)
        try:
            return float(u.get("favorability", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def level_of(self, key):
        """好感度等级（Lv.0~10，核心共用档位计算）"""
        return self._core.level_of(self.total_of(key))


def register(core):
    core.expose("affection", AffectionApi(core))
