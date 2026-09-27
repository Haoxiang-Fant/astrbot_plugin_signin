# -*- coding: utf-8 -*-
"""图片响应模块（统一图片输出出口）。

所有插件的图片输出都经本模块：core.image.text(...) / core.image.rich(...) /
core.image.snapshot(...) / core.image.build_help(...) / 专用格式渲染器。

图片格式由发起调用的插件定义（各插件把要展示的数据组织成渲染器入参），
渲染与发图统一在本模块完成。专用格式渲染器位于同目录各子模块（自动发现）：
  signin.py / pet.py / farm.py / shop.py / rank.py / loan.py / activity.py
每个子模块只导出 `render_*` 前缀函数，本包自动聚合为 core.image.render_xxx(...)。
"""
import importlib
import pkgutil

from astrbot.api import logger

from .common import DS_BG, DS_SURFACE, DS_TEXT, DS_TEXT_2, DS_MUTED, DS_BORDER, \
    DS_ACCENT, DS_ACCENT_STRONG, DS_GOLD, DS_DANGER, DS_SUCCESS, DS_GREEN_SOFT, DS_BLUE
from .generic import text, rich, snapshot, help_menu, build_help

__all__ = ["text", "rich", "snapshot", "help_menu", "build_help"]


def _discover():
    """自动聚合各专用渲染子模块的 render_* 函数（缺失的模块记警告不阻断启动）"""
    for m in pkgutil.iter_modules(__path__):
        if m.name in ("common", "generic"):
            continue
        try:
            mod = importlib.import_module(f".{m.name}", __name__)
        except Exception as e:
            logger.warning(f"[图片] 专用渲染模块 {m.name} 未加载: {e}")
            continue
        found = False
        for n in dir(mod):
            if n.startswith("render_") and callable(getattr(mod, n)):
                globals()[n] = getattr(mod, n)
                __all__.append(n)
                found = True
        if not found:
            logger.warning(f"[图片] 渲染模块 {m.name} 未提供 render_* 函数")


_discover()
