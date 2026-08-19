# -*- coding: utf-8 -*-
"""双倍签到活动：活动期间（至 2026-08-31 04:00）任意玩家每日签到
会再完整运行 N 次签到程序（默认 2 次）——金币 / 好感度 / 宠物经验 / 属性丸 全部翻倍。

「签到倍数 times」可在 WebUI 活动中心修改（自定义参数示例）。
"""
from datetime import date

from activities import BaseActivity, register_activity


@register_activity
class DoubleSigninActivity(BaseActivity):
    id = "double_signin"
    name = "双倍签到"
    start = ""
    end = "2026-08-31 04:00"
    desc = "活动期间每日签到可获得双倍奖励：签到会运行两次！"
    requirement = "无（所有玩家均可参与）"
    commands = {}
    # 自定义可调参数（WebUI 活动中心可改）
    params = {
        "times": {"label": "签到倍数", "type": "int", "desc": "签到奖励运行次数（2 = 双倍）", "default": 2},
    }

    plugin = None

    def attach(self, plugin):
        self.plugin = plugin

    def on_sign_in(self, event, data, key, lines):
        """签到成功后，再完整运行 N 次签到程序（默认 2 次 = 双倍）"""
        p = self.plugin
        n = max(1, int(getattr(self, "times", 2) or 2))
        for i in range(n):
            second = p._apply_signin_once(data, key, date.today().isoformat())
            lines.append("")
            lines.append(f"🎉 {self.name}（活动）· 第 {i + 1} 次签到：")
            lines.extend(second)
