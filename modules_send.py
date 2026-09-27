# -*- coding: utf-8 -*-
"""指令插件私有辅助：消息链构造、底层直发（拿 message_id）、昵称包装事件、定时撤回。
自 2.3.0 base.py / main.py 原样迁移，仅指令插件使用；功能插件不接触消息发送。"""
import asyncio
import base64
import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent  # noqa: F401  仅用于类型标注

try:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )
except Exception:
    AiocqhttpMessageEvent = None

try:
    from astrbot.core.message.components import Plain as _Plain
    from astrbot.core.message.components import Image as _Image
    from astrbot.core.message.message_event_result import MessageChain as _MessageChain
except Exception:
    _Plain = None
    _Image = None
    _MessageChain = None


class _NameOverrideEvent:
    """把事件的发送者昵称替换为自定义昵称（2.0.3），其余属性/方法原样委托给原事件。"""

    def __init__(self, event: AstrMessageEvent, name: str):
        self._base_event = event
        self._override_name = name

    def __getattr__(self, item):
        return getattr(self._base_event, item)

    def get_sender_name(self):
        return self._override_name


def _build_text_image_chain(text, img_path):
    """构造「文本 + 图片」组合消息链（返回 None 时表示组件不可用）"""
    if _Plain is None or _Image is None or _MessageChain is None:
        return None
    return _MessageChain(chain=[_Plain(text=text), _Image(file=img_path)])


def _resolve_file_value(file_v):
    """把文件值（bytes / 本地路径 / URL）统一转为 OneBot 可用的字符串；本地文件读字节转 base64"""
    if isinstance(file_v, (bytes, bytearray)):
        return "base64://" + base64.b64encode(bytes(file_v)).decode()
    if isinstance(file_v, str) and os.path.exists(file_v):
        with open(file_v, "rb") as f:
            return "base64://" + base64.b64encode(f.read()).decode()
    return file_v if file_v else None


def _chain_to_onebot_segments(chain):
    """把 AstrBot 消息链转成 OneBot v11 段数组；本地图片读字节转 base64 最稳"""
    segs = []
    for comp in chain.chain:
        cname = type(comp).__name__
        if cname == "Plain":
            segs.append({"type": "text", "data": {"text": comp.text}})
        elif cname in ("Image", "Record"):
            file_v = (
                getattr(comp, "file", None)
                or getattr(comp, "path", None)
                or getattr(comp, "url", None)
            )
            resolved = _resolve_file_value(file_v)
            if resolved:
                segs.append({"type": "image" if cname == "Image" else "record",
                             "data": {"file": resolved}})
        elif cname == "At":
            qq = getattr(comp, "qq", None)
            if qq is not None:
                segs.append({"type": "at", "data": {"qq": str(qq)}})
        else:
            logger.warning(f"[指令插件] 序列化忽略未知消息组件 {cname}")
    return segs


async def _send_with_mid(event, chain):
    """发送消息链并尽可能拿到 message_id（用于定时撤回）。

    1) aiocqhttp：用 event.bot.api.call_action 直发 OneBot 消息，响应中的 message_id 最可靠；
    2) QQ 官方机器人：send_by_session 后从 platform._session_last_message_id 取；
    3) 兜底：event.send(chain)（返回值可能为 None）。
    """
    if AiocqhttpMessageEvent is not None and isinstance(event, AiocqhttpMessageEvent):
        bot = getattr(event, "bot", None)
        call_action = getattr(getattr(bot, "api", None), "call_action", None)
        if call_action is not None:
            segs = _chain_to_onebot_segments(chain)
            if segs:
                payloads = {"message": segs}
                if event.is_private_chat():
                    payloads["user_id"] = event.get_sender_id()
                    action = "send_private_msg"
                else:
                    payloads["group_id"] = event.get_group_id()
                    action = "send_group_msg"
                try:
                    result = await call_action(action, **payloads)
                    mid = result.get("message_id") if isinstance(result, dict) else None
                    if mid is not None:
                        logger.info(f"[指令插件] aiocqhttp 直发成功，message_id={mid!r}")
                    else:
                        logger.warning(f"[指令插件] aiocqhttp 直发响应无 message_id: {result!r}")
                    return mid
                except Exception as e:
                    logger.error(f"[指令插件] aiocqhttp 直发失败，回退 event.send: {e}")
            else:
                logger.warning("[指令插件] 消息链无法转成 OneBot 段，回退 event.send")
        else:
            logger.warning("[指令插件] aiocqhttp 事件无 api.call_action，跳过直发")
    platform = getattr(getattr(event, "bot", None), "platform", None)
    if platform is not None and hasattr(platform, "send_by_session"):
        try:
            await platform.send_by_session(event.session, chain)
            mid = getattr(platform, "_session_last_message_id", {}).get(event.session_id)
            logger.info(f"[指令插件] QQ官方 send_by_session 发送，message_id={mid!r}")
            return mid
        except Exception as e:
            logger.error(f"[指令插件] QQ官方发送失败，回退 event.send: {e}")
    return await event.send(chain)


