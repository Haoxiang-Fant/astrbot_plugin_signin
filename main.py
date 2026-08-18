import asyncio
import base64
import json
import math
import os
import random
import shutil
from datetime import date, timedelta, datetime, time

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
except Exception:
    get_astrbot_plugin_data_path = None

# aiocqhttp（OneBot v11 / NapCat）事件类型，用于底层直发以可靠获取 message_id
try:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )
except Exception:
    AiocqhttpMessageEvent = None


def _chain_to_onebot_segments(chain):
    """把 AstrBot 消息链转成 OneBot v11 段数组；本地图片读字节转 base64 最稳"""
    segs = []
    for comp in chain.chain:
        cname = type(comp).__name__
        if cname == "Plain":
            segs.append({"type": "text", "data": {"text": comp.text}})
        elif cname == "Image":
            file_v = (
                getattr(comp, "file", None)
                or getattr(comp, "path", None)
                or getattr(comp, "url", None)
            )
            data = {}
            if isinstance(file_v, (bytes, bytearray)):
                data["file"] = "base64://" + base64.b64encode(bytes(file_v)).decode()
            elif isinstance(file_v, str) and os.path.exists(file_v):
                with open(file_v, "rb") as f:
                    data["file"] = "base64://" + base64.b64encode(f.read()).decode()
            elif file_v:
                data["file"] = file_v  # URL / file:// / 服务端相对路径
            if data:
                segs.append({"type": "image", "data": data})
        elif cname == "At":
            qq = getattr(comp, "qq", None)
            if qq is not None:
                segs.append({"type": "at", "data": {"qq": str(qq)}})
        elif cname == "Record":
            file_v = (
                getattr(comp, "file", None)
                or getattr(comp, "path", None)
                or getattr(comp, "url", None)
            )
            data = {}
            if isinstance(file_v, (bytes, bytearray)):
                data["file"] = "base64://" + base64.b64encode(bytes(file_v)).decode()
            elif isinstance(file_v, str) and os.path.exists(file_v):
                with open(file_v, "rb") as f:
                    data["file"] = "base64://" + base64.b64encode(f.read()).decode()
            elif file_v:
                data["file"] = file_v
            if data:
                segs.append({"type": "record", "data": data})
        else:
            logger.warning(f"[插件] 序列化忽略未知消息组件 {cname}")
    return segs


async def _send_with_mid(event, chain):
    """发送消息链并尽可能拿到 message_id（用于定时撤回）。

    1) aiocqhttp：用 event.bot.api.call_action 直发 OneBot 消息，响应中的 message_id 最可靠；
    2) QQ 官方机器人：send_by_session 后从 platform._session_last_message_id 取；
    3) 兜底：event.send(chain)（返回值可能为 None）。
    """
    # 1) aiocqhttp（OneBot v11 / NapCat 等）
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
                        logger.info(f"[插件] aiocqhttp 直发成功，message_id={mid!r}")
                    else:
                        logger.warning(f"[插件] aiocqhttp 直发响应无 message_id: {result!r}")
                    return mid
                except Exception as e:
                    logger.error(f"[插件] aiocqhttp 直发失败，回退 event.send: {e}")
            else:
                logger.warning("[插件] 消息链无法转成 OneBot 段，回退 event.send")
        else:
            logger.warning("[插件] aiocqhttp 事件无 api.call_action，跳过直发")
    # 2) QQ 官方机器人
    platform = getattr(getattr(event, "bot", None), "platform", None)
    if platform is not None and hasattr(platform, "send_by_session"):
        try:
            await platform.send_by_session(event.session, chain)
            mid = getattr(platform, "_session_last_message_id", {}).get(event.session_id)
            logger.info(f"[插件] QQ官方 send_by_session 发送，message_id={mid!r}")
            return mid
        except Exception as e:
            logger.error(f"[插件] QQ官方发送失败，回退 event.send: {e}")
    # 3) 兜底
    return await event.send(chain)

# ============ 签到 / 好感度 ============
MIN_COINS = 30          # 每次签到最少获得的金币
MAX_COINS = 300         # 每次签到最多获得的金币
MIN_FAV = 0.01          # 每次签到最少增加的好感度
MAX_FAV = 1.0           # 每次签到最多增加的好感度
MAX_LEVEL = 10          # 好感度最高等级（初始为 0）
LEVEL_STEP = 10.0       # 好感度每满 10 点提升一级

# ============ 左轮手枪 ============
ROULETTE_MAGAZINES = 7      # 弹匣数量
ROULETTE_MAX_BULLETS = 6    # 子弹数量上限
ROULETTE_MIN_PLAYERS = 2    # 最少玩家人数
ROULETTE_MAX_PLAYERS = 3    # 最多玩家人数
ROULETTE_JOIN_TIMEOUT = 30  # 加入等待秒数
ROULETTE_FEE_RATE = 0.1     # 手续费比例（10%）

# ============ 宠物 ============
PET_UNLOCK_COST = 1000      # 解锁宠物所需金币
PET_MAX_LEVEL = 100         # 宠物最大等级
PET_EXP_PER_LEVEL = 100.0   # 每 100 经验升一级
PET_MAX_HEALTH = 200.0      # 健康度最大值
PET_SIGNIN_EXP_MIN = 10.0   # 签到宠物经验下限
PET_SIGNIN_EXP_MAX = 60.0   # 签到宠物经验上限
PILL_NAME = "属性丸"        # 属性丸道具名
PILL_DROP_CHANCE = 0.5      # 签到掉落属性丸概率
PILL_DROP_MIN = 1           # 属性丸最少掉落数量
PILL_DROP_MAX = 5           # 属性丸最多掉落数量
MONEY_EVENT_CHANCE = 0.01   # 玩耍捡到钱概率（1%）
MONEY_EVENT_GAIN = 100      # 捡到钱的金币
MONEY_EVENT_MAX_PER_DAY = 2  # 每个周期最多触发次数

# ============ 农场 ============
FARM_UNLOCK_COST = 1500     # 解锁农场所需金币
FARM_PLOT_COST = 800        # 每块新土地所需金币
FARM_FREE_PLOTS = 2         # 解锁农场赠送土地数量
FARM_MAX_PLOTS = 24         # 最大土地数量
FARM_MAX_LEVEL = 100        # 农场最大等级
FARM_EXP_BASE = 1000.0      # 升到 N 级需 1000*N 经验
# 土地等级：grade -> (名称, 产量加成(相对贫瘠), 时间减少(相对贫瘠))
FARM_GRADES = [
    ("贫瘠土地", 0.0, 0.0),
    ("红土地", 1.0, 0.0),
    ("普通土地", 2.0, 0.10),
    ("肥沃土地", 2.5, 0.20),
    ("黑土地", 4.0, 0.35),
]
# 从当前 grade 升到 grade+1 的金币
FARM_UPGRADE_COSTS = [1000, 1500, 2000, 3000]
# =============================================

# ============ 银行贷款 ============
LOAN_SPECIAL_AMOUNT = 2500       # 强制解锁特别贷款金额
LOAN_SPECIAL_RATE = 1.0          # 特别贷款日息 1%
LOAN_SPECIAL_DAYS = 30           # 特别贷款限期 30 天
LOAN_SPECIAL_TAKE = 0.2          # 特别贷款逾期后收取仓库价值比例（20%）
LOAN_COIN_DEDUCT = 0.2           # 逾期后获取金币自动扣 20% 还款
LOAN_FAV_DROP_SPECIAL = (1.0, 1.5)      # 特别贷款逾期每日好感度降低范围
LOAN_FAV_DROP_NORMAL = (1.01, 1.25)     # 一般逾期每日好感度降低范围
LOAN_OVERDUE_YEAR_LIMIT = 4      # 每年最多逾期次数
LOAN_GENERAL_OVERDUE_DAYS = 15   # 一般/自定义套餐逾期天数
LOAN_SHORT_GRACE_DAYS = 10       # 短期套餐免息天数
LOAN_SHORT_RATE = 6.0            # 短期套餐逾期日利率
LOAN_DAILY_MULT = 2.0            # 每日累计贷款上限 = 2 × 套餐上限
LOAN_FARM_ROLLBACK_DAYS = 30     # 逾期超 30 天农场回退
LOAN_AUTO_TIME = (23, 0)         # 每日自动卖仓库/自动签到时间
# =============================================

# 需要以图片形式响应的指令（文本响应自动转图片），值为图片标题
IMAGE_COMMANDS = {
    "签到": "签到",
    "我的签到": "我的签到",
    "装弹": "左轮手枪",
    "加入": "左轮手枪",
    "开始": "左轮手枪",
    "开枪": "左轮手枪",
    "我的战绩": "我的战绩",
    "存款": "金币银行",
    "取款": "金币银行",
    "银行统计": "金币银行",
    "借款": "银行贷款",
    "还款": "银行贷款",
    "我的贷款": "我的贷款",
    "我的征信": "我的征信",
}

# 插件消息发送后多少秒撤回（防刷屏，0 = 不撤回）
RECALL_AFTER = 15

PLUGIN_NAME = "astrbot_plugin_signin"
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# 插件数据存放到 AstrBot 的 plugin_data 目录（而非 plugins 目录）
if get_astrbot_plugin_data_path is not None:
    try:
        _DATA_DIR = os.path.join(get_astrbot_plugin_data_path(), PLUGIN_NAME)
        os.makedirs(_DATA_DIR, exist_ok=True)
    except Exception:
        _DATA_DIR = _PLUGIN_DIR
else:
    _DATA_DIR = _PLUGIN_DIR

DATA_FILE = os.path.join(_DATA_DIR, "data.json")
CONFIG_FILE = os.path.join(_DATA_DIR, "后台.txt")
CROP_FILE = os.path.join(_DATA_DIR, "作物.txt")
FERT_FILE = os.path.join(_DATA_DIR, "肥料.txt")
LOAN_FILE = os.path.join(_DATA_DIR, "贷款套餐.txt")
FONT_FILE = os.path.join(_PLUGIN_DIR, "OPPOSans-M.ttf")


def _migrate_old_data_files():
    """把旧位置（插件目录）的数据文件迁移到新位置（plugin_data），只迁一次"""
    pairs = [
        ("data.json", DATA_FILE),
        ("后台.txt", CONFIG_FILE),
        ("作物.txt", CROP_FILE),
        ("肥料.txt", FERT_FILE),
        ("贷款套餐.txt", LOAN_FILE),
    ]
    try:
        for fn, target in pairs:
            src = os.path.join(_PLUGIN_DIR, fn)
            if os.path.exists(src) and not os.path.exists(target):
                shutil.move(src, target)
    except Exception as e:
        logger.error(f"[插件] 迁移旧数据文件失败: {e}")


_migrate_old_data_files()

ATTR_LABELS = {"satiety": "饱食度", "thirst": "口渴值", "stamina": "体力", "mood": "心情值", "health": "健康度"}
ATTR_SHORT = {"satiety": "饱食", "thirst": "口渴", "stamina": "体力", "mood": "心情", "health": "健康"}


def _parse_item_qty(message_str):
    """解析「<指令> <道具名> [数量]」→ (名称, 数量, 错误提示)；数量省略时默认 1"""
    parts = message_str.split(maxsplit=2)
    if len(parts) < 2:
        return None, None, "格式：<道具名> [数量]，例如：属性丸 5"
    item_name = parts[1].strip()
    qty = 1
    if len(parts) >= 3:
        qty_s = parts[2].strip()
        try:
            qty = int(qty_s)
        except ValueError:
            return None, None, f"数量「{qty_s}」不是数字，应为整数。"
        if qty < 1:
            return None, None, "数量至少为 1。"
        if qty > 999:
            return None, None, "数量最多为 999。"
    return item_name, qty, None


class RouletteGame:
    """一局左轮手枪游戏的内存状态"""

    def __init__(self, group_id, starter_id, starter_name, bullets, stake):
        self.group_id = group_id
        self.bullets = bullets
        self.stake = stake
        self.players = [{"id": starter_id, "name": starter_name}]
        self.status = "waiting"      # waiting -> playing -> finished
        self.magazines = []          # bool 列表，True 表示该弹匣有子弹
        self.shot_index = 0          # 下一个要用的弹匣下标
        self.turn_index = 0          # 当前回合玩家下标
        self.event = None            # 发起时的 AstrMessageEvent，供定时器主动发消息
        self.timer_task = None       # 30 秒超时任务

    @property
    def starter_id(self):
        return self.players[0]["id"]

    def player_names(self):
        return "、".join(p["name"] for p in self.players)


