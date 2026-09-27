# -*- coding: utf-8 -*-
"""调试插件（3.0.0）：调试模式口令解锁与「加钱」调试指令。

2.3.0 main.py::_route 调试段移植：
  - 对话框输入调试口令（DEBUG_PASSWORD，WebUI 运行参数可改）→ 解锁 WebUI 后台的
    「开启调试模式」按钮（不持久化，重启后消失；调试模式本体开关由 WebUI 插件承载）；
  - 「加钱」：仅调试模式（core.debug）可用，获得 50000 金币（经核心唯一金币出入口，
    自动记流水；调试模式下 core.save 仅更新内存）。
指令头为运行参数值（动态），注册前查重避免与真实指令头冲突。
"""
from astrbot.api import logger

from ..core import DEBUG_PASSWORD

NAME = "debug"


def register(core):
    # 调试模式解锁标记初始化（口令解锁前恒为 False；WebUI 插件读取该标记显示按钮）
    if not hasattr(core, "_debug_unlocked"):
        core._debug_unlocked = False

    # ================= 调试口令 → 解锁 WebUI 调试按钮 =================
    pwd = str(core.param("DEBUG_PASSWORD", DEBUG_PASSWORD))
    if not pwd.strip():
        pwd = str(DEBUG_PASSWORD)

    def handle_debug_unlock(event):
        core._debug_unlocked = True
        return "🔓 调试模式已解锁：WebUI 后台将显示「开启调试模式」按钮（重启后消失）。"

    if core.has_command(pwd):
        logger.warning(f"[调试] 调试口令「{pwd}」与已注册指令头冲突，跳过解锁指令注册"
                       f"（请在 WebUI 运行参数中更换 DEBUG_PASSWORD）")
    else:
        core.command(pwd)(handle_debug_unlock)

    # ================= 「加钱」：仅调试模式可用 =================
    @core.command("加钱")
    def handle_debug_add_coins(event):
        if not getattr(core, "debug", False):
            return "「加钱」仅在调试模式下可用。"
        key = core.user_key(event)
        core.add_coins(key, 50000, "调试加钱")
        core.save()
        return f"💰 调试模式：已获得 50000 金币（当前 {core.coins_of(key)}）。"