async def recall_message_later(event, mid, after_seconds: float):
    """after_seconds 秒后撤回消息：适配器原生撤回接口优先（OneBot / botpy / platform 通用兜底）"""
    try:
        await asyncio.sleep(max(0.0, float(after_seconds)))
    except asyncio.CancelledError:
        return
    try:
        # 1) aiocqhttp：event.bot.delete_msg（OneBot v11 标准撤回接口）
        if AiocqhttpMessageEvent is not None and isinstance(event, AiocqhttpMessageEvent):
            bot = getattr(event, "bot", None)
            delete_msg = getattr(bot, "delete_msg", None)
            if delete_msg is not None:
                try:
                    await delete_msg(message_id=int(mid))
                    logger.info(f"[指令插件] 撤回成功（event.bot.delete_msg, mid={mid!r}）")
                    return
                except Exception as e:
                    logger.error(f"[指令插件] event.bot.delete_msg 撤回失败: {e}")
        # 2) QQ 官方机器人：botpy 原生撤回
        try:
            from botpy.http import Route
            from botpy.message import (
                C2CMessage,
                DirectMessage,
                GroupMessage,
                Message as BotpyMessage,
            )
        except Exception:
            Route = GroupMessage = C2CMessage = DirectMessage = BotpyMessage = None
        if Route is not None:
            source = getattr(getattr(event, "message_obj", None), "raw_message", None)
            bot = getattr(event, "bot", None)
            try:
                route_path = None
                route_params = {}
                if isinstance(source, GroupMessage):
                    route_path = "/v2/groups/{group_openid}/messages/{message_id}"
                    route_params["group_openid"] = source.group_openid
                elif isinstance(source, C2CMessage):
                    route_path = "/v2/users/{openid}/messages/{message_id}"
                    route_params["openid"] = source.author.user_openid
                elif isinstance(source, DirectMessage):
                    route_path = "/dms/{guild_id}/messages/{message_id}"
                    route_params["guild_id"] = source.guild_id
                elif isinstance(source, BotpyMessage):
                    await bot.api.recall_message(
                        channel_id=source.channel_id, message_id=str(mid)
                    )
                    logger.info("[指令插件] 撤回成功（botpy api.recall_message）")
                    return
                if route_path:
                    await bot.api._http.request(
                        Route("DELETE", route_path, message_id=str(mid), **route_params)
                    )
                    logger.info("[指令插件] 撤回成功（botpy DELETE 路由）")
                    return
            except Exception as e:
                logger.error(f"[指令插件] QQ官方撤回失败: {e}")
        # 3) 通用兜底：platform / event 的撤回类方法
        platform = getattr(event, "platform", None)
        if platform is not None and hasattr(platform, "recall_message"):
            await platform.recall_message(mid)
            logger.info("[指令插件] 撤回成功（platform.recall_message）")
            return
        if hasattr(event, "recall_message"):
            await event.recall_message(mid)
            logger.info("[指令插件] 撤回成功（event.recall_message）")
            return
        if platform is not None:
            for name in ("delete_message", "delete_msg", "recall_msg"):
                fn = getattr(platform, name, None)
                if fn:
                    await fn(mid)
                    logger.info(f"[指令插件] 撤回成功（{name}）")
                    return
        logger.warning("[指令插件] 未找到可用撤回接口，跳过撤回")
    except Exception as e:
        logger.error(f"[指令插件] 撤回消息失败: {e}")
