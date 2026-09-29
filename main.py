# -*- coding: utf-8 -*-
"""指令插件（3.0.0 入口）：从 AstrBot 接收消息，经核心框架处理后发送回复。

职责：事件接入、消息链构造与发送、定时撤回、插件挂载启动。
指令鉴权/别名展开/路由/数据/金币均在核心框架（core.py）；业务在各功能插件。
"""
import asyncio

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from . import core as _core
from .core import Core
from .modules_send import (  # noqa: F401  发送与撤回辅助（指令插件私有）
    _build_text_image_chain, _send_with_mid, _NameOverrideEvent,
)

# 功能插件挂载清单（顺序即挂载顺序；新插件在此登记，第三方插件走 thirdparty 挂载）
_PLUGIN_MODULES = [
    "signin", "affection", "warehouse", "pet", "pet_shop", "pet_item", "pet_work",
    "pet_play", "pet_auto_care", "pet_auto_work", "farm", "farm_shop", "farm_sell",
    "farm_item", "farm_guard", "farm_steal", "redpacket", "roulette",
    "bank_deposit", "bank_loan", "ledger", "rank", "thirdparty",
    "datamgr", "webui", "debug", "migrate", "update",
]


@register("astrbot_plugin_signin", "sishijiu", "签到娱乐系统 3.0（万物皆插件）", "3.0.2")
class SignInPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.core = Core(context, self.config)

        # 插件挂载（导入失败不阻断其他插件）
        import importlib
        for name in _PLUGIN_MODULES:
            try:
                mod = importlib.import_module(f".plugins.{name}", __package__)
                self.core.mount(mod)
            except Exception as e:
                logger.error(f"[指令插件] 挂载插件 {name} 失败: {e}")
        logger.info(f"[指令插件] 已挂载 {len(self.core._mounted)} 个功能插件")

        # 一次性数据迁移（2.x → 3.0；幂等，migrate 插件实现）
        # 巡检循环启动（承载各插件注册的每日结算与周期任务；无事件循环时等首条消息触发）
        self.core.start_ticker()

    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        if not text:
            return
        head = text.split(maxsplit=1)[0]
        core = self.core
        async with core.lock:
            # 聊天登记 + 三态权限：完全禁止的聊天静默（不展开别名、不记账、不响应）
            new_chat = core.touch_chat(event)
            mode, _ = core.perm_of(core.chat_sid(event))
            if mode == "deny":
                if new_chat:
                    core.save()
                return
            key = core.user_key(event)
            # 自定义昵称优先（包装事件替换发送者昵称）
            custom = core.custom_name_of(key)
            if custom:
                event = _NameOverrideEvent(event, custom)
            reply = await core.dispatch(head, event)
            # 用户识别维护：最后活跃 + 本群成员标记（指令命中或已知用户才写盘）
            gid = event.get_group_id()
            dirty = new_chat or core.touch_user_active(key) or core.mark_group_member(
                gid, key, event.get_sender_name())
            if dirty:
                core.save()
        if reply is None:
            return
        head2 = core.expand_alias(head)
        # 发送（回复协议：str / ("image", path) / ("image_text", 文本, path)）
        if isinstance(reply, tuple) and len(reply) == 2 and reply[0] == "image":
            chain = event.image_result(reply[1])
        elif isinstance(reply, tuple) and len(reply) == 3 and reply[0] == "image_text":
            chain = _build_text_image_chain(reply[1], reply[2])
            if chain is None:
                logger.warning("[指令插件] 消息链组件不可用，附加图片回退为纯文本")
                chain = event.plain_result(reply[1])
        else:
            # 指定指令的文本响应自动转图片（图片响应模块渲染，IMAGE_COMMANDS 定义标题）
            img = self._to_image_for_command(head2, reply)
            chain = event.image_result(img[1]) if img is not None else event.plain_result(reply)
        # 发送并按配置撤回
        try:
            mid = await _send_with_mid(event, chain)
            if mid and self._should_recall(head2):
                asyncio.create_task(self._recall_later(event, mid))
        except Exception as e:
            logger.error(f"[指令插件] 主动发送失败，改用响应管线: {e}")
            yield chain

    # ================= 文本转图片（图片响应模块） =================
    def _to_image_for_command(self, head: str, reply):
        """指定指令的纯文本回复转图片（图片格式由图片响应模块统一定义；调试模式下停用）"""
        if getattr(self.core, "debug", False):
            return None
        # 3.0.1：签到成果一律走新版签到页渲染，渲染不可用回退纯文本，不再转旧版样式图
        if head in ("签到", "我的签到"):
            return None
        if not isinstance(reply, str) or not reply.strip():
            return None
        title = _core.IMAGE_COMMANDS.get(head, head)
        img = self.core.image.text(title, reply.splitlines())
        return img if img is not None else None

    # ================= 撤回 =================
    def _should_recall(self, head: str) -> bool:
        """全局总开关关闭 → 全部不撤回；开启 → 按单指令开关（未配置的默认撤回）"""
        if not bool(self.core.param("RECALL_ENABLED", True)):
            return False
        key = _core.RECALL_EXEMPT.get(head)
        if key is None:
            return True
        return bool(self.core.param(key, True))

    async def _recall_later(self, event, mid):
        """RECALL_AFTER 秒后撤回消息（适配器原生接口优先）"""
        from .modules_send import recall_message_later
        await recall_message_later(event, mid, float(self.core.param("RECALL_AFTER", _core.RECALL_AFTER)))
