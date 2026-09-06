# -*- coding: utf-8 -*-
# 基础模块：常量定义、共享工具函数与 CoreMixin（数据存取 / 通用方法）。
# 由 main.py 与各功能模块共同导入（`from .base import *` 取常量与工具）。

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
import random
import re
import secrets
import shutil
import socket
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

# 消息链组件（文本+图片组合回复用）
try:
    from astrbot.core.message.components import Plain as _Plain
    from astrbot.core.message.components import Image as _Image
    from astrbot.core.message.message_event_result import MessageChain as _MessageChain
except Exception:
    _Plain = None
    _Image = None
    _MessageChain = None


# ================= 局域网管理页面开放（1.7.9） =================
LAN_DATA_KEY = "lan"                # data.json 中的存储键
LAN_COOKIE = "astrbot_signin_lan"   # 局域网访客会话 Cookie 名
LAN_SESSION_HOURS = 12              # 输入正确密码后的免密会话时长（小时）
LAN_MAX_RECORDS = 500               # 访问记录上限（超出丢弃最旧）
# 局域网访问默认不建议开放；开启后同一局域网设备需输入哈希校验的管理密码才能访问 WebUI
# 本地访问（loopback）免密；修改密码/开关/查看记录/管理黑名单仅限本地服务器
_LAN_SALT_LEN = 16


def _lan_hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """PBKDF2-SHA256 哈希密码。返回 (salt_hex, hash_hex)。不存储明文。"""
    if salt is None:
        salt = secrets.token_hex(_LAN_SALT_LEN)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), 120_000)
    return salt, digest.hex()


def _lan_verify_password(stored: str | None, password: str) -> bool:
    """校验密码：stored 形如 'salt$hash'，否则直接失败。"""
    if not isinstance(stored, str) or not isinstance(password, str):
        return False
    if "$" not in stored:
        return False
    salt, want = stored.split("$", 1)
    try:
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  bytes.fromhex(salt), 120_000).hex()
    except Exception:
        return False
    return hmac.compare_digest(got, want)


def _is_loopback(host: str | None) -> bool:
    """判断 client_host 是否为本地回环地址（x 为空/解析失败按非本地处理视为 False）。"""
    if not host:
        return False
    host = host.strip()
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    try:
        addr = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped.is_loopback
    return False


def _lan_ip_in_blacklist(ip: str | None, blacklist: list) -> bool:
    """判断 IP 是否命中黑名单（支持精确 IP 或 CIDR）。"""
    if not ip or not blacklist:
        return False
    ip = ip.split("%")[0]
    for entry in blacklist:
        if not isinstance(entry, str) or not entry.strip():
            continue
        entry = entry.strip()
        if entry == ip:
            return True
        try:
            if "/" in entry and ipaddress.ip_address(ip) in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def _enumerate_lan_ipv4() -> list[str]:
    """枚举本机局域网 IPv4 地址（用于「管理网址」指令返回访问地址）。"""
    ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not _is_loopback(ip) and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # 兜底：通过 UDP 连接探测拿本机出口地址（不会真正发包）
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                if ip and not _is_loopback(ip):
                    ips.append(ip)
            finally:
                s.close()
        except Exception:
            pass
    return ips


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
EXP_BALL_NAME = "农场经验球"  # 农场经验球道具名
ITEM_TO_COIN = 10           # 未开通对应功能时，道具自动转金币的单价
PILL_DROP_CHANCE = 0.5      # 签到掉落属性丸概率（旧）
PILL_DROP_MIN = 1           # 属性丸最少掉落数量
PILL_DROP_MAX = 5           # 属性丸最多掉落数量
# 签到额外奖励池（互斥）：40% 无 / 30% 属性丸 / 30% 农场经验球
SIGNIN_NO_REWARD_CHANCE = 0.40
SIGNIN_PILL_CHANCE = 0.30
SIGNIN_BALL_CHANCE = 0.30
# 道具每日使用上限（WebUI 可改）
PILL_DAILY_LIMIT = 3
EXP_BALL_DAILY_LIMIT = 3
# 属性丸效果：随机提升的属性种类数量 / 提升范围（WebUI 可改）
PILL_ATTR_COUNT = 2
PILL_BOOST_MIN = 5.0
PILL_BOOST_MAX = 20.0
# 农场经验球效果：获得升级所需总经验的百分比范围（WebUI 可改）
EXP_BALL_MIN_PCT = 0.05
EXP_BALL_MAX_PCT = 0.20
# 农场土地状态图片：一行显示的卡片数量（WebUI 可改）
FARM_PLOT_COLS = 4
# 宠物商店：每行卡片数 / 价格与分割线-底边的距离 N（WebUI 可改）
SHOP_CARD_COLS = 3
SHOP_PRICE_PAD = 4
# 背包：每行卡片数（WebUI 可改，范围 3-7）
BAG_CARD_COLS = 5
# 农场商店：每行卡片数（WebUI 可改）
FARM_SHOP_COLS = 4
# 农场商店：默认展示的 可购种子 / 不可购灰卡 个数（WebUI「设置 → 商店」可改）
FARM_SHOP_SHOW_BUY = 9
FARM_SHOP_SHOW_LOCKED = 3
# 打工/玩耍列表（策略一）：可进行项目中等级要求最高的 / 不可进行项目中等级要求最低的 显示个数（WebUI「设置 → 宠物」可改）
WORK_SHOW_DOABLE = 6
WORK_SHOW_LOCKED = 2
PLAY_SHOW_DOABLE = 6
PLAY_SHOW_LOCKED = 2
# 宠物属性上限：不同健康值范围内的 饱食/口渴/体力/心情 上限（WebUI「设置 → 宠物 → 属性」可改）
# 格式：健康值下限=饱食,口渴,体力,心情，竖线分隔，从高到低匹配
PET_ATTR_MAX_RANGES = "140=200,200,200,120|80=120,120,120,100|40=100,100,100,100|0=80,80,60,80"
# 宠物每日结算·超扣转健康抵扣（2.0.3）：结算扣减的属性若超出结算前属性值，
# 超出部分按「PET_SETTLE_HEALTH_EXCHANGE 属性点 = 1 健康」用健康值抵扣（WebUI「设置 → 签到 → 宠物结算范围」可改）
PET_SETTLE_HEALTH_EXCHANGE = 4

# 宠物自动购买（2.0.4，默认关闭）：主人用「自动购买 开/关」开启。
# 触发判定：饱食/口渴/心情/健康 任一属性进入第 3/4 档（饱食/口渴/心情 < 二档下限、健康 < 40）自动触发；
# 按 饱食→口渴→心情→健康 顺序补满，且道具数量最少化（如缺口 120：优先 3 个 +40 而不是 12 个 +10）；
# 优先消耗仓库已有道具（免费），不足再购买（实时价 × AUTO_FEED_PRICE_MULT，计入打工基准金币 work_base）；
# 购买/使用记录带数量标记（🛒/📦 ×N）。AUTO_PURCHASE_COOLDOWN_MIN=触发后金币不足未能补满时的冷却分钟数（防刷屏）。
AUTO_FEED_ENABLED = False
AUTO_FEED_PRICE_MULT = 1.2
AUTO_FEED_LOG_MAX = 30
AUTO_PURCHASE_COOLDOWN_MIN = 10

# 自动打工（2.0.4，默认总开关开启）：开启自动购买的用户自动开启自动打工（也可「自动打工 开/关」单独控制，
# 未开启自动购买不允许开启自动打工）。自动购买消耗的金币 = 打工基准金币（work_base）；
# 自动选择「报酬最接近打工基准金币」的可行打工项目，默认只给金币不给经验；完成后 新基准 = |基准 - 报酬|（溢出计入基准）；
# 基准金币 ≤ AUTO_WORK_PAUSE_BASE（100）暂停，直到下次自动购买使基准 > 100 自动恢复；
# 独立计时器：冷却结束 + AUTO_WORK_DELAY_MIN 分钟后安排下一次自动打工，循环往复。
# 2.1.0：AUTO_WORK_EXP_ENABLED=自动打工是否产生经验收益（默认关闭）；开启后经验 = 该打工项目经验 × AUTO_WORK_EXP_MULT
#（倍率 0.1~1 倍，可在 WebUI「设置 → 宠物 → 自动打工」调整，默认 0.5 = 手动打工经验的一半）。
AUTO_WORK_ENABLED = True
AUTO_WORK_DELAY_MIN = 10
AUTO_WORK_PAUSE_BASE = 100
AUTO_WORK_LOG_MAX = 30
AUTO_WORK_EXP_ENABLED = False
AUTO_WORK_EXP_MULT = 0.5

# 2.1.0：固定刷新时间 —— 每天 DAILY_SETTLE_HOUR（默认 0 点）固定开始结算插件数据
# （全部宠物每日结算 + 自动购买每日结算触发），每天 BANK_SETTLE_HOUR（默认 4 点）结算银行存款数据
# （解锁到期存单并发放利息）。由固定结算循环（_daily_settle_loop）执行，可在 WebUI「设置 → 固定结算」调整。
DAILY_SETTLE_HOUR = 0
BANK_SETTLE_HOUR = 4
DAILY_SETTLE_LOOP_INTERVAL = 60  # 固定结算循环巡检间隔（秒）

# 缺货自动购买（2.0.3，默认关闭）：使用道具时仓库不足则自动购买足额道具并使用（倍率可调）
AUTO_BUY_SHORT_ENABLED = False
AUTO_BUY_SHORT_MULT = 1.0

# 商店价格浮动（2.0.4 更改，默认关闭）：宠物商店价格固定偶数点刷新（0/2/4/…/22 整点），
# 同一 2 小时窗口内价格稳定（按窗口种子固定随机）；特价时段（10/12/18/0 时窗口）随机选取
# SHOP_PRICE_DISCOUNT_MIN~MAX 种商品打 二~八折（价格 = 原价 × 倍率 LO~HI），其余时段全部商品回原价；
# 购买按实时价结算；价格变动被记录，可在 WebUI「运行记录 → 商店价格」查看。
# 种子折扣（2.0.3，默认关闭）：种子每日有概率让 1~3 款打八折（肥料不受影响，仍独立受 SEED_DISCOUNT_* 控制）
SHOP_PRICE_FLOAT_ENABLED = False
SHOP_PRICE_REFRESH_HOURS = (0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22)
SHOP_PRICE_SPECIAL_HOURS = (10, 12, 18, 0)
SHOP_PRICE_DISCOUNT_MIN = 2
SHOP_PRICE_DISCOUNT_MAX = 5
SHOP_PRICE_DISCOUNT_LO = 0.2
SHOP_PRICE_DISCOUNT_HI = 0.8
SHOP_PRICE_RECORD_MAX = 60
SEED_DISCOUNT_ENABLED = False
SEED_DISCOUNT_CHANCE = 0.5
SEED_DISCOUNT_MIN = 1
SEED_DISCOUNT_MAX = 3
SEED_DISCOUNT_PCT = 0.8
MONEY_EVENT_CHANCE = 0.01   # 玩耍捡到钱概率（1%）
MONEY_EVENT_GAIN = 100      # 捡到钱的金币
MONEY_EVENT_MAX_PER_DAY = 2  # 每个周期最多触发次数
WEAK_HEAL_COST = 500        # 治疗虚弱宠物的金币消耗（连续两天结算健康为 0 会进入虚弱）
# 虚弱状态下被锁定的宠物功能指令（重新激活 = 发送「治疗宠物」花 WEAK_HEAL_COST 金币）
_PET_WEAK_LOCKED_HEADS = frozenset(("打工", "玩耍", "购买", "使用", "看家"))

# ============ 同义口令（WebUI「同义口令」可编辑，别名效果与标准指令相同） ============
# 全部标准指令口令（供 WebUI 下拉与保存校验；别名必须指向其中之一）
CMD_HEADS = frozenset((
    "签到", "我的签到", "签到帮助", "游戏帮助", "帮助", "宠物帮助", "农场帮助", "左轮手枪帮助",
    "修改昵称", "自动购买", "自动打工", "自动化", "自动化帮助", "结算日志", "装弹", "加入", "开始", "开枪", "我的战绩",
    "解锁宠物", "宠物", "更改宠物名字", "治疗宠物", "打工", "玩耍",
    "商店", "购买", "使用", "背包", "看家",
    "农场", "解锁农场", "购买土地", "土地升级", "种子商店", "农场商店", "购买种子",
    "肥料商店", "购买肥料", "种植", "种地", "施肥", "收割", "收获", "取消种植", "土地状态", "我的农场", "农场仓库",
    "售卖", "农场帮助", "农场经验球", "偷菜", "自动偷菜",
    "存款", "取款", "银行统计", "借款", "还款", "我的贷款", "我的征信",
    "查询流水", "流水查询", "消费记录", "金币红包", "开红包", "抢红包", "开",
    "金币排行", "宠物排行", "农场排行", "活动", "活动中心",
    "查看后台配置", "保存后台配置", "导出数据", "导入数据", "重置数据",
    "管理网址",
))
# 默认同义词（首次加载或 WebUI 删除全部后重置为空）；用户可随时增删改
DEFAULT_ALIAS_CMDS = {
    "打卡": "签到",
    "我的宠物": "宠物",
    "宠物状态": "宠物",
    "工作": "打工",
    "钱袋": "背包",
    "我的背包": "背包",
    "存金币": "存款",
    "取金币": "取款",
    "今日流水": "查询流水",
    "发红包": "金币红包",
    "财富榜": "金币排行",
    "宠物榜": "宠物排行",
    "农场榜": "农场排行",
    "治疗": "治疗宠物",
    "赌博": "装弹",
    "查看帮助": "游戏帮助",
}

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


def _farm_grades(raw=None):
    """解析 FARM_GRADES 参数（WebUI「设置 → 农场 → 土地」表格编辑）：
    格式：等级名=产量加成%,时间减免%（竖线分隔，如 贫瘠土地=0,0|红土地=100,0|普通土地=200,10|肥沃土地=250,20|黑土地=400,35）。
    返回 [(名称, 产量加成小数, 时间减免小数)]；解析失败回退默认。
    raw 为 None 时读取本模块全局（未同步时即默认常量）；调用方（farm.py）应传入其模块中已同步的参数值。"""
    if raw is None:
        raw = globals().get("FARM_GRADES", "")
    if not isinstance(raw, str) or not str(raw).strip():
        return list(FARM_GRADES)
    out = []
    for seg in str(raw).replace("；", "|").replace(";", "|").split("|"):
        seg = seg.strip()
        if not seg or "=" not in seg:
            continue
        name, body = seg.split("=", 1)
        nums = []
        for x in body.replace("，", ",").split(","):
            try:
                nums.append(float(x.strip()))
            except (TypeError, ValueError):
                nums = []
                break
        if len(nums) == 2:
            # UI 存百分比（如 100 = 100%），内部换算为小数
            out.append((name.strip(), nums[0] / 100.0, nums[1] / 100.0))
    return out or list(FARM_GRADES)

# 2.0.0：作物等级（按贫瘠土地上的成熟分钟数划分）与各等级成长阶段数（WebUI「设置 → 农场 → 成长阶段」可改）
# CROP_LEVEL_RANGES = 各等级上限（分钟）：0~240=一级 / 241~480=二级 / 481~720=三级 / 721~1440=四级
CROP_LEVEL_RANGES = (0, 240, 480, 720, 1440)
# CROP_LEVEL_STAGES = 一~四级作物在总生长周期内划分的成长阶段数
CROP_LEVEL_STAGES = (4, 5, 5, 6)
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
    "查询流水": "金币账单",
    "流水查询": "金币账单",
    "消费记录": "金币账单",
    "金币红包": "金币红包",
    "开红包": "金币红包",
    "开": "金币红包",
    "抢红包": "金币红包",
    "活动": "活动中心",
    "自动化": "自动化状态",
    "自动化帮助": "自动化帮助",
}

# 插件消息发送后多少秒撤回（防刷屏，0 = 不撤回）
RECALL_AFTER = 15
# 全局撤回总开关：False 时所有消息都不撤回（WebUI 运行参数「撤回设置→通用」可改，默认开启）
RECALL_ENABLED = True

# 调试模式口令（管理员在对话框输入后解锁 WebUI 调试按钮）
DEBUG_PASSWORD = "88224646"

# 金币账单最多展示条数
LEDGER_SHOW = 30

# 自定义昵称（2.0.3）：有效期 90 天（秒），优先级高于获取的昵称
CUSTOM_NAME_TTL = 90 * 86400
CUSTOM_NAME_MAX_LEN = 50

# 金币红包：每人每天最多发送次数、有效期（秒）
REDPACKET_DAILY_LIMIT = 4
REDPACKET_TTL = 600