@register("astrbot_plugin_signin", "sishijiu", "群签到 + 左轮手枪 + 宠物养成 + 金币银行 + 农场", "1.6.0")
class SignInPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        def _get(key, default, cast=int):
            v = self.config.get(key, default)
            try:
                return cast(v)
            except (TypeError, ValueError):
                return default

        # 从 WebUI 配置页（_conf_schema.json）读取设置，未配置时用默认值
        self.min_coins = _get("min_coins", MIN_COINS)
        self.max_coins = _get("max_coins", MAX_COINS)
        self.pet_unlock_cost = _get("pet_unlock_cost", PET_UNLOCK_COST)
        self.pet_signin_exp_min = _get("pet_signin_exp_min", PET_SIGNIN_EXP_MIN, float)
        self.pet_signin_exp_max = _get("pet_signin_exp_max", PET_SIGNIN_EXP_MAX, float)
        self.pill_drop_chance = _get("pill_drop_chance", PILL_DROP_CHANCE, float)
        self.pill_drop_min = _get("pill_drop_min", PILL_DROP_MIN)
        self.pill_drop_max = _get("pill_drop_max", PILL_DROP_MAX)
        self.money_event_chance = _get("money_event_chance", MONEY_EVENT_CHANCE, float)
        self.money_event_gain = _get("money_event_gain", MONEY_EVENT_GAIN)
        self.money_event_max_per_day = _get("money_event_max_per_day", MONEY_EVENT_MAX_PER_DAY)

        self._lock = asyncio.Lock()       # 保护数据文件 + 游戏内存状态
        self._games = {}                  # group_id -> RouletteGame

        # 一次性迁移旧数据：按群（gid:uid）→ 跨群（uid）
        self._migrate_legacy_data()

        # 注册 WebUI Pages 的后端 API
        context.register_web_api(
            f"/{PLUGIN_NAME}/backend/config", self.web_get_backend_config, ["GET"], "读取后台.txt"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/backend/config", self.web_save_backend_config, ["POST"], "保存后台.txt"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/data/export", self.web_export_data, ["GET"], "导出 data.json"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/data/import", self.web_import_data, ["POST"], "导入 data.json"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/farm/crops", self.web_get_crops, ["GET"], "读取作物.txt"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/farm/crops", self.web_save_crops, ["POST"], "保存作物.txt"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/farm/ferts", self.web_get_ferts, ["GET"], "读取肥料.txt"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/farm/ferts", self.web_save_ferts, ["POST"], "保存肥料.txt"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/loan/packages", self.web_get_loan_pkgs, ["GET"], "读取贷款套餐.txt"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/loan/packages", self.web_save_loan_pkgs, ["POST"], "保存贷款套餐.txt"
        )

    # ================= 消息路由（无需前缀 / @） =================
    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        if not text:
            return
        head = text.split(maxsplit=1)[0]
        async with self._lock:
            # 贷款逾期懒处理（标记逾期 + 每日好感度降低 / 23 点自动卖仓库签到还款）
            data = self._load()
            key = event.get_sender_id()
            if data.get("loans", {}).get(key):
                sync_c = self._loan_sync(data, key)
                daily_c = self._loan_daily_process(data, key)
                if sync_c or daily_c:
                    self._save(data)
            reply = self._route(head, event)
        if reply is None:
            return
        if isinstance(reply, tuple) and len(reply) == 2 and reply[0] == "image":
            chain = event.image_result(reply[1])
        else:
            # 指定指令的文本响应自动转成图片（签到/左轮/银行）
            img = self._to_image_for_command(head, reply)
            chain = event.image_result(img[1]) if img is not None else event.plain_result(reply)
        # 发送并安排 RECALL_AFTER 秒后撤回；优先走适配器底层发送以可靠拿到 message_id
        try:
            mid = await _send_with_mid(event, chain)
            logger.info(f"[插件] 消息已发送，message_id={mid!r}")
            if mid:
                asyncio.create_task(self._recall_later(event, mid))
            else:
                logger.warning("[插件] 未能获取 message_id，无法安排撤回")
        except Exception as e:
            logger.error(f"[插件] 主动发送失败，改用响应管线: {e}")
            yield chain

    async def _recall_later(self, event, mid):
        """RECALL_AFTER 秒后撤回消息：优先走适配器原生撤回接口（参考 astrbot_plugin_music）"""
        try:
            await asyncio.sleep(RECALL_AFTER)
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
                        logger.info(f"[插件] 撤回成功（event.bot.delete_msg, mid={mid!r}）")
                        return
                    except Exception as e:
                        logger.error(f"[插件] event.bot.delete_msg 撤回失败: {e}")
            # 2) QQ 官方机器人：botpy 原生撤回（照搬音乐插件做法）
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
                        logger.info("[插件] 撤回成功（botpy api.recall_message）")
                        return
                    if route_path:
                        await bot.api._http.request(
                            Route("DELETE", route_path, message_id=str(mid), **route_params)
                        )
                        logger.info("[插件] 撤回成功（botpy DELETE 路由）")
                        return
                except Exception as e:
                    logger.error(f"[插件] QQ官方撤回失败: {e}")
            # 3) 通用兜底：platform / event 的撤回类方法
            platform = getattr(event, "platform", None)
            if platform is not None and hasattr(platform, "recall_message"):
                await platform.recall_message(mid)
                logger.info("[插件] 撤回成功（platform.recall_message）")
                return
            if hasattr(event, "recall_message"):
                await event.recall_message(mid)
                logger.info("[插件] 撤回成功（event.recall_message）")
                return
            if platform is not None:
                for name in ("delete_message", "delete_msg", "recall_msg"):
                    fn = getattr(platform, name, None)
                    if fn:
                        await fn(mid)
                        logger.info(f"[插件] 撤回成功（{name}）")
                        return
            cands = []
            if platform is not None:
                cands = [a for a in dir(platform) if any(k in a.lower() for k in ("recall", "delete", "withdraw"))]
            logger.warning(f"[插件] 未找到可用撤回接口。event 属性: {[a for a in dir(event) if not a.startswith('_')][:40]}；platform 相关方法: {cands}")
        except Exception as e:
            logger.error(f"[插件] 撤回消息失败: {e}")

    def _to_image_for_command(self, head: str, reply):
        # 所有文本回复都转为图片；未映射标题的指令用指令名作标题
        if not isinstance(reply, str) or not reply.strip():
            return None
        title = IMAGE_COMMANDS.get(head, head)
        return self._render_text_image(title, reply.splitlines())

    def _route(self, head: str, event: AstrMessageEvent):
        if head == "签到":
            return self._handle_sign_in(event)
        if head == "我的签到":
            return self._handle_my_info(event)
        if head == "签到帮助":
            return self._handle_help_signin()
        if head == "游戏帮助":
            return self._handle_help_game()
        if head == "装弹":
            return self._handle_load(event)
        if head == "加入":
            return self._handle_join(event)
        if head == "开始":
            return self._handle_start(event)
        if head == "开枪":
            return self._handle_shoot(event)
        if head == "我的战绩":
            return self._handle_stats(event)
        if head == "解锁宠物":
            return self._handle_unlock_pet(event)
        if head == "宠物":
            return self._handle_pet_status(event)
        if head == "更改宠物名字":
            return self._handle_rename_pet(event)
        if head == "打工":
            return self._handle_work(event)
        if head == "玩耍":
            return self._handle_play(event)
        if head == "商店":
            return self._handle_shop(event)
        if head == "购买":
            return self._handle_buy(event)
        if head == "使用":
            return self._handle_use_item(event)
        if head == "背包":
            return self._handle_bag(event)
        if head == "宠物帮助":
            return self._handle_help_pet()
        if head == "农场帮助":
            return self._handle_help_farm()
        if head == "左轮手枪帮助":
            return self._handle_help_roulette()
        if head == "查看后台配置":
            return self._handle_view_config()
        if head == "保存后台配置":
            return self._handle_save_backend_config(event)
        if head == "导出数据":
            return self._handle_export_data()
        if head == "导入数据":
            return self._handle_import_data(event)
        if head == "存款":
            return self._handle_bank_deposit(event)
        if head == "取款":
            return self._handle_bank_withdraw(event)
        if head == "银行统计":
            return self._handle_bank_stats(event)
        if head == "借款":
            return self._handle_loan_borrow(event)
        if head == "还款":
            return self._handle_loan_repay(event)
        if head == "我的贷款":
            return self._handle_my_loans(event)
        if head == "我的征信":
            return self._handle_my_credit(event)
        if head == "解锁农场":
            return self._handle_farm_unlock(event)
        if head == "购买土地":
            return self._handle_farm_buy_land(event)
        if head == "土地升级":
            return self._handle_farm_upgrade(event)
        if head == "种子商店":
            return self._handle_farm_seed_shop(event)
        if head == "购买种子":
            return self._handle_farm_buy_seed(event)
        if head == "肥料商店":
            return self._handle_farm_fert_shop(event)
        if head == "购买肥料":
            return self._handle_farm_buy_fert(event)
        if head == "种植":
            return self._handle_farm_plant(event)
        if head == "施肥":
            return self._handle_farm_fertilize(event)
        if head == "收割":
            return self._handle_farm_harvest(event)
        if head == "取消种植":
            return self._handle_farm_cancel(event)
        if head == "土地状态":
            return self._handle_farm_plots(event)
        if head == "农场仓库":
            return self._handle_farm_warehouse(event)
        if head == "售卖种子":
            return self._handle_farm_sell_seed(event)
        if head == "售卖":
            return self._handle_farm_sell(event)
        return None

    # ================= 数据存取 =================
    def _load(self) -> dict:
        if not os.path.exists(DATA_FILE):
            return {"users": {}, "roulette": {}, "pets": {}, "bank": {}, "farms": {}, "loans": {}}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"users": {}, "roulette": {}, "pets": {}, "bank": {}, "farms": {}, "loans": {}}
            data.setdefault("users", {})
            data.setdefault("roulette", {})
            data.setdefault("pets", {})
            data.setdefault("bank", {})
            data.setdefault("farms", {})
            data.setdefault("loans", {})
            return data
        except Exception as e:
            logger.error(f"[插件] 读取数据失败: {e}")
            return {"users": {}, "roulette": {}, "pets": {}, "bank": {}, "farms": {}, "loans": {}}

    def _save(self, data: dict) -> None:
        try:
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, DATA_FILE)
        except Exception as e:
            logger.error(f"[插件] 保存数据失败: {e}")

    def _migrate_legacy_data(self) -> None:
        """一次性迁移旧数据：按群存储（gid:uid / private:uid）→ 跨群（uid）。
        金币求和；好感度取最大；签到日期取最新；宠物保留等级/经验最高的一只；左轮战绩求和合并。"""
        if not os.path.exists(DATA_FILE):
            return
        data = self._load()
        if data.get("_migrated_cross_group"):
            return

        def _uid(key: str) -> str:
            return key.rsplit(":", 1)[-1] if ":" in key else key

        # users：金币求和、好感度取最大、签到日期取最新
        new_users = {}
        for key, u in data.get("users", {}).items():
            if not isinstance(u, dict):
                new_users[key] = u
                continue
            uid = _uid(key)
            d = new_users.setdefault(uid, {"coins": 0, "favorability": 0.0, "last_date": ""})
            d["coins"] = int(d.get("coins", 0)) + int(u.get("coins", 0))
            d["favorability"] = max(float(d.get("favorability", 0.0)), float(u.get("favorability", 0.0)))
            d["last_date"] = max(d.get("last_date", "") or "", u.get("last_date", "") or "")
        data["users"] = new_users

        # roulette：局数/净收益求和，lost_to / won_from 按对手求和合并
        new_r = {}
        for key, s in data.get("roulette", {}).items():
            if not isinstance(s, dict):
                new_r[key] = s
                continue
            uid = _uid(key)
            d = new_r.setdefault(uid, {"wins": 0, "losses": 0, "net": 0, "lost_to": {}, "won_from": {}})
            d["wins"] += int(s.get("wins", 0))
            d["losses"] += int(s.get("losses", 0))
            d["net"] += int(s.get("net", 0))
            for side in ("lost_to", "won_from"):
                for opp, e in s.get(side, {}).items():
                    if not isinstance(e, dict):
                        continue
                    dd = d[side].setdefault(opp, {"name": e.get("name", ""), "amount": 0})
                    dd["amount"] += int(e.get("amount", 0))
                    if e.get("name"):
                        dd["name"] = e["name"]
        data["roulette"] = new_r

        # pets：每位用户最多一只，保留等级/经验最高的一只
        new_pets = {}
        for key, p in data.get("pets", {}).items():
            if not isinstance(p, dict):
                new_pets[key] = p
                continue
            uid = _uid(key)
            existing = new_pets.get(uid)
            if existing is None or (p.get("level", 0), p.get("exp", 0)) > (existing.get("level", 0), existing.get("exp", 0)):
                new_pets[uid] = p
        data["pets"] = new_pets

        data["_migrated_cross_group"] = True
        self._save(data)

    @staticmethod
    def _user_key(event: AstrMessageEvent) -> str:
        # 数据按用户维度存储，跨群聊共享
        return event.get_sender_id()

    @staticmethod
    def _level_of(favorability: float) -> int:
        return min(MAX_LEVEL, int(favorability // LEVEL_STEP))

    def _ensure_user(self, data: dict, key: str) -> dict:
        return data.setdefault("users", {}).setdefault(key, {"coins": 0, "favorability": 0.0})

    def _coins_of(self, data: dict, key: str) -> int:
        v = data.get("users", {}).get(key, {}).get("coins")
        return int(v) if isinstance(v, (int, float)) else 0

    def _add_coins(self, data: dict, key: str, amount: int) -> None:
        # 有逾期贷款时，获得金币自动划扣 20% 还款
        if amount > 0 and data.get("loans", {}).get(key):
            rec = data["loans"][key]
            if self._has_overdue_now(rec, datetime.now().timestamp()):
                take = int(amount * LOAN_COIN_DEDUCT)
                if take > 0:
                    repaid = self._repay_loans(data, key, take)
                    amount -= int(repaid)
        user = self._ensure_user(data, key)
        cur = user.get("coins")
        if not isinstance(cur, (int, float)):
            cur = 0
        user["coins"] = max(0, int(cur) + amount)

    def _ensure_stat(self, data: dict, key: str) -> dict:
        return data.setdefault("roulette", {}).setdefault(key, {
            "wins": 0, "losses": 0, "net": 0, "lost_to": {}, "won_from": {},
        })

    def _record(self, data: dict, key: str, side: str, opp_id: str, opp_name: str, amount: int) -> None:
        stat = self._ensure_stat(data, key)
        bucket = stat.setdefault(side, {})
        entry = bucket.setdefault(opp_id, {"name": opp_name, "amount": 0})
        entry["amount"] += amount
        entry["name"] = opp_name

    # ================= 通用工具 =================
    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    @staticmethod
    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    # ================= 签到 =================
    def _handle_sign_in(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        today = date.today().isoformat()

        data = self._load()
        user = data.get("users", {}).get(key)

        if user and user.get("last_date") == today:
            coins = user.get("coins", 0)
            fav = float(user.get("favorability", 0.0))
            lv = self._level_of(fav)
            reply = (f"{name}，你今天已经签到过啦～\n"
                     f"💰 当前金币：{coins}\n"
                     f"💗 当前好感度：{fav:.2f}（Lv.{lv}）")
            # 今天已签过：仍结算到期的存单
            settled, bank_paid = self._bank_settle(data, key)
            if settled > 0:
                self._save(data)
                reply += f"\n🏦 {settled} 笔存单已解锁，利息 +{bank_paid} 已自动入账，本金可发送「取款」取出。"
            return reply

        if user is None:
            user = self._ensure_user(data, key)

        coins_got = random.randint(self.min_coins, self.max_coins)
        fav_got = round(random.uniform(MIN_FAV, MAX_FAV), 2)

        old_fav = float(user.get("favorability", 0.0))
        old_lv = self._level_of(old_fav)

        user["coins"] = int(user.get("coins", 0)) + coins_got
        new_fav = round(old_fav + fav_got, 2)
        user["favorability"] = new_fav
        user["last_date"] = today

        new_lv = self._level_of(new_fav)

        lines = [
            f"✅ {name} 签到成功！",
            f"💰 获得金币：+{coins_got}（当前 {user['coins']}）",
            f"💗 好感度：+{fav_got:.2f}（当前 {new_fav:.2f}）",
        ]
        if new_lv > old_lv:
            lines.append(f"🎉 好感度突破 {int(new_lv * LEVEL_STEP)}，等级提升至 Lv.{new_lv}！")
        else:
            lines.append(f"🏅 当前好感等级：Lv.{new_lv}")

        pet = data.get("pets", {}).get(key)
        if pet:
            self._bring_pet_up_to_date(pet, today)

            exp_got = round(random.uniform(self.pet_signin_exp_min, self.pet_signin_exp_max), 2)
            pet["exp"] = round(float(pet.get("exp", 0.0)) + exp_got, 2)
            lvl_msg = self._apply_exp(pet)
            lines.append(f"🐾 宠物经验：+{exp_got:.1f}{lvl_msg}")

            if random.random() < self.pill_drop_chance:
                pills = random.randint(self.pill_drop_min, self.pill_drop_max)
                inv = pet.setdefault("inventory", {})
                inv[PILL_NAME] = int(inv.get(PILL_NAME, 0)) + pills
                lines.append(f"💊 运气不错，获得 {pills} 个属性丸（发送「使用 属性丸」使用）！")

            settle_lines = self._settle_display_lines(pet)
            if settle_lines:
                lines.append("")
                lines.extend(settle_lines)

        # ---- 银行：解锁到期的存单并发放利息 ----
        settled, bank_paid = self._bank_settle(data, key)
        if settled > 0:
            lines.append(f"🏦 {settled} 笔存单已解锁，利息 +{bank_paid} 已自动入账，本金可发送「取款」取出。")

        self._save(data)
        return "\n".join(lines)

    def _handle_my_info(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)

        data = self._load()
        user = data.get("users", {}).get(key)
        if not user:
            return f"{name} 还没有签到记录，发送「签到」开始吧～"
        coins = user.get("coins", 0)
        fav = float(user.get("favorability", 0.0))
        lv = self._level_of(fav)
        last = user.get("last_date", "无")
        lines = [
            f"{name} 的签到信息：",
            f"💰 金币：{coins}",
            f"💗 好感度：{fav:.2f}",
            f"🏅 好感等级：Lv.{lv}",
            f"📅 上次签到：{last}",
        ]
        return "\n".join(lines)

    # ================= 宠物：结算逻辑 =================
    @staticmethod
    def _attr_max(health: float):
        """返回 (饱食上限, 口渴上限, 体力上限, 心情上限)，由健康度决定"""
        if health >= 100:
            return 120.0, 120.0, 200.0, 100.0
        if health >= 80:
            return 120.0, 120.0, 120.0, 100.0
        if health >= 40:
            return 100.0, 100.0, 100.0, 100.0
        return 80.0, 80.0, 60.0, 80.0

    def _clamp_attrs(self, pet: dict) -> None:
        sat_max, thr_max, sta_max, mood_max = self._attr_max(pet["health"])
        pet["satiety"] = round(self._clamp(pet["satiety"], 0, sat_max), 2)
        pet["thirst"] = round(self._clamp(pet["thirst"], 0, thr_max), 2)
        pet["stamina"] = round(self._clamp(pet["stamina"], 0, sta_max), 2)
        pet["mood"] = round(self._clamp(pet["mood"], 0, mood_max), 2)
        pet["health"] = round(self._clamp(pet["health"], 0, PET_MAX_HEALTH), 2)

    @staticmethod
    def _worst_tier(satiety: float, thirst: float, mood: float) -> int:
        """每个属性单独定档，取最差档（3 最差）"""
        tiers = []
        tiers.append(1 if satiety >= 100 else (2 if satiety >= 50 else 3))
        tiers.append(1 if thirst >= 100 else (2 if thirst >= 70 else 3))
        tiers.append(1 if mood >= 80 else (2 if mood >= 50 else 3))
        return max(tiers)

    def _settle_once(self, pet: dict, settle_date: str) -> None:
        """执行一次每日结算"""
        health = pet["health"]

        # 1. 基础结算（按健康档位）
        if health >= 100:
            sat_d = -random.uniform(10.0, 15.0)
            thr_d = -random.uniform(10.0, 15.0)
            sta_d = random.uniform(100.0, 120.0)
            mood_d = random.uniform(1.0, 5.0)
            health_base_d = 0.0
        elif health >= 40:
            sat_d = -random.uniform(15.0, 20.0)
            thr_d = -random.uniform(15.0, 20.0)
            if random.random() < 0.8:
                sta_d = random.uniform(80.0, 100.0)
            else:
                sta_d = random.uniform(100.0, 120.0)
            mood_d = random.uniform(1.0, 2.5)
            health_base_d = 0.0
        else:
            sat_d = -random.uniform(20.0, 25.0)
            thr_d = -random.uniform(20.0, 25.0)
            sta_d = random.uniform(40.0, 60.0)
            mood_d = -random.uniform(2.0, 5.0)
            health_base_d = -random.uniform(1.0, 8.0)

        # 2. 饱食/口渴/心情 三档对健康的影响
        tier = self._worst_tier(pet["satiety"], pet["thirst"], pet["mood"])
        if tier == 1:
            health_tier_d = random.uniform(5.0, 10.0)
        elif tier == 2:
            health_tier_d = random.uniform(0.1, 6.0)
        else:
            health_tier_d = -random.uniform(0.1, 10.0)

        health_d = health_base_d + health_tier_d

        # 3. 应用（先按当前健康度的上限 clamp 属性，再改健康度，最后统一 clamp）
        sat_max, thr_max, sta_max, mood_max = self._attr_max(health)
        pet["satiety"] = round(self._clamp(pet["satiety"] + sat_d, 0, sat_max), 2)
        pet["thirst"] = round(self._clamp(pet["thirst"] + thr_d, 0, thr_max), 2)
        pet["stamina"] = round(self._clamp(pet["stamina"] + sta_d, 0, sta_max), 2)
        pet["mood"] = round(self._clamp(pet["mood"] + mood_d, 0, mood_max), 2)
        pet["health"] = round(self._clamp(pet["health"] + health_d, 0, PET_MAX_HEALTH), 2)
        self._clamp_attrs(pet)

        pet["last_settle"] = {
            "date": settle_date,
            "satiety_d": round(sat_d, 2),
            "thirst_d": round(thr_d, 2),
            "stamina_d": round(sta_d, 2),
            "mood_d": round(mood_d, 2),
            "health_d": round(health_d, 2),
            "rested_well": sta_d > 100.0,
            "sick": pet["health"] <= 39.0,
            "tier3": tier == 3,
        }

    def _bring_pet_up_to_date(self, pet: dict, today: str) -> None:
        """把宠物结算到今日（缺几天结算几天）"""
        last = pet.get("last_settle_date", "")
        if last == today:
            return
        if last:
            try:
                start = date.fromisoformat(last)
                end = date.fromisoformat(today)
                d = start
                while d < end:
                    d = d + timedelta(days=1)
                    self._settle_once(pet, d.isoformat())
            except ValueError:
                pass
        pet["last_settle_date"] = today
        if pet.get("money_event_date") != today:
            pet["money_event_date"] = today
            pet["money_event_count"] = 0

    def _settle_display_lines(self, pet: dict):
        ls = pet.get("last_settle")
        if not ls:
            return []
        lines = ["🐾 宠物结算（昨晚）："]
        lines.append(f"🍖 饱食度 {ls['satiety_d']:+.1f}，💧 口渴值 {ls['thirst_d']:+.1f}，"
                     f"⚡ 体力 {ls['stamina_d']:+.1f}，😊 心情 {ls['mood_d']:+.1f}，❤️ 健康 {ls['health_d']:+.1f}")
        if ls.get("rested_well"):
            lines.append("😴 昨晚你的宠物休息得很好！")
        if ls.get("sick"):
            lines.append("🤒 宠物生病了，快给它吃药吧！")
        if ls.get("tier3"):
            lines.append("⚠️ 有属性处于第三档（太差），注意喂食 / 饮水 / 陪伴！")
        return lines

    def _apply_exp(self, pet: dict) -> str:
        new_level = min(PET_MAX_LEVEL, int(float(pet.get("exp", 0.0)) // PET_EXP_PER_LEVEL) + 1)
        old = int(pet.get("level", 1))
        pet["level"] = new_level
        if new_level > old:
            return f"\n🎊 宠物升级！Lv.{old} → Lv.{new_level}"
        return ""

    def _fav_multipliers(self, fav_level: int):
        """返回 (正面效果倍率, 负面效果倍率)"""
        if fav_level <= 1:
            return 1.0, 1.0
        if fav_level <= 5:
            return 1.1, 1.0
        if fav_level <= 9:
            return 1.1, 0.9
        return 1.2, 0.8

    # ================= 后台配置解析 =================
    def _load_config(self) -> dict:
        cfg = {"jobs": [], "plays": [], "shop": []}
        if not os.path.exists(CONFIG_FILE):
            return cfg
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw_lines = f.readlines()
        except Exception as e:
            logger.error(f"[插件] 读取后台配置失败: {e}")
            return cfg

        cur = None
        for raw in raw_lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                inner = line[1:-1]
                if ":" in inner:
                    typ, nm = inner.split(":", 1)
                    typ, nm = typ.strip(), nm.strip()
                    cur = {"type": typ, "name": nm, "data": {}}
                    if typ == "打工":
                        cfg["jobs"].append(cur)
                    elif typ == "玩耍":
                        cfg["plays"].append(cur)
                    elif typ == "商店":
                        cfg["shop"].append(cur)
                    else:
                        cur = None
                continue
            if cur is None:
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                cur["data"][k.strip()] = v.strip()

        return self._normalize_config(cfg)

    def _normalize_config(self, cfg: dict) -> dict:
        jobs = []
        for j in cfg["jobs"]:
            d = j["data"]
            jobs.append({
                "name": j["name"],
                "desc": d.get("描述", ""),
                "min_level": self._f(d.get("最低等级", 0)),
                "min_health": self._f(d.get("最低健康度", 0)),
                "min_mood": self._f(d.get("最低心情值", 0)),
                "cost": {
                    "stamina": self._f(d.get("消耗体力", 0)),
                    "satiety": self._f(d.get("消耗饱食度", 0)),
                    "thirst": self._f(d.get("消耗口渴值", 0)),
                    "health": self._f(d.get("消耗健康度", 0)),
                    "mood": self._f(d.get("消耗心情值", 0)),
                },
                "time": self._f(d.get("需要时间", 0)),
                "coins": self._f(d.get("金币", 0)),
                "exp": self._f(d.get("经验", 0)),
            })

        plays = []
        for p in cfg["plays"]:
            d = p["data"]
            plays.append({
                "name": p["name"],
                "desc": d.get("描述", ""),
                "min_level": self._f(d.get("最低等级", 0)),
                "min_health": self._f(d.get("最低健康度", 0)),
                "min_mood": self._f(d.get("最低心情值", 0)),
                "cost": {
                    "stamina": self._f(d.get("消耗体力", 0)),
                    "satiety": self._f(d.get("消耗饱食度", 0)),
                    "thirst": self._f(d.get("消耗口渴值", 0)),
                    "health": self._f(d.get("消耗健康度", 0)),
                },
                "time": self._f(d.get("需要时间", 0)),
                "exp": self._f(d.get("经验", 0)),
                "mood": self._f(d.get("心情值", 0)),
            })

        shop = []
        for s in cfg["shop"]:
            d = s["data"]
            shop.append({
                "name": s["name"],
                "type": d.get("类型", ""),
                "price": self._f(d.get("价格", 0)),
                "effects": {
                    "satiety": self._f(d.get("饱食度", 0)),
                    "thirst": self._f(d.get("口渴值", 0)),
                    "stamina": self._f(d.get("体力", 0)),
                    "mood": self._f(d.get("心情值", 0)),
                    "health": self._f(d.get("健康度", 0)),
                },
            })

        return {"jobs": jobs, "plays": plays, "shop": shop}

    # ================= WebUI 后端数据接口 =================
    def _read_config_text(self) -> str:
        """读取 后台.txt 原文，供 WebUI 编辑器使用"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            logger.error(f"[插件] 读取后台配置失败: {e}")
        return ""

    def _write_config_text(self, text: str):
        """保存 后台.txt 原文，返回 (ok, msg)"""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                f.write(text)
            return True, "保存成功"
        except Exception as e:
            return False, f"保存失败: {e}"

    def _read_data_text(self) -> str:
        """读取 data.json 原文，供 WebUI 导入导出使用"""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            logger.error(f"[插件] 读取数据失败: {e}")
        return "{}"

    def _write_data_text(self, text: str):
        """导入 data.json（覆盖），先做 JSON 校验，返回 (ok, msg)"""
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                return False, "数据必须是 JSON 对象（{...}）"
        except Exception as e:
            return False, f"JSON 格式错误: {e}"
        try:
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, DATA_FILE)
            return True, "导入成功"
        except Exception as e:
            return False, f"写入失败: {e}"

    # ================= WebUI Pages 后端 API =================
    async def web_get_backend_config(self):
        """读取 后台.txt 内容"""
        async with self._lock:
            return json_response({"content": self._read_config_text()})

    async def web_save_backend_config(self):
        """保存 后台.txt 内容"""
        async with self._lock:
            payload = await request.json(default={})
            content = payload.get("content")
            if not isinstance(content, str):
                return error_response("content 必须是字符串", status_code=400)
            ok, msg = self._write_config_text(content)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"saved": True})

    async def web_export_data(self):
        """导出 data.json：返回 JSON 内容，由前端用 Blob 触发下载"""
        async with self._lock:
            if not os.path.exists(DATA_FILE):
                return error_response("data.json 不存在", status_code=404)
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                return error_response(f"读取失败: {e}", status_code=500)
            return json_response({"content": content})

    async def web_import_data(self):
        """导入 data.json：JSON 请求体携带 content，覆盖写入"""
        async with self._lock:
            payload = await request.json(default={})
            content = payload.get("content")
            if not isinstance(content, str):
                return error_response("content 必须是字符串", status_code=400)
            ok, msg = self._write_data_text(content)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"imported": True})

    def _read_file(self, path):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            logger.error(f"[插件] 读取文件失败: {e}")
        return ""

    def _write_file(self, path, text):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return True, "保存成功"
        except Exception as e:
            return False, f"保存失败: {e}"

    async def web_get_crops(self):
        async with self._lock:
            return json_response({"content": self._read_file(CROP_FILE)})

    async def web_save_crops(self):
        async with self._lock:
            payload = await request.json(default={})
            content = payload.get("content")
            if not isinstance(content, str):
                return error_response("content 必须是字符串", status_code=400)
            ok, msg = self._write_file(CROP_FILE, content)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"saved": True})

    async def web_get_ferts(self):
        async with self._lock:
            return json_response({"content": self._read_file(FERT_FILE)})

    async def web_save_ferts(self):
        async with self._lock:
            payload = await request.json(default={})
            content = payload.get("content")
            if not isinstance(content, str):
                return error_response("content 必须是字符串", status_code=400)
            ok, msg = self._write_file(FERT_FILE, content)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"saved": True})

    async def web_get_loan_pkgs(self):
        async with self._lock:
            return json_response({"content": self._read_file(LOAN_FILE)})

    async def web_save_loan_pkgs(self):
        async with self._lock:
            payload = await request.json(default={})
            content = payload.get("content")
            if not isinstance(content, str):
                return error_response("content 必须是字符串", status_code=400)
            ok, msg = self._write_file(LOAN_FILE, content)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"saved": True})

    # ================= 宠物：指令 =================
    def _handle_unlock_pet(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        if key in data.get("pets", {}):
            pet = data["pets"][key]
            return f"{name} 已经拥有一只宠物「{pet['name']}」啦，每位玩家最多只能养一只。"
        if self._coins_of(data, key) < self.pet_unlock_cost:
            return f"解锁宠物需要 {self.pet_unlock_cost} 金币（当前 {self._coins_of(data, key)}）。"

        self._add_coins(data, key, -self.pet_unlock_cost)
        today = date.today().isoformat()
        pet = {
            "name": "宠物", "level": 1, "exp": 0.0,
            "satiety": 100.0, "thirst": 100.0, "stamina": 100.0, "health": 120.0, "mood": 80.0,
            "last_settle_date": today, "last_settle": None,
            "inventory": {}, "money_event_date": today, "money_event_count": 0,
        }
        data.setdefault("pets", {})[key] = pet
        self._save(data)
        return (f"🎉 {name} 花费 {self.pet_unlock_cost} 金币解锁了一只宠物！\n"
                f"发送「更改宠物名字 <名字>」给它起名，\n"
                f"发送「宠物帮助」查看玩法。")

    def _handle_pet_status(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」（需 {self.pet_unlock_cost} 金币）领养一只吧。"
        self._bring_pet_up_to_date(pet, date.today().isoformat())
        self._save(data)

        sat_max, thr_max, sta_max, mood_max = self._attr_max(pet["health"])
        need_exp = PET_EXP_PER_LEVEL - (pet["exp"] % PET_EXP_PER_LEVEL)
        lines = [
            f"🐾 {name} 的宠物「{pet['name']}」：",
            f"⭐ 等级：Lv.{pet['level']}（经验 {pet['exp']:.1f}）",
        ]
        if pet["level"] < PET_MAX_LEVEL:
            lines.append(f"📚 距下一级还需 {need_exp:.0f} 经验")
        lines += [
            f"🍖 饱食度：{pet['satiety']:.1f}/{sat_max:.0f}",
            f"💧 口渴值：{pet['thirst']:.1f}/{thr_max:.0f}",
            f"⚡ 体力：{pet['stamina']:.1f}/{sta_max:.0f}",
            f"😊 心情值：{pet['mood']:.1f}/{mood_max:.0f}",
            f"❤️ 健康度：{pet['health']:.1f}/{PET_MAX_HEALTH:.0f}",
        ]
        if pet["health"] <= 39:
            lines.append("🤒 宠物生病了，快给它吃药吧！")
        return "\n".join(lines)

    def _handle_rename_pet(self, event: AstrMessageEvent) -> str:
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：更改宠物名字 <新名字>"
        new_name = parts[1].strip()
        if not new_name:
            return "格式：更改宠物名字 <新名字>"
        if len(new_name) > 12:
            return "名字太长了（最多 12 个字）。"

        key = self._user_key(event)
        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"你还没有宠物，发送「解锁宠物」（需 {self.pet_unlock_cost} 金币）领养一只吧。"
        pet["name"] = new_name
        self._save(data)
        return f"✅ 宠物名字已改为「{new_name}」。"

    def _handle_work(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)

        if len(parts) < 2:
            return self._work_list()
        job_name = parts[1].strip()

        cfg = self._load_config()
        job = next((j for j in cfg["jobs"] if j["name"] == job_name), None)
        if not job:
            return f"没有名为「{job_name}」的打工，发送「打工」查看列表。"

        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"
        self._bring_pet_up_to_date(pet, date.today().isoformat())

        # 冷却检查：打工中的宠物不能继续打工（冷却 = 该工作的「需要时间」）
        now_ts = datetime.now().timestamp()
        work_until = float(pet.get("work_until", 0) or 0)
        if now_ts < work_until:
            return f"{name} 的宠物还在打工中（冷却剩余 {self._fmt_duration(work_until - now_ts)}），暂时不能打工。"

        # 要求检查
        if pet["level"] < job["min_level"]:
            return f"宠物等级不足（需要 Lv.{int(job['min_level'])}，当前 Lv.{pet['level']}）。"
        if pet["health"] < job["min_health"]:
            return f"宠物健康度不足（需要 {job['min_health']:.0f}，当前 {pet['health']:.1f}）。"
        if pet["mood"] < job["min_mood"]:
            return f"宠物心情不足（需要 {job['min_mood']:.0f}，当前 {pet['mood']:.1f}）。"

        # 消耗检查
        for attr, cost in job["cost"].items():
            if cost > 0 and pet[attr] < cost:
                return f"{ATTR_LABELS[attr]}不足，无法打工（需要 {cost:.0f}，当前 {pet[attr]:.1f}）。"

        # 应用消耗
        for attr, cost in job["cost"].items():
            pet[attr] = round(max(0.0, pet[attr] - cost), 2)

        # 报酬
        user = self._ensure_user(data, key)
        user["coins"] = int(user.get("coins", 0)) + int(job["coins"])
        pet["exp"] = round(float(pet.get("exp", 0.0)) + job["exp"], 2)
        lvl_msg = self._apply_exp(pet)
        self._clamp_attrs(pet)
        # 进入打工冷却
        pet["work_until"] = now_ts + int(job["time"]) * 60
        self._save(data)

        cd = f"（冷却 {int(job['time'])} 分钟）" if job["time"] > 0 else ""
        return (f"💼 {name} 的宠物去「{job['name']}」打工完成！{cd}\n"
                f"💰 金币 +{int(job['coins'])}，🐾 经验 +{job['exp']:.1f}{lvl_msg}")

    def _work_list(self):
        cfg = self._load_config()
        if not cfg["jobs"]:
            return "后台还没有配置打工项目（请管理员编辑 后台.txt）。"
        lines = ["发送「打工 <名称>」开始", ""]
        for j in cfg["jobs"]:
            lines.append(f"· {j['name']}：{j['desc']}｜要求 Lv.{int(j['min_level'])}+ / 健康 {j['min_health']:.0f}+ / 心情 {j['min_mood']:.0f}+｜耗时 {j['time']:.0f}分｜金币 +{int(j['coins'])} 经验 +{j['exp']:.0f}")
        img = self._render_text_image("打工列表", lines)
        if img is not None:
            return img
        return "\n".join(["💼 打工列表（发送「打工 <名称>」开始）："] + lines)

    def _handle_play(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)

        if len(parts) < 2:
            return self._play_list()
        play_name = parts[1].strip()

        cfg = self._load_config()
        play = next((p for p in cfg["plays"] if p["name"] == play_name), None)
        if not play:
            return f"没有名为「{play_name}」的玩耍项目，发送「玩耍」查看列表。"

        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"
        self._bring_pet_up_to_date(pet, date.today().isoformat())

        # 冷却检查：玩耍中的宠物不能继续玩耍（冷却 = 该玩耍项目的「需要时间」）
        now_ts = datetime.now().timestamp()
        play_until = float(pet.get("play_until", 0) or 0)
        if now_ts < play_until:
            return f"{name} 的宠物还在玩耍中（冷却剩余 {self._fmt_duration(play_until - now_ts)}），暂时不能玩耍。"

        if pet["level"] < play["min_level"]:
            return f"宠物等级不足（需要 Lv.{int(play['min_level'])}，当前 Lv.{pet['level']}）。"
        if pet["health"] < play["min_health"]:
            return f"宠物健康度不足（需要 {play['min_health']:.0f}，当前 {pet['health']:.1f}）。"
        if pet["mood"] < play["min_mood"]:
            return f"宠物心情不足（需要 {play['min_mood']:.0f}，当前 {pet['mood']:.1f}）。"

        for attr, cost in play["cost"].items():
            if cost > 0 and pet[attr] < cost:
                return f"{ATTR_LABELS[attr]}不足，无法玩耍（需要 {cost:.0f}，当前 {pet[attr]:.1f}）。"

        for attr, cost in play["cost"].items():
            pet[attr] = round(max(0.0, pet[attr] - cost), 2)

        pet["exp"] = round(float(pet.get("exp", 0.0)) + play["exp"], 2)
        pet["mood"] = round(pet["mood"] + play["mood"], 2)
        lvl_msg = self._apply_exp(pet)
        self._clamp_attrs(pet)

        bonus = ""
        if pet.get("money_event_count", 0) < self.money_event_max_per_day and random.random() < self.money_event_chance:
            user = self._ensure_user(data, key)
            user["coins"] = int(user.get("coins", 0)) + self.money_event_gain
            pet["money_event_count"] = int(pet.get("money_event_count", 0)) + 1
            bonus = f"\n🍀 触发「捡到钱了」事件，金币 +{self.money_event_gain}！"

        # 进入玩耍冷却
        pet["play_until"] = now_ts + int(play["time"]) * 60
        self._save(data)
        cd = f"（冷却 {int(play['time'])} 分钟）" if play["time"] > 0 else ""
        return (f"🎾 {name} 的宠物去「{play['name']}」玩耍完成！{cd}\n"
                f"🐾 经验 +{play['exp']:.1f}，😊 心情 +{play['mood']:.1f}{lvl_msg}{bonus}")

    def _play_list(self):
        cfg = self._load_config()
        if not cfg["plays"]:
            return "后台还没有配置玩耍项目（请管理员编辑 后台.txt）。"
        lines = ["发送「玩耍 <名称>」开始", ""]
        for p in cfg["plays"]:
            lines.append(f"· {p['name']}：{p['desc']}｜要求 Lv.{int(p['min_level'])}+ / 健康 {p['min_health']:.0f}+ / 心情 {p['min_mood']:.0f}+｜耗时 {p['time']:.0f}分｜经验 +{p['exp']:.0f} 心情 +{p['mood']:.0f}")
        img = self._render_text_image("玩耍列表", lines)
        if img is not None:
            return img
        return "\n".join(["🎾 玩耍列表（发送「玩耍 <名称>」开始）："] + lines)

    def _handle_shop(self, event: AstrMessageEvent) -> str:
        cfg = self._load_config()
        if not cfg["shop"]:
            return "商店暂无商品（请管理员编辑 后台.txt）。"
        lines = ["发送「购买 <道具名> [数量]」购买，发送「使用 <道具名> [数量]」使用", ""]
        for it in cfg["shop"]:
            lines.append(f"· {it['name']}（{it['type']}）{int(it['price'])}金币：{self._effect_desc(it['effects'])}")
        lines.append(f"· {PILL_NAME}（特殊）：随机提升全属性 1.0~5.0（签到有几率获得）")
        img = self._render_text_image("宠物商店", lines)
        if img is not None:
            return img
        return "\n".join(["🛒 宠物商店（发送「购买 <道具名> [数量]」购买，发送「使用 <道具名> [数量]」使用）："] + lines)

    def _effect_desc(self, effects: dict) -> str:
        parts = []
        for k, short in ATTR_SHORT.items():
            v = effects.get(k, 0)
            if v > 0:
                parts.append(f"{short}+{v:.0f}")
            elif v < 0:
                parts.append(f"{short}{v:.0f}")
        return " ".join(parts) if parts else "无效果"

    def _render_text_image(self, title: str, lines):
        """把标题 + 正文行渲染为 PNG 图片（使用 OPPOSans-M.ttf）。返回 ("image", path)；失败返回 None"""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except Exception as e:
            logger.error(f"[插件] 缺少 Pillow，无法生成图片: {e}")
            return None
        if not os.path.exists(FONT_FILE):
            return None
        try:
            title_font = ImageFont.truetype(FONT_FILE, 36)
            body_font = ImageFont.truetype(FONT_FILE, 24)
        except Exception as e:
            logger.error(f"[插件] 加载字体 {FONT_FILE} 失败: {e}")
            return None

        pad = 30
        title_h = 64
        line_h = 42

        try:
            probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
            def _w(s, f):
                b = probe.textbbox((0, 0), s, font=f)
                return b[2] - b[0]
            all_w = [_w(title, title_font)] + [_w(l, body_font) for l in lines]
            width = max(480, int(max(all_w) + pad * 2))
        except Exception:
            width = 720

        height = pad * 2 + title_h + line_h * len(lines)
        img = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        y = pad
        draw.text((pad, y), title, font=title_font, fill=(20, 20, 20))
        y += title_h
        for line in lines:
            draw.text((pad, y), line, font=body_font, fill=(70, 70, 70))
            y += line_h

        base = os.path.dirname(DATA_FILE)
        path = os.path.join(base, f"_list_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png")
        try:
            img.save(path)
        except Exception as e:
            logger.error(f"[插件] 保存图片失败: {e}")
            return None

        # 清理 10 分钟前的临时图片，避免堆积
        try:
            now = datetime.now().timestamp()
            for fn in os.listdir(base):
                if fn.startswith("_list_") and fn.endswith(".png"):
                    fp = os.path.join(base, fn)
                    if now - os.path.getmtime(fp) > 600:
                        os.remove(fp)
        except Exception:
            pass

        return ("image", path)

    def _handle_buy(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        item_name, qty, err = _parse_item_qty(event.message_str)
        if err:
            return f"格式：购买 <道具名> [数量]。{err}"

        cfg = self._load_config()
        item = next((it for it in cfg["shop"] if it["name"] == item_name), None)
        if not item:
            return f"商店没有「{item_name}」这个道具，发送「商店」查看。"

        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"

        price = int(item["price"])
        total = price * qty
        coins = self._coins_of(data, key)
        if coins < total:
            return f"金币不足：{qty} × {price} = {total} 金币，当前 {coins}。"

        self._add_coins(data, key, -total)
        inv = pet.setdefault("inventory", {})
        inv[item_name] = int(inv.get(item_name, 0)) + qty
        self._save(data)
        return f"🛒 {name} 花费 {total} 金币购买了「{item_name}」×{qty}。发送「使用 {item_name}」使用。"

    def _handle_use_item(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        item_name, qty, err = _parse_item_qty(event.message_str)
        if err:
            return f"格式：使用 <道具名> [数量]。{err}"

        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"
        self._bring_pet_up_to_date(pet, date.today().isoformat())

        user = data.get("users", {}).get(key, {})
        fav_level = self._level_of(float(user.get("favorability", 0.0)))
        pos_mult, neg_mult = self._fav_multipliers(fav_level)

        inv = pet.setdefault("inventory", {})

        # 属性丸（特殊道具）
        if item_name == PILL_NAME:
            have = int(inv.get(PILL_NAME, 0))
            if have < qty:
                return f"属性丸不足：需要 {qty} 个，当前 {have} 个（签到有几率获得）。"
            boosts = {}
            for _ in range(qty):
                for attr in ATTR_LABELS:
                    v = round(random.uniform(1.0, 5.0) * pos_mult, 2)
                    boosts[attr] = round(boosts.get(attr, 0) + v, 2)
                    pet[attr] = round(pet[attr] + v, 2)
            inv[PILL_NAME] = have - qty
            if inv[PILL_NAME] <= 0:
                inv.pop(PILL_NAME, None)
            self._clamp_attrs(pet)
            self._save(data)
            desc = "，".join(f"{ATTR_SHORT[a]}+{v:.1f}" for a, v in boosts.items())
            return f"💊 {name} 使用了属性丸×{qty}：{desc}"

        # 商店道具
        cfg = self._load_config()
        item = next((it for it in cfg["shop"] if it["name"] == item_name), None)
        if not item:
            return f"没有「{item_name}」这个道具，发送「商店」查看。"
        have = int(inv.get(item_name, 0))
        if have < qty:
            return f"你没有足够的「{item_name}」：需要 {qty} 个，当前 {have} 个。发送「购买 {item_name} {qty}」购买。"

        changes = {}
        for _ in range(qty):
            for attr in ATTR_LABELS:
                val = item["effects"].get(attr, 0)
                if val > 0:
                    applied = round(val * pos_mult, 2)
                elif val < 0:
                    applied = round(val * neg_mult, 2)
                else:
                    continue
                pet[attr] = round(pet[attr] + applied, 2)
                changes[attr] = round(changes.get(attr, 0) + applied, 2)

        inv[item_name] = have - qty
        if inv[item_name] <= 0:
            inv.pop(item_name, None)
        self._clamp_attrs(pet)
        self._save(data)

        if not changes:
            return f"✅ {name} 使用了「{item_name}」×{qty}（无效果）。"
        desc = "，".join(f"{ATTR_SHORT[a]}{v:+.1f}" for a, v in changes.items())
        return f"✅ {name} 使用了「{item_name}」×{qty}：{desc}"

    def _handle_bag(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物。"
        inv = pet.get("inventory", {})
        if not inv:
            return f"{name} 的背包是空的。"
        lines = ["🎒 背包："]
        for item, cnt in inv.items():
            lines.append(f"· {item} ×{cnt}")
        return "\n".join(lines)

    def _handle_help_signin(self):
        sections = [
            ("签到", [
                ("签到", "每日签到，获得金币 / 好感度 / 宠物经验 / 属性丸"),
                ("我的签到", "查看金币与好感度"),
                ("签到帮助", "查看签到模块指令"),
                ("游戏帮助", "查看全部模块指令"),
            ]),
        ]
        return self._build_help("签到帮助", sections)

    def _handle_help_pet(self):
        sections = [
            ("宠物", [
                ("解锁宠物", f"花 {self.pet_unlock_cost} 金币领养宠物（每人限一只）"),
                ("宠物", "查看宠物属性 / 等级 / 经验"),
                ("更改宠物名字 <名字>", "给宠物起名"),
                ("打工 / 打工 <名称>", "打工赚金币与经验"),
                ("玩耍 / 玩耍 <名称>", "玩耍赚经验与心情"),
                ("商店", "查看宠物商店"),
                ("购买 <道具名> [数量]", "购买道具（不填数量 = 1 个）"),
                ("使用 <道具名> [数量]", "使用道具（不填数量 = 1 个）"),
                ("背包", "查看背包"),
            ]),
        ]
        return self._build_help("宠物帮助", sections)

    def _handle_help_roulette(self):
        sections = [
            ("左轮手枪", [
                ("装弹 <子弹数 1~6> <金币>", "发起一局游戏"),
                ("加入", "加入当前这局游戏"),
                ("开始", "发起人提前开始（≥2 人）"),
                ("开枪", "轮到你的回合时开枪"),
                ("我的战绩", "查看胜率 / 净收益 / 金币往来"),
            ]),
        ]
        return self._build_help("左轮手枪帮助", sections)

    def _handle_help_farm(self):
        sections = [
            ("农场", [
                ("解锁农场", "花 1500 金币解锁农场（赠 2 块地）"),
                ("购买土地", "花 800 金币开垦新土地（最多 24 块）"),
                ("土地升级 <编号>", "升级土地等级"),
                ("种子商店 / 肥料商店", "查看商店"),
                ("购买种子 / 购买肥料", "购买种子 / 肥料"),
                ("种植 <作物> [起] [止/数量]", "种植（不填=种满空闲地）"),
                ("施肥 <肥料> [起] [止] [次数]", "施肥"),
                ("收割 [编号]", "收割成熟作物（不填=全部）"),
                ("取消种植 <编号>", "取消种植"),
                ("土地状态 / 农场仓库", "查看土地与仓库"),
                ("售卖 / 售卖种子", "出售作物 / 种子"),
            ]),
        ]
        return self._build_help("农场帮助", sections)

    def _handle_help_game(self):
        sections = [
            ("签到", [
                ("签到", "每日签到，获得金币 / 好感度 / 宠物经验 / 属性丸"),
                ("我的签到", "查看金币与好感度"),
                ("签到帮助", "查看签到模块指令"),
                ("游戏帮助", "查看全部模块指令（本菜单）"),
            ]),
            ("左轮手枪", [
                ("装弹 <子弹数 1~6> <金币>", "发起一局游戏"),
                ("加入", "加入当前这局游戏"),
                ("开始", "发起人提前开始（≥2 人）"),
                ("开枪", "轮到你的回合时开枪"),
                ("我的战绩", "查看胜率 / 净收益 / 金币往来"),
            ]),
            ("宠物", [
                ("解锁宠物", f"花 {self.pet_unlock_cost} 金币领养宠物"),
                ("宠物", "查看宠物属性 / 等级 / 经验"),
                ("更改宠物名字 <名字>", "给宠物起名"),
                ("打工 / 打工 <名称>", "打工赚金币与经验"),
                ("玩耍 / 玩耍 <名称>", "玩耍赚经验与心情"),
                ("商店", "查看宠物商店"),
                ("购买 / 使用 <道具名> [数量]", "购买 / 使用道具（不填数量 = 1 个）"),
                ("背包", "查看背包"),
            ]),
            ("金币银行", [
                ("存款 <金额>", "存钱生息（不填=存最大可存金额）"),
                ("取款 <金额>", "取出本金（不填=全部）"),
                ("银行统计", "查看存款次数 / 存单 / 利息 / 额度"),
                ("借款 <套餐> <金额>", "贷款（0=特别 / 1=一般 / 2=短期 / 3~10=自定义）"),
                ("还款 <套餐> [金额]", "还款（不填套餐=还全部）"),
                ("我的贷款 / 我的征信", "查看贷款账单 / 征信"),
            ]),
            ("农场", [
                ("解锁农场 / 购买土地", "解锁农场 / 开垦土地"),
                ("土地升级 <编号>", "升级土地等级"),
                ("种子商店 / 肥料商店", "查看商店"),
                ("购买种子 / 购买肥料", "购买种子 / 肥料"),
                ("种植 / 施肥 / 收割", "种植、施肥、收割"),
                ("土地状态 / 农场仓库", "查看土地与仓库"),
                ("售卖 / 售卖种子", "出售作物 / 种子"),
            ]),
            ("数据管理", [
                ("查看后台配置 / 保存后台配置", "管理后台配置"),
                ("导出数据 / 导入数据", "数据导入导出"),
            ]),
        ]
        return self._build_help("游戏帮助（全部指令）", sections)

    # ================= 数据管理（后台.txt 编辑 / 数据导入导出） =================
    def _handle_view_config(self) -> str:
        text = self._read_config_text()
        if not text.strip():
            return "后台配置为空。"
        return f"当前 后台.txt 内容：\n{text}"

    def _handle_save_backend_config(self, event: AstrMessageEvent) -> str:
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return "格式：保存后台配置 <内容>（先「查看后台配置」复制全文，改好后粘贴到指令后）"
        ok, msg = self._write_config_text(parts[1].strip() + "\n")
        return f"✅ {msg}" if ok else f"❌ {msg}"

    def _handle_export_data(self) -> str:
        data = self._load()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = os.path.join(os.path.dirname(DATA_FILE), f"data_export_{ts}.json")
        try:
            with open(bak, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"❌ 导出失败: {e}"
        s = json.dumps(data, ensure_ascii=False)
        if len(s) <= 3500:
            return f"✅ 数据已导出到：{bak}\n内容：\n{s}"
        return f"✅ 数据已导出到：{bak}\n数据较大（{len(s)} 字符），请直接到上述路径取文件。"

    def _handle_import_data(self, event: AstrMessageEvent) -> str:
        parts = event.message_str.split(maxsplit=1)
        if len(parts) >= 2 and parts[1].strip():
            raw = parts[1].strip()
        else:
            imp = os.path.join(os.path.dirname(DATA_FILE), "data_import.json")
            if not os.path.exists(imp):
                return "请把要导入的数据保存为插件目录下的 data_import.json，或直接发送「导入数据 <JSON内容>」。"
            try:
                with open(imp, "r", encoding="utf-8") as f:
                    raw = f.read()
            except Exception as e:
                return f"❌ 读取 data_import.json 失败: {e}"
        ok, msg = self._write_data_text(raw)
        return "✅ 导入成功！" if ok else f"❌ {msg}"

    # ================= 金币银行 =================
    def _bank_base_rate(self, data: dict, key: str) -> float:
        """基础利率（%），由签到好感度等级决定"""
        user = data.get("users", {}).get(key, {})
        fav = user.get("favorability")
        if not isinstance(fav, (int, float)):
            fav = 0.0
        lv = self._level_of(float(fav))
        if lv <= 3:
            lo, hi = 0.02, 0.10
        elif lv <= 5:
            lo, hi = 0.02, 0.15
        elif lv <= 8:
            lo, hi = 0.05, 0.15
        else:
            lo, hi = 0.05, 0.18
        return round(random.uniform(lo, hi), 2)

    def _bank_bonus_rate(self, data: dict, key: str) -> float:
        """利率加成（%），由宠物等级决定；无宠物为 0"""
        pet = data.get("pets", {}).get(key)
        if not pet:
            return 0.0
        lv = int(pet.get("level", 1))
        if lv <= 10:
            lo, hi = 0.00, 0.01
        elif lv <= 40:
            lo, hi = 0.00, 0.02
        elif lv <= 80:
            lo, hi = 0.01, 0.02
        else:
            lo, hi = 0.01, 0.03
        return round(random.uniform(lo, hi), 2)

    def _bank_unlock_time(self):
        """下一个 4:00（凌晨 0-4 点存款则在当天 4:00 解锁，其余在次日 4:00）"""
        now = datetime.now()
        today_4am = datetime.combine(now.date(), time(4, 0))
        if now < today_4am:
            return today_4am
        return datetime.combine(now.date() + timedelta(days=1), time(4, 0))

    def _bank_hours(self) -> int:
        """到下一个 4:00 的小时数（向上取整）"""
        unlock = self._bank_unlock_time()
        sec = (unlock - datetime.now()).total_seconds()
        return int(math.ceil(sec / 3600))

    def _max_bank_storage(self, data: dict, key: str) -> int:
        """最大存储额度 = 好感度×100 + 宠物经验"""
        user = data.get("users", {}).get(key, {})
        fav = user.get("favorability")
        if not isinstance(fav, (int, float)):
            fav = 0.0
        pet = data.get("pets", {}).get(key)
        pet_exp = 0.0
        if pet:
            pe = pet.get("exp")
            pet_exp = float(pe) if isinstance(pe, (int, float)) else 0.0
        return int(float(fav) * 100 + pet_exp)

    def _bank_settle(self, data: dict, key: str):
        """懒结算：到期的存单解锁、利息自动入账。返回 (解锁笔数, 已发放利息)"""
        bank = data.get("bank", {}).get(key)
        if not bank:
            return 0, 0
        ts = datetime.now().timestamp()
        count = 0
        paid = 0
        for d in bank.get("deposits", []):
            uts = d.get("unlock_ts")
            if d.get("status") == "locked" and isinstance(uts, (int, float)) and ts >= uts:
                d["status"] = "matured"
                itr = d.get("interest")
                pay = int(itr) if isinstance(itr, (int, float)) else 0
                paid += pay
                if pay > 0:
                    self._add_coins(data, key, pay)
                ti = bank.get("total_interest")
                if not isinstance(ti, (int, float)):
                    ti = 0.0
                bank["total_interest"] = round(float(ti) + float(itr or 0), 2)
                count += 1
        return count, paid

    @staticmethod
    def _dep_amount(d) -> int:
        """安全读取存单本金，防御异常/空数据"""
        v = d.get("amount") if isinstance(d, dict) else None
        return int(v) if isinstance(v, (int, float)) else 0

    def _handle_bank_deposit(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)

        data = self._load()
        settled, _ = self._bank_settle(data, key)
        if settled > 0:
            self._save(data)  # 即使后续存款失败，也要先持久化已到期的存单结算

        max_store = self._max_bank_storage(data, key)
        if max_store <= 0:
            return "你的最大存储额度为 0（额度 = 好感度×100 + 宠物经验），先通过签到提升好感度 / 宠物经验吧。"

        bank = data.setdefault("bank", {}).setdefault(key, {"deposits": [], "total_deposits": 0, "total_interest": 0})
        stored = sum(self._dep_amount(d) for d in bank.get("deposits", []))
        coins = self._coins_of(data, key)
        auto = False

        if len(parts) < 2:
            # 未指定金额：默认存入还能存入的最大金额
            auto = True
            amount = min(max_store - stored, coins)
            if amount <= 0:
                if stored >= max_store:
                    return f"存款失败：存储额度已满（已存 {stored}/{max_store}），先提升好感度 / 宠物经验吧。"
                return f"存款失败：金币余额为 0，无法存款。"
        else:
            try:
                amount = int(parts[1].strip())
            except ValueError:
                return "金额必须是整数。格式：存款 <金额>（不填则存入最大可存金额）"
            if amount <= 0:
                return "存款金额必须为正整数。"
            if stored + amount > max_store:
                return f"存款失败：超出最大存储额度（已存 {stored}，额度 {max_store} = 好感度×100 + 宠物经验）。"
            if coins < amount:
                return f"金币不足（当前 {coins}，需要 {amount}）。"

        base_rate = self._bank_base_rate(data, key)
        bonus_rate = self._bank_bonus_rate(data, key)
        hours = self._bank_hours()
        interest = int(round(amount * (base_rate + bonus_rate) / 100.0 * hours))

        now = datetime.now()
        unlock = self._bank_unlock_time()

        self._add_coins(data, key, -amount)
        bank["deposits"].append({
            "amount": amount,
            "base_rate": base_rate,
            "bonus_rate": bonus_rate,
            "hours": hours,
            "interest": interest,
            "deposit_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "unlock_ts": unlock.timestamp(),
            "status": "locked",
        })
        td = bank.get("total_deposits")
        bank["total_deposits"] = (int(td) if isinstance(td, (int, float)) else 0) + 1
        self._save(data)

        head = f"🏦 {name} 存款成功！"
        if auto:
            head = f"🏦 {name} 存款成功（未指定金额，已自动存入最大可存金额）！"
        return (head + "\n"
                f"💰 本金：{amount}（已锁定）\n"
                f"📈 利率：{base_rate:.2f}% + {bonus_rate:.2f}% = {base_rate + bonus_rate:.2f}%/小时\n"
                f"⏰ 计息 {hours} 小时，下一个 4:00 解锁\n"
                f"🧮 预计利息：{interest} 金币\n"
                f"🔓 解锁后利息自动入账，本金发送「取款」取出。")

    def _handle_bank_withdraw(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)

        data = self._load()
        settled, _ = self._bank_settle(data, key)
        if settled > 0:
            self._save(data)  # 即使后续取款失败，也要先持久化已到期的存单结算

        bank = data.get("bank", {}).get(key)
        if not bank or not bank.get("deposits"):
            return f"{name} 银行里还没有存款，发送「存款 <金额>」存钱吧。"

        matured_sum = sum(self._dep_amount(d) for d in bank["deposits"] if d.get("status") == "matured")
        locked_cnt = sum(1 for d in bank["deposits"] if d.get("status") == "locked")
        if matured_sum <= 0:
            return f"{name} 还没有已解锁的本金（锁定中的存单 {locked_cnt} 笔，4:00 解锁）。"

        if len(parts) < 2:
            withdraw = matured_sum
        else:
            try:
                withdraw = int(parts[1].strip())
            except ValueError:
                return "金额必须是整数。格式：取款 <金额>（不填则全部取出）"
            if withdraw <= 0:
                return "取款金额必须为正整数。"
            if withdraw > matured_sum:
                return f"已解锁的本金只有 {matured_sum}，无法取出 {withdraw}。"

        remaining = withdraw
        new_deposits = []
        for d in bank["deposits"]:
            if d.get("status") == "matured" and remaining > 0:
                amt = self._dep_amount(d)
                take = min(amt, remaining)
                remaining -= take
                if take < amt:
                    d["amount"] = amt - take
                    new_deposits.append(d)
            else:
                new_deposits.append(d)
        bank["deposits"] = new_deposits

        self._add_coins(data, key, withdraw)
        self._save(data)
        return f"🏦 {name} 取款成功：取出本金 {withdraw} 金币（已解锁本金剩余 {matured_sum - withdraw}）。"

    def _handle_bank_stats(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        settled, _ = self._bank_settle(data, key)
        if settled > 0:
            self._save(data)

        max_store = self._max_bank_storage(data, key)
        bank = data.get("bank", {}).get(key, {})
        deposits = bank.get("deposits", [])
        locked = [d for d in deposits if d.get("status") == "locked"]
        matured = [d for d in deposits if d.get("status") == "matured"]
        locked_sum = sum(self._dep_amount(d) for d in locked)
        matured_sum = sum(self._dep_amount(d) for d in matured)
        td = bank.get("total_deposits")
        ti = bank.get("total_interest")
        td = int(td) if isinstance(td, (int, float)) else 0
        ti = float(ti) if isinstance(ti, (int, float)) else 0.0

        lines = [
            f"🏦 {name} 的银行统计：",
            f"📊 累计存款次数：{td}",
            f"💳 生效中存单：{len(deposits)} 笔（锁定 {len(locked)} / 可取 {len(matured)}）",
            f"💰 银行内本金：{locked_sum + matured_sum}（锁定 {locked_sum} / 可取 {matured_sum}）",
            f"🧮 已获取利息总额：{ti:.2f} 金币",
            f"📈 最大存储额度：{max_store}（= 好感度×100 + 宠物经验）",
        ]
        if deposits:
            lines.append("📜 存单明细：")
            for d in deposits:
                st = "🔒" if d.get("status") == "locked" else "✅"
                lines.append(f"· {st} 本金 {self._dep_amount(d)}｜利率 {d.get('base_rate', 0):.2f}%+{d.get('bonus_rate', 0):.2f}%×{d.get('hours', 0)}h｜利息 {d.get('interest', 0)}")
        return "\n".join(lines)

    # ================= 银行贷款 =================
    def _load_loan_packages(self):
        """自定义贷款套餐（代码 3~10）"""
        pkgs = []
        if not os.path.exists(LOAN_FILE):
            return pkgs
        try:
            with open(LOAN_FILE, "r", encoding="utf-8") as f:
                raw_lines = f.readlines()
        except Exception as e:
            logger.error(f"[插件] 读取贷款套餐配置失败: {e}")
            return pkgs
        cur = None
        for raw in raw_lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                inner = line[1:-1]
                if ":" in inner:
                    _, code = inner.split(":", 1)
                    cur = {"code": code.strip(), "data": {}}
                    pkgs.append(cur)
                continue
            if cur is None:
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                cur["data"][k.strip()] = v.strip()
        result = []
        for it in pkgs:
            d = it["data"]
            try:
                result.append({
                    "code": int(it["code"]),
                    "max_amount": int(self._f(d.get("最大金额", 0))),
                    "fav_req": int(self._f(d.get("好感度等级要求", 0))),
                    "pet_req": int(self._f(d.get("宠物等级要求", 0))),
                    "farm_req": int(self._f(d.get("农场等级要求", 0))),
                    "rate": self._f(d.get("利息", 0)),
                })
            except Exception:
                continue
        return result

    def _loans_of(self, data, key):
        return data.get("loans", {}).get(key)

    def _ensure_loans(self, data, key):
        return data.setdefault("loans", {}).setdefault(key, {
            "loans": [], "overdue_records": [],
            "overdue_year": 0, "overdue_year_key": str(date.today().year),
            "ban": False, "daily_borrowed": 0, "daily_date": "", "daily_process_date": "",
        })

    def _loan_unlocked(self, data, key):
        return key in data.get("pets", {}) or key in data.get("farms", {})

    @staticmethod
    def _loan_general_max(farm_level):
        if farm_level <= 10:
            return 3000
        if farm_level <= 25:
            return 10000
        if farm_level <= 50:
            return 20000
        if farm_level <= 75:
            return 40000
        return 100000

    @staticmethod
    def _loan_short_max(fav_level):
        if fav_level <= 2:
            return 1000
        if fav_level <= 5:
            return 2000
        if fav_level <= 8:
            return 3000
        if fav_level == 9:
            return 4000
        return 6000

    def _loan_general_rate(self, pet_level):
        """一般套餐日息 2%~5% 随机，宠物等级减免"""
        rate = random.uniform(2.0, 5.0)
        if pet_level > 0:
            if random.random() < pet_level / 100.0:
                rate -= 1.0
            if pet_level <= 25:
                rate -= 0.1
            elif pet_level <= 75:
                rate -= 0.2
            else:
                rate -= 0.5
        return max(0.1, round(rate, 2))

    def _pet_level(self, data, key):
        pet = data.get("pets", {}).get(key)
        return int(pet.get("level", 0)) if pet else 0

    def _loan_package(self, data, key, code):
        """返回套餐信息：0 特别、1 一般、2 短期、3+ 自定义"""
        if code == 0:
            return {"code": 0, "max_amount": LOAN_SPECIAL_AMOUNT, "rate": LOAN_SPECIAL_RATE,
                    "fav_req": 0, "pet_req": 0, "farm_req": 0, "special": True}
        if code == 1:
            farm = data.get("farms", {}).get(key)
            return {"code": 1, "max_amount": self._loan_general_max(int(farm.get("level", 0)) if farm else 0),
                    "rate": self._loan_general_rate(self._pet_level(data, key)),
                    "fav_req": 0, "pet_req": 0, "farm_req": 0, "special": False}
        if code == 2:
            user = data.get("users", {}).get(key, {})
            return {"code": 2, "max_amount": self._loan_short_max(self._level_of(float(user.get("favorability", 0.0)))),
                    "rate": LOAN_SHORT_RATE, "fav_req": 0, "pet_req": 0, "farm_req": 0, "special": False}
        for pkg in self._load_loan_packages():
            if pkg["code"] == code:
                return {"code": code, "max_amount": pkg["max_amount"], "rate": pkg["rate"],
                        "fav_req": pkg["fav_req"], "pet_req": pkg["pet_req"], "farm_req": pkg["farm_req"], "special": False}
        return None

    @staticmethod
    def _loan_accrued(loan, now_ts):
        if loan.get("remaining", 0) <= 0:
            return 0.0
        start = max(loan.get("free_until_ts", 0), loan.get("borrow_ts", 0))
        if now_ts <= start:
            return 0.0
        days = (now_ts - start) // 86400
        if days <= 0:
            return 0.0
        return round(loan.get("remaining", 0) * loan.get("rate", 0) / 100.0 * days, 2)

    def _loan_owed(self, loan, now_ts):
        return round(loan.get("remaining", 0) + self._loan_accrued(loan, now_ts), 2)

    @staticmethod
    def _loan_is_overdue(loan, now_ts):
        return now_ts > loan.get("due_ts", 0) and loan.get("remaining", 0) > 0

    def _has_overdue_now(self, rec, now_ts):
        return any(self._loan_is_overdue(l, now_ts) for l in rec.get("loans", []))

    def _force_unlock(self, data, key):
        """强制解锁：赠送农场（2 块地）和宠物"""
        if key not in data.get("farms", {}):
            farm = self._ensure_farm(data, key)
            for _ in range(FARM_FREE_PLOTS):
                farm["plots"].append(self._new_plot())
        if key not in data.get("pets", {}):
            today = date.today().isoformat()
            data.setdefault("pets", {})[key] = {
                "name": "宠物", "level": 1, "exp": 0.0,
                "satiety": 100.0, "thirst": 100.0, "stamina": 100.0, "health": 120.0, "mood": 80.0,
                "last_settle_date": today, "last_settle": None,
                "inventory": {}, "money_event_date": today, "money_event_count": 0,
            }

    def _special_overdue(self, data, key):
        """特别贷款逾期：重锁农场/宠物，收取仓库总价值 10%（清除数据）"""
        farm = data.get("farms", {}).get(key)
        if farm:
            wh = farm.get("warehouse", {})
            crops = self._load_crops()
            ferts = self._load_fertilizers()
            total = 0
            for nm, cnt in list(wh.get("crops", {}).items()):
                c = self._find_item(crops, nm)
                total += int(round(int(cnt) * (float(c["crop_price"]) if c else 0.0)))
            for nm, cnt in list(wh.get("seeds", {}).items()):
                c = self._find_item(crops, nm)
                total += int(round(int(cnt) * (float(c["seed_sell_price"]) if c else 0.0)))
            for nm, cnt in list(wh.get("fertilizers", {}).items()):
                f = self._find_item(ferts, nm)
                total += int(cnt) * (int(f["price"]) if f else 0)
            take = int(total * 0.1)
            if take > 0:
                self._repay_loans(data, key, take)
            farm["warehouse"] = {"crops": {}, "seeds": {}, "fertilizers": {}}
        data.get("pets", {}).pop(key, None)
        data.get("farms", {}).pop(key, None)

    def _loan_sync(self, data, key, now_ts=None):
        """标记逾期 + 逾期处置（特别贷款重锁、逾期记录、年度计数、30 天农场回退、禁用）"""
        now_ts = now_ts or datetime.now().timestamp()
        rec = self._ensure_loans(data, key)
        changed = False
        year = str(date.today().year)
        if rec.get("overdue_year_key") != year:
            rec["overdue_year_key"] = year
            rec["overdue_year"] = 0
            rec["ban"] = False  # 跨年后解除临时禁用
        for loan in rec.get("loans", []):
            if loan.get("remaining", 0) <= 0:
                continue
            if now_ts > loan.get("due_ts", 0):
                if not loan.get("overdue"):
                    loan["overdue"] = True
                    changed = True
                    rec["overdue_year"] = int(rec.get("overdue_year", 0)) + 1
                    rec["overdue_records"].append({
                        "amount": loan.get("remaining", 0),
                        "package": loan.get("package", 0),
                        "time": datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M"),
                    })
                    if loan.get("special"):
                        self._special_overdue(data, key)
                if now_ts - loan.get("due_ts", 0) > LOAN_FARM_ROLLBACK_DAYS * 86400:
                    farm = data.get("farms", {}).get(key)
                    if farm:
                        farm["level"] = 0
                        farm["exp"] = 0.0
                        farm["plots"] = [self._new_plot() for _ in range(FARM_FREE_PLOTS)]
                        farm["warehouse"] = {"crops": {}, "seeds": {}, "fertilizers": {}}
        if rec.get("overdue_year", 0) >= LOAN_OVERDUE_YEAR_LIMIT:
            rec["ban"] = True
        return changed

    def _repay_loans(self, data, key, amount, code=None):
        """还款，返回实际还款金额；优先还逾期最久 / 即将到期的账单"""
        rec = self._ensure_loans(data, key)
        now_ts = datetime.now().timestamp()
        candidates = [l for l in rec.get("loans", []) if l.get("remaining", 0) > 0 and (code is None or l.get("package") == code)]
        if not candidates:
            return 0
        candidates.sort(key=lambda l: (0 if l.get("overdue") else 1, l.get("due_ts", 0)))
        remaining_money = amount
        repaid = 0
        for loan in candidates:
            if remaining_money <= 0:
                break
            owed = self._loan_owed(loan, now_ts)
            take = min(remaining_money, owed)
            remaining_money -= take
            repaid += take
            loan["remaining"] = round(loan["remaining"] - take, 2)
            if loan["remaining"] <= 0:
                loan["remaining"] = 0
        rec["loans"] = [l for l in rec.get("loans", []) if l.get("remaining", 0) > 0]
        return round(repaid, 2)

    def _loan_daily_process(self, data, key, now=None):
        """每日逾期处置：好感度降低 + 23:00 自动卖仓库/自动签到还款（懒执行，一天一次）"""
        now = now or datetime.now()
        rec = self._ensure_loans(data, key)
        if not self._has_overdue_now(rec, now.timestamp()):
            return False
        today = now.strftime("%Y-%m-%d")
        if rec.get("daily_process_date") == today:
            return False
        rec["daily_process_date"] = today
        user = self._ensure_user(data, key)
        special = any(l.get("special") and l.get("remaining", 0) > 0 for l in rec.get("loans", []))
        lo, hi = LOAN_FAV_DROP_SPECIAL if special else LOAN_FAV_DROP_NORMAL
        drop = random.uniform(lo, hi)
        user["favorability"] = round(max(0.0, float(user.get("favorability", 0.0)) - drop), 2)
        if (now.hour, now.minute) >= LOAN_AUTO_TIME:
            farm = data.get("farms", {}).get(key)
            if farm:
                coins = self._sell_warehouse_all(data, key, farm)
                if coins > 0:
                    self._repay_loans(data, key, coins)
            coins = self._auto_signin(data, key)
            if coins > 0:
                self._repay_loans(data, key, coins)
        return True

    def _sell_warehouse_all(self, data, key, farm):
        """卖出仓库全部物品，返回所得金币（不进入余额，直接用于还款）"""
        wh = farm.get("warehouse", {})
        crops = self._load_crops()
        ferts = self._load_fertilizers()
        total = 0
        for nm, cnt in list(wh.get("crops", {}).items()):
            c = self._find_item(crops, nm)
            total += int(round(int(cnt) * (float(c["crop_price"]) if c else 0.0)))
        for nm, cnt in list(wh.get("seeds", {}).items()):
            c = self._find_item(crops, nm)
            total += int(round(int(cnt) * (float(c["seed_sell_price"]) if c else 0.0)))
        for nm, cnt in list(wh.get("fertilizers", {}).items()):
            f = self._find_item(ferts, nm)
            total += int(cnt) * (int(f["price"]) if f else 0)
        farm["warehouse"] = {"crops": {}, "seeds": {}, "fertilizers": {}}
        return total

    def _auto_signin(self, data, key):
        """逾期自动签到：只发放金币与好感度（金币用于抵债），标记当日已签到"""
        today = date.today().isoformat()
        user = data.get("users", {}).get(key)
        if user and user.get("last_date") == today:
            return 0
        if user is None:
            user = self._ensure_user(data, key)
        coins = random.randint(self.min_coins, self.max_coins)
        user["favorability"] = round(float(user.get("favorability", 0.0)) + round(random.uniform(MIN_FAV, MAX_FAV), 2), 2)
        user["last_date"] = today
        return coins

    # ---- 贷款指令 ----
    def _loan_packages_info(self, data, key):
        """所有贷款套餐信息，用于「借款」无参数时的概览"""
        info = []
        info.append({
            "code": 0, "name": "特别贷款（强制解锁）",
            "max": f"{LOAN_SPECIAL_AMOUNT}（固定，不发放金币）",
            "rate": f"{LOAN_SPECIAL_RATE}%/日",
            "note": "贷款 2500 用于强制解锁农场+宠物，不发放金币；已开通宠物/农场不可用；30 天内还清，逾期重锁并收取仓库价值 10%",
        })
        general = " / ".join([f"{lv}级:{amt}" for lv, amt in
                              [(0, 3000), (11, 10000), (26, 20000), (51, 40000), (76, 100000)]])
        info.append({
            "code": 1, "name": "一般贷款",
            "max": f"按农场等级（农场{general}）",
            "rate": "2%~5%/日随机（宠物等级减免）",
            "note": "最近 4:00 后开始计息，15 天逾期",
        })
        short = " / ".join([f"{lv}级:{amt}" for lv, amt in
                            [(0, 1000), (3, 2000), (6, 3000), (9, 4000), (10, 6000)]])
        info.append({
            "code": 2, "name": "短期贷款",
            "max": f"按好感度等级（好感{short}）",
            "rate": "6%/日",
            "note": "10 天免息期，之后每日 6%",
        })
        for pkg in self._load_loan_packages():
            reqs = []
            if pkg.get("fav_req"):
                reqs.append(f"好感Lv.{pkg['fav_req']}+")
            if pkg.get("pet_req"):
                reqs.append(f"宠物Lv.{pkg['pet_req']}+")
            if pkg.get("farm_req"):
                reqs.append(f"农场Lv.{pkg['farm_req']}+")
            req_str = "，".join(reqs) if reqs else "无要求"
            info.append({
                "code": pkg["code"], "name": "自定义贷款",
                "max": f"{pkg['max_amount']}",
                "rate": f"{pkg['rate']}%/日",
                "note": f"要求：{req_str}",
            })
        return info

    def _render_loan_packages(self, data, key):
        rows = []
        rows.append([("格式：借款 <套餐代码> <金额>", (90, 90, 90), False)])
        rows.append([("每日累计贷款上限 = 2 × 套餐上限", (90, 90, 90), False)])
        rows.append([("", (0, 0, 0), False)])
        for pkg in self._loan_packages_info(data, key):
            rows.append([(f"[套餐 {pkg['code']} - {pkg['name']}]", (110, 110, 110), False)])
            rows.append([(f"最大可借：{pkg['max']}", (20, 20, 20), False)])
            rows.append([(f"日利率：{pkg['rate']}", (20, 20, 20), False)])
            if pkg.get("note"):
                rows.append([(f"说明：{pkg['note']}", (80, 80, 80), False)])
            rows.append([("", (0, 0, 0), False)])
        img = self._render_rich_image("借款（贷款套餐一览）", rows)
        if img is not None:
            return img
        lines = ["借款（贷款套餐一览）：", "格式：借款 <套餐代码> <金额>", "每日累计贷款上限 = 2 × 套餐上限"]
        for pkg in self._loan_packages_info(data, key):
            lines.append(f"[套餐 {pkg['code']} - {pkg['name']}]")
            lines.append(f"最大可借：{pkg['max']}")
            lines.append(f"日利率：{pkg['rate']}")
            if pkg.get("note"):
                lines.append(f"说明：{pkg['note']}")
        return "\n".join(lines)

    def _handle_loan_borrow(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            # 无参数：展示所有贷款套餐一览（图片）
            data = self._load()
            return self._render_loan_packages(data, key)
        args = parts[1].split()
        if len(args) < 2:
            return "格式：借款 <套餐代码> <金额>（发送「借款」查看套餐一览）"
        try:
            code = int(args[0])
            amount = int(args[1])
        except ValueError:
            return "套餐代码和金额必须是整数。格式：借款 <套餐代码> <金额>"
        if code not in (0, 1, 2) and not (3 <= code <= 10):
            return "套餐代码无效（0=特别，1=一般，2=短期，3~10=自定义）。"
        if amount <= 0:
            return "金额必须为正整数。"

        data = self._load()
        self._loan_sync(data, key)
        rec = self._ensure_loans(data, key)
        if rec.get("ban"):
            return f"{name} 已因年度逾期超限被禁用贷款功能。"
        if code == 0:
            if key in data.get("pets", {}) or key in data.get("farms", {}):
                return "你已经开通了宠物或农场，不能使用强制解锁（特别贷款）。"
            if amount != LOAN_SPECIAL_AMOUNT:
                return f"特别贷款固定金额为 {LOAN_SPECIAL_AMOUNT} 金币。"
            if any(l.get("remaining", 0) > 0 for l in rec.get("loans", [])):
                return "你有未结清的贷款（含特别贷款），还清前不能再次申请。"
        else:
            if not self._loan_unlocked(data, key):
                return "贷款功能需要先解锁宠物系统或农场（发送「解锁宠物」或「解锁农场」）。"
            if self._has_overdue_now(rec, datetime.now().timestamp()):
                return "你有逾期贷款，还清前不能新增贷款。"
        if any(l.get("special") and l.get("remaining", 0) > 0 for l in rec.get("loans", [])):
            return "你有未结清的特别贷款，还清前不能再次申请任何贷款。"

        pkg = self._loan_package(data, key, code)
        if pkg is None:
            return "该套餐未配置。"
        if amount > pkg["max_amount"]:
            return f"该套餐最大可借 {pkg['max_amount']} 金币。"
        user = data.get("users", {}).get(key, {})
        fav_level = self._level_of(float(user.get("favorability", 0.0)))
        if fav_level < pkg.get("fav_req", 0):
            return f"好感度等级不足（需要 Lv.{pkg['fav_req']}）。"
        if self._pet_level(data, key) < pkg.get("pet_req", 0):
            return f"宠物等级不足（需要 Lv.{pkg['pet_req']}）。"
        farm = data.get("farms", {}).get(key)
        if (int(farm.get("level", 0)) if farm else 0) < pkg.get("farm_req", 0):
            return f"农场等级不足（需要 Lv.{pkg['farm_req']}）。"

        today = date.today().isoformat()
        if rec.get("daily_date") != today:
            rec["daily_date"] = today
            rec["daily_borrowed"] = 0
        if rec.get("daily_borrowed", 0) + amount > int(pkg["max_amount"] * LOAN_DAILY_MULT):
            return f"今日累计贷款已达上限（{int(pkg['max_amount'] * LOAN_DAILY_MULT)}），请明天再申请。"

        now = datetime.now()
        now_ts = now.timestamp()
        if code == 0:
            self._force_unlock(data, key)
            free_until = now_ts
            due = now_ts + LOAN_SPECIAL_DAYS * 86400
            rate = LOAN_SPECIAL_RATE
            special = True
        elif code == 1:
            free_until = self._bank_unlock_time().timestamp()
            due = now_ts + LOAN_GENERAL_OVERDUE_DAYS * 86400
            rate = pkg["rate"]
            special = False
        elif code == 2:
            free_until = now_ts + LOAN_SHORT_GRACE_DAYS * 86400
            due = now_ts + LOAN_SHORT_GRACE_DAYS * 86400
            rate = LOAN_SHORT_RATE
            special = False
        else:
            free_until = self._bank_unlock_time().timestamp()
            due = now_ts + LOAN_GENERAL_OVERDUE_DAYS * 86400
            rate = pkg["rate"]
            special = False
        rec["loans"].append({
            "package": code, "amount": amount, "rate": rate,
            "borrow_ts": now_ts, "free_until_ts": free_until, "due_ts": due,
            "remaining": amount, "overdue": False, "special": special,
        })
        rec["daily_borrowed"] = int(rec.get("daily_borrowed", 0)) + amount
        if code != 0:
            # 只有普通/短期/自定义套餐才发放现金；特别贷款（0）的 2500 是解锁服务费，不发放金币
            self._add_coins(data, key, amount)
        self._save(data)
        if code == 0:
            extra = "\n🔓 已强制解锁农场与宠物系统（产生 2500 金币贷款，日息 1%，30 天内还清，未发放金币）"
        else:
            extra = ""
        return (f"🏦 {name} 借款成功！\n"
                f"💳 套餐 {code}，金额 {amount} 金币\n"
                f"📈 日利率：{rate}%\n"
                f"⏰ 免息至 {datetime.fromtimestamp(free_until).strftime('%m-%d %H:%M')}，逾期日 {datetime.fromtimestamp(due).strftime('%m-%d %H:%M')}{extra}")

    def _handle_loan_repay(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        data = self._load()
        self._loan_sync(data, key)
        rec = self._ensure_loans(data, key)
        if not rec.get("loans"):
            return f"{name} 名下没有贷款。"
        now_ts = datetime.now().timestamp()
        coins = self._coins_of(data, key)

        if len(parts) < 2:
            # 还所有贷款：优先还逾期最久/即将到期
            if coins <= 0:
                return f"{name} 金币余额为 0，无法还款。"
            repaid = self._repay_loans(data, key, coins)
            if repaid > 0:
                self._add_coins(data, key, -int(repaid))
            total = sum(self._loan_owed(l, now_ts) for l in rec.get("loans", []))
            self._save(data)
            return f"🏦 已用全部金币还款 {round(repaid, 2)}，剩余待还 {round(total, 2)}。"
        args = parts[1].split()
        try:
            code = int(args[0])
        except ValueError:
            return "套餐代码必须是整数。"
        targets = [l for l in rec.get("loans", []) if l.get("package") == code and l.get("remaining", 0) > 0]
        if not targets:
            return f"没有套餐 {code} 的未结清贷款。"
        if len(args) >= 2:
            try:
                amount = int(args[1])
            except ValueError:
                return "金额必须是整数。"
            if amount <= 0:
                return "金额必须为正整数。"
        else:
            amount = int(sum(self._loan_owed(l, now_ts) for l in targets))
        if coins <= 0:
            return f"{name} 金币余额为 0。"
        amount = min(amount, coins)
        repaid = self._repay_loans(data, key, amount, code)
        if repaid > 0:
            self._add_coins(data, key, -int(repaid))
        self._save(data)
        return f"🏦 已对套餐 {code} 还款 {round(repaid, 2)} 金币。"

    def _handle_my_loans(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        changed = self._loan_sync(data, key)
        rec = self._ensure_loans(data, key)
        lines = [f"🏦 {name} 的贷款账单："]
        loans = rec.get("loans", [])
        if not loans:
            lines.append("暂无生效中的贷款。")
        now_ts = datetime.now().timestamp()
        for l in loans:
            days = max(0, int((now_ts - max(l.get("free_until_ts", l.get("borrow_ts", 0)), l.get("borrow_ts", 0))) // 86400))
            owed = self._loan_owed(l, now_ts)
            st = "⚠️逾期" if l.get("overdue") else "✅正常"
            lines.append(f"· 套餐{l['package']}｜借款 {l['amount']}｜利率 {l['rate']}%/日｜剩余 {l['remaining']}｜计息 {days} 天｜欠款 {owed}｜{st}")
        if changed:
            self._save(data)
        return "\n".join(lines)

    def _handle_my_credit(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        changed = self._loan_sync(data, key)
        rec = self._ensure_loans(data, key)
        lines = [f"🏦 {name} 的征信报告："]
        overdue = rec.get("overdue_records", [])
        if overdue:
            for r in overdue[-10:]:
                lines.append(f"· 逾期 {r['amount']} 金币（套餐 {r['package']}，{r['time']}）")
        else:
            lines.append("· 暂无逾期记录")
        lines.append("---")
        loans = rec.get("loans", [])
        if not loans:
            lines.append("暂无生效中的贷款账单。")
        else:
            now_ts = datetime.now().timestamp()
            for l in loans:
                owed = self._loan_owed(l, now_ts)
                due = datetime.fromtimestamp(l["due_ts"]).strftime("%m-%d")
                st = "⚠️逾期" if l.get("overdue") else "✅"
                lines.append(f"· 套餐{l['package']}｜借款 {l['amount']}｜利率 {l['rate']}%｜欠款 {owed}｜逾期日 {due}｜{st}")
        if rec.get("ban"):
            lines.append("🚫 已因年度逾期超限被禁用贷款功能")
        if changed:
            self._save(data)
        return "\n".join(lines)

    # ================= 农场 =================
    def _parse_crop_fert(self, path: str, kind: str):
        items = []
        if not os.path.exists(path):
            return items
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_lines = f.readlines()
        except Exception as e:
            logger.error(f"[插件] 读取{kind}配置失败: {e}")
            return items
        cur = None
        for raw in raw_lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                inner = line[1:-1]
                if ":" in inner:
                    _, nm = inner.split(":", 1)
                    cur = {"name": nm.strip(), "data": {}}
                    items.append(cur)
                continue
            if cur is None:
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                cur["data"][k.strip()] = v.strip()
        result = []
        for it in items:
            d = it["data"]
            if kind == "作物":
                result.append({
                    "name": it["name"],
                    "seed_price": self._f(d.get("种子价格", 0)),
                    "seed_sell_price": self._f(d.get("种子卖出价格", 0)),
                    "yield": int(self._f(d.get("产量", 0))),
                    "crop_price": self._f(d.get("成熟作物价格", 0)),
                    "exp": int(self._f(d.get("收获经验值", 0))),
                    "min_level": int(self._f(d.get("最低农场等级要求", 0))),
                    "grow_minutes": int(self._f(d.get("成熟时间", 0))),
                })
            else:
                result.append({
                    "name": it["name"],
                    "price": int(self._f(d.get("肥料价格", 0))),
                    "time_reduce": self._f(d.get("减少时间", 0)),
                    "yield_add": self._f(d.get("增加产量", 0)),
                    "max_uses": int(self._f(d.get("最大使用次数", -1))),
                })
        return result

    def _load_crops(self):
        return self._parse_crop_fert(CROP_FILE, "作物")

    def _load_fertilizers(self):
        return self._parse_crop_fert(FERT_FILE, "肥料")

    def _farm_of(self, data, key):
        return data.get("farms", {}).get(key)

    def _ensure_farm(self, data, key):
        return data.setdefault("farms", {}).setdefault(key, {
            "level": 0, "exp": 0.0, "plots": [],
            "warehouse": {"crops": {}, "seeds": {}, "fertilizers": {}},
        })

    @staticmethod
    def _new_plot():
        return {"grade": 0, "crop": None, "seed": None, "plant_ts": 0, "mature_ts": 0,
                "base_time": 0, "yield": 0, "fert_time": 0.0, "fert_yield": 0.0, "fert": {}}

    @staticmethod
    def _plot_grade(grade):
        return FARM_GRADES[grade] if 0 <= grade < len(FARM_GRADES) else FARM_GRADES[0]

    @staticmethod
    def _plot_free(plot):
        return plot is None or plot.get("crop") is None

    def _farm_seed_mult(self, farm):
        lv = int(farm.get("level", 0))
        if lv <= 19:
            return 1.5
        if lv <= 49:
            return 1.0
        if lv <= 99:
            return 0.8
        return 0.7

    def _farm_gain_exp(self, farm, amount) -> str:
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

    @staticmethod
    def _fmt_duration(sec):
        sec = max(0, int(sec))
        if sec < 60:
            return f"{sec}秒"
        minutes = sec // 60
        if minutes < 60:
            return f"{minutes}分钟"
        hours, rem_min = divmod(minutes, 60)
        if hours < 24:
            return f"{hours}小时{rem_min}分"
        days, rem_h = divmod(hours, 24)
        return f"{days}天{rem_h}小时"

    @staticmethod
    def _fmt_price(v):
        """价格显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0"""
        v = float(v)
        if v == int(v):
            return str(int(v))
        return f"{v:.2f}".rstrip("0").rstrip(".")

    @staticmethod
    def _find_item(items, name):
        return next((x for x in items if x["name"] == name), None)

    # ---- 农场富文本图片 ----
    def _render_rich_image(self, title, rows):
        """rows: 每行是 (text, color, strike) 元组列表。返回 ('image', path) 或 None"""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except Exception:
            return None
        if not os.path.exists(FONT_FILE):
            return None
        try:
            title_font = ImageFont.truetype(FONT_FILE, 34)
            body_font = ImageFont.truetype(FONT_FILE, 24)
        except Exception:
            return None
        pad = 26
        title_h = 56
        line_h = 40
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

        def sw(t, font):
            return probe.textlength(t, font=font)

        max_w = sw(title, title_font)
        for r in rows:
            max_w = max(max_w, sum(sw(s[0], body_font) for s in r))
        width = max(460, int(max_w + pad * 2))
        height = pad * 2 + title_h + line_h * len(rows)

        img = Image.new("RGB", (width, height), (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((pad, pad), title, font=title_font, fill=(20, 20, 20))
        y = pad + title_h
        for r in rows:
            x = pad
            for seg in r:
                text, color, strike = seg
                d.text((x, y), text, font=body_font, fill=color)
                if strike:
                    bb = d.textbbox((x, y), text, font=body_font)
                    midy = (bb[1] + bb[3]) // 2
                    d.line([(bb[0], midy), (bb[2], midy)], fill=color, width=2)
                x += sw(text, body_font)
            y += line_h

        base = os.path.dirname(DATA_FILE)
        path = os.path.join(base, f"_farm_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png")
        try:
            img.save(path)
        except Exception as e:
            logger.error(f"[插件] 保存农场图片失败: {e}")
            return None
        try:
            now = datetime.now().timestamp()
            for fn in os.listdir(base):
                if fn.startswith("_farm_") and fn.endswith(".png"):
                    fp = os.path.join(base, fn)
                    if now - os.path.getmtime(fp) > 600:
                        os.remove(fp)
        except Exception:
            pass
        return ("image", path)

    def _render_help(self, title, sections):
        """把帮助菜单渲染成图片。sections: [(小标题, [(指令, 说明), ...]), ...]"""
        rows = []
        for header, items in sections:
            if header:
                rows.append([(header, (110, 110, 110), False)])
            for cmd, desc in items:
                rows.append([(cmd, (20, 20, 20), False), (f"  {desc}", (90, 90, 90), False)])
        return self._render_rich_image(title, rows)

    def _build_help(self, title, sections):
        """返回图片 ('image', path) 或文本 str（图片失败时回退）"""
        img = self._render_help(title, sections)
        if img is not None:
            return img
        lines = [f"{title}："]
        for header, items in sections:
            if header:
                lines.append(f"【{header}】")
            for cmd, desc in items:
                lines.append(f"{cmd}：{desc}")
        return "\n".join(lines)

    def _render_seed_shop(self, name, farm, crops):
        rows = []
        rows.append([(name, (20, 20, 20), False)])
        lv = int(farm.get("level", 0))
        mult = self._farm_seed_mult(farm)
        if mult > 1:
            bonus = f"农场等级 Lv.{lv}：种子价格 +{int(round((mult - 1) * 100))}%"
        elif mult < 1:
            bonus = f"农场等级 Lv.{lv}：种子价格 -{int(round((1 - mult) * 100))}%"
        else:
            bonus = f"农场等级 Lv.{lv}：种子价格无加成"
        rows.append([(bonus, (90, 90, 90), False)])
        rows.append([("", (0, 0, 0), False)])
        for c in crops:
            base = float(c["seed_price"])
            p = round(base * mult, 2)
            lv_req = f"（需 Lv.{c['min_level']}）" if c["min_level"] > 0 else ""
            if p > base:
                rows.append([(f"{c['name']} {self._fmt_price(p)} 金币{lv_req}", (20, 20, 20), False)])
            elif p < base:
                rows.append([(f"{c['name']} ", (20, 20, 20), False),
                             (f"{self._fmt_price(base)}", (20, 20, 20), True),
                             (" ", (0, 0, 0), False),
                             (f"{self._fmt_price(p)} 金币{lv_req}", (192, 0, 0), False)])
            else:
                rows.append([(f"{c['name']} {self._fmt_price(p)} 金币{lv_req}", (20, 20, 20), False)])
        return self._render_rich_image("种子商店", rows)

    def _render_fertilizer_shop(self, ferts):
        rows = []
        for f in ferts:
            maxu = "不限" if f["max_uses"] < 0 else f"{f['max_uses']}次"
            rows.append([(f"{f['name']} {f['price']}金币 减时{f['time_reduce']:.0f}% 增产{f['yield_add']:.0f}%（每株最多{maxu}）", (20, 20, 20), False)])
        return self._render_rich_image("肥料商店", rows)

    def _render_warehouse(self, farm, crops, ferts):
        wh = farm.get("warehouse", {})
        rows = []
        for key, label in [("crops", "作物"), ("seeds", "种子"), ("fertilizers", "肥料")]:
            rows.append([(f"----{label}----", (60, 60, 60), False)])
            items = wh.get(key, {})
            if not items:
                rows.append([("（空）", (140, 140, 140), False)])
                continue
            for nm, cnt in items.items():
                if key == "crops":
                    c = self._find_item(crops, nm)
                    price = c["crop_price"] if c else 0.0
                elif key == "seeds":
                    c = self._find_item(crops, nm)
                    price = c["seed_sell_price"] if c else 0.0
                else:
                    f = self._find_item(ferts, nm)
                    price = float(f["price"]) if f else 0.0
                rows.append([(f"{nm} ×{cnt} 可售 {self._fmt_price(price)}金币", (20, 20, 20), False)])
        return self._render_rich_image("农场仓库", rows)

    def _unusable_ferts(self, plot, ferts):
        used = plot.get("fert", {})
        bad = [f["name"] for f in ferts if f["max_uses"] >= 0 and int(used.get(f["name"], 0)) >= f["max_uses"]]
        return "、".join(bad) if bad else "无"

    def _render_plot_status(self, name, farm, crops, ferts):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except Exception:
            return None
        if not os.path.exists(FONT_FILE):
            return None
        try:
            title_font = ImageFont.truetype(FONT_FILE, 32)
            small_font = ImageFont.truetype(FONT_FILE, 18)
        except Exception:
            return None
        now = datetime.now().timestamp()
        plots = farm.get("plots", [])
        cards = []
        for i, plot in enumerate(plots):
            num = i + 1
            gname = self._plot_grade(int(plot.get("grade", 0)))[0]
            if plot.get("crop") is None:
                lines = [f"#{num} {gname}", "空闲中", "", ""]
            else:
                crop_name = plot.get("crop", "")
                c = self._find_item(crops, crop_name)
                price = c["crop_price"] if c else 0.0
                income = int(round(int(plot.get("yield", 0)) * float(price)))
                if now >= plot.get("mature_ts", 0):
                    state = "已成熟"
                    remain = "已可收割"
                else:
                    state = "占用中"
                    remain = self._fmt_duration(plot.get("mature_ts", 0) - now)
                unusable = self._unusable_ferts(plot, ferts)
                lines = [
                    f"#{num} {gname} {state}",
                    crop_name,
                    f"剩余 {remain} 预计 {income}金币",
                    f"不可用化肥：{unusable}",
                ]
            cards.append(lines)

        cols = 3
        card_w = 360
        card_h = 140
        gap = 16
        pad = 20
        title_h = 52
        rows_n = (len(cards) + cols - 1) // cols if cards else 1
        width = pad * 2 + card_w * cols + gap * (cols - 1)
        height = pad * 2 + title_h + card_h * rows_n + gap * (rows_n - 1)
        img = Image.new("RGB", (width, height), (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((pad, pad), f"{name} 的土地状态", font=title_font, fill=(20, 20, 20))
        for idx, lines in enumerate(cards):
            r, c = divmod(idx, cols)
            x0 = pad + c * (card_w + gap)
            y0 = pad + title_h + r * (card_h + gap)
            d.rectangle([x0, y0, x0 + card_w, y0 + card_h], outline=(205, 205, 205), width=1)
            yy = y0 + 10
            for ln in lines:
                if ln:
                    d.text((x0 + 10, yy), ln, font=small_font, fill=(40, 40, 40))
                yy += 29
        base = os.path.dirname(DATA_FILE)
        path = os.path.join(base, f"_farm_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png")
        try:
            img.save(path)
        except Exception as e:
            logger.error(f"[插件] 保存土地状态图片失败: {e}")
            return None
        try:
            now = datetime.now().timestamp()
            for fn in os.listdir(base):
                if fn.startswith("_farm_") and fn.endswith(".png"):
                    fp = os.path.join(base, fn)
                    if now - os.path.getmtime(fp) > 600:
                        os.remove(fp)
        except Exception:
            pass
        return ("image", path)

    # ---- 农场指令 ----
    def _farm_need(self, data, key, name):
        if not self._farm_of(data, key):
            return f"{name} 还没有农场，发送「解锁农场」（需 {FARM_UNLOCK_COST} 金币）解锁。"
        return None

    def _handle_farm_unlock(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        if self._farm_of(data, key):
            return f"{name} 已经拥有农场啦。"
        if self._coins_of(data, key) < FARM_UNLOCK_COST:
            return f"解锁农场需要 {FARM_UNLOCK_COST} 金币（当前 {self._coins_of(data, key)}）。"
        self._add_coins(data, key, -FARM_UNLOCK_COST)
        farm = self._ensure_farm(data, key)
        for _ in range(FARM_FREE_PLOTS):
            farm["plots"].append(self._new_plot())
        self._save(data)
        return f"🎉 {name} 花费 {FARM_UNLOCK_COST} 金币解锁了农场，赠送 {FARM_FREE_PLOTS} 块土地！发送「土地状态」查看。"

    def _handle_farm_buy_land(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if len(farm["plots"]) >= FARM_MAX_PLOTS:
            return "土地数量已达上限（24 块）。"
        if self._coins_of(data, key) < FARM_PLOT_COST:
            return f"购买土地需要 {FARM_PLOT_COST} 金币（当前 {self._coins_of(data, key)}）。"
        self._add_coins(data, key, -FARM_PLOT_COST)
        farm["plots"].append(self._new_plot())
        self._save(data)
        return f"✅ {name} 花费 {FARM_PLOT_COST} 金币开垦了一块新土地（当前共 {len(farm['plots'])} 块）。"

    def _handle_farm_upgrade(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：土地升级 <土地编号>"
        try:
            num = int(parts[1].strip())
        except ValueError:
            return "土地编号必须是整数。"
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if num < 1 or num > len(farm["plots"]):
            return f"没有编号为 {num} 的土地（当前共 {len(farm['plots'])} 块）。"
        plot = farm["plots"][num - 1]
        grade = int(plot.get("grade", 0))
        if grade >= len(FARM_UPGRADE_COSTS):
            return "这块地已经是最高等级（黑土地）了。"
        if plot.get("crop") is not None:
            return "这块土地正在种植中，收割后才能升级。"
        cost = FARM_UPGRADE_COSTS[grade]
        if self._coins_of(data, key) < cost:
            return f"升级需要 {cost} 金币（当前 {self._coins_of(data, key)}）。"
        self._add_coins(data, key, -cost)
        plot["grade"] = grade + 1
        self._save(data)
        ng = FARM_GRADES[grade + 1]
        return f"✅ {num} 号土地升级为 {ng[0]}（产量 +{int(ng[1] * 100)}%，时间 -{int(ng[2] * 100)}%）！"

    def _handle_farm_buy_seed(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：购买种子 <作物名> <数量>"
        args = parts[1].split()
        crop_name = args[0]
        count = 1
        if len(args) >= 2:
            try:
                count = int(args[1])
            except ValueError:
                return "数量必须是整数。"
        if count <= 0:
            return "数量必须为正整数。"
        crops = self._load_crops()
        crop = self._find_item(crops, crop_name)
        if not crop:
            return f"没有「{crop_name}」这种作物，发送「种子商店」查看。"
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if int(farm.get("level", 0)) < crop["min_level"]:
            return f"农场等级不足（需要 Lv.{crop['min_level']}，当前 Lv.{farm['level']}）。"
        p = round(crop["seed_price"] * self._farm_seed_mult(farm), 2)
        total = int(round(p * count))
        if self._coins_of(data, key) < total:
            return f"金币不足（需要 {total}，当前 {self._coins_of(data, key)}）。"
        self._add_coins(data, key, -total)
        wh = farm["warehouse"].setdefault("seeds", {})
        wh[crop_name] = int(wh.get(crop_name, 0)) + count
        self._save(data)
        return f"✅ 购买 {crop_name} 种子 ×{count}，花费 {total} 金币（单价 {self._fmt_price(p)}）。"

    def _handle_farm_buy_fert(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：购买肥料 <肥料名> <数量>"
        args = parts[1].split()
        fert_name = args[0]
        count = 1
        if len(args) >= 2:
            try:
                count = int(args[1])
            except ValueError:
                return "数量必须是整数。"
        if count <= 0:
            return "数量必须为正整数。"
        ferts = self._load_fertilizers()
        fert = self._find_item(ferts, fert_name)
        if not fert:
            return f"没有「{fert_name}」这种肥料，发送「肥料商店」查看。"
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        total = int(fert["price"]) * count
        if self._coins_of(data, key) < total:
            return f"金币不足（需要 {total}，当前 {self._coins_of(data, key)}）。"
        self._add_coins(data, key, -total)
        wh = farm["warehouse"].setdefault("fertilizers", {})
        wh[fert_name] = int(wh.get(fert_name, 0)) + count
        self._save(data)
        return f"✅ 购买 {fert_name} ×{count}，花费 {total} 金币。"

    def _handle_farm_plant(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：种植 <作物名> [起始编号] [结束编号/数量]"
        args = parts[1].split()
        crop_name = args[0]
        crops = self._load_crops()
        crop = self._find_item(crops, crop_name)
        if not crop:
            return f"没有「{crop_name}」这种作物，发送「种子商店」查看。"
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if int(farm.get("level", 0)) < crop["min_level"]:
            return f"农场等级不足（需要 Lv.{crop['min_level']}，当前 Lv.{farm['level']}）。"
        plots = farm["plots"]
        if not plots:
            return "还没有土地，发送「购买土地」开垦。"

        if len(args) == 1:
            targets = [i for i, p in enumerate(plots) if self._plot_free(p)]
            if not targets:
                return "没有空闲的土地可以种植。"
        elif len(args) == 2:
            try:
                count = int(args[1])
            except ValueError:
                return "数量必须是整数。"
            if count <= 0:
                return "数量必须为正整数。"
            free = [i for i, p in enumerate(plots) if self._plot_free(p)]
            if count > len(free):
                return f"空闲土地只有 {len(free)} 块，无法种植 {count} 块。"
            targets = free[:count]
        else:
            try:
                start, end = int(args[1]), int(args[2])
            except ValueError:
                return "土地编号必须是整数。"
            if start < 1 or end < start or end > len(plots):
                return f"土地编号无效（当前共 {len(plots)} 块，范围 1~{len(plots)}）。"
            targets = list(range(start - 1, end))
            for i in targets:
                if not self._plot_free(plots[i]):
                    return f"{i + 1} 号土地不是空闲状态，无法种植。"

        wh = farm["warehouse"].setdefault("seeds", {})
        have = int(wh.get(crop_name, 0))
        if have < len(targets):
            return f"{crop_name} 种子不足（需要 {len(targets)}，当前 {have}），发送「购买种子」购买。"

        now = datetime.now().timestamp()
        for i in targets:
            plot = plots[i]
            gname, gy, gt = self._plot_grade(int(plot.get("grade", 0)))
            base_sec = crop["grow_minutes"] * 60
            plot["crop"] = crop_name
            plot["seed"] = crop_name
            plot["plant_ts"] = now
            plot["base_time"] = base_sec
            plot["yield"] = int(crop["yield"] * (1 + gy))
            plot["mature_ts"] = now + base_sec * (1 - gt)
            plot["fert_time"] = 0.0
            plot["fert_yield"] = 0.0
            plot["fert"] = {}
        wh[crop_name] = have - len(targets)
        if wh[crop_name] <= 0:
            wh.pop(crop_name, None)
        self._save(data)
        return f"✅ 在 {len(targets)} 块土地上种下 {crop_name}（编号 {targets[0] + 1}~{targets[-1] + 1}）。"

    def _handle_farm_fertilize(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：施肥 <肥料名> [起始编号] [结束编号] [次数]"
        args = parts[1].split()
        fert_name = args[0]
        ferts = self._load_fertilizers()
        fert = self._find_item(ferts, fert_name)
        if not fert:
            return f"没有「{fert_name}」这种肥料，发送「肥料商店」查看。"
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        plots = farm["plots"]
        now = datetime.now().timestamp()

        growing = [i for i, p in enumerate(plots) if p.get("crop") is not None and now < p.get("mature_ts", 0)]
        if len(args) == 1:
            targets = growing
        else:
            try:
                start = int(args[1])
                end = int(args[2]) if len(args) >= 3 else start
            except ValueError:
                return "土地编号必须是整数。"
            if start < 1 or end < start or end > len(plots):
                return f"土地编号无效（当前共 {len(plots)} 块）。"
            targets = list(range(start - 1, end))
        if not targets:
            return "没有正在生长中的作物可以施肥。"

        times = None
        if len(args) >= 4:
            try:
                times = int(args[3])
            except ValueError:
                return "次数必须是整数。"
            if times <= 0:
                return "次数必须为正整数。"

        wh = farm["warehouse"].setdefault("fertilizers", {})
        have = int(wh.get(fert_name, 0))
        max_uses = int(fert["max_uses"])

        # 计算每块地的使用次数
        use_plan = {}
        for i in targets:
            used = int(plots[i].get("fert", {}).get(fert_name, 0))
            if max_uses >= 0 and used >= max_uses:
                continue
            if times is not None:
                want = times
                if max_uses >= 0:
                    want = min(want, max_uses - used)
            else:
                want = (max_uses - used) if max_uses >= 0 else 1
            if want > 0:
                use_plan[i] = want
        if not use_plan:
            return "目标土地都无法再使用该肥料（已达最大次数）。"
        total_need = sum(use_plan.values())
        if have < total_need:
            return f"{fert_name} 库存不足（需要 {total_need}，当前 {have}）。"

        for i, cnt in use_plan.items():
            plot = plots[i]
            plot["fert"][fert_name] = int(plot["fert"].get(fert_name, 0)) + cnt
            plot["fert_time"] = float(plot.get("fert_time", 0.0)) + cnt * (fert["time_reduce"] / 100.0)
            plot["fert_yield"] = float(plot.get("fert_yield", 0.0)) + cnt * (fert["yield_add"] / 100.0)
            gname, gy, gt = self._plot_grade(int(plot.get("grade", 0)))
            crop = self._find_item(self._load_crops(), plot.get("crop", ""))
            base_time = int(plot.get("base_time", 0)) or (crop["grow_minutes"] * 60 if crop else 0)
            # 重算成熟时间：plant_ts + 基础时间*(1 - 土地时间减免 - 化肥时间减免)，至少 1 分钟
            time_mult = max(0.05, 1 - gt - float(plot.get("fert_time", 0.0)))
            plot["mature_ts"] = float(plot.get("plant_ts", 0)) + base_time * time_mult
            plot["yield"] = int((crop["yield"] if crop else 0) * (1 + gy + float(plot.get("fert_yield", 0.0))))
        wh[fert_name] = have - total_need
        if wh[fert_name] <= 0:
            wh.pop(fert_name, None)
        self._save(data)
        return f"✅ 对 {len(use_plan)} 块地使用了 {fert_name} ×{total_need}（剩余 {wh.get(fert_name, 0)}）。"

    def _handle_farm_harvest(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        crops = self._load_crops()
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        plots = farm["plots"]
        now = datetime.now().timestamp()
        if len(parts) >= 2:
            try:
                num = int(parts[1].strip())
            except ValueError:
                return "土地编号必须是整数。"
            if num < 1 or num > len(plots):
                return f"土地编号无效（当前共 {len(plots)} 块）。"
            targets = [num - 1]
        else:
            targets = [i for i, p in enumerate(plots) if p.get("crop") is not None and now >= p.get("mature_ts", 0)]
        if not targets:
            return "没有可收割的成熟作物。"

        wh = farm["warehouse"].setdefault("crops", {})
        total_exp = 0
        harvested = []
        for i in targets:
            plot = plots[i]
            if plot.get("crop") is None or now < plot.get("mature_ts", 0):
                continue
            crop = self._find_item(crops, plot["crop"])
            amount = int(plot.get("yield", 0))
            wh[plot["crop"]] = int(wh.get(plot["crop"], 0)) + amount
            total_exp += int(crop["exp"]) if crop else 0
            harvested.append(i + 1)
            plot["crop"] = None
            plot["seed"] = None
            plot["plant_ts"] = 0
            plot["mature_ts"] = 0
            plot["base_time"] = 0
            plot["yield"] = 0
            plot["fert_time"] = 0.0
            plot["fert_yield"] = 0.0
            plot["fert"] = {}
        if not harvested:
            return "指定的土地没有成熟作物。"
        lvl_msg = self._farm_gain_exp(farm, total_exp)
        self._save(data)
        return f"✅ 收割了 {len(harvested)} 块地（编号 {harvested}），作物已入库，农场经验 +{total_exp}{lvl_msg}。"

    def _handle_farm_cancel(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：取消种植 <土地编号>"
        try:
            num = int(parts[1].strip())
        except ValueError:
            return "土地编号必须是整数。"
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if num < 1 or num > len(farm["plots"]):
            return f"土地编号无效。"
        plot = farm["plots"][num - 1]
        if plot.get("crop") is None:
            return f"{num} 号土地本来就是空闲的。"
        plot["crop"] = None
        plot["seed"] = None
        plot["plant_ts"] = 0
        plot["mature_ts"] = 0
        plot["base_time"] = 0
        plot["yield"] = 0
        plot["fert_time"] = 0.0
        plot["fert_yield"] = 0.0
        plot["fert"] = {}
        self._save(data)
        return f"✅ 已取消 {num} 号土地的种植。"

    def _handle_farm_sell(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        crops = self._load_crops()
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        wh = farm["warehouse"].setdefault("crops", {})

        if len(parts) < 2 or not parts[1].strip():
            if not wh:
                return "仓库里没有作物。"
            total = 0
            for nm, cnt in list(wh.items()):
                c = self._find_item(crops, nm)
                total += int(round(int(cnt) * (float(c["crop_price"]) if c else 0.0)))
            wh.clear()
            self._add_coins(data, key, total)
            self._save(data)
            return f"✅ 卖出全部作物，获得 {total} 金币。"
        args = parts[1].split()
        crop_name = args[0]
        if crop_name not in wh:
            return f"仓库里没有「{crop_name}」。"
        c = self._find_item(crops, crop_name)
        price = float(c["crop_price"]) if c else 0.0
        have = int(wh[crop_name])
        if len(args) >= 2:
            try:
                cnt = int(args[1])
            except ValueError:
                return "数量必须是整数。"
            if cnt <= 0:
                return "数量必须为正整数。"
            if cnt > have:
                return f"{crop_name} 只有 {have} 个。"
        else:
            cnt = have
        gain = int(round(cnt * price))
        self._add_coins(data, key, gain)
        if cnt >= have:
            wh.pop(crop_name, None)
        else:
            wh[crop_name] = have - cnt
        self._save(data)
        return f"✅ 卖出 {crop_name} ×{cnt}（单价 {self._fmt_price(price)}），获得 {gain} 金币。"

    def _handle_farm_sell_seed(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        crops = self._load_crops()
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        wh = farm["warehouse"].setdefault("seeds", {})

        if len(parts) < 2 or not parts[1].strip():
            if not wh:
                return "仓库里没有种子。"
            total = 0
            for nm, cnt in list(wh.items()):
                c = self._find_item(crops, nm)
                total += int(round(int(cnt) * (float(c["seed_sell_price"]) if c else 0.0)))
            wh.clear()
            self._add_coins(data, key, total)
            self._save(data)
            return f"✅ 卖出全部种子，获得 {total} 金币。"
        args = parts[1].split()
        seed_name = args[0]
        if seed_name not in wh:
            return f"仓库里没有「{seed_name}」种子。"
        c = self._find_item(crops, seed_name)
        price = float(c["seed_sell_price"]) if c else 0.0
        have = int(wh[seed_name])
        if len(args) >= 2:
            try:
                cnt = int(args[1])
            except ValueError:
                return "数量必须是整数。"
            if cnt <= 0:
                return "数量必须为正整数。"
            if cnt > have:
                return f"{seed_name} 种子只有 {have} 个。"
        else:
            cnt = have
        gain = int(round(cnt * price))
        self._add_coins(data, key, gain)
        if cnt >= have:
            wh.pop(seed_name, None)
        else:
            wh[seed_name] = have - cnt
        self._save(data)
        return f"✅ 卖出 {seed_name} 种子 ×{cnt}（单价 {self._fmt_price(price)}），获得 {gain} 金币。"

    def _handle_farm_seed_shop(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        crops = self._load_crops()
        if not crops:
            return "作物配置为空（请管理员在 WebUI 编辑 作物.txt）。"
        img = self._render_seed_shop(name, self._farm_of(data, key), crops)
        if img is not None:
            return img
        rows = [name, f"农场等级 Lv.{self._farm_of(data, key)['level']}"]
        for c in crops:
            p = round(c["seed_price"] * self._farm_seed_mult(self._farm_of(data, key)), 2)
            rows.append(f"{c['name']} {self._fmt_price(p)} 金币")
        return "\n".join(["种子商店："] + rows)

    def _handle_farm_fert_shop(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        ferts = self._load_fertilizers()
        if not ferts:
            return "肥料配置为空（请管理员在 WebUI 编辑 肥料.txt）。"
        img = self._render_fertilizer_shop(ferts)
        if img is not None:
            return img
        rows = [f"{f['name']} {f['price']}金币 减时{f['time_reduce']:.0f}% 增产{f['yield_add']:.0f}%" for f in ferts]
        return "\n".join(["肥料商店："] + rows)

    def _handle_farm_warehouse(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        crops = self._load_crops()
        ferts = self._load_fertilizers()
        img = self._render_warehouse(self._farm_of(data, key), crops, ferts)
        if img is not None:
            return img
        wh = self._farm_of(data, key).get("warehouse", {})
        lines = ["农场仓库："]
        for k, label in [("crops", "作物"), ("seeds", "种子"), ("fertilizers", "肥料")]:
            lines.append(f"----{label}----")
            for nm, cnt in wh.get(k, {}).items():
                lines.append(f"{nm} ×{cnt}")
        return "\n".join(lines)

    def _handle_farm_plots(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        crops = self._load_crops()
        ferts = self._load_fertilizers()
        img = self._render_plot_status(name, self._farm_of(data, key), crops, ferts)
        if img is not None:
            return img
        return "图片生成失败（缺少 Pillow 或字体），请查看日志。"

    # ================= 左轮手枪：内部逻辑 =================
    def _start_game(self, game: RouletteGame) -> None:
        if game.timer_task and game.timer_task is not asyncio.current_task():
            game.timer_task.cancel()
        game.timer_task = None
        game.status = "playing"
        game.magazines = [False] * ROULETTE_MAGAZINES
        for idx in random.sample(range(ROULETTE_MAGAZINES), game.bullets):
            game.magazines[idx] = True
        game.shot_index = 0
        game.turn_index = 0

    def _start_announcement(self, game: RouletteGame) -> str:
        cur = game.players[0]
        return (f"🔫 左轮手枪游戏开始！\n"
                f"👥 玩家：{game.player_names()}\n"
                f"🔹 子弹：{game.bullets} 发（共 {ROULETTE_MAGAZINES} 个弹匣，随机排列）\n"
                f"💰 赌注：{game.stake} 金币/人\n"
                f"🎯 当前回合：{cur['name']}（发送「开枪」）")

    def _finish_game(self, data: dict, game: RouletteGame, loser_index: int) -> str:
        loser = game.players[loser_index]
        winners = [p for i, p in enumerate(game.players) if i != loser_index]

        loser_key = loser["id"]
        actual_loss = min(game.stake, self._coins_of(data, loser_key))
        self._add_coins(data, loser_key, -actual_loss)

        # 双人局手续费 10%，三人局手续费 5%
        fee_rate = 0.05 if len(game.players) == 3 else ROULETTE_FEE_RATE
        payout_total = int(actual_loss * (1 - fee_rate))
        share = payout_total // len(winners)

        loser_stat = self._ensure_stat(data, loser_key)
        loser_stat["losses"] += 1
        loser_stat["net"] -= actual_loss

        lines = [f"💥 {loser['name']} 开枪：第 {game.shot_index + 1} 个弹匣——砰！有子弹！"]
        lines.append(f"😵 {loser['name']} 判负，扣除 {actual_loss} 金币。")

        winner_names = []
        for w in winners:
            wkey = w["id"]
            self._add_coins(data, wkey, share)
            wstat = self._ensure_stat(data, wkey)
            wstat["wins"] += 1
            wstat["net"] += share
            self._record(data, wkey, "won_from", loser["id"], loser["name"], share)
            self._record(data, loser_key, "lost_to", w["id"], w["name"], share)
            winner_names.append(w["name"])

        if len(winner_names) == 1:
            lines.append(f"🏆 {winner_names[0]} 获得 {share} 金币（已扣除 {int(fee_rate * 100)}% 手续费）。")
        else:
            lines.append(f"🏆 {('、'.join(winner_names))} 各获得 {share} 金币（已扣除 {int(fee_rate * 100)}% 手续费）。")
        return "\n".join(lines)

    async def _proactive_send(self, event, title, text):
        """后台定时器主动发消息：优先渲染成图片，失败回退文本；发送后 RECALL_AFTER 秒撤回"""
        img = self._render_text_image(title, text.splitlines())
        try:
            chain = event.image_result(img[1]) if img is not None else event.plain_result(text)
            mid = await _send_with_mid(event, chain)
            if mid:
                asyncio.create_task(self._recall_later(event, mid))
        except Exception as e:
            logger.error(f"[插件] 主动消息发送失败: {e}")

    def _schedule_timeout(self, game: RouletteGame) -> None:
        async def _timeout():
            try:
                await asyncio.sleep(ROULETTE_JOIN_TIMEOUT)
            except asyncio.CancelledError:
                return
            async with self._lock:
                g = self._games.get(game.group_id)
                if g is not game or g.status != "waiting":
                    return
                if len(g.players) >= ROULETTE_MIN_PLAYERS:
                    self._start_game(g)
                    text = self._start_announcement(g)
                else:
                    self._games.pop(g.group_id, None)
                    text = f"⏰ {ROULETTE_JOIN_TIMEOUT} 秒超时，无人加入，游戏已结束，无事发生。"
            await self._proactive_send(game.event, "左轮手枪", text)

        game.timer_task = asyncio.create_task(_timeout())

    # ================= 左轮手枪：指令 =================
    def _handle_load(self, event: AstrMessageEvent) -> str:
        gid = event.get_group_id()
        if not gid:
            return "左轮手枪游戏只能在群里玩哦～"
        if self._games.get(gid):
            g = self._games[gid]
            return f"本群已有一局左轮手枪游戏进行中（{g.player_names()}），请等它结束。"

        uid = event.get_sender_id()
        name = event.get_sender_name()
        parts = event.message_str.split()
        if len(parts) != 3:
            return f"格式：装弹 <子弹数量 1~{ROULETTE_MAX_BULLETS}> <金币>，例如：装弹 3 100"

        try:
            bullets = int(parts[1])
            stake = int(parts[2])
        except ValueError:
            return "子弹数量和金币必须是整数。格式：装弹 <子弹数量 1~6> <金币>"

        if not (1 <= bullets <= ROULETTE_MAX_BULLETS):
            return f"子弹数量必须在 1~{ROULETTE_MAX_BULLETS} 之间。"
        if stake < 1:
            return "金币必须为正整数。"

        key = uid
        data = self._load()
        if self._coins_of(data, key) < stake:
            return f"你的金币不足（当前 {self._coins_of(data, key)}，需要 {stake}）。"

        game = RouletteGame(gid, uid, name, bullets, stake)
        game.event = event
        self._games[gid] = game
        self._schedule_timeout(game)

        return (f"🔫 {name} 发起了一局左轮手枪游戏！\n"
                f"🔹 子弹：{bullets} 发（共 {ROULETTE_MAGAZINES} 个弹匣，随机排列）\n"
                f"💰 赌注：{stake} 金币/人\n"
                f"👥 发送「加入」参与（至少 {ROULETTE_MIN_PLAYERS} 人，最多 {ROULETTE_MAX_PLAYERS} 人）\n"
                f"⏳ 超时时间：{ROULETTE_JOIN_TIMEOUT} 秒。超时后无人加入则游戏结束，无事发生。")

    def _handle_join(self, event: AstrMessageEvent) -> str:
        gid = event.get_group_id()
        if not gid:
            return "左轮手枪游戏只能在群里玩哦～"
        uid = event.get_sender_id()
        name = event.get_sender_name()
        game = self._games.get(gid)
        if not game or game.status != "waiting":
            return "当前没有正在等待加入的左轮手枪游戏。"
        if any(p["id"] == uid for p in game.players):
            return f"{name}，你已经在这局游戏里了。"
        if len(game.players) >= ROULETTE_MAX_PLAYERS:
            return "本局人数已满。"

        key = uid
        data = self._load()
        if self._coins_of(data, key) < game.stake:
            return f"{name} 金币不足，无法加入（需要 {game.stake} 金币）。"

        game.players.append({"id": uid, "name": name})
        if len(game.players) >= ROULETTE_MAX_PLAYERS:
            self._start_game(game)
            return self._start_announcement(game)

        return (f"✅ {name} 加入了游戏！\n"
                f"👥 当前玩家（{len(game.players)}/{ROULETTE_MAX_PLAYERS}）：{game.player_names()}\n"
                f"发起人可发送「开始」立即开始，或等待更多玩家加入。")

    def _handle_start(self, event: AstrMessageEvent) -> str:
        gid = event.get_group_id()
        uid = event.get_sender_id()
        game = self._games.get(gid)
        if not game or game.status != "waiting":
            return "当前没有待开始的左轮手枪游戏。"
        if uid != game.starter_id:
            return "只有发起人可以开始游戏。"
        if len(game.players) < ROULETTE_MIN_PLAYERS:
            return f"至少需要 {ROULETTE_MIN_PLAYERS} 名玩家才能开始。"
        self._start_game(game)
        return self._start_announcement(game)

    def _handle_shoot(self, event: AstrMessageEvent) -> str:
        gid = event.get_group_id()
        uid = event.get_sender_id()
        name = event.get_sender_name()
        game = self._games.get(gid)
        if not game:
            return "当前没有进行中的左轮手枪游戏。"
        if game.status != "playing":
            return "游戏还没开始，等待玩家加入或发送「开始」。"

        cur = game.players[game.turn_index]
        if cur["id"] != uid:
            return f"还没轮到你开枪，当前是 {cur['name']} 的回合。"

        shot_num = game.shot_index + 1
        if not game.magazines[game.shot_index]:
            game.shot_index += 1
            game.turn_index = (game.turn_index + 1) % len(game.players)
            nxt = game.players[game.turn_index]
            return (f"🔫 {name} 开枪：第 {shot_num} 个弹匣——咔嚓，是空膛！\n"
                    f"🎯 轮到 {nxt['name']}（发送「开枪」）")

        data = self._load()
        text = self._finish_game(data, game, game.turn_index)
        self._save(data)
        self._games.pop(gid, None)
        return text

    def _handle_stats(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        stat = data.get("roulette", {}).get(key)

        if not stat or (stat.get("wins", 0) + stat.get("losses", 0)) == 0:
            return (f"{name} 还没有左轮手枪战绩，\n"
                    f"发送「装弹 <子弹数量 1~6> <金币>」开局吧～")

        wins = stat.get("wins", 0)
        losses = stat.get("losses", 0)
        total = wins + losses
        rate = wins / total * 100
        net = stat.get("net", 0)
        net_str = f"+{net}" if net >= 0 else str(net)

        lines = [
            f"📊 {name} 的左轮手枪战绩：",
            f"🎮 局数：{total}（胜 {wins} / 负 {losses}）",
            f"📈 胜率：{rate:.1f}%",
            f"💰 净收益：{net_str} 金币",
        ]
        lost_to = stat.get("lost_to", {})
        won_from = stat.get("won_from", {})
        if lost_to:
            worst = max(lost_to.items(), key=lambda kv: kv[1]["amount"])
            lines.append(f"👊 拿走你最多金币的人：{worst[1]['name']}（{worst[1]['amount']} 金币）")
        else:
            lines.append("👊 拿走你最多金币的人：无")
        if won_from:
            best = max(won_from.items(), key=lambda kv: kv[1]["amount"])
            lines.append(f"💸 你拿走最多金币的人：{best[1]['name']}（{best[1]['amount']} 金币）")
        else:
            lines.append("💸 你拿走最多金币的人：无")
        return "\n".join(lines)
