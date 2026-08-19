# -*- coding: utf-8 -*-
"""活动模板示例：复制本文件修改即可快速编写新活动，完整说明见 ACTIVITY.md。

真实活动参考：daily_draw.py（双倍签到，通过 on_sign_in 钩子再运行一次签到程序）。
"""
from activities import BaseActivity, register_activity


@register_activity
class ExampleActivity(BaseActivity):
    id = "example_activity"
    name = "示例活动"
    # 例如开始/结束：start = "2025-01-01 00:00" / end = "2025-02-01 00:00"
    start = ""
    end = ""
    desc = "这是一个活动模板示例（复制本文件修改即可）"
    requirement = "无（所有玩家均可参与）"
    commands = {}
