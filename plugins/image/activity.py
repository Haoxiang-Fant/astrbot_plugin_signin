# -*- coding: utf-8 -*-
"""图片响应模块 · 活动中心渲染器（2.3.0 ActivityMixin::_render_activity_image 移植）。

每个活动一个卡片、一行一卡：活动名称为卡题，时间 / 简介 / 参与要求 [+ 相关指令]
为内容行；文字自动换行、卡片高度自适应。渲染统一走通用快照瀑布渲染器
generic.snapshot（老 _render_snapshot_image），返回 ("image", path)，
渲染环境不可用时返回 None（调用方回退纯文本列表）。
"""
from .common import DS_TEXT_2
from .generic import snapshot

__all__ = ["render_activity_image"]


def render_activity_image(activities):
    """活动中心：每个活动一张卡片（名称 / 时间 / 简介 / 参与要求 [+指令]）"""
    modules = []
    for a in activities or []:
        rows = [
            (f"🕐 {a.time_str()}", DS_TEXT_2),
            (f"📝 {a.desc}", DS_TEXT_2),
            (f"✅ 参与要求：{a.requirement_text()}", DS_TEXT_2),
        ]
        if getattr(a, "commands", None):
            rows.append((f"💬 相关指令：{' / '.join(a.commands.keys())}", DS_TEXT_2))
        modules.append((f"📌 {getattr(a, 'name', '')}", rows, False))
    if not modules:
        return None
    return snapshot("活动中心", modules)