# 红包雨活动：单轮总金额 / 红包个数 / 开启时间（逗号分隔的小时）/ 有效期（小时）
RAIN_AMOUNT = 1000
RAIN_COUNT = 10
RAIN_TIMES = "8,12,16,20"
RAIN_HOURS = 1

# 自动偷菜（1.7.6）：每日成功次数上限 / 每次随机抽取目标数（目标数固定 4）
AUTO_STEAL_DAILY_LIMIT = 5
AUTO_STEAL_TARGETS = 4

# ============ 排行榜（1.7.5） ============
RANK_DISPLAY = 20                        # 每个排行榜最多展示的名次数（前 N 名）
RANK_HIGHLIGHT_COLOR = "#92D050"         # 查询人自己在榜上的高亮颜色（绿色）
RANK_TEXT_COLOR = "#000000"              # 普通行（本群非自己）文字颜色
RANK_MASKED_COLOR = "#7F7F7F"            # 脱敏行（非本群用户）文字颜色
RANK_SEP_COLOR = "#D9D9D9"               # 行间分割线颜色
RANK_IMAGE_SCALE = 2.5                   # 排行榜图片宽度倍数（2.5 = 内容宽度的 250%）
RANK_BAR_LIGHTEN = 0.2                   # 进度条颜色比文字颜色浅的比例（0.2 = 浅 20%）
RANK_NAME_MAX_CHARS = 6                  # 排行榜名字显示最大字符数（超出部分用 ... 代替）
RANK_BAR_GAP_LEFT_MULT = 2.0             # 进度条左侧留白倍数（基准 12px，2 = 200%）
RANK_BAR_GAP_RIGHT_MULT = 3.0            # 进度条右侧留白倍数（基准 12px，3 = 300%）
RANK_ROW_SEP_PCT = 0.03                  # 行间分割线高度 = 文字行高的比例（0.03 = 原先 10% 的 30%）
GROUP_MEMBER_TTL_HOURS = 48              # 本群成员标记有效期（小时）：触发插件功能后维持
RANK_KINDS = {"金币排行": "coins", "宠物排行": "pet", "农场排行": "farm"}  # 排行榜指令 → 榜单类型
# 金币排行：积分 = 持有金币 × 金币权重 + 银行存款 × 存款权重
RANK_COIN_COIN_W = 1.0
RANK_COIN_BANK_W = 1.0
# 宠物排行：积分 = 经验 × 权重 + 健康度 × 权重 + （饱食+口渴+体力+心情）× 权重
RANK_PET_EXP_W = 2.0
RANK_PET_HEALTH_W = 1.5
RANK_PET_ATTR_W = 0.5
# 农场排行：积分 = 经验 × 权重 + （土地数-2）× 权重 + 土地等级分合计 × 权重
RANK_FARM_EXP_W = 2.0
RANK_FARM_PLOT_W = 400.0
RANK_FARM_GRADE_W = 0.5
# 土地等级分：各土地等级的累计升级花费（贫瘠→红→普通→肥沃→黑；WebUI 可改）
RANK_PLOT_SCORES = (0, 1000, 2500, 4500, 7500)
# =============================================

# ============ WebUI 运行参数（协议：GET/POST /astrbot_plugin_signin/params） ============
# 每项：key=模块常量名（保存后 globals 更新、立即生效），attr=同时同步的实例属性名（可选）
# type 支持 int / float / bool / string；min/max 为校验范围；group 为一级折叠分组，subgroup 为二级折叠分组
RUNTIME_PARAMS = [
    # ---- 撤回设置 ----
    {"key": "RECALL_ENABLED", "label": "全局撤回开关", "type": "bool", "group": "撤回设置", "subgroup": "通用",
     "desc": "全局总开关：开启 = 启用撤回功能（各指令按下方开关决定是否撤回）；关闭 = 所有消息都不撤回", "default": True},
    {"key": "RECALL_AFTER", "label": "消息撤回秒数", "type": "int", "group": "撤回设置", "subgroup": "通用",
     "desc": "插件消息发送后多少秒撤回（0 = 不撤回）", "default": 15, "min": 0, "max": 600},
    {"key": "RECALL_ACTIVITY", "label": "「活动」指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「活动」指令的图片回复正常撤回，不勾选则不撤回", "default": False},
    {"key": "RECALL_SHOP", "label": "「商店」指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「商店」指令（宠物商店）的图片回复正常撤回，不勾选则不撤回", "default": False},
    {"key": "RECALL_SEED_SHOP", "label": "「种子商店」指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「种子商店」指令的图片回复正常撤回，不勾选则不撤回", "default": False},
    {"key": "RECALL_FERT_SHOP", "label": "「肥料商店」指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「肥料商店」指令的图片回复正常撤回，不勾选则不撤回", "default": False},
    {"key": "RECALL_FARM_SHOP", "label": "「农场商店」指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「农场商店」指令的图片回复正常撤回，不勾选则不撤回（默认不撤回）", "default": False},
    {"key": "RECALL_HELP", "label": "帮助指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「签到帮助/宠物帮助/农场帮助/左轮手枪帮助/游戏帮助」的图片回复正常撤回，不勾选则不撤回", "default": False},
    # ---- 签到 ----
    {"key": "MIN_COINS", "label": "签到最少金币", "type": "int", "group": "签到", "subgroup": "金币",
     "desc": "每日签到随机获得金币的下限", "default": 30, "min": 1, "max": 10000, "attr": "min_coins"},
    {"key": "MAX_COINS", "label": "签到最多金币", "type": "int", "group": "签到", "subgroup": "金币",
     "desc": "每日签到随机获得金币的上限", "default": 300, "min": 1, "max": 100000, "attr": "max_coins"},
    {"key": "SIGNIN_NO_REWARD_CHANCE", "label": "奖池·无奖品概率", "type": "float", "group": "签到", "subgroup": "奖池概率",
     "desc": "签到额外奖励抽中「什么都不送」的概率（0.4 = 40%；三项总和超过 1 时自动按比例归一）", "default": 0.4, "min": 0, "max": 1, "attr": "signin_no_reward_chance"},
    {"key": "SIGNIN_PILL_CHANCE", "label": "奖池·属性丸概率", "type": "float", "group": "签到", "subgroup": "奖池概率",
     "desc": "签到额外奖励抽中「属性丸」的概率（0.3 = 30%；三项总和超过 1 时自动按比例归一）", "default": 0.3, "min": 0, "max": 1, "attr": "signin_pill_chance"},
    {"key": "SIGNIN_BALL_CHANCE", "label": "奖池·农场经验球概率", "type": "float", "group": "签到", "subgroup": "奖池概率",
     "desc": "签到额外奖励抽中「农场经验球」的概率（0.3 = 30%；三项总和超过 1 时自动按比例归一）", "default": 0.3, "min": 0, "max": 1, "attr": "signin_ball_chance"},
    # ---- 宠物 ----
    {"key": "PET_UNLOCK_COST", "label": "解锁宠物价格", "type": "int", "group": "宠物", "subgroup": "解锁",
     "desc": "领养宠物所需金币", "default": 1000, "min": 0, "max": 1000000, "attr": "pet_unlock_cost"},
    {"key": "PET_SIGNIN_EXP_MIN", "label": "签到宠物最小经验", "type": "float", "group": "宠物", "subgroup": "宠物经验",
     "desc": "每日签到宠物获得的最小经验", "default": 10.0, "min": 0, "max": 1000, "attr": "pet_signin_exp_min"},
    {"key": "PET_SIGNIN_EXP_MAX", "label": "签到宠物最大经验", "type": "float", "group": "宠物", "subgroup": "宠物经验",
     "desc": "每日签到宠物获得的最大经验", "default": 60.0, "min": 0, "max": 5000, "attr": "pet_signin_exp_max"},
    {"key": "PILL_DROP_CHANCE", "label": "属性丸掉落概率", "type": "float", "group": "宠物", "subgroup": "属性丸",
     "desc": "签到额外奖励中抽中属性丸的概率（30% 即 0.3）", "default": 0.3, "min": 0, "max": 1, "attr": "pill_drop_chance"},
    {"key": "PILL_DROP_MIN", "label": "属性丸最少掉落", "type": "int", "group": "宠物", "subgroup": "属性丸",
     "desc": "签到奖励中属性丸的最少数量", "default": 1, "min": 1, "max": 99, "attr": "pill_drop_min"},
    {"key": "PILL_DROP_MAX", "label": "属性丸最多掉落", "type": "int", "group": "宠物", "subgroup": "属性丸",
     "desc": "签到奖励中属性丸的最多数量", "default": 5, "min": 1, "max": 99, "attr": "pill_drop_max"},
    {"key": "PILL_ATTR_COUNT", "label": "属性丸提升属性数", "type": "int", "group": "宠物", "subgroup": "属性丸",
     "desc": "使用属性丸时随机提升的属性种类数量", "default": 2, "min": 1, "max": 5, "attr": "pill_attr_count"},
    {"key": "PILL_BOOST_MIN", "label": "属性丸提升下限", "type": "float", "group": "宠物", "subgroup": "属性丸",
     "desc": "属性丸单个属性提升的最小值", "default": 5.0, "min": 0, "max": 100, "attr": "pill_boost_min"},
    {"key": "PILL_BOOST_MAX", "label": "属性丸提升上限", "type": "float", "group": "宠物", "subgroup": "属性丸",
     "desc": "属性丸单个属性提升的最大值", "default": 20.0, "min": 0, "max": 500, "attr": "pill_boost_max"},
    {"key": "PILL_DAILY_LIMIT", "label": "属性丸每日使用上限", "type": "int", "group": "宠物", "subgroup": "属性丸",
     "desc": "属性丸每天最多可使用次数", "default": 3, "min": 1, "max": 50, "attr": "pill_daily_limit"},
    {"key": "MONEY_EVENT_CHANCE", "label": "玩耍捡钱概率", "type": "float", "group": "宠物", "subgroup": "玩耍",
     "desc": "玩耍触发「捡到钱了」的概率（0.01 = 1%）", "default": 0.01, "min": 0, "max": 1, "attr": "money_event_chance"},
    {"key": "MONEY_EVENT_GAIN", "label": "玩耍捡钱金额", "type": "int", "group": "宠物", "subgroup": "玩耍",
     "desc": "触发「捡到钱了」获得的金币", "default": 100, "min": 0, "max": 100000, "attr": "money_event_gain"},
    {"key": "MONEY_EVENT_MAX_PER_DAY", "label": "玩耍捡钱每日上限", "type": "int", "group": "宠物", "subgroup": "玩耍",
     "desc": "「捡到钱了」每个周期最多触发次数", "default": 2, "min": 1, "max": 100, "attr": "money_event_max_per_day"},
    {"key": "WORK_CARD_COLS", "label": "打工列表每行卡片数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「打工」列表图片一行展示的卡片数量", "default": 2, "min": 1, "max": 4},
    {"key": "PLAY_CARD_COLS", "label": "玩耍列表每行卡片数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「玩耍」列表图片一行展示的卡片数量", "default": 2, "min": 1, "max": 4},
    {"key": "WORK_PLAY_CARD_WIDTH", "label": "打工玩耍卡片宽度", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「打工/玩耍」列表每张内容卡片的宽度（默认 522 = 原 290 的 180%）", "default": 522, "min": 290, "max": 800},
    {"key": "WORK_SHOW_DOABLE", "label": "打工列表·可进行显示个数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「打工」（不带参数）策略一：可进行项目中等级要求最高的显示数量；「打工 全部」显示全部", "default": 6, "min": 1, "max": 50},
    {"key": "WORK_SHOW_LOCKED", "label": "打工列表·不可进行显示个数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「打工」（不带参数）策略一：不可进行项目中等级要求最低的显示数量", "default": 2, "min": 0, "max": 50},
    {"key": "PLAY_SHOW_DOABLE", "label": "玩耍列表·可进行显示个数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「玩耍」（不带参数）策略一：可进行项目中等级要求最高的显示数量；「玩耍 全部」显示全部", "default": 6, "min": 1, "max": 50},
    {"key": "PLAY_SHOW_LOCKED", "label": "玩耍列表·不可进行显示个数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「玩耍」（不带参数）策略一：不可进行项目中等级要求最低的显示数量", "default": 2, "min": 0, "max": 50},
    {"key": "PET_ATTR_MAX_RANGES", "label": "属性上限·健康值范围", "type": "string", "group": "宠物", "subgroup": "属性",
     "desc": "不同健康值范围内 饱食/口渴/体力/心情 的属性上限；格式：健康值下限=饱食,口渴,体力,心情，竖线分隔、按健康值从高到低匹配（如 140=200,200,200,120|80=120,120,120,100|40=100,100,100,100|0=80,80,60,80）",
     "default": "140=200,200,200,120|80=120,120,120,100|40=100,100,100,100|0=80,80,60,80"},
    # ---- 商店（独立折叠分组） ----
    {"key": "SHOP_CARD_COLS", "label": "宠物商店每行卡片数", "type": "int", "group": "商店", "subgroup": "宠物商店",
     "desc": "宠物商店（商店指令）一行展示的卡片数量", "default": 3, "min": 2, "max": 6},
    {"key": "FARM_SHOP_COLS", "label": "农场商店每行卡片数", "type": "int", "group": "商店", "subgroup": "农场商店",
     "desc": "农场商店一行展示的卡片数量", "default": 4, "min": 2, "max": 6},
    {"key": "FARM_SHOP_SHOW_BUY", "label": "农场商店·可购种子显示个数", "type": "int", "group": "商店", "subgroup": "农场商店",
     "desc": "「农场商店」（不带参数）默认展示的可以购买（等级足够）的种子数量", "default": 9, "min": 1, "max": 99},
    {"key": "FARM_SHOP_SHOW_LOCKED", "label": "农场商店·不可购灰卡个数", "type": "int", "group": "商店", "subgroup": "农场商店",
     "desc": "「农场商店」（不带参数）默认展示的不能购买（等级不足）的种子灰卡数量；「农场商店 全部」显示全部商品", "default": 3, "min": 0, "max": 99},
    {"key": "SHOP_PRICE_PAD", "label": "商店价格底边距（像素）", "type": "int", "group": "商店", "subgroup": "通用",
     "desc": "商店卡片价格与卡片底部/分割线的距离 N", "default": 4, "min": 0, "max": 30},
    # ---- 商店（补充）2.0.3：缺货自动购买；2.0.4：价格刷新改为固定时段折扣 ----
    {"key": "AUTO_BUY_SHORT_ENABLED", "label": "缺货自动购买开关", "type": "bool", "group": "商店", "subgroup": "通用",
     "desc": "开启后：使用道具时仓库不足则自动购买足额道具并使用（默认关闭）", "default": False},
    {"key": "AUTO_BUY_SHORT_MULT", "label": "缺货自动购买价格倍率", "type": "float", "group": "商店", "subgroup": "通用",
     "desc": "缺货自动购买道具的价格倍率（1.0 = 原价）", "default": 1.0, "min": 0.1, "max": 10},
    {"key": "SHOP_PRICE_FLOAT_ENABLED", "label": "宠物商店价格浮动开关", "type": "bool", "group": "商店", "subgroup": "宠物商店",
     "desc": "开启后：宠物商店价格固定偶数点（0/2/4/…/22 整点）刷新、同窗口（2 小时）内稳定；特价时段（10/12/18/0 时窗口）随机选取 MIN~MAX 种商品按 LO~HI 倍率打折，其余时段全部回原价（默认关闭）", "default": False},
    {"key": "SHOP_PRICE_DISCOUNT_MIN", "label": "特价时段·最少折扣商品数", "type": "int", "group": "商店", "subgroup": "宠物商店",
     "desc": "特价时段（10/12/18/0 时窗口）随机选取的打折商品最少数量", "default": 2, "min": 0, "max": 20},
    {"key": "SHOP_PRICE_DISCOUNT_MAX", "label": "特价时段·最多折扣商品数", "type": "int", "group": "商店", "subgroup": "宠物商店",
     "desc": "特价时段随机选取的打折商品最多数量", "default": 5, "min": 0, "max": 20},
    {"key": "SHOP_PRICE_DISCOUNT_LO", "label": "折扣倍率下限（二折）", "type": "float", "group": "商店", "subgroup": "宠物商店",
     "desc": "折扣价格 = 原价 × [下限, 上限]（0.2 = 打二折）", "default": 0.2, "min": 0.05, "max": 1},
    {"key": "SHOP_PRICE_DISCOUNT_HI", "label": "折扣倍率上限（八折）", "type": "float", "group": "商店", "subgroup": "宠物商店",
     "desc": "折扣价格 = 原价 × [下限, 上限]（0.8 = 打八折）", "default": 0.8, "min": 0.05, "max": 1},
    {"key": "SHOP_PRICE_RECORD_MAX", "label": "价格变动·保留记录条数", "type": "int", "group": "商店", "subgroup": "宠物商店",
     "desc": "WebUI「运行记录 → 商店价格」保留的最近价格变动记录条数（每次刷新窗口记一条）", "default": 60, "min": 1, "max": 500},
    {"key": "SEED_DISCOUNT_ENABLED", "label": "种子每日折扣开关", "type": "bool", "group": "商店", "subgroup": "农场商店",
     "desc": "开启后：每天有概率让 1~3 款种子打八折（肥料不受影响；默认关闭）", "default": False},
    {"key": "SEED_DISCOUNT_CHANCE", "label": "种子折扣发生概率", "type": "float", "group": "商店", "subgroup": "农场商店",
     "desc": "每天触发种子折扣的概率（0.5 = 50%）", "default": 0.5, "min": 0, "max": 1},
    {"key": "SEED_DISCOUNT_MIN", "label": "种子折扣最少款数", "type": "int", "group": "商店", "subgroup": "农场商店",
     "desc": "每天被打折的种子最少数量", "default": 1, "min": 1, "max": 20},
    {"key": "SEED_DISCOUNT_MAX", "label": "种子折扣最多款数", "type": "int", "group": "商店", "subgroup": "农场商店",
     "desc": "每天被打折的种子最多数量", "default": 3, "min": 1, "max": 20},
    {"key": "SEED_DISCOUNT_PCT", "label": "种子折扣倍率", "type": "float", "group": "商店", "subgroup": "农场商店",
     "desc": "折扣后的价格倍率（0.8 = 打八折）", "default": 0.8, "min": 0.05, "max": 1},
    # ---- 宠物（补充）2.0.3 自动喂养 → 2.0.4 改为自动购买 + 自动打工 ----
    {"key": "AUTO_FEED_ENABLED", "label": "自动购买总开关", "type": "bool", "group": "宠物", "subgroup": "自动购买",
     "desc": "总开关（默认关闭）：开启后，用「自动购买 开/关」启用了自动购买的用户，在宠物 饱食/口渴/心情/健康 任一属性进入第 3/4 档时自动补满；自动购买同时自动开启自动打工", "default": False},
    {"key": "AUTO_FEED_PRICE_MULT", "label": "自动购买·购买价格倍率", "type": "float", "group": "宠物", "subgroup": "自动购买",
     "desc": "自动购买（补满缺口）商品的价格倍率（1.2 = 比手动购买高 20%）；仓库已有道具免费使用", "default": 1.2, "min": 0.1, "max": 10},
    {"key": "AUTO_PURCHASE_COOLDOWN_MIN", "label": "自动购买·失败冷却分钟", "type": "int", "group": "宠物", "subgroup": "自动购买",
     "desc": "自动购买触发后因金币不足未能补满时，多少分钟后才允许再次触发（防止每消息反复尝试）", "default": 10, "min": 0, "max": 1440},
    {"key": "AUTO_WORK_ENABLED", "label": "自动打工总开关", "type": "bool", "group": "宠物", "subgroup": "自动打工",
     "desc": "总开关（默认开启）：开启后，开启了自动购买的用户自动开启自动打工（也可「自动打工 开/关」单独控制）", "default": True},
    {"key": "AUTO_WORK_DELAY_MIN", "label": "自动打工·冷却后间隔分钟", "type": "int", "group": "宠物", "subgroup": "自动打工",
     "desc": "自动打工完成进入冷却后，冷却结束再过 N 分钟安排下一次自动打工（独立计时器循环）", "default": 10, "min": 0, "max": 600},
    {"key": "AUTO_WORK_PAUSE_BASE", "label": "自动打工·暂停基准金币", "type": "int", "group": "宠物", "subgroup": "自动打工",
     "desc": "打工基准金币 ≤ 该值（100）时暂停自动打工；下次自动购买使其超过该值后自动恢复", "default": 100, "min": 0, "max": 1000000},
    {"key": "AUTO_WORK_EXP_ENABLED", "label": "自动打工·是否产生经验", "type": "bool", "group": "宠物", "subgroup": "自动打工",
     "desc": "自动打工是否产生经验收益（默认关闭 = 只给金币不给经验）；开启后经验 = 打工项目经验 × 可调倍率", "default": False},
    {"key": "AUTO_WORK_EXP_MULT", "label": "自动打工·经验收益倍率", "type": "float", "group": "宠物", "subgroup": "自动打工",
     "desc": "自动打工经验 = 该打工项目经验 × 此倍率（0.1~1 倍，默认 0.5 = 手动打工经验的一半；仅在开启经验收益时生效）", "default": 0.5, "min": 0.1, "max": 1.0},
    # ---- 固定结算（2.1.0：固定刷新时间） ----
    {"key": "DAILY_SETTLE_HOUR", "label": "插件数据结算时间（时）", "type": "int", "group": "固定结算", "subgroup": "每日结算",
     "desc": "每天该整点固定开始结算插件数据：全部宠物每日结算 + 自动购买「每日结算」触发（默认 0 = 零点）", "default": 0, "min": 0, "max": 23},
    {"key": "BANK_SETTLE_HOUR", "label": "银行存款结算时间（时）", "type": "int", "group": "固定结算", "subgroup": "每日结算",
     "desc": "每天该整点结算银行存款数据：解锁到期存单并发放利息（默认 4 = 四点）", "default": 4, "min": 0, "max": 23},
    {"key": "DAILY_SETTLE_LOOP_INTERVAL", "label": "固定结算·巡检间隔（秒）", "type": "int", "group": "固定结算", "subgroup": "每日结算",
     "desc": "固定结算循环每隔多少秒巡检一次当前时间（到达设定整点后触发结算；默认 60）", "default": 60, "min": 5, "max": 3600},
    # ---- 背包（独立折叠分组） ----
    {"key": "BAG_CARD_COLS", "label": "背包每行卡片数", "type": "int", "group": "背包", "subgroup": "卡片显示",
     "desc": "「背包」图片一行展示的卡片数量", "default": 5, "min": 3, "max": 7},
    # ---- 金币红包 ----
    {"key": "REDPACKET_DAILY_LIMIT", "label": "红包每日发送上限", "type": "int", "group": "金币红包", "subgroup": "发送",
     "desc": "每位玩家每天最多发送金币红包的次数", "default": 4, "min": 1, "max": 50},
    {"key": "REDPACKET_TTL", "label": "红包有效期（秒）", "type": "int", "group": "金币红包", "subgroup": "有效期",
     "desc": "红包发出后多少秒内有效，超时剩余自动退回", "default": 600, "min": 60, "max": 86400},
    {"key": "WEAK_HEAL_COST", "label": "治疗虚弱宠物费用", "type": "int", "group": "宠物", "subgroup": "虚弱治疗",
     "desc": "发送「治疗宠物」使虚弱宠物恢复所需金币", "default": 500, "min": 0, "max": 100000},
    {"key": "AUTO_STEAL_TARGETS", "label": "自动偷菜目标数", "type": "int", "group": "偷菜", "subgroup": "自动偷菜",
     "desc": "「自动偷菜」每次随机抽取的目标用户数量", "default": 4, "min": 1, "max": 10},
    # ---- 农场 ----
    {"key": "FARM_UNLOCK_COST", "label": "解锁农场价格", "type": "int", "group": "农场", "subgroup": "价格",
     "desc": "解锁农场所需金币（赠送 2 块地）", "default": 1500, "min": 0, "max": 1000000},
    {"key": "FARM_PLOT_COST", "label": "购买土地价格", "type": "int", "group": "农场", "subgroup": "价格",
     "desc": "开垦一块新土地所需金币", "default": 800, "min": 0, "max": 1000000},
    {"key": "FARM_MAX_PLOTS", "label": "最大土地数量", "type": "int", "group": "农场", "subgroup": "土地",
     "desc": "农场最多可拥有的土地块数", "default": 24, "min": 2, "max": 99},
    {"key": "FARM_PLOT_CARD_WIDTH", "label": "农场卡片宽度", "type": "int", "group": "农场", "subgroup": "卡片显示",
     "desc": "「土地状态/我的农场」每张土地卡片宽度（默认 270 = 原 360 的 75%）", "default": 270, "min": 160, "max": 420},
    {"key": "FARM_PLOT_COLS", "label": "农场卡片列数", "type": "int", "group": "农场", "subgroup": "卡片显示",
     "desc": "「土地状态/我的农场」一行显示的卡片数量", "default": 4, "min": 2, "max": 8},
    {"key": "EXP_BALL_DAILY_LIMIT", "label": "经验球每日使用上限", "type": "int", "group": "农场", "subgroup": "农场经验球",
     "desc": "农场经验球每天最多可使用次数", "default": 3, "min": 1, "max": 50, "attr": "exp_ball_daily_limit"},
    {"key": "EXP_BALL_MIN_PCT", "label": "经验球增加百分比下限", "type": "float", "group": "农场", "subgroup": "农场经验球",
     "desc": "使用农场经验球获得升级所需总经验的最小百分比（0.05 = 5%）", "default": 0.05, "min": 0, "max": 1, "attr": "exp_ball_min_pct"},
    {"key": "EXP_BALL_MAX_PCT", "label": "经验球增加百分比上限", "type": "float", "group": "农场", "subgroup": "农场经验球",
     "desc": "使用农场经验球获得升级所需总经验的最大百分比（0.20 = 20%）", "default": 0.20, "min": 0, "max": 2, "attr": "exp_ball_max_pct"},
    # ---- 偷菜（2.0.1 重做） ----
    {"key": "STEAL_ENABLED", "label": "偷菜功能开关", "type": "bool", "group": "偷菜", "subgroup": "通用",
     "desc": "全局总开关：关闭后「偷菜」指令提示功能未开启", "default": True},
    {"key": "STEAL_LOSS_MIN", "label": "偷菜收益比例下限", "type": "float", "group": "偷菜", "subgroup": "规则",
     "desc": "偷菜成功获得地块当前产量收益的比例下限（0.05 = 5%），农场主损失对应收益", "default": 0.05, "min": 0.01, "max": 1},
    {"key": "STEAL_LOSS_MAX", "label": "偷菜收益比例上限", "type": "float", "group": "偷菜", "subgroup": "规则",
     "desc": "偷菜成功获得地块当前产量收益的比例上限（0.20 = 20%）", "default": 0.20, "min": 0.01, "max": 1},
    {"key": "STEAL_PROTECT_RATIO", "label": "保护地块产量阈值", "type": "float", "group": "偷菜", "subgroup": "规则",
     "desc": "地块当前产量低于原有产量该比例（0.5 = 50%）即进入保护状态，剩余作物不可再被偷（提示「被偷完了」）", "default": 0.5, "min": 0.1, "max": 1},
    {"key": "STEAL_LEVEL_GAP", "label": "农场等级差限制", "type": "int", "group": "偷菜", "subgroup": "规则",
     "desc": "偷菜者无法向农场等级高于自己该级数的农场主发起偷菜", "default": 10, "min": 1, "max": 100},
    # ---- 宠物加护 ----
    {"key": "STEAL_GUARD_CATCH", "label": "加护·抓到你了概率", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "宠物激活且空闲、状态档位1-2时，偷菜触发「抓到你了」（偷菜失败+气味记忆24h+主人宠物体力-2~5）的概率（0.1 = 10%）", "default": 0.10, "min": 0, "max": 1},
    {"key": "STEAL_GUARD_STOP", "label": "加护·给我站住概率", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「给我站住」（农场主损失减半+体力-3~6+等级压制失效12h+偷菜者缴原金额110%罚款）的概率（0.2 = 20%）", "default": 0.20, "min": 0, "max": 1},
    {"key": "STEAL_GUARD_CATCH_STAMINA_MIN", "label": "抓到你了·主人体力下降下限", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「抓到你了」时农场主宠物体力随机下降的最小值", "default": 2, "min": 0, "max": 200},
    {"key": "STEAL_GUARD_CATCH_STAMINA_MAX", "label": "抓到你了·主人体力下降上限", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「抓到你了」时农场主宠物体力随机下降的最大值", "default": 5, "min": 0, "max": 200},
    {"key": "STEAL_GUARD_STOP_STAMINA_MIN", "label": "给我站住·主人体力下降下限", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「给我站住」时农场主宠物体力随机下降的最小值", "default": 3, "min": 0, "max": 200},
    {"key": "STEAL_GUARD_STOP_STAMINA_MAX", "label": "给我站住·主人体力下降上限", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「给我站住」时农场主宠物体力随机下降的最大值", "default": 6, "min": 0, "max": 200},
    {"key": "STEAL_FINE_RATIO", "label": "给我站住·罚款比例", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「给我站住」时偷菜者需缴纳的罚款为偷菜原金额的比例（1.1 = 110%）", "default": 1.10, "min": 0.5, "max": 5},
    {"key": "STEAL_SUPPRESS_HOURS", "label": "等级压制失效时长（小时）", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「给我站住」后，等级压制失效的时长", "default": 12, "min": 0, "max": 168},
    {"key": "STEAL_PET_GAP", "label": "宠物等级压制级差", "type": "int", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "偷菜者宠物等级比农场主宠物等级低该级数及以上时，宠物加护触发概率减半", "default": 10, "min": 1, "max": 100},
    {"key": "STEAL_PET_GAP_DIV", "label": "等级压制概率除数", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "等级压制生效时宠物加护触发概率除以该值（2 = 减半）", "default": 2.0, "min": 1.1, "max": 10},
    {"key": "STEAL_GUARD_TIER_MAX", "label": "加护失效状态档位", "type": "int", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "宠物状态档位达到该档及以上时宠物加护失效（3 = 三、四档失效）", "default": 3, "min": 2, "max": 4},
    # ---- 气味记忆 ----
    {"key": "STEAL_SCENT_CONSEC", "label": "气味记忆·连续成功次数", "type": "int", "group": "偷菜", "subgroup": "气味记忆",
     "desc": "24小时内同一偷菜者对同一农场主连续成功偷菜达到该次数即被施加气味记忆", "default": 3, "min": 1, "max": 50},
    {"key": "STEAL_SCENT_HOURS", "label": "气味记忆·持续时长（小时）", "type": "float", "group": "偷菜", "subgroup": "气味记忆",
     "desc": "气味记忆效果的持续时长", "default": 24, "min": 1, "max": 168},
    {"key": "STEAL_SCENT_MULT", "label": "气味记忆·加护概率倍数", "type": "float", "group": "偷菜", "subgroup": "气味记忆",
     "desc": "生效时偷菜者偷取施加者触发宠物加护的概率倍数（3 = 3倍；不受等级压制影响）", "default": 3.0, "min": 1, "max": 20},
    {"key": "AUTO_STEAL_DAILY_LIMIT", "label": "自动偷菜每日成功次数", "type": "int", "group": "偷菜", "subgroup": "自动偷菜",
     "desc": "自动偷菜每天最多成功次数（失败不消耗次数；被宠物抓到则当天锁定）", "default": 5, "min": 1, "max": 20},
    # ---- 左轮手枪 ----
    {"key": "ROULETTE_JOIN_TIMEOUT", "label": "左轮加入超时（秒）", "type": "int", "group": "左轮手枪", "subgroup": "规则",
     "desc": "左轮手枪开局后等待加入的超时秒数", "default": 30, "min": 10, "max": 300},
    # ---- 贷款 ----
    {"key": "LOAN_SPECIAL_AMOUNT", "label": "特别贷款金额", "type": "int", "group": "贷款", "subgroup": "特别贷款",
     "desc": "套餐 0 强制解锁贷款金额（不发放金币，作为解锁服务费）", "default": 2500, "min": 0, "max": 100000},
    # ---- 金币账单 ----
    {"key": "LEDGER_SHOW", "label": "金币账单展示条数", "type": "int", "group": "金币账单", "subgroup": "展示",
     "desc": "「查询流水」最多展示最近多少条", "default": 30, "min": 5, "max": 200},
    # ---- 签到 · 好感度（补充） ----
    {"key": "MIN_FAV", "label": "签到最少好感度", "type": "float", "group": "签到", "subgroup": "好感度",
     "desc": "每次签到最少增加的好感度", "default": 0.01, "min": 0, "max": 10, "attr": "min_fav"},
    {"key": "MAX_FAV", "label": "签到最多好感度", "type": "float", "group": "签到", "subgroup": "好感度",
     "desc": "每次签到最多增加的好感度", "default": 1.0, "min": 0, "max": 100, "attr": "max_fav"},
    {"key": "MAX_LEVEL", "label": "好感度最高等级", "type": "int", "group": "签到", "subgroup": "好感度",
     "desc": "好感度最高等级（初始为 0）", "default": 10, "min": 1, "max": 100},
    {"key": "LEVEL_STEP", "label": "好感度每级所需点数", "type": "float", "group": "签到", "subgroup": "好感度",
     "desc": "好感度每满该值提升一级", "default": 10.0, "min": 1, "max": 1000, "attr": "level_step"},
    # ---- 签到（补充）2.0.0：宠物结算各属性值档位变化范围 ----
    {"key": "PET_SETTLE_RANGES", "label": "宠物结算·各档位属性变化范围", "type": "string", "group": "签到", "subgroup": "宠物结算范围",
     "desc": "每日结算时宠物各属性按状态档位随机变化。2.0.1 固定四档 T1~T4（按饱食/口渴/心情最差档：1 最好 ~ 4 最差），每档定义全部五属性变化范围。格式：档位=属性最低~最高，逗号分隔；属性名：饱食/口渴/体力/心情/健康。示例：T1=饱食-15~-10,口渴-15~-10,体力100~120,心情3~7,健康5~10|T2=饱食-15~-10,口渴-15~-10,体力100~120,心情3~7,健康0.1~6|T3=饱食-20~-15,口渴-20~-15,体力80~120,心情1~2.5,健康-10~-4|T4=饱食-25~-20,口渴-25~-20,体力40~60,心情-5~-2,健康-15~-8",
     "default": "T1=饱食-15~-10,口渴-15~-10,体力100~120,心情3~7,健康5~10|T2=饱食-15~-10,口渴-15~-10,体力100~120,心情3~7,健康0.1~6|T3=饱食-20~-15,口渴-20~-15,体力80~120,心情1~2.5,健康-10~-4|T4=饱食-25~-20,口渴-25~-20,体力40~60,心情-5~-2,健康-15~-8",
     "attr": "pet_settle_ranges"},
    {"key": "PET_SETTLE_TIERS", "label": "宠物结算·档位数值（各档判定阈值）", "type": "string", "group": "签到", "subgroup": "宠物结算范围",
     "desc": "四档判定阈值（一档下限,二档下限,三档下限，从高到低）：属性值 ≥一档下限→1档；≥二档下限→2档；≥三档下限→3档；否则4档。格式：饱食=120,50,30|口渴=120,70,30|心情=80,50,30",
     "default": "饱食=120,50,30|口渴=120,70,30|心情=80,50,30",
     "attr": "pet_settle_tiers"},
    {"key": "PET_SETTLE_HEALTH_EXCHANGE", "label": "宠物结算·超扣转健康抵扣比例", "type": "int", "group": "签到", "subgroup": "宠物结算范围",
     "desc": "每日结算扣减的属性若超出结算前属性值，超出部分按「N 属性点 = 1 健康」用健康值抵扣（默认 4：少 4 点属性点扣 1 点健康；超出部分向上取整）",
     "default": 4, "min": 1, "max": 100},
    # ---- 左轮手枪（补充） ----
    {"key": "ROULETTE_MAGAZINES", "label": "弹匣数量", "type": "int", "group": "左轮手枪", "subgroup": "规则",
     "desc": "左轮手枪弹匣容量", "default": 7, "min": 3, "max": 20},
    {"key": "ROULETTE_MAX_BULLETS", "label": "子弹数量上限", "type": "int", "group": "左轮手枪", "subgroup": "规则",
     "desc": "单局最多装的子弹数量", "default": 6, "min": 1, "max": 10},
    {"key": "ROULETTE_MIN_PLAYERS", "label": "最少玩家人数", "type": "int", "group": "左轮手枪", "subgroup": "规则",
     "desc": "开局所需的最少玩家人数", "default": 2, "min": 2, "max": 5},
    {"key": "ROULETTE_MAX_PLAYERS", "label": "最多玩家人数", "type": "int", "group": "左轮手枪", "subgroup": "规则",
     "desc": "一局最多参与的玩家人数", "default": 3, "min": 2, "max": 6},
    {"key": "ROULETTE_FEE_RATE", "label": "手续费比例", "type": "float", "group": "左轮手枪", "subgroup": "规则",
     "desc": "左轮手枪抽取的手续费比例（0.1 = 10%，3 人局固定 5%）", "default": 0.1, "min": 0, "max": 0.5},
    # ---- 宠物（补充） ----
    {"key": "PET_MAX_LEVEL", "label": "宠物最大等级", "type": "int", "group": "宠物", "subgroup": "解锁",
     "desc": "宠物等级上限", "default": 100, "min": 10, "max": 999},
    {"key": "PET_MAX_HEALTH", "label": "健康度最大值", "type": "float", "group": "宠物", "subgroup": "属性",
     "desc": "宠物健康度上限", "default": 200.0, "min": 50, "max": 1000},
    {"key": "PILL_NAME", "label": "属性丸道具名", "type": "string", "group": "宠物", "subgroup": "属性丸",
     "desc": "签到获得的属性丸名称", "default": "属性丸"},
    {"key": "EXP_BALL_NAME", "label": "农场经验球道具名", "type": "string", "group": "农场", "subgroup": "农场经验球",
     "desc": "签到获得的农场经验球名称", "default": "农场经验球"},
    {"key": "ITEM_TO_COIN", "label": "道具转金币单价", "type": "int", "group": "宠物", "subgroup": "属性丸",
     "desc": "未开通对应功能时，属性丸/经验球自动转换为金币的单价", "default": 10, "min": 1, "max": 100000},
    # ---- 农场（补充） ----
    {"key": "FARM_FREE_PLOTS", "label": "解锁赠送土地数", "type": "int", "group": "农场", "subgroup": "价格",
     "desc": "解锁农场时赠送的土地数量", "default": 2, "min": 1, "max": 10},
    {"key": "FARM_MAX_LEVEL", "label": "农场最大等级", "type": "int", "group": "农场", "subgroup": "土地",
     "desc": "农场等级上限", "default": 100, "min": 10, "max": 999},
    {"key": "FARM_EXP_BASE", "label": "农场升级经验基数", "type": "float", "group": "农场", "subgroup": "土地",
     "desc": "升到 N 级需 1000×N 经验（此值即基数 1000）", "default": 1000.0, "min": 100, "max": 100000},
    {"key": "FARM_UPGRADE_COSTS", "label": "土地升级费用（逗号分隔）", "type": "list", "group": "农场", "subgroup": "土地",
     "desc": "土地从当前等级升到下一级的金币，依次为 贫瘠→红→普通→肥沃→黑", "default": "1000,1500,2000,3000"},
    {"key": "FARM_GRADE_BONUSES", "label": "土地等级加成（表格）", "type": "string", "group": "农场", "subgroup": "土地",
     "desc": "不同土地等级的 产量加成/时间减免（百分比）与升级价格（在「土地等级加成」表格中编辑，升级价格随表格一并保存到 FARM_UPGRADE_COSTS）；格式：等级名=产量%,时间%，竖线分隔",
     "default": "贫瘠土地=0,0|红土地=100,0|普通土地=200,10|肥沃土地=250,20|黑土地=400,35"},
    # ---- 农场（补充）2.0.0：作物成长阶段 ----
    {"key": "CROP_LEVEL_RANGES", "label": "作物等级划分（分钟上限，逗号分隔）", "type": "list", "group": "农场", "subgroup": "成长阶段",
     "desc": "按贫瘠土地上的成熟分钟数划分作物等级：0~第1个数=一级，依此类推。如 0,240,480,720,1440 表示 0-240/241-480/481-720/721-1440", "default": "0,240,480,720,1440"},
    {"key": "CROP_LEVEL_STAGES", "label": "各等级成长阶段数（逗号分隔）", "type": "list", "group": "农场", "subgroup": "成长阶段",
     "desc": "一级/二级/三级/四级作物在总生长周期内划分的成长阶段数量（化肥每次加速推进一个阶段）", "default": "4,5,5,6"},
    # ---- 贷款（补充） ----
    {"key": "LOAN_SPECIAL_RATE", "label": "特别贷款日息", "type": "float", "group": "贷款", "subgroup": "特别贷款",
     "desc": "特别贷款每日利息（% / 日）", "default": 1.0, "min": 0, "max": 100, "attr": "loan_special_rate"},
    {"key": "LOAN_SPECIAL_DAYS", "label": "特别贷款限期（天）", "type": "int", "group": "贷款", "subgroup": "特别贷款",
     "desc": "特别贷款还款限期天数", "default": 30, "min": 1, "max": 365, "attr": "loan_special_days"},
    {"key": "LOAN_SPECIAL_TAKE", "label": "特别贷款逾期收取比例", "type": "float", "group": "贷款", "subgroup": "特别贷款",
     "desc": "特别贷款逾期后收取仓库价值的比例（0.2 = 20%）", "default": 0.2, "min": 0, "max": 1},
    {"key": "LOAN_COIN_DEDUCT", "label": "逾期金币自动扣款比例", "type": "float", "group": "贷款", "subgroup": "规则",
     "desc": "有逾期贷款时，获得金币自动扣款还款的比例（0.2 = 20%）", "default": 0.2, "min": 0, "max": 1},
    {"key": "LOAN_FAV_DROP_SPECIAL", "label": "特别贷款逾期好感度降低范围", "type": "list", "group": "贷款", "subgroup": "规则",
     "desc": "特别贷款逾期每日好感度降低的随机范围（逗号分隔，如 1.0,1.5）", "default": "1.0,1.5"},
    {"key": "LOAN_FAV_DROP_NORMAL", "label": "一般逾期好感度降低范围", "type": "list", "group": "贷款", "subgroup": "规则",
     "desc": "一般逾期每日好感度降低的随机范围（逗号分隔，如 1.01,1.25）", "default": "1.01,1.25"},
    {"key": "LOAN_OVERDUE_YEAR_LIMIT", "label": "每年最多逾期次数", "type": "int", "group": "贷款", "subgroup": "规则",
     "desc": "每年逾期次数达到该值后禁用贷款功能", "default": 4, "min": 1, "max": 100},
    {"key": "LOAN_GENERAL_OVERDUE_DAYS", "label": "一般套餐逾期天数", "type": "int", "group": "贷款", "subgroup": "规则",
     "desc": "一般/自定义套餐的还款限期天数", "default": 15, "min": 1, "max": 365},
    {"key": "LOAN_SHORT_GRACE_DAYS", "label": "短期套餐免息天数", "type": "int", "group": "贷款", "subgroup": "规则",
     "desc": "短期套餐免息期天数", "default": 10, "min": 1, "max": 365},
    {"key": "LOAN_SHORT_RATE", "label": "短期套餐逾期日利率", "type": "float", "group": "贷款", "subgroup": "规则",
     "desc": "短期套餐逾期后的日利率（% / 日）", "default": 6.0, "min": 0, "max": 100},
    {"key": "LOAN_DAILY_MULT", "label": "每日累计贷款倍数", "type": "float", "group": "贷款", "subgroup": "规则",
     "desc": "每日累计贷款上限 = 该值 × 套餐上限", "default": 2.0, "min": 1, "max": 100},
    {"key": "LOAN_FARM_ROLLBACK_DAYS", "label": "逾期农场回退天数", "type": "int", "group": "贷款", "subgroup": "规则",
     "desc": "逾期超过该天数后农场回退至初始状态", "default": 30, "min": 1, "max": 365},
    {"key": "LOAN_AUTO_TIME", "label": "每日自动处理时间（时,分）", "type": "list", "group": "贷款", "subgroup": "规则",
     "desc": "每日自动卖仓库/自动签到还款的时间（逗号分隔，如 23,0）", "default": "23,0"},
    # ---- 红包雨（1.7.5 起迁移至 WebUI「活动中心」配置，运行参数面板不再展示） ----
    # ---- 调试 / 通用 ----
    {"key": "DEBUG_PASSWORD", "label": "调试模式口令", "type": "string", "group": "调试", "subgroup": "口令",
     "desc": "管理员在对话框输入此口令解锁 WebUI 调试按钮（重启后失效）", "default": "88224646"},
    {"key": "TEMP_IMAGE_TTL", "label": "临时图片保留秒数", "type": "int", "group": "调试", "subgroup": "图片",
     "desc": "生成的临时图片超过该秒数后清理（0 表示不清理）", "default": 600, "min": 0, "max": 86400},
    # ---- 排行榜 ----
    {"key": "RANK_DISPLAY", "label": "排行榜展示名次", "type": "int", "group": "排行榜", "subgroup": "通用",
     "desc": "每个排行榜最多展示的名次数（前 N 名）", "default": 20, "min": 5, "max": 100},
    {"key": "RANK_HIGHLIGHT_COLOR", "label": "我的名次高亮颜色", "type": "string", "group": "排行榜", "subgroup": "通用",
     "desc": "查询人自己在榜上的高亮颜色（十六进制，如 #92D050）", "default": "#92D050"},
    {"key": "RANK_TEXT_COLOR", "label": "普通行文字颜色", "type": "string", "group": "排行榜", "subgroup": "通用",
     "desc": "本群（非自己）用户的文字颜色（十六进制，如 #000000）", "default": "#000000"},
    {"key": "RANK_MASKED_COLOR", "label": "脱敏行文字颜色", "type": "string", "group": "排行榜", "subgroup": "通用",
     "desc": "非本群（脱敏 ** 显示）用户的文字颜色（十六进制，如 #7F7F7F）", "default": "#7F7F7F"},
    {"key": "RANK_SEP_COLOR", "label": "分割线颜色", "type": "string", "group": "排行榜", "subgroup": "通用",
     "desc": "行间分割线颜色（十六进制，如 #D9D9D9）", "default": "#D9D9D9"},
    {"key": "RANK_IMAGE_SCALE", "label": "图片宽度倍数", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "排行榜图片宽度 = 内容宽度 × 该倍数（默认 2.5 = 原来的 250%）", "default": 2.5, "min": 1.0, "max": 10.0},
    {"key": "RANK_BAR_LIGHTEN", "label": "进度条颜色浅化比例", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "进度条颜色比文字颜色浅的比例（默认 0.2 = 浅 20%）", "default": 0.2, "min": 0.0, "max": 0.9},
    {"key": "RANK_NAME_MAX_CHARS", "label": "名字显示最大字符数", "type": "int", "group": "排行榜", "subgroup": "通用",
     "desc": "用户名超过该字符数时截断，超出部分用 ... 代替", "default": 6, "min": 1, "max": 20},
    {"key": "RANK_BAR_GAP_LEFT_MULT", "label": "进度条左侧留白倍数", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "进度条左侧留白 = 基准 12px × 该倍数（2 = 原先的 200%）", "default": 2.0, "min": 0.5, "max": 10.0},
    {"key": "RANK_BAR_GAP_RIGHT_MULT", "label": "进度条右侧留白倍数", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "进度条右侧留白 = 基准 12px × 该倍数（3 = 原先的 300%）", "default": 3.0, "min": 0.5, "max": 10.0},
    {"key": "RANK_ROW_SEP_PCT", "label": "行分割线高度占比", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "行间分割线高度 = 文字行高 × 该比例（默认 0.03 = 原先 10% 的 30%）", "default": 0.03, "min": 0.0, "max": 0.5},
    {"key": "GROUP_MEMBER_TTL_HOURS", "label": "群成员标记有效期（小时）", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "用户在本群触发插件功能后被标记为本群成员的时长，超时后按非本群用户脱敏显示", "default": 48, "min": 1, "max": 720},
    {"key": "RANK_COIN_COIN_W", "label": "金币权重（持有金币）", "type": "float", "group": "排行榜", "subgroup": "金币排行",
     "desc": "金币排行积分 = 持有金币 × 该权重 + 银行存款 × 存款权重", "default": 1.0, "min": 0, "max": 1000},
    {"key": "RANK_COIN_BANK_W", "label": "银行存款权重", "type": "float", "group": "排行榜", "subgroup": "金币排行",
     "desc": "金币排行积分 = 持有金币 × 金币权重 + 银行存款 × 该权重", "default": 1.0, "min": 0, "max": 1000},
    {"key": "RANK_PET_EXP_W", "label": "宠物经验权重", "type": "float", "group": "排行榜", "subgroup": "宠物排行",
     "desc": "宠物排行积分 = 经验 × 该权重 + 健康度 × 健康权重 + 其它属性 × 属性权重", "default": 2.0, "min": 0, "max": 1000},
    {"key": "RANK_PET_HEALTH_W", "label": "宠物健康度权重", "type": "float", "group": "排行榜", "subgroup": "宠物排行",
     "desc": "宠物排行中健康度的权重", "default": 1.5, "min": 0, "max": 1000},
    {"key": "RANK_PET_ATTR_W", "label": "其它属性权重", "type": "float", "group": "排行榜", "subgroup": "宠物排行",
     "desc": "宠物排行中饱食+口渴+体力+心情 总和的权重", "default": 0.5, "min": 0, "max": 1000},
    {"key": "RANK_FARM_EXP_W", "label": "农场经验权重", "type": "float", "group": "排行榜", "subgroup": "农场排行",
     "desc": "农场排行积分 = 经验 × 该权重 + (土地数-2) × 土地权重 + 等级分合计 × 等级权重", "default": 2.0, "min": 0, "max": 1000},
    {"key": "RANK_FARM_PLOT_W", "label": "土地数权重", "type": "float", "group": "排行榜", "subgroup": "农场排行",
     "desc": "农场排行中（拥有土地数 - 2）× 该权重", "default": 400.0, "min": 0, "max": 100000},
    {"key": "RANK_FARM_GRADE_W", "label": "土地等级分权重", "type": "float", "group": "排行榜", "subgroup": "农场排行",
     "desc": "农场排行中土地等级分合计 × 该权重", "default": 0.5, "min": 0, "max": 1000},
    {"key": "RANK_PLOT_SCORES", "label": "土地等级分（逗号分隔）", "type": "list", "group": "排行榜", "subgroup": "农场排行",
     "desc": "各土地等级的累计升级分数：贫瘠→红→普通→肥沃→黑（如 0,1000,2500,4500,7500）", "default": "0,1000,2500,4500,7500"},
]
PARAM_KEYS = {p["key"]: p for p in RUNTIME_PARAMS}

# 可单独控制是否撤回的指令 → 对应运行参数 key（参数为 False 时该指令回复不撤回）
RECALL_EXEMPT = {
    "活动": "RECALL_ACTIVITY",
    "商店": "RECALL_SHOP",
    "种子商店": "RECALL_SEED_SHOP",
    "肥料商店": "RECALL_FERT_SHOP",
    "农场商店": "RECALL_FARM_SHOP",
    "签到帮助": "RECALL_HELP",
    "宠物帮助": "RECALL_HELP",
    "农场帮助": "RECALL_HELP",
    "左轮手枪帮助": "RECALL_HELP",
    "游戏帮助": "RECALL_HELP",
}

PLUGIN_NAME = "astrbot_plugin_signin"
# 插件根目录：base.py 位于 modules/ 子目录，需上溯一级到插件包根目录
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
PET_SHOP_FILE = os.path.join(_DATA_DIR, "宠物商店.txt")
CROP_FILE = os.path.join(_DATA_DIR, "作物.txt")
FERT_FILE = os.path.join(_DATA_DIR, "肥料.txt")
LOAN_FILE = os.path.join(_DATA_DIR, "贷款套餐.txt")
# 1.7.7：商店/打工/玩耍数值的 JSON 存储（WebUI 表格编辑；首次启动从旧版 txt 自动迁移）
ITEMS_JSON_FILE = os.path.join(_DATA_DIR, "game_items.json")
FONT_FILE = os.path.join(_PLUGIN_DIR, "OPPOSans-M.ttf")
# 2.1.1：标题衬线字体（思源宋体 Bold，对齐 WebUI --font-display 衬线标题层级）
TITLE_FONT_FILE = os.path.join(_PLUGIN_DIR, "SourceHanSerifCN-Bold.otf")

# 宠物商店按类型拆分的文件（1.7.3）
PET_SHOP_TYPE_FILES = {
    "食物": os.path.join(_DATA_DIR, "宠物商店-食物.txt"),
    "饮料": os.path.join(_DATA_DIR, "宠物商店-饮料.txt"),
    "药物": os.path.join(_DATA_DIR, "宠物商店-药物.txt"),
    "玩具": os.path.join(_DATA_DIR, "宠物商店-玩具.txt"),
}
PET_SHOP_TYPES = list(PET_SHOP_TYPE_FILES.keys())


def _migrate_old_data_files():
    """把旧位置（插件目录）的数据文件迁移到新位置（plugin_data），只迁一次"""
    pairs = [
        ("data.json", DATA_FILE),
        ("后台.txt", CONFIG_FILE),
        ("作物.txt", CROP_FILE),
        ("肥料.txt", FERT_FILE),
        ("贷款套餐.txt", LOAN_FILE),
        ("宠物商店.txt", PET_SHOP_FILE),
    ]
    try:
        for fn, target in pairs:
            src = os.path.join(_PLUGIN_DIR, fn)
            if os.path.exists(src) and not os.path.exists(target):
                shutil.move(src, target)
    except Exception as e:
        logger.error(f"[插件] 迁移旧数据文件失败: {e}")


def _migrate_split_shop_config():
    """1.7.2：把 后台.txt 里的 [商店:xxx] 段落拆到独立的 宠物商店.txt（自动迁移一次）。
    1.7.3：把 宠物商店.txt 按 类型=食物/饮料/药物/玩具 拆到独立文件（自动迁移一次）。
    后台.txt 只保留打工/玩耍。"""
    try:
        # 1.7.2 迁移：后台.txt → 宠物商店.txt
        if not os.path.exists(PET_SHOP_FILE) and os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw = f.read()
            lines = raw.splitlines(keepends=True)
            in_shop = False
            shop_lines = []
            keep_lines = []
            for ln in lines:
                s = ln.strip()
                if s.startswith("[") and s.endswith("]"):
                    if ":" in s[1:-1]:
                        typ = s[1:-1].split(":", 1)[0].strip()
                        if typ == "商店":
                            in_shop = True
                            shop_lines.append(ln)
                            continue
                        else:
                            in_shop = False
                if in_shop:
                    shop_lines.append(ln)
                else:
                    keep_lines.append(ln)
            if shop_lines:
                with open(PET_SHOP_FILE, "w", encoding="utf-8") as f:
                    f.write("".join(shop_lines))
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    f.write("".join(keep_lines))
                logger.info(f"[插件] 已将后台.txt 的商店段落拆分为 宠物商店.txt（{len(shop_lines)} 行）")
        # 1.7.3 迁移：宠物商店.txt → 按类型文件
        _migrate_split_petshop_types()
    except Exception as e:
        logger.error(f"[插件] 拆分宠物商店配置失败: {e}")


def _migrate_split_petshop_types():
    """把 宠物商店.txt 按 类型= 字段拆到 PET_SHOP_TYPE_FILES；宠物商店.txt 保留（兼容回退）。
    若任一类型文件不存在且宠物商店.txt 存在 → 按类型拆分。"""
    try:
        if not os.path.exists(PET_SHOP_FILE):
            return
        if all(os.path.exists(p) for p in PET_SHOP_TYPE_FILES.values()):
            return
        sections = _parse_kv_sections(PET_SHOP_FILE, "宠物商店", types=("商店",))
        by_type = {t: [] for t in PET_SHOP_TYPES}
        other = []
        for sec in sections:
            typ = sec["data"].get("类型", "").strip() or "其他"
            # 兼容：旧类型「食品」归入「食物」文件
            if typ == "食品":
                typ = "食物"
            lines = [f"[商店:{sec['name']}]\n"]
            for k, v in sec["data"].items():
                lines.append(f"{k}={v}\n")
            lines.append("\n")
            if typ in by_type:
                by_type[typ].append("".join(lines))
            else:
                other.append("".join(lines))
        wrote = False
        for typ, lines in by_type.items():
            if lines and not os.path.exists(PET_SHOP_TYPE_FILES[typ]):
                with open(PET_SHOP_TYPE_FILES[typ], "w", encoding="utf-8") as f:
                    f.write("".join(lines))
                wrote = True
        if wrote:
            logger.info("[插件] 已将 宠物商店.txt 按类型拆分为独立文件（食物/饮料/药物/玩具）")
    except Exception as e:
        logger.error(f"[插件] 拆分宠物商店类型文件失败: {e}")


def _load_activity_modules():
    """动态加载插件目录下 activities/ 中的活动模块。返回 (活动实例列表, 是否可用)"""
    try:
        pkg_dir = os.path.join(_PLUGIN_DIR, "activities")
        if not os.path.exists(os.path.join(pkg_dir, "__init__.py")):
            return [], False
        import importlib
        import sys
        if _PLUGIN_DIR not in sys.path:
            sys.path.insert(0, _PLUGIN_DIR)
        # 先清掉缓存，保证插件重载后活动模块被重新注册
        sys.modules.pop("activities", None)
        pkg = importlib.import_module("activities")
        acts = pkg.load_all()
        logger.info(f"[插件] 活动中心加载完成，共 {len(acts)} 个活动模块")
        return acts, True
    except Exception as e:
        logger.warning(f"[插件] 活动中心初始化失败: {e}")
        return [], False

ATTR_LABELS = {"satiety": "饱食度", "thirst": "口渴值", "stamina": "体力", "mood": "心情值", "health": "健康度"}
ATTR_SHORT = {"satiety": "饱食", "thirst": "口渴", "stamina": "体力", "mood": "心情", "health": "健康"}

# 临时图片保留秒数（超过则在下次生成同类图片时清理）
TEMP_IMAGE_TTL = 600


def _parse_kv_sections(path: str, kind: str, types=None):
    """解析「[类型:名称] + key=value」格式的配置文件。

    types 为 None 时接受所有段落类型；否则只保留其中列出的类型。
    返回 [{"type": 类型, "name": 名称, "data": {k: v}}]，读取失败返回空列表。
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()
    except Exception as e:
        logger.error(f"[插件] 读取{kind}配置失败: {e}")
        return []
    return _parse_kv_text(raw_lines, types)


def _parse_kv_sections_text(text: str, kind: str, types=None):
    """解析字符串形式的「[类型:名称] + key=value」配置（供旧版前端兼容保存使用）"""
    return _parse_kv_text(text.splitlines(keepends=True), types)


def _parse_kv_text(raw_lines, types=None):
    """解析「[类型:名称] + key=value」行列表，types 为 None 接受所有类型"""
    sections = []
    cur = None
    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = None
            inner = line[1:-1]
            if ":" in inner:
                typ, nm = inner.split(":", 1)
                typ, nm = typ.strip(), nm.strip()
                if types is None or typ in types:
                    cur = {"type": typ, "name": nm, "data": {}}
                    sections.append(cur)
            continue
        if cur is None:
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            cur["data"][k.strip()] = v.strip()
    return sections


# 数据文件迁移（依赖 _parse_kv_sections，须在其定义之后执行）
_migrate_old_data_files()
_migrate_split_shop_config()

# ============ 1.7.7 迁移：为旧版 txt 配置补充「描述」字段 ============
# 首次启动新版插件时，把运行环境的旧版 作物/肥料/后台/宠物商店-*.txt
# 中缺失的 描述= 行按插件包内置默认模板（Benchmark data）按段名补齐；
# 仅插入缺失项（已有描述 / 用户自定义不动），保留注释与原有内容，原文件备份为 .v176bak。
# ============ 1.7.7：Benchmark data 默认数值（新格式 game_items.json） ============
BENCH_ITEMS_FILE = os.path.join(_PLUGIN_DIR, "Benchmark data", "game_items.json")
_ITEMS_KEYS = ("jobs", "plays", "shop", "crops", "ferts", "loans")


def _load_benchmark_items():
    """读取 Benchmark data/game_items.json（默认数值模板）；不存在/损坏返回 None"""
    try:
        if not os.path.exists(BENCH_ITEMS_FILE):
            return None
        with open(BENCH_ITEMS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return None
        return {k: raw.get(k) if isinstance(raw.get(k), list) else [] for k in _ITEMS_KEYS}
    except Exception as e:
        logger.warning(f"[插件] 读取 Benchmark data/game_items.json 失败: {e}")
        return None


def _benchmark_desc_map(cat_keys):
    """从 Benchmark data/game_items.json 构建 {名称: [描述,...]}（同名多条按出现顺序）。
    cat_keys 为要合并的类别（如 ("作物",)；后台.txt 用 ("打工", "玩耍")）"""
    key_map = {"作物": "crops", "肥料": "ferts", "商店": "shop", "打工": "jobs", "玩耍": "plays"}
    m = {}
    flat = _load_benchmark_items() or {}
    for cat in cat_keys:
        for it in flat.get(key_map.get(cat, ""), []):
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "")).strip()
            desc = str(it.get("desc", "") or "").strip()
            if name and desc:
                m.setdefault(name, []).append(desc)
    return m


def _migrate_desc_file(path, tpl_map):
    """为单个旧版 txt 文件补充缺失的 描述= 行（行级插入，保留注释/空行/原有键值）。
    tpl_map: {段名: [描述,...]}（来自 Benchmark data/game_items.json）"""
    import re
    try:
        if not os.path.exists(path):
            return
        if not tpl_map:
            return
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        out = []
        used = {}
        changed = False
        i = 0
        n = len(lines)
        while i < n:
            ln = lines[i]
            s = ln.strip()
            ms = re.match(r"^\[[^:\]]+:(.+)\]$", s)
            out.append(ln)
            if ms:
                name = ms.group(1).strip()
                # 向后查找本段是否已有 描述= 行（到下一个段头为止）
                j = i + 1
                has_desc = False
                while j < n:
                    sj = lines[j].strip()
                    if sj.startswith("[") and sj.endswith("]"):
                        break
                    if sj.startswith("描述="):
                        has_desc = True
                        break
                    j += 1
                if not has_desc:
                    pool = tpl_map.get(name)
                    if pool:
                        idx = used.get(name, 0)
                        if idx < len(pool):
                            out.append("描述=" + pool[idx])
                            used[name] = idx + 1
                            changed = True
            i += 1
        if changed:
            bak = path + ".v176bak"
            if not os.path.exists(bak):
                try:
                    shutil.copy2(path, bak)
                except Exception:
                    pass
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
            logger.info(f"[插件] 已为 {os.path.basename(path)} 补充商品描述（原文件备份为 {os.path.basename(bak)}）")
    except Exception as e:
        logger.warning(f"[插件] 描述迁移失败 {os.path.basename(path)}: {e}")


def _migrate_txt_migrated_flag():
    """读取/写入 data.json 的 migrations.txt_desc_v176 标记（只迁一次）"""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, encoding="utf-8") as f:
                d = json.load(f)
        else:
            d = {}
        mig = d.setdefault("migrations", {})
        return d, mig
    except Exception:
        return {}, {}


def _migrate_add_descriptions():
    """为全部旧版 txt 配置补充描述（首次启动新版插件执行一次；描述模板来自 Benchmark data/game_items.json）"""
    try:
        if not _should_migrate_txt_descriptions():
            return  # 已迁移过（标记存在）→ 不再补全，尊重用户后续编辑
        crop_map = _benchmark_desc_map(("作物",))
        fert_map = _benchmark_desc_map(("肥料",))
        shop_map = _benchmark_desc_map(("商店",))
        backend_map = _benchmark_desc_map(("打工", "玩耍"))
        pairs = [
            (CROP_FILE, crop_map),
            (FERT_FILE, fert_map),
            (PET_SHOP_FILE, shop_map),
            (CONFIG_FILE, backend_map),
        ]
        for typ, f in PET_SHOP_TYPE_FILES.items():
            pairs.append((f, shop_map))
        for path, tpl_map in pairs:
            _migrate_desc_file(path, tpl_map)
        # 记录标记（data.json 尚不存在时跳过，下次启动幂等兜底）
        d, mig = _migrate_txt_migrated_flag()
        if not mig.get("txt_desc_v176"):
            mig["txt_desc_v176"] = True
            try:
                with open(DATA_FILE, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"[插件] 配置描述迁移失败: {e}")


def _should_migrate_txt_descriptions():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return not (d.get("migrations") or {}).get("txt_desc_v176")
    except Exception:
        pass
    return True  # data.json 不存在 → 首次启动 → 执行迁移（幂等）


if _should_migrate_txt_descriptions():
    _migrate_add_descriptions()


_FONT_CACHE = {}


def _load_fonts(*sizes, font_file=FONT_FILE):
    """按字号批量加载字体（带进程内缓存）。

    默认加载 OPPOSans（FONT_FILE）；传 font_file=TITLE_FONT_FILE 可加载标题衬线字体
    （思源宋体 Bold）。返回字号对应的字体元组；Pillow 缺失、字体文件缺失或加载失败时返回 None。
    """
    try:
        from PIL import ImageFont
    except Exception as e:
        logger.error(f"[插件] 缺少 Pillow，无法生成图片: {e}")
        return None
    if not os.path.exists(font_file):
        logger.error(f"[插件] 字体文件不存在: {font_file}")
        return None
    fonts = []
    for size in sizes:
        key = (font_file, size)
        font = _FONT_CACHE.get(key)
        if font is None:
            try:
                font = ImageFont.truetype(font_file, size)
            except Exception as e:
                logger.error(f"[插件] 加载字体 {font_file} 失败: {e}")
                return None
            _FONT_CACHE[key] = font
        fonts.append(font)
    return tuple(fonts)


# ================= 全局图片设计系统（温暖简约和风 · 与 WebUI style.css 同源） =================
# 2.1.1：统一所有响应图片的视觉语言——米白暖底 / 白卡片 / 深林绿主色 / 校徽金强调 /
# 米金描边 / 危险红语义色；标题一律用思源宋体 Bold（衬线，对齐 --font-display）。
DS_BG = (252, 252, 250)             # 页面背景 米白 #fcfcfa
DS_SURFACE = (255, 255, 255)        # 卡片/面板 白 #ffffff
DS_SURFACE_2 = (251, 250, 245)      # 次级表面 #fbfaf5
DS_TEXT = (34, 50, 42)              # 主文本 深林绿黑 #22322a
DS_TEXT_2 = (63, 74, 64)            # 次级文本 #3f4a40
DS_MUTED = (101, 113, 95)           # 说明/占位 #65715f
DS_BORDER = (230, 226, 210)         # 常规描边 米金 #e6e2d2
DS_BORDER_2 = (217, 211, 191)       # 强调描边 #d9d3bf
DS_ACCENT = (31, 95, 62)            # 深林绿 #1f5f3e
DS_ACCENT_STRONG = (22, 71, 44)     # 深林绿加深 #16472c
DS_GOLD = (214, 161, 26)            # 校徽金 #d6a11a
DS_GOLD_2 = (226, 176, 42)          # 校徽金亮 #e2b02a
DS_DANGER = (179, 57, 46)           # 危险红 #b3392e
DS_DANGER_STRONG = (143, 43, 34)    # 危险红加深 #8f2b22
DS_SUCCESS = (44, 122, 80)          # 成功绿 #2c7a50
DS_GREEN_SOFT = (128, 160, 110)     # 柔和鼠尾草绿（属性条/空闲进度填充）
DS_BLUE = (52, 88, 132)             # 提示蓝（深调）
# 标题字号档位（思源宋体 Bold）
DS_TITLE_SIZES = {"list": 42, "rich": 40, "rank": 42, "snapshot": 36, "pet": 36,
                  "shop": 36, "bag": 36, "farm": 36, "activity": 36, "work": 36}


def _title_font(size=None, kind="list"):
    """加载标题衬线字体（思源宋体 Bold，size 缺省按 kind 取档位）；思源宋体缺失时回退 OPPOSans。

    返回字体对象；两种字体均不可用时返回 None。
    """
    if size is None:
        size = int(DS_TITLE_SIZES.get(kind, 36))
    for ff in (TITLE_FONT_FILE, FONT_FILE):
        t = _load_fonts(size, font_file=ff)
        if t is not None:
            return t[0]
    return None


def _draw_underlined_title(d, xy, title, font, color=DS_ACCENT, width=None, gap=8, line_w=2):
    """标题 + 下方页头分隔线（对齐 WebUI .page-head：深林绿 2px 底边）。

    xy: 标题左上角；width: 分隔线宽度（缺省按 xy 起延伸）；gap: 标题底到分隔线间距。
    返回分隔线 y 坐标（供后续内容定位）。
    """
    x, y = xy
    d.text((int(x), int(y)), title, font=font, fill=color)
    ly = int(y) + int(font.size * 1.25) + gap
    d.line([(int(x), ly), (int(x) + (width if width is not None else 200), ly)],
           fill=color, width=line_w)
    return ly


def _text_measurer():
    """返回像素宽度测量函数 tw(text, font)；Pillow 不可用时返回 None"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def tw(s, font):
        return probe.textlength(s, font=font)

    return tw


# ================= 汉字排序工具（2.1.0：用户信息排序自定义） =================
# GB2312 一级汉字按拼音字母顺序排列，可用码位区间查拼音首字母（零依赖经典方案）。
# 区间取值自公开的 GB2312 一级汉字拼音字母分段表（I/U/V 无对应首字母，因为
# 普通话拼音不以 I/U/V 开头；ü 开头字归入 L/N 排列）。
_GB2312_PINYIN_RANGES = [
    ("A", 0xB0A1, 0xB0C4), ("B", 0xB0C5, 0xB2C0), ("C", 0xB2C1, 0xB4ED),
    ("D", 0xB4EE, 0xB6E9), ("E", 0xB6EA, 0xB7A1), ("F", 0xB7A2, 0xB8C0),
    ("G", 0xB8C1, 0xB9FD), ("H", 0xB9FE, 0xBBF6), ("J", 0xBBF7, 0xBFA5),
    ("K", 0xBFA6, 0xC0AB), ("L", 0xC0AC, 0xC2E7), ("M", 0xC2E8, 0xC4C2),
    ("N", 0xC4C3, 0xC5B5), ("O", 0xC5B6, 0xC5BD), ("P", 0xC5BE, 0xC6D9),
    ("Q", 0xC6DA, 0xC8BA), ("R", 0xC8BB, 0xC8F5), ("S", 0xC8F6, 0xCBF9),
    ("T", 0xCBFA, 0xCDD9), ("W", 0xCDDA, 0xCEF3), ("X", 0xCEF4, 0xD188),
    ("Y", 0xD189, 0xD4D0), ("Z", 0xD4D1, 0xD7F9),
]


def _hanzi_pinyin_initial(ch):
    """汉字 → 拼音首字母（大写 A-Z）；非 GB2312 一级汉字（如生僻字/非汉字）返回空串。"""
    if not ch or not ("\u4e00" <= ch <= "\u9fff"):
        return ""
    try:
        gb = ch.encode("gb2312")
    except Exception:
        return ""
    if len(gb) != 2:
        return ""
    code = (gb[0] << 8) + gb[1]
    for letter, lo, hi in _GB2312_PINYIN_RANGES:
        if lo <= code <= hi:
            return letter
    return ""


# 常用汉字笔画数表（2.1.0 排序自定义：昵称首字笔画）。覆盖常见姓氏与昵称常用字；
# 未收录的汉字在排序时作为「笔画未知」处理（排在该类别内部偏后，按拼音首字母稳定排序）。
_COMMON_STROKES = {
    "一": 1, "乙": 1, "三": 3, "上": 3, "下": 3, "不": 4, "中": 4, "为": 4, "主": 5, "之": 3,
    "义": 3, "云": 4, "五": 4, "人": 2, "天": 4, "小": 3, "山": 3, "工": 3, "平": 5, "强": 12,
    "心": 4, "永": 5, "白": 5, "百": 6, "石": 5, "福": 13, "秀": 7, "立": 5, "笑": 10, "红": 6,
    "亮": 9, "伟": 6, "华": 6, "广": 3, "建": 8, "明": 8, "星": 9, "晨": 11, "月": 4, "有": 6,
    "木": 4, "林": 8, "水": 4, "火": 4, "玉": 5, "王": 4, "田": 5, "男": 7, "女": 3, "安": 6,
    "宏": 7, "家": 10, "富": 12, "宝": 8, "小": 3, "文": 4, "新": 13, "方": 4, "日": 4, "早": 6,
    "旺": 8, "春": 9, "夏": 10, "秋": 9, "冬": 5, "可": 5, "爱": 10, "娟": 10, "婷": 12, "燕": 16,
    "鹏": 13, "龙": 5, "虎": 8, "凤": 4, "海": 10, "洋": 9, "波": 8, "涛": 10, "江": 6, "河": 8,
    "湖": 12, "山": 3, "石": 5, "花": 7, "草": 9, "树": 9, "松": 8, "柏": 9, "梅": 11, "兰": 5,
    "竹": 6, "菊": 11, "鸿": 11, "兴": 6, "旺": 8, "昌": 8, "盛": 11, "成": 6, "功": 5, "杰": 8,
    "俊": 9, "勇": 9, "刚": 6, "毅": 15, "超": 12, "越": 12, "飞": 3, "翔": 12, "玉": 5, "琪": 12,
    "璐": 17, "瑶": 14, "佩": 8, "珊": 9, "霞": 17, "丽": 7, "美": 9, "秀": 7, "英": 8, "莉": 10,
    "薇": 16, "梦": 11, "欣": 8, "悦": 10, "怡": 8, "慧": 15, "聪": 14, "灵": 8, "巧": 5, "君": 7,
    "俊": 9, "楷": 13, "轩": 7, "恒": 9, "志": 7, "诚": 8, "信": 9, "礼": 5, "义": 3, "仁": 4,
    "德": 15, "道": 12, "言": 7, "语": 9, "佳": 8, "娜": 9, "婷": 12, "云": 4, "宇": 6, "宙": 8,
    "宏": 7, "伟": 6, "东": 5, "西": 6, "南": 9, "北": 5, "风": 4, "雪": 11, "雨": 8, "雷": 13,
    "电": 5, "光": 6, "辉": 12, "耀": 20, "阳": 6, "阴": 6, "天": 4, "地": 6, "乾": 11, "坤": 8,
    "震": 15, "巽": 12, "离": 10, "兑": 7, "泰": 10, "丰": 4, "国": 8, "邦": 11, "民": 5, "众": 6,
    "群": 13, "团": 6, "队": 4, "日": 4, "时": 7, "辰": 7, "年": 6, "岁": 6, "世": 5, "界": 9,
    "宇": 6, "航": 10, "飞": 3, "机": 6, "车": 4, "马": 3, "牛": 4, "羊": 6, "猪": 11, "狗": 8,
    "猫": 11, "兔": 8, "鸡": 7, "鸭": 10, "鹅": 12, "鱼": 8, "虾": 9, "蟹": 19, "龟": 7,
    "龙": 5, "蛇": 11, "象": 12, "虎": 8, "狮": 9, "狼": 10, "熊": 14, "鹿": 11, "豹": 10,
}


def _hanzi_stroke(ch):
    """汉字 → 总笔画数（内置常用表）；未收录返回 0（排序时视为未知，拼音首字母稳定兜底）。"""
    if not ch:
        return 0
    return int(_COMMON_STROKES.get(ch, 0) or 0)


def _record_nick_sort_key(nick, mode):
    """用户信息排序键（2.1.0）：
    mode="pinyin"：昵称首字拼音首字母（中文 A-Z → 英文 A-Z → 数字 0-9 → 特殊字符计入 #）；
    mode="stroke"：昵称首字笔画（升序即 中文按笔画 → 英文 A-Z → 数字 0-9 → 特殊字符计入 #）。
    返回 (类别权重, 子键, 兜底串)；升序 = 中文(0) < 英文(1) < 数字(2) < 特殊(3)。"""
    s = str(nick or "").strip()
    ch = s[0] if s else ""
    if not ch:
        return (9, "", "")
    if "\u4e00" <= ch <= "\u9fff":  # 中文
        if mode == "stroke":
            st = _hanzi_stroke(ch)
            return (0, st, ch)
        py = _hanzi_pinyin_initial(ch)
        return (0, py, ch)
    if "A" <= ch <= "Z":
        return (1, ch, ch)
    if "a" <= ch <= "z":
        return (1, ch.upper(), ch)
    if "0" <= ch <= "9":
        return (2, ch, ch)
    return (3, "#", ch)  # 特殊字符系列算入 #


def _ensure_pillow():
    """尝试导入 Pillow 的 Image 和 ImageDraw；不可用时返回 (None, None)"""
    try:
        from PIL import Image, ImageDraw
        return Image, ImageDraw
    except Exception as e:
        logger.error(f"[插件] 缺少 Pillow，无法生成图片: {e}")
        return None, None


def _make_wrapper(tw, default_width, mode="fill"):
    """生成按像素宽度换行的函数：自适应填满最大可用宽度后才换行。

    策略（mode="fill"，默认）：优先在空格处断行（保持单词完整）；若空格断点会让
    当前行留下大片空白（还能再装入尾部词的一段），则把尾部词按「段」并入当前行
    直至满宽——段 = 连续数字/英文字母（永不拆分，如 100000）、单个中文字符。
    无空格时按字符硬切（同样满宽）。

    mode="word"：只在空格处换行，保持每个词（空格分隔的单元）完整整体换行，
    不填充、不拆分单元内部（用于商店卡片的效果描述，如「饱食+10 体力+5」）。"""

    if mode == "word":
        def wrap(text, font, max_w=None):
            limit = default_width if max_w is None else max_w
            lines = []
            for word in text.split(" "):
                if not word:
                    continue
                if not lines:
                    lines.append(word)
                elif tw(lines[-1] + " " + word, font) <= limit:
                    lines[-1] += " " + word
                else:
                    # 当前行放不下该单元 → 换行放置（正常场景单元很短，不会超宽）
                    if tw(word, font) > limit and lines:
                        # 极端：单个单元超过一行宽 → 按字符硬切
                        cur = ""
                        for ch in word:
                            if tw(cur + ch, font) > limit and cur:
                                lines.append(cur)
                                cur = ch
                            else:
                                cur += ch
                        if cur:
                            lines.append(cur)
                    else:
                        lines.append(word)
            return [s for s in (_clean_img_text(x) for x in lines) if s] or [""]

        return wrap

    def wrap(text, font, max_w=None):
        limit = default_width if max_w is None else max_w
        lines = []
        cur = ""
        for ch in text:
            if tw(cur + ch, font) <= limit:
                cur += ch
                continue
            # 超宽 → 换行
            sp = cur.rfind(" ")
            if sp > 0:
                tail = cur[sp + 1:]
                # 若还能把尾部词至少一段并入当前行，则逐段并入至满宽（词/数字保持完整）
                # 行1 前缀 = cur[:sp] + 断点空格 + 已并入的 tail 段
                if tail and tw(cur[:sp] + " " + tail[0], font) <= limit:
                    i = 0
                    n = len(tail)
                    while i < n:
                        if tail[i].isalnum() and tail[i].isascii():
                            j = i
                            while j < n and tail[j].isalnum() and tail[j].isascii():
                                j += 1
                            seg = tail[i:j]
                        else:
                            seg = tail[i]
                            j = i + 1
                        if tw(cur[:sp] + " " + tail[:i] + seg, font) > limit:
                            break
                        i = j
                    if i > 0:
                        lines.append(cur[:sp] + " " + tail[:i])
                        cur = tail[i:] + ch
                        continue
                lines.append(cur[:sp])
                cur = tail + ch
                continue
            # 无空格：字符级硬切（满宽）
            if cur:
                lines.append(cur)
            cur = ch
        if cur:
            lines.append(cur)
        return [s for s in (_clean_img_text(x) for x in lines) if s] or [""]

    return wrap


def _save_temp_image(img, prefix: str, kind: str):
    """保存渲染结果到数据目录并清理同前缀的过期图片。返回 ("image", path) 或 None"""
    base = os.path.dirname(DATA_FILE)
    path = os.path.join(base, f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png")
    try:
        img.save(path)
    except Exception as e:
        logger.error(f"[插件] 保存{kind}图片失败: {e}")
        return None
    try:
        now = datetime.now().timestamp()
        for fn in os.listdir(base):
            if fn.startswith(prefix) and fn.endswith(".png"):
                fp = os.path.join(base, fn)
                if now - os.path.getmtime(fp) > TEMP_IMAGE_TTL:
                    os.remove(fp)
    except Exception:
        pass
    return ("image", path)


# ============ 去 Emoji：响应图片不出现 Emoji（直接删除，不替换为标志） ============
# 渲染到图片里的文本一律经 _clean_img_text 处理：把各类 Emoji 代码点直接移除，
# 输出干净纯文本（不填充任何极简标志；OPPOSans 无 emoji 字形，避免豆腐块）。
_EMOJI_STRIP_RX = None


def _clean_img_text(text):
    """移除图片文本中的各类 Emoji（2.0.1 响应图片去 Emoji → 直接删除，保留纯文本）。"""
    if not text:
        return text
    global _EMOJI_STRIP_RX
    if _EMOJI_STRIP_RX is None:
        # 覆盖 Emoji、杂项符号、补充符号、部首/变体选择符等常见表情区
        _EMOJI_STRIP_RX = re.compile(
            "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2300-\u23FF]"
        )
    return _EMOJI_STRIP_RX.sub("", str(text))


def _dtext(d, xy, text, **kw):
    """绘制图片文本前统一移除 Emoji（2.0.1 响应图片去 Emoji）。"""
    return d.text(xy, _clean_img_text(text), **kw)


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


class CoreMixin:
    """通用方法：数据存取、金币流水、路由辅助、文本/富文本渲染等。
    从原 main.py 的 SignInPlugin 拆出，供各功能 Mixin 与入口类共享。"""

    @staticmethod
    def _expand_alias(data: dict, head: str) -> str:
        """同义口令展开：返回标准指令；未配置别名时原样返回（一步展开，不递归）"""
        aliases = data.get("alias_cmds") or {}
        target = aliases.get(head)
        return target if isinstance(target, str) and target else head

    # ================= 指令路由表（字典分发，替代 if-elif 链） =================
    # 无参数指令 → 方法名（不需要 event 参数的帮助类指令）
    def _load(self) -> dict:
        """读取数据。调试模式下返回内存缓存（调试期间的改动跨指令保留，但不写盘）"""
        if getattr(self, "_debug", False):
            if self._debug_data is None:
                self._debug_data = self._load_disk()
            return self._debug_data
        return self._load_disk()

    @staticmethod
    def _default_data() -> dict:
        """返回空白数据模板（每次调用返回新字典）"""
        return {"users": {}, "roulette": {}, "pets": {}, "bank": {}, "farms": {}, "loans": {},
                "ledger": {}, "redpackets": [], "activities": {}, "group_members": {}, "group_names": {},
                "alias_cmds": {**DEFAULT_ALIAS_CMDS}, LAN_DATA_KEY: {}, "settle_dates": {}}

    # 需要 setdefault 的字典键列表（与 _default_data 保持一致）
    _DATA_DICT_KEYS = ("users", "roulette", "pets", "bank", "farms", "loans",
                       "ledger", "activities", "activity_config", "params",
                       "group_members", "group_names", LAN_DATA_KEY, "settle_dates")

    def _load_disk(self) -> dict:
        if not os.path.exists(DATA_FILE):
            return self._default_data()
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return self._default_data()
            for k in self._DATA_DICT_KEYS:
                data.setdefault(k, {})
            data.setdefault("redpackets", [])
            # 同义口令：没有该键时写入默认同义词（用户可在 WebUI 编辑）
            if not isinstance(data.get("alias_cmds"), dict):
                data["alias_cmds"] = {**DEFAULT_ALIAS_CMDS}
            # 旧数据迁移：宠物等级按新经验体系重算（所需经验 = 当前等级 × 100）
            self._migrate_pet_levels(data)
            return data
        except Exception as e:
            logger.error(f"[插件] 读取数据失败: {e}")
            return self._default_data()

    def _migrate_pet_levels(self, data: dict) -> None:
        """按新经验体系重算所有宠物的等级（旧数据按经验总值匹配新等级体系）"""
        try:
            for pet in (data.get("pets") or {}).values():
                if not isinstance(pet, dict):
                    continue
                pet["level"] = min(PET_MAX_LEVEL, self._pet_level_from_exp(float(pet.get("exp", 0.0))))
        except Exception as e:
            logger.error(f"[插件] 宠物等级迁移失败: {e}")

    # ================= WebUI 运行参数 =================
    def _save(self, data: dict) -> None:
        # 调试模式：仅更新内存缓存（跨指令保留），不写盘；退出后回到开启前状态
        if getattr(self, "_debug", False):
            self._debug_data = data
            return
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

    def _custom_name_of(self, data: dict, key: str) -> str:
        """返回用户在有效期内的自定义昵称（2.0.3）；未设置或已过期返回空串。"""
        u = data.get("users", {}).get(key)
        if not u:
            return ""
        ts = u.get("custom_name_ts")
        if not ts:
            return ""
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            return ""
        if datetime.now().timestamp() - ts > float(globals().get("CUSTOM_NAME_TTL", 90 * 86400)):
            return ""
        return str(u.get("custom_name") or "").strip()

    def _handle_change_name(self, event: AstrMessageEvent) -> str:
        """修改昵称 <任意字符>：设置自定义昵称（2.0.3），有效期 90 天，优先级高于获取的昵称"""
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return f"{name} 请指定要设置的昵称：修改昵称 <任意字符>（如：修改昵称 小明）"
        custom = parts[1].strip()
        max_len = int(globals().get("CUSTOM_NAME_MAX_LEN", 50))
        if len(custom) > max_len:
            return f"昵称过长（最多 {max_len} 个字符）。"
        data = self._load()
        u = self._ensure_user(data, key)
        u["custom_name"] = custom
        u["custom_name_ts"] = datetime.now().timestamp()
        self._save(data)
        ttl_days = int(float(globals().get("CUSTOM_NAME_TTL", 90 * 86400)) // 86400)
        return (f"✅ {name} 已设置自定义昵称为「{custom}」（有效期 {ttl_days} 天，"
                f"优先级高于获取的昵称，后续响应将使用该昵称）。")

    @staticmethod
    def _level_of(favorability: float) -> int:
        return min(MAX_LEVEL, int(favorability // LEVEL_STEP))

    def _ensure_user(self, data: dict, key: str) -> dict:
        return data.setdefault("users", {}).setdefault(key, {"coins": 0, "favorability": 0.0})

    def _coins_of(self, data: dict, key: str) -> int:
        v = data.get("users", {}).get(key, {}).get("coins")
        return int(v) if isinstance(v, (int, float)) else 0

    def _rank_score_coins(self, data: dict, key: str) -> int:
        """金币排行积分（与金币排行榜一致）：金币 × 权重 + 存款本金 × 权重"""
        cw = float(globals().get("RANK_COIN_COIN_W", 1.0))
        bw = float(globals().get("RANK_COIN_BANK_W", 1.0))
        bank = data.get("bank", {}).get(key)
        dep = sum(self._dep_amount(d) for d in (bank.get("deposits", []) if isinstance(bank, dict) else []))
        return int(self._coins_of(data, key) * cw + dep * bw)

    def _ensure_bag_base(self, data: dict, key: str):
        """背包「今日净收益」的零点基线（金币排行积分）。
        积分只在金币/存款变动时变化，因此在「当日第一笔金币变动前」或「当日首次查看背包时」
        记录的积分即等于当日零点的积分。返回 (基线值, 是否新建基线)。"""
        user = self._ensure_user(data, key)
        today = date.today().isoformat()
        base = user.get("bag_base") or {}
        if base.get("date") == today:
            return int(base.get("score", 0)), False
        score = self._rank_score_coins(data, key)
        user["bag_base"] = {"date": today, "score": score}
        return score, True

    def _add_coins(self, data: dict, key: str, amount: int, reason: str = "") -> int:
        """增加/扣除金币并记录流水（只有 reason 非空且金额变动才记）。amount 正为获得、负为消费。返回变动后的余额。
        有逾期贷款时，获得金币自动划扣 20% 还款（划扣部分是还贷，不重复记流水）。"""
        # 1.7.8：当日第一笔金币变动前先记录背包净收益零点基线（保证红包/利息等全部计入）
        self._ensure_bag_base(data, key)
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
        new_bal = max(0, int(cur) + amount)
        user["coins"] = new_bal
        if amount != 0 and reason:
            self._log_ledger(data, key, amount, reason, new_bal)
        return new_bal

    def _log_ledger(self, data: dict, key: str, amount: int, reason: str, balance: int) -> None:
        """记录一条金币流水（只记发生金额变动的操作），最多保留 200 条"""
        ledger = data.setdefault("ledger", {}).setdefault(key, [])
        ledger.append({
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
            "delta": amount,
            "balance": balance,
        })
        if len(ledger) > 200:
            del ledger[: len(ledger) - 200]

    def _coin_line(self, data: dict, key: str) -> str:
        """金币余额提示行（消费/获得金币类回复末尾附加）"""
        return f"💰 当前金币：{self._coins_of(data, key)}"

    def _pet_state_snippet(self, pet: dict) -> str:
        """宠物当前状态摘要（打工/玩耍/使用道具反馈末尾附加）。
        1.7.6：状态低判定采用第三档标准——饱食 <50 或 口渴 <60 或 心情 <40 时附加红色提示。"""
        sat_max, thr_max, sta_max, mood_max = self._attr_max(pet["health"])
        weak = "，😷 虚弱（发送「治疗宠物」）" if pet.get("weak") else ""
        line = (f"🐾 {pet.get('name', '宠物')}：饱食 {pet['satiety']:.0f}/{sat_max:.0f}，"
                f"口渴 {pet['thirst']:.0f}/{thr_max:.0f}，体力 {pet['stamina']:.0f}/{sta_max:.0f}，"
                f"心情 {pet['mood']:.0f}/{mood_max:.0f}，健康 {pet['health']:.0f}/{PET_MAX_HEALTH:.0f}{weak}")
        # 第三档标准判定状态低
        lows = []
        if pet["satiety"] < 50:
            lows.append("饿了")
        if pet["thirst"] < 60:
            lows.append("渴了")
        if pet["mood"] < 40:
            lows.append("不开心")
        if lows:
            line += f"（{'、'.join(lows)}，状态低！）"
        return line

    @staticmethod
    def _pet_busy_until(pet: dict) -> float:
        """打工/玩耍共用冷却计时器：返回忙碌结束时间戳（兼容旧数据 work_until / play_until）"""
        busy = float(pet.get("busy_until", 0) or 0)
        old = max(float(pet.get("work_until", 0) or 0), float(pet.get("play_until", 0) or 0))
        return max(busy, old)

    def _farm_state_snippet(self, farm: dict) -> str:
        """农场当前状态摘要（农场变更反馈末尾附加）"""
        wh = farm.get("warehouse", {})
        n_plot = len(farm.get("plots", []))
        n_crop = sum(int(v) for v in wh.get("crops", {}).values())
        n_seed = sum(int(v) for v in wh.get("seeds", {}).values())
        n_fert = sum(int(v) for v in wh.get("fertilizers", {}).values())
        return (f"🌾 农场 Lv.{farm.get('level', 0)}｜土地 {n_plot} 块｜"
                f"仓库：作物 {n_crop} / 种子 {n_seed} / 肥料 {n_fert}")

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

    @staticmethod
    def _image_text_or_plain(img, text):
        """若 img 是有效的 ("image", path) 元组，返回 ("image_text", text, path)；否则返回纯文本"""
        if img is not None and isinstance(img, tuple) and img[0] == "image":
            return ("image_text", text, img[1])
        return text

    # ================= 签到 =================
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
        """把标题 + 正文行渲染为 PNG 图片。标题用思源宋体 Bold + 页头分隔线，
        正文用 OPPOSans（温暖简约和风 · 米白暖底）。返回 ("image", path)；失败返回 None"""
        Image, ImageDraw = _ensure_pillow()
        if Image is None:
            return None
        # 字号语义：标题 42（衬线）/ 正文 24
        title_font = _title_font(kind="list")
        fonts = _load_fonts(24)
        if title_font is None or fonts is None:
            return None
        body_font = fonts[0]

        pad = 30
        title_h = 80
        line_h = 42

        tw = _text_measurer()
        if tw is None:
            width = 720
        else:
            all_w = [tw(title, title_font)] + [tw(l, body_font) for l in lines]
            width = max(480, int(max(all_w) + pad * 2))

        height = pad * 2 + title_h + line_h * len(lines)
        img = Image.new("RGB", (width, height), DS_BG)
        draw = ImageDraw.Draw(img)
        y = pad
        _draw_underlined_title(draw, (pad, y), title, title_font, color=DS_ACCENT,
                               width=width - pad * 2, gap=10)
        y += title_h
        for line in lines:
            draw.text((pad, y), line, font=body_font, fill=DS_TEXT_2)
            y += line_h

        return _save_temp_image(img, "_list_", "")

    def _render_snapshot_image(self, title, modules, width=900, pad=20, title_h=64, line_h=30, mod_gap=14,
                               col_gap=12, header_right=None):
        """2.1.1 签到实时数据快照：信息流瀑布平铺渲染。
        modules: 每项为一张卡片 (模块标题, 内容行列表, 高亮?) 或一行并排的多张卡片
                 [卡片1, 卡片2, ...]（如 签到信息|好感度信息、银行与征信|排行榜信息）。
        并排行末尾可带列宽权重元组 [卡1, 卡2, (w1, w2)]，各行比例可不同；
        卡片内容行支持：
          - (文本, 颜色[, 边框高亮色])：普通文本行；
          - ("__bar__", 比例0~1, 填充色, 标签文本)：矩形边框+百分比填充的进度条行。
        整体加宽至 900，保证并排卡片（排行榜信息等）内容单行不换行；
        并排行内卡片高度取最高者，较矮卡片（如 好感度信息）内容在行内垂直居中，
        利用同排较高卡片（如 签到信息）右侧的空白部分；
        文本含「｜」时按「｜」分段整体换行（宠物属性/自动化等不会被切开）；
        图片总高度 = 各行卡片高度之和 + 间距（避免文字溢出）；卡片高度计入上下内边距
        （inner*2），内容最后一行与卡片底部线条不重叠；高亮规范：高亮不变动卡片填充
        颜色，只改变 边框颜色 + 字体颜色。
        header_right: (文本, 颜色) 时显示在标题行右侧（右上角，如 金币数量）。
        返回 ("image", path)；失败返回 None。"""
        Image, ImageDraw = _ensure_pillow()
        if Image is None:
            return None
        # 标题用思源宋体 Bold（衬线，对齐 WebUI --font-display），正文/模块标题用 OPPOSans；
        # 思源宋体缺失时回退 OPPOSans（保证图片仍可渲染）
        fonts = _load_fonts(24, 20, 18)
        if fonts is None:
            return None
        mod_font, body_font, small_font = fonts
        title_font = _title_font(kind="snapshot")
        if title_font is None:
            return None
        tw = _text_measurer()
        if tw is None:
            return None
        content_w = width - pad * 2
        inner = 10
        mod_title_h = 34
        bar_h = 16            # 矩形进度条高度
        wrap = _make_wrapper(tw, content_w - inner * 2)
        # 全局图片设计系统（温暖简约和风 · 与 WebUI style.css 一致）：
        # 背景米白 / 卡片白 / 深林绿主色 / 校徽金强调 / 米金描边 / 危险红语义色
        BG = DS_BG                    # 页面背景 米白暖色
        HL = DS_GOLD                  # 高亮边框/强调金
        NORMAL = DS_BORDER            # 常规描边米金
        TITLE_DARK = DS_ACCENT        # 标题深林绿
        MOD_TITLE = DS_TEXT           # 模块标题深林绿黑
        TEXT = DS_TEXT_2              # 正文
        MUTED = DS_MUTED              # 说明/次级
        # 标题下划线到首行卡片的间距（卡片整体下移量）
        TITLE_UNDERLINE_GAP = 12

        # 规范化：modules → 行列表，每行 = [卡片, ...]（单卡行含一张）
        # 并排行末尾可带列宽权重元组，如 [卡1, 卡2, (0.42, 0.58)]（各行比例可不同）
        lines = []
        for m in modules:
            if isinstance(m, (list, tuple)) and m and isinstance(m[0], (list, tuple)):
                row = list(m)
                weights = None
                if row and isinstance(row[-1], tuple) and len(row[-1]) == len(row) - 1 \
                        and all(isinstance(w, (int, float)) for w in row[-1]):
                    weights = row.pop()          # 末位权重元组
                lines.append((row, weights))     # 已是并排行 [卡片1, 卡片2, ...]
            else:
                lines.append(([m], None))        # 单卡行

        # 每行列宽：默认并排等宽；带权重元组时按比例分配（行内等高底部对齐）
        col_widths = []
        for row, weights in lines:
            n = len(row)
            avail = content_w - col_gap * (n - 1)
            if weights and len(weights) == n:
                total_w = float(sum(weights))
                ws = []
                used = 0
                for i, w in enumerate(weights):
                    if i == n - 1:
                        ws.append(avail - used)
                    else:
                        cw = int(avail * w / total_w)
                        ws.append(cw)
                        used += cw
                col_widths.append(ws)
            else:
                col_widths.append([avail // n] * n)

        def wrap_pipe(text, font, max_w):
            """含「｜」→ 按「｜」分段整体换行（段内不切开）；否则字符级 fill 换行"""
            if "｜" not in text:
                return [wl for wl in wrap(text, font, max_w)]
            segs = text.split("｜")
            out = []
            cur = ""
            for seg in segs:
                piece = ("｜" + seg) if cur else seg
                if tw(cur + piece, font) <= max_w:
                    cur += piece
                    continue
                if cur:
                    out.append(cur)
                if tw(seg, font) > max_w:
                    cur = ""
                    for ch in seg:
                        if tw(cur + ch, font) <= max_w and cur:
                            cur += ch
                        else:
                            if cur:
                                out.append(cur)
                            cur = ch
                else:
                    cur = seg
            if cur:
                out.append(cur)
            return out

        # ---- 第一遍：计算每行卡片高度（行内等高） ----
        boxes = []  # 每行: (line_h_max, [(h, mod_rows), ...])
        total_h = pad * 2 + title_h + TITLE_UNDERLINE_GAP
        for (row, weights), col_ws in zip(lines, col_widths):
            line_plan = []
            line_h_max = 0
            for (mtitle, rows, hl), col_w in zip(row, col_ws):
                inner_w = col_w - inner * 2
                mod_rows = []
                # 卡片高度 = 上下内边距 + 模块标题 + 内容行（与 _render_activity_image 等
                # 卡片渲染一致计入 inner*2；否则内容从 y+inner 起绘，最后一行会压住卡片底部边框）
                h = inner * 2 + mod_title_h
                for item in rows:
                    if isinstance(item, (list, tuple)) and len(item) >= 2 and item[0] == "__bar__":
                        ratio = max(0.0, min(1.0, float(item[1])))
                        color = item[2] if len(item) > 2 else (52, 168, 83)
                        label = str(item[3]) if len(item) > 3 else ""
                        mod_rows.append(("__bar__", ratio, color, label))
                        h += line_h
                        continue
                    text, color = item[0], item[1]
                    hl_color = item[2] if len(item) > 2 else (HL if hl else NORMAL)
                    font = body_font
                    if tw(text, body_font) > inner_w:
                        for wl in wrap_pipe(text, body_font, inner_w):
                            mod_rows.append((wl, color, font, hl_color))
                            h += line_h
                    else:
                        mod_rows.append((text, color, font, hl_color))
                        h += line_h
                line_plan.append((h, mod_rows))
                line_h_max = max(line_h_max, h)
            boxes.append((line_h_max, line_plan))
            total_h += line_h_max + mod_gap
        total_h += pad

        img = Image.new("RGB", (width, total_h), BG)
        d = ImageDraw.Draw(img)
        y = pad
        # 标题（右上角 header_right：如 金币数量）+ 页头分隔线（对齐 WebUI .page-head）
        _dtext(d, (pad, y), title, font=title_font, fill=TITLE_DARK)
        if header_right:
            rt_text, rt_color = header_right
            _dtext(d, (int(width - pad - tw(rt_text, small_font)), y + (title_h - 20) // 2),
                   rt_text, font=small_font, fill=rt_color)
        y += title_h - 6
        d.line([(pad, y), (width - pad, y)], fill=TITLE_DARK, width=2)
        y += 6 + TITLE_UNDERLINE_GAP
        for (row, weights), col_ws, (line_h_max, line_plan) in zip(lines, col_widths, boxes):
            for ci, ((mtitle, rows, hl), (h, mod_rows)) in enumerate(zip(row, line_plan)):
                x0 = pad + sum(col_ws[:ci]) + col_gap * ci
                x1 = x0 + col_ws[ci]
                # 卡片：白底填充，边框色按高亮（金/米金）变化；行内等高 → 底边对齐
                border = HL if hl else NORMAL
                d.rectangle([x0, y, x1, y + line_h_max], fill=(255, 255, 255),
                            outline=border, width=(2 if hl else 1))
                # 较矮卡片（如 好感度信息）内容垂直居中，利用同行较高卡片右侧空白
                yy = y + (line_h_max - h) // 2 + inner
                _dtext(d, (x0 + inner, yy), mtitle, font=mod_font, fill=MOD_TITLE)
                yy += mod_title_h
                for item in mod_rows:
                    if item[0] == "__bar__":
                        ratio, color, label = item[1], item[2], item[3]
                        bar_y = yy + (line_h - bar_h) // 2
                        bx0, bx1 = x0 + inner, x1 - inner
                        d.rectangle([bx0, bar_y, bx1, bar_y + bar_h], fill=(255, 255, 255),
                                    outline=NORMAL, width=1)
                        fw = int((bx1 - bx0 - 2) * ratio)
                        if fw > 0:
                            d.rectangle([bx0 + 1, bar_y + 1, bx0 + 1 + fw, bar_y + bar_h - 1], fill=color)
                        if label:
                            _dtext(d, (int(bx1 - tw(label, small_font)), bar_y - 4),
                                   label, font=small_font, fill=color)
                        yy += line_h
                    else:
                        text, color, font, hl_color = item
                        # 高亮规范：高亮只改边框色 + 字体颜色（行内已带色）；无高亮行保持原色
                        _dtext(d, (x0 + inner, yy), text, font=font, fill=color)
                        yy += line_h
            y += line_h_max + mod_gap
        return _save_temp_image(img, "_snap_", "数据快照")

    def _render_rich_image(self, title, rows):
        """rows: 每行是 (text, color, strike) 元组列表。返回 ('image', path) 或 None"""
        Image, ImageDraw = _ensure_pillow()
        if Image is None:
            return None
        # 字号语义：标题 40（衬线）/ 正文 24
        title_font = _title_font(kind="rich")
        fonts = _load_fonts(24)
        if title_font is None or fonts is None:
            return None
        body_font = fonts[0]
        pad = 26
        title_h = 76
        line_h = 40
        sw = _text_measurer()
        if sw is None:
            return None

        max_w = sw(title, title_font)
        for r in rows:
            max_w = max(max_w, sum(sw(s[0], body_font) for s in r))
        width = max(460, int(max_w + pad * 2))
        height = pad * 2 + title_h + line_h * len(rows)

        img = Image.new("RGB", (width, height), DS_BG)
        d = ImageDraw.Draw(img)
        _draw_underlined_title(d, (pad, pad), title, title_font, color=DS_ACCENT,
                               width=width - pad * 2, gap=10)
        y = pad + title_h
        for r in rows:
            x = pad
            for seg in r:
                text, color, strike = seg
                # 坐标必须转 int（Pillow 对非整 float 报 TypeError）
                _dtext(d, (int(x), y), text, font=body_font, fill=color)
                if strike:
                    bb = d.textbbox((int(x), y), text, font=body_font)
                    midy = (int(bb[1]) + int(bb[3])) // 2
                    d.line([(int(bb[0]), midy), (int(bb[2]), midy)], fill=color, width=2)
                x += sw(text, body_font)
            y += line_h

        return _save_temp_image(img, "_farm_", "农场")

    def _render_help(self, title, sections):
        """把帮助菜单渲染成图片。sections: [(小标题, [(指令, 说明), ...]), ...]"""
        rows = []
        for header, items in sections:
            if header:
                rows.append([(header, DS_MUTED, False)])
            for cmd, desc in items:
                rows.append([(cmd, DS_TEXT, False), (f"  {desc}", DS_MUTED, False)])
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


# ================= 运行时参数跨模块同步 =================
# WebUI「运行参数」保存后会更新对应常量。因各功能模块通过 `from .base import *`
# 复制了常量名，必须在所有已注册模块的 globals 中同步更新，才能让
# `globals().get(...)` 与裸名读取立即生效（无需重启）。
_RUNTIME_PARAM_MODULES = []


def _register_runtime_module(mod):
    """功能模块导入时注册自身 module 对象（参与运行时参数同步）"""
    if mod not in _RUNTIME_PARAM_MODULES:
        _RUNTIME_PARAM_MODULES.append(mod)


def _sync_runtime_global(key, val):
    """把运行参数常量同步写入所有已注册模块（含 main.py / base.py）"""
    for _m in list(_RUNTIME_PARAM_MODULES):
        try:
            setattr(_m, key, val)
        except Exception:
            pass


__all__ = [
    "asyncio",
    "base64",
    "hashlib",
    "hmac",
    "ipaddress",
    "json",
    "math",
    "os",
    "random",
    "secrets",
    "shutil",
    "socket",
    "date",
    "timedelta",
    "datetime",
    "time",
    "filter",
    "AstrMessageEvent",
    "EventMessageType",
    "Context",
    "Star",
    "register",
    "logger",
    "error_response",
    "json_response",
    "request",
    "LAN_DATA_KEY",
    "LAN_COOKIE",
    "LAN_SESSION_HOURS",
    "LAN_MAX_RECORDS",
    "_LAN_SALT_LEN",
    "_lan_hash_password",
    "_lan_verify_password",
    "_is_loopback",
    "_lan_ip_in_blacklist",
    "_enumerate_lan_ipv4",
    "_build_text_image_chain",
    "_resolve_file_value",
    "_chain_to_onebot_segments",
    "_send_with_mid",
    "MIN_COINS",
    "MAX_COINS",
    "MIN_FAV",
    "MAX_FAV",
    "MAX_LEVEL",
    "LEVEL_STEP",
    "ROULETTE_MAGAZINES",
    "ROULETTE_MAX_BULLETS",
    "ROULETTE_MIN_PLAYERS",
    "ROULETTE_MAX_PLAYERS",
    "ROULETTE_JOIN_TIMEOUT",
    "ROULETTE_FEE_RATE",
    "PET_UNLOCK_COST",
    "PET_MAX_LEVEL",
    "PET_EXP_PER_LEVEL",
    "PET_MAX_HEALTH",
    "PET_SIGNIN_EXP_MIN",
    "PET_SIGNIN_EXP_MAX",
    "PILL_NAME",
    "EXP_BALL_NAME",
    "ITEM_TO_COIN",
    "PILL_DROP_CHANCE",
    "PILL_DROP_MIN",
    "PILL_DROP_MAX",
    "SIGNIN_NO_REWARD_CHANCE",
    "SIGNIN_PILL_CHANCE",
    "SIGNIN_BALL_CHANCE",
    "PILL_DAILY_LIMIT",
    "EXP_BALL_DAILY_LIMIT",
    "PILL_ATTR_COUNT",
    "PILL_BOOST_MIN",
    "PILL_BOOST_MAX",
    "EXP_BALL_MIN_PCT",
    "EXP_BALL_MAX_PCT",
    "FARM_PLOT_COLS",
    "SHOP_CARD_COLS",
    "SHOP_PRICE_PAD",
    "BAG_CARD_COLS",
    "FARM_SHOP_COLS",
    "FARM_SHOP_SHOW_BUY",
    "FARM_SHOP_SHOW_LOCKED",
    "WORK_SHOW_DOABLE",
    "WORK_SHOW_LOCKED",
    "PLAY_SHOW_DOABLE",
    "PLAY_SHOW_LOCKED",
    "PET_ATTR_MAX_RANGES",
    "PET_SETTLE_HEALTH_EXCHANGE",
    "AUTO_FEED_ENABLED",
    "AUTO_FEED_PRICE_MULT",
    "AUTO_FEED_LOG_MAX",
    "AUTO_PURCHASE_COOLDOWN_MIN",
    "AUTO_WORK_ENABLED",
    "AUTO_WORK_DELAY_MIN",
    "AUTO_WORK_PAUSE_BASE",
    "AUTO_WORK_LOG_MAX",
    "AUTO_WORK_EXP_ENABLED",
    "AUTO_WORK_EXP_MULT",
    "DAILY_SETTLE_HOUR",
    "BANK_SETTLE_HOUR",
    "DAILY_SETTLE_LOOP_INTERVAL",
    "AUTO_BUY_SHORT_ENABLED",
    "AUTO_BUY_SHORT_MULT",
    "SHOP_PRICE_FLOAT_ENABLED",
    "SHOP_PRICE_REFRESH_HOURS",
    "SHOP_PRICE_SPECIAL_HOURS",
    "SHOP_PRICE_DISCOUNT_MIN",
    "SHOP_PRICE_DISCOUNT_MAX",
    "SHOP_PRICE_DISCOUNT_LO",
    "SHOP_PRICE_DISCOUNT_HI",
    "SHOP_PRICE_RECORD_MAX",
    "SEED_DISCOUNT_ENABLED",
    "SEED_DISCOUNT_CHANCE",
    "SEED_DISCOUNT_MIN",
    "SEED_DISCOUNT_MAX",
    "SEED_DISCOUNT_PCT",
    "MONEY_EVENT_CHANCE",
    "MONEY_EVENT_GAIN",
    "MONEY_EVENT_MAX_PER_DAY",
    "WEAK_HEAL_COST",
    "_PET_WEAK_LOCKED_HEADS",
    "CMD_HEADS",
    "DEFAULT_ALIAS_CMDS",
    "FARM_UNLOCK_COST",
    "FARM_PLOT_COST",
    "FARM_FREE_PLOTS",
    "FARM_MAX_PLOTS",
    "FARM_MAX_LEVEL",
    "FARM_EXP_BASE",
    "FARM_GRADES",
    "FARM_UPGRADE_COSTS",
    "LOAN_SPECIAL_AMOUNT",
    "LOAN_SPECIAL_RATE",
    "LOAN_SPECIAL_DAYS",
    "LOAN_SPECIAL_TAKE",
    "LOAN_COIN_DEDUCT",
    "LOAN_FAV_DROP_SPECIAL",
    "LOAN_FAV_DROP_NORMAL",
    "LOAN_OVERDUE_YEAR_LIMIT",
    "LOAN_GENERAL_OVERDUE_DAYS",
    "LOAN_SHORT_GRACE_DAYS",
    "LOAN_SHORT_RATE",
    "LOAN_DAILY_MULT",
    "LOAN_FARM_ROLLBACK_DAYS",
    "LOAN_AUTO_TIME",
    "IMAGE_COMMANDS",
    "RECALL_AFTER",
    "RECALL_ENABLED",
    "DEBUG_PASSWORD",
    "LEDGER_SHOW",
    "REDPACKET_DAILY_LIMIT",
    "REDPACKET_TTL",
    "RAIN_AMOUNT",
    "RAIN_COUNT",
    "RAIN_TIMES",
    "RAIN_HOURS",
    "AUTO_STEAL_DAILY_LIMIT",
    "AUTO_STEAL_TARGETS",
    "RANK_DISPLAY",
    "RANK_HIGHLIGHT_COLOR",
    "RANK_TEXT_COLOR",
    "RANK_MASKED_COLOR",
    "RANK_SEP_COLOR",
    "RANK_IMAGE_SCALE",
    "RANK_BAR_LIGHTEN",
    "RANK_NAME_MAX_CHARS",
    "RANK_BAR_GAP_LEFT_MULT",
    "RANK_BAR_GAP_RIGHT_MULT",
    "RANK_ROW_SEP_PCT",
    "GROUP_MEMBER_TTL_HOURS",
    "RANK_KINDS",
    "RANK_COIN_COIN_W",
    "RANK_COIN_BANK_W",
    "RANK_PET_EXP_W",
    "RANK_PET_HEALTH_W",
    "RANK_PET_ATTR_W",
    "RANK_FARM_EXP_W",
    "RANK_FARM_PLOT_W",
    "RANK_FARM_GRADE_W",
    "RANK_PLOT_SCORES",
    "RUNTIME_PARAMS",
    "PARAM_KEYS",
    "RECALL_EXEMPT",
    "PLUGIN_NAME",
    "_PLUGIN_DIR",
    "DATA_FILE",
    "CONFIG_FILE",
    "PET_SHOP_FILE",
    "CROP_FILE",
    "FERT_FILE",
    "LOAN_FILE",
    "ITEMS_JSON_FILE",
    "FONT_FILE",
    "PET_SHOP_TYPE_FILES",
    "PET_SHOP_TYPES",
    "_migrate_old_data_files",
    "_migrate_split_shop_config",
    "_migrate_split_petshop_types",
    "_load_activity_modules",
    "ATTR_LABELS",
    "ATTR_SHORT",
    "TEMP_IMAGE_TTL",
    "_farm_grades",
    "_parse_kv_sections",
    "_parse_kv_sections_text",
    "_parse_kv_text",
    "BENCH_ITEMS_FILE",
    "_ITEMS_KEYS",
    "_load_benchmark_items",
    "_benchmark_desc_map",
    "_migrate_desc_file",
    "_migrate_txt_migrated_flag",
    "_migrate_add_descriptions",
    "_should_migrate_txt_descriptions",
    "_FONT_CACHE",
    "_load_fonts",
    "_text_measurer",
    "_ensure_pillow",
    "_make_wrapper",
    "_save_temp_image",
    "_parse_item_qty",
    "get_astrbot_plugin_data_path",
    "AiocqhttpMessageEvent",
    "_Plain",
    "_Image",
    "_MessageChain",
    "_clean_img_text",
    "_dtext",
    # 2.1.0：汉字排序工具（用户信息排序自定义）
    "_GB2312_PINYIN_RANGES",
    "_hanzi_pinyin_initial",
    "_COMMON_STROKES",
    "_hanzi_stroke",
    "_record_nick_sort_key",
    # 2.1.1：全局图片设计系统（温暖简约和风，与 WebUI style.css 同源）
    "TITLE_FONT_FILE",
    "DS_BG", "DS_SURFACE", "DS_SURFACE_2", "DS_TEXT", "DS_TEXT_2", "DS_MUTED",
    "DS_BORDER", "DS_BORDER_2", "DS_ACCENT", "DS_ACCENT_STRONG", "DS_GOLD",
    "DS_GOLD_2", "DS_DANGER", "DS_DANGER_STRONG", "DS_SUCCESS", "DS_GREEN_SOFT", "DS_BLUE",
    "DS_TITLE_SIZES", "_title_font", "_draw_underlined_title",
]
