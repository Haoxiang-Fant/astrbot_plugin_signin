# -*- coding: utf-8 -*-
"""核心框架（3.0.0 万物皆插件骨架）。

职责（核心框架自我处理）：
  1. 插件挂载：把 plugins/ 下的功能插件挂到本框架（第三方插件经 thirdparty 插件挂载）；
  2. 指令处理：同义口令展开 → 用户鉴权（管理员/功能开关/聊天三态权限）→ 分发到插件处理器；
  3. 用户识别：用户 key、自定义昵称、聊天登记、群成员登记、最后活跃；
  4. 金币变化：唯一的金币出入口（自动记流水 + 发联动事件供银行贷款等插件挂钩）；
  5. 数据管理：data.json（含各插件命名空间）+ game_items.json（数值配置）+ 运行参数；
  6. 插件间通信：core.expose / core.service（接口调用）、core.on / core.emit（联动事件）；
  7. 定时任务：统一巡检循环承载各插件的固定结算（幂等每日任务 + 周期任务）。

回复协议（各插件统一返回值）：
  str                      → 纯文本（部分指令按 IMAGE_COMMANDS 自动转图片）
  ("image", path)          → 图片
  ("image_text", 文本, path) → 文本 + 图片
  None                     → 不回复
"""
import asyncio
import json
import os
import random
from datetime import date, datetime, time, timedelta

from astrbot.api import logger

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
except Exception:
    get_astrbot_plugin_data_path = None

VERSION = "3.0.0"
PLUGIN_NAME = "astrbot_plugin_signin3"
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# 插件数据目录：AstrBot 要求存放在 data/plugin_data/ 下。
# 使用 2.x 同名目录 astrbot_plugin_signin（非本插件注册名），2.x 老用户升级后数据原地可用。
_DATA_DIR_NAME = "astrbot_plugin_signin"

# ============ 3.0 数据布局（全部位于 _DATA_DIR 下） ============
# 用户数据：每用户一个文件（user_data/<uid>.json，内含 user/pet/farm/bank/loans/roulette/ledger）
# 设置与参数：单独文件（settings.json / params.json / shop_prices.json）
# 群共享运行数据：runtime.json；备份配置：backup_data/
if get_astrbot_plugin_data_path is not None:
    try:
        _DATA_DIR = os.path.join(get_astrbot_plugin_data_path(), _DATA_DIR_NAME)
        os.makedirs(_DATA_DIR, exist_ok=True)
    except Exception:
        _DATA_DIR = _PLUGIN_DIR
else:
    _DATA_DIR = _PLUGIN_DIR

USER_DATA_DIR = os.path.join(_DATA_DIR, "user_data")
BACKUP_DATA_DIR = os.path.join(_DATA_DIR, "backup_data")
TEMP_IMAGE_DIR = os.path.join(_DATA_DIR, "temp_images")  # 响应图片临时目录（TTL 自清理）
SYSTEM_FILE = os.path.join(_DATA_DIR, "runtime.json")
SETTINGS_FILE = os.path.join(_DATA_DIR, "settings.json")
PARAMS_FILE = os.path.join(_DATA_DIR, "params.json")
SHOP_PRICES_FILE = os.path.join(_DATA_DIR, "shop_prices.json")


def _safe_uid(key):
    """用户 key → 安全文件名（平台 id 通常为数字/字母，防御性替换特殊字符）"""
    import re
    return re.sub(r"[^\w.-]", "_", str(key)) or "unknown"


def _read_json(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"[核心] 读取 {os.path.basename(path)} 失败: {e}")
    return None


def _write_json(path, obj):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.error(f"[核心] 写入 {os.path.basename(path)} 失败: {e}")
        return False

DATA_FILE = os.path.join(_DATA_DIR, "data.json")
RECORDS_FILE = os.path.join(_DATA_DIR, "records.json")
CONFIG_FILE = os.path.join(_DATA_DIR, "后台.txt")
PET_SHOP_FILE = os.path.join(_DATA_DIR, "宠物商店.txt")
CROP_FILE = os.path.join(_DATA_DIR, "作物.txt")
FERT_FILE = os.path.join(_DATA_DIR, "肥料.txt")
LOAN_FILE = os.path.join(_DATA_DIR, "贷款套餐.txt")
ITEMS_JSON_FILE = os.path.join(_DATA_DIR, "game_items.json")
DRAFT_FILE = os.path.join(_DATA_DIR, "config_draft.json")
HISTORY_DIR = BACKUP_DATA_DIR  # 历史回溯版本文件存于 backup_data/
FONT_FILE = os.path.join(_PLUGIN_DIR, "OPPOSans-M.ttf")
TITLE_FONT_FILE = os.path.join(_PLUGIN_DIR, "SourceHanSerifCN-Bold.otf")
PET_SHOP_TYPE_FILES = {
    "食物": os.path.join(_DATA_DIR, "宠物商店-食物.txt"),
    "饮料": os.path.join(_DATA_DIR, "宠物商店-饮料.txt"),
    "药物": os.path.join(_DATA_DIR, "宠物商店-药物.txt"),
    "玩具": os.path.join(_DATA_DIR, "宠物商店-玩具.txt"),
}
PET_SHOP_TYPES = list(PET_SHOP_TYPE_FILES.keys())
BENCH_ITEMS_FILE = os.path.join(_PLUGIN_DIR, "Benchmark data", "game_items.json")
_ITEM_BENCH = os.path.join(_PLUGIN_DIR, "game_items.json")

# ============ 共享常量（与 2.3.0 数值一致；WebUI 运行参数可经 core.set_param 运行时覆盖） ============
MIN_COINS = 30
MAX_COINS = 300
MIN_FAV = 0.01
MAX_FAV = 1.0
MAX_LEVEL = 10
LEVEL_STEP = 10.0
ROULETTE_MAGAZINES = 7
ROULETTE_MAX_BULLETS = 6
ROULETTE_MIN_PLAYERS = 2
ROULETTE_MAX_PLAYERS = 3
ROULETTE_JOIN_TIMEOUT = 30
ROULETTE_FEE_RATE = 0.1
PET_UNLOCK_COST = 1000
PET_MAX_LEVEL = 100
PET_EXP_PER_LEVEL = 100.0
PET_MAX_HEALTH = 200.0
PET_SIGNIN_EXP_MIN = 10.0
PET_SIGNIN_EXP_MAX = 60.0
PILL_NAME = "属性丸"
EXP_BALL_NAME = "农场经验球"
ITEM_TO_COIN = 10
PILL_DROP_CHANCE = 0.5
PILL_DROP_MIN = 1
PILL_DROP_MAX = 5
SIGNIN_NO_REWARD_CHANCE = 0.40
SIGNIN_PILL_CHANCE = 0.30
SIGNIN_BALL_CHANCE = 0.30
PILL_DAILY_LIMIT = 3
EXP_BALL_DAILY_LIMIT = 3
PILL_ATTR_COUNT = 2
PILL_BOOST_MIN = 5.0
PILL_BOOST_MAX = 20.0
EXP_BALL_MIN_PCT = 0.05
EXP_BALL_MAX_PCT = 0.20
FARM_PLOT_COLS = 4
SHOP_CARD_COLS = 3
SHOP_PRICE_PAD = 4
BAG_CARD_COLS = 5
FARM_SHOP_COLS = 4
FARM_SHOP_SHOW_BUY = 9
FARM_SHOP_SHOW_LOCKED = 3
WORK_SHOW_DOABLE = 6
WORK_SHOW_LOCKED = 2
PLAY_SHOW_DOABLE = 6
PLAY_SHOW_LOCKED = 2
PET_ATTR_MAX_RANGES = "140=200,200,200,120|80=120,120,120,100|40=100,100,100,100|0=80,80,60,80"
PET_SETTLE_HEALTH_EXCHANGE = 4
AUTO_FEED_ENABLED = False
AUTO_BUY_PRICE_MULT = 1.1
AUTO_FEED_LOG_MAX = 30
AUTO_WORK_ENABLED = True
AUTO_WORK_LOG_MAX = 30
AUTO_FEED_TARGET_HEALTH_PCT = 0.8
ADMIN_UIDS = ""
DAILY_SETTLE_HOUR = 0
BANK_SETTLE_HOUR = 4
DAILY_SETTLE_LOOP_INTERVAL = 60
AUTO_BUY_SHORT_ENABLED = False
AUTO_BUY_SHORT_MULT = 1.0
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
MONEY_EVENT_CHANCE = 0.01
MONEY_EVENT_GAIN = 100
MONEY_EVENT_MAX_PER_DAY = 2
WEAK_HEAL_COST = 500
FARM_UNLOCK_COST = 1500
FARM_PLOT_COST = 800
FARM_FREE_PLOTS = 2
FARM_MAX_PLOTS = 24
FARM_MAX_LEVEL = 100
FARM_EXP_BASE = 1000.0
FARM_GRADES = [
    ("贫瘠土地", 0.0, 0.0),
    ("红土地", 1.0, 0.0),
    ("普通土地", 2.0, 0.10),
    ("肥沃土地", 2.5, 0.20),
    ("黑土地", 4.0, 0.35),
]
FARM_UPGRADE_COSTS = [1000, 1500, 2000, 3000]
CROP_LEVEL_RANGES = (0, 240, 480, 720, 1440)
CROP_LEVEL_STAGES = (4, 5, 5, 6)
LOAN_SPECIAL_AMOUNT = 2500
LOAN_SPECIAL_RATE = 1.0
LOAN_SPECIAL_DAYS = 30
LOAN_SPECIAL_TAKE = 0.2
LOAN_COIN_DEDUCT = 0.2
LOAN_FAV_DROP_SPECIAL = (1.0, 1.5)
LOAN_FAV_DROP_NORMAL = (1.01, 1.25)
LOAN_OVERDUE_YEAR_LIMIT = 4
LOAN_GENERAL_OVERDUE_DAYS = 15
LOAN_SHORT_GRACE_DAYS = 10
LOAN_SHORT_RATE = 6.0
LOAN_DAILY_MULT = 2.0
LOAN_FARM_ROLLBACK_DAYS = 30
LOAN_AUTO_TIME = (23, 0)
RECALL_AFTER = 15
RECALL_ENABLED = True
DEBUG_PASSWORD = os.environ.get("SIGNIN_DEBUG_PASSWORD") or "88224646"
LEDGER_SHOW = 30
CUSTOM_NAME_TTL = 90 * 86400
CUSTOM_NAME_MAX_LEN = 50
REDPACKET_DAILY_LIMIT = 4
REDPACKET_TTL = 600
RAIN_AMOUNT = 1000
RAIN_COUNT = 10
RAIN_TIMES = "8,12,16,20"
RAIN_HOURS = 1
AUTO_STEAL_DAILY_LIMIT = 5
AUTO_STEAL_TARGETS = 4
RANK_DISPLAY = 20
RANK_HIGHLIGHT_COLOR = "#92D050"
RANK_TEXT_COLOR = "#000000"
RANK_MASKED_COLOR = "#7F7F7F"
RANK_SEP_COLOR = "#D9D9D9"
RANK_IMAGE_SCALE = 2.5
RANK_BAR_LIGHTEN = 0.2
RANK_NAME_MAX_CHARS = 6
RANK_BAR_GAP_LEFT_MULT = 2.0
RANK_BAR_GAP_RIGHT_MULT = 3.0
RANK_ROW_SEP_PCT = 0.03
GROUP_MEMBER_TTL_HOURS = 48
RANK_KINDS = {"金币排行": "coins", "宠物排行": "pet", "农场排行": "farm"}
RANK_COIN_COIN_W = 1.0
RANK_COIN_BANK_W = 1.0
RANK_PET_EXP_W = 2.0
RANK_PET_HEALTH_W = 1.5
RANK_PET_ATTR_W = 0.5
RANK_FARM_EXP_W = 2.0
RANK_FARM_PLOT_W = 400.0
RANK_FARM_GRADE_W = 0.5
RANK_PLOT_SCORES = (0, 1000, 2500, 4500, 7500)
# 指定指令的文本响应自动转图片（值 = 图片标题）
IMAGE_COMMANDS = {
    "签到": "签到", "我的签到": "我的签到",
    "装弹": "左轮手枪", "加入": "左轮手枪", "开始": "左轮手枪", "开枪": "左轮手枪", "我的战绩": "我的战绩",
    "存款": "金币银行", "取款": "金币银行", "银行统计": "金币银行",
    "借款": "银行贷款", "还款": "银行贷款", "我的贷款": "我的贷款", "我的征信": "我的征信",
    "查询流水": "金币账单", "流水查询": "金币账单", "消费记录": "金币账单",
    "金币红包": "金币红包", "开红包": "金币红包", "开": "金币红包", "抢红包": "金币红包",
    "活动": "活动中心", "自动化": "自动化状态", "自动化帮助": "自动化帮助",
}
# 可单独控制撤回的指令 → 运行参数 key
RECALL_EXEMPT = {
    "活动": "RECALL_ACTIVITY", "商店": "RECALL_SHOP", "种子商店": "RECALL_SEED_SHOP",
    "肥料商店": "RECALL_FERT_SHOP", "农场商店": "RECALL_FARM_SHOP",
    "签到帮助": "RECALL_HELP", "宠物帮助": "RECALL_HELP", "农场帮助": "RECALL_HELP",
    "左轮手枪帮助": "RECALL_HELP", "游戏帮助": "RECALL_HELP",
}
DEFAULT_ALIAS_CMDS = {
    "打卡": "签到", "我的宠物": "宠物", "宠物状态": "宠物", "工作": "打工", "钱袋": "背包",
    "我的背包": "背包", "存金币": "存款", "取金币": "取款", "今日流水": "查询流水",
    "发红包": "金币红包", "财富榜": "金币排行", "宠物榜": "宠物排行", "农场榜": "农场排行",
    "治疗": "治疗宠物", "赌博": "装弹", "查看帮助": "游戏帮助",
}
ATTR_LABELS = {"satiety": "饱食度", "thirst": "口渴值", "stamina": "体力", "mood": "心情值", "health": "健康度"}
ATTR_SHORT = {"satiety": "饱食", "thirst": "口渴", "stamina": "体力", "mood": "心情", "health": "健康"}
TEMP_IMAGE_TTL = 600
MIN_IMG_RATIO = 4 / 3   # 输出图片最小长宽比（宽/高），0/None 不限制
MAX_IMG_RATIO = 16 / 9  # 输出图片最大长宽比
LAN_DATA_KEY = "lan"

# ============ 功能开关（聊天级三态权限 + 功能总开关的模块清单） ============
FEATURE_MODULES = [
    {"key": "signin", "label": "每日签到", "cmds": ["签到", "我的签到", "修改昵称"]},
    {"key": "pet", "label": "宠物养成", "cmds": ["解锁宠物", "宠物", "更改宠物名字", "治疗宠物", "宠物帮助"]},
    {"key": "pet_work", "label": "宠物打工", "cmds": ["打工"]},
    {"key": "pet_play", "label": "宠物玩耍", "cmds": ["玩耍"]},
    {"key": "pet_shop", "label": "宠物商店", "cmds": ["商店", "购买"]},
    {"key": "pet_item", "label": "宠物道具", "cmds": ["使用"]},
    {"key": "pet_auto", "label": "自动化（照顾/打工）", "cmds": ["自动照顾", "自动打工", "自动化", "自动化帮助", "结算日志"]},
    {"key": "farm", "label": "农场种地", "cmds": ["解锁农场", "购买土地", "土地升级", "种植", "种地", "收割", "收获", "取消种植", "土地状态", "我的农场", "农场帮助"]},
    {"key": "farm_shop", "label": "农场商店", "cmds": ["种子商店", "农场商店", "购买种子", "肥料商店", "购买肥料", "施肥"]},
    {"key": "farm_sell", "label": "农场售卖", "cmds": ["售卖", "售卖种子"]},
    {"key": "farm_steal", "label": "农场偷菜", "cmds": ["偷菜", "自动偷菜"]},
    {"key": "farm_guard", "label": "宠物看家", "cmds": ["看家"]},
    {"key": "warehouse", "label": "仓库背包", "cmds": ["背包", "农场仓库"]},
    {"key": "redpacket", "label": "金币红包", "cmds": ["金币红包", "开红包", "抢红包", "开"]},
    {"key": "roulette", "label": "左轮手枪", "cmds": ["装弹", "加入", "开始", "开枪", "我的战绩", "左轮手枪帮助"]},
    {"key": "bank", "label": "银行存款", "cmds": ["存款", "取款", "银行统计"]},
    {"key": "loan", "label": "银行贷款", "cmds": ["借款", "还款", "我的贷款", "我的征信"]},
    {"key": "ledger", "label": "金币流水", "cmds": ["查询流水", "流水查询", "消费记录"]},
    {"key": "rank", "label": "排行榜", "cmds": ["金币排行", "宠物排行", "农场排行"]},
    {"key": "activity", "label": "活动中心", "cmds": ["活动", "活动中心"]},
]

ITEMS_KEYS = ("jobs", "plays", "shop", "crops", "ferts", "loans")

# 数据命名空间分类：用户级（每用户一个文件）/ 设置（settings.json）/ 群共享（runtime.json）
_USER_FILE_NS = {"users": "user", "pets": "pet", "farms": "farm", "bank": "bank",
                 "loans": "loans", "roulette": "roulette", "ledger": "ledger"}
_SETTINGS_NS = ("features", "alias_cmds", "activities", "activity_config")
_RUNTIME_NS = ("redpackets", "settle_dates", "group_members", "group_names",
               LAN_DATA_KEY, "umos")


def _default_items():
    """game_items.json 默认数值：优先插件目录 game_items.json，其次 Benchmark data 模板"""
    for path in (ITEMS_JSON_FILE, BENCH_ITEMS_FILE, _ITEM_BENCH):
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    return {k: raw.get(k) if isinstance(raw.get(k), list) else [] for k in ITEMS_KEYS}
        except Exception as e:
            logger.warning(f"[核心] 读取数值配置 {path} 失败: {e}")
    return {k: [] for k in ITEMS_KEYS}


class Core:
    """核心框架实例（全插件共享；由指令插件 main.py 创建并挂载功能插件）"""

    def __init__(self, context=None, config=None):
        self.context = context
        self.config = config or {}          # AstrBot _conf_schema 配置（param 的最低优先级回退）
        self.lock = asyncio.Lock()          # 保护数据文件与游戏内存状态（与 2.3.0 一致的全局锁）
        self._handlers = {}                 # 指令头 → {"fn": 处理器, "feature": 模块key, "admin": bool}
        self._multi = {}                    # 备用别名指令头 → 同一处理器（一个头只注册一个）
        self._help = []                     # [(模块标题, [(指令, 说明), ...]), ...]（游戏帮助聚合）
        self._services = {}                 # 插件服务名 → API 对象（core.expose / core.service）
        self._events = {}                   # 事件名 → [回调]
        self._fallback = None               # 动态指令兜底路由（第三方插件挂载用）
        self._mounted = []                  # 已挂载插件模块（运行参数全局同步用）
        self._plugins_meta = []             # [{"name":..., "module":...}]（调试/诊断）
        self._daily_jobs = []               # [(flag, fn)]
        self._interval_jobs = []            # [(seconds, fn)]
        self._ticker_task = None
        self._items_cache = None            # game_items.json 缓存（首次读取后常驻，保存时写盘）
        self.data = self._load()
        from .plugins import image          # 图片响应模块（统一图片输出）
        self.image = image

        # 「游戏帮助」（聚合全部插件的 add_help 段）与「帮助」同义——指令处理属核心职责
        @self.command("游戏帮助", "帮助")
        def _handle_game_help(event):
            img = self.image.build_help("游戏帮助（全部指令）", self._help)
            return img if img is not None else "各模块帮助：发送 签到帮助 / 宠物帮助 / 农场帮助 / 左轮手枪帮助"

    # ================= 插件挂载 =================
    def mount(self, module):
        """挂载一个功能插件模块（模块需实现 register(core)）"""
        name = getattr(module, "NAME", getattr(module, "__name__", "?"))
        module.register(self)
        self._mounted.append(module)
        self._plugins_meta.append({"name": name, "module": module.__name__})
        logger.info(f"[核心] 插件已挂载: {name}")

    def expose(self, name, api_obj):
        """插件把自己的接口对象注册为核心服务（供其他插件 core.service(name) 调用）"""
        self._services[name] = api_obj

    def service(self, name):
        """取其他插件暴露的接口对象；插件未挂载时返回 None（调用方自行降级）"""
        return self._services.get(name)

    def on(self, event, cb):
        """订阅插件联动事件"""
        self._events.setdefault(event, []).append(cb)

    def emit(self, event, **payload):
        """触发联动事件（同步顺序调用所有订阅者；订阅者异常只记日志不阻断）"""
        for cb in self._events.get(event, []):
            try:
                cb(**payload)
            except Exception as e:
                logger.error(f"[核心] 联动事件 {event} 订阅者异常: {e}")

    def add_help(self, title, sections):
        """注册帮助菜单段（「游戏帮助」聚合展示）"""
        self._help.append((title, sections))

    def register_fallback(self, fn):
        """注册动态指令兜底路由 fn(head, event) → 回复 | None（第三方插件挂载用）"""
        self._fallback = fn

    # ================= 指令注册与处理 =================
    def command(self, *heads, feature=None, admin=False):
        """注册指令处理器。用法：
            @core.command("签到", feature="signin")
            def handle_signin(event): ...
        heads 为一个或多个指令头；feature 为功能开关模块 key（FEATURE_MODULES）；
        admin=True 时仅管理员可用（用户鉴权）。"""

        def deco(fn):
            for h in heads:
                if h in self._handlers:
                    raise ValueError(f"指令「{h}」已被重复注册: {self._handlers[h]['fn'].__name__} / {fn.__name__}")
                self._handlers[h] = {"fn": fn, "feature": feature, "admin": admin}
            return fn

        return deco

    def expand_alias(self, head):
        """同义口令展开（一步展开，不递归；别名存于 data["alias_cmds"]，WebUI 可编辑）"""
        target = self.data.get("alias_cmds", {}).get(head)
        return target if isinstance(target, str) and target else head

    def _feature_of_head(self, head):
        for m in FEATURE_MODULES:
            if head in m["cmds"]:
                return m["key"]
        return None

    def feature_enabled(self, key):
        """功能总开关（WebUI 可关）：默认开启"""
        if not key:
            return True
        feats = self.data.get("features")
        if not isinstance(feats, dict):
            return True
        v = feats.get(key)
        return True if v is None else bool(v)

    def is_admin(self, event) -> bool:
        """用户鉴权：管理员判定（ADMIN_UIDS 配置 → OneBot 群主/管理员 → AstrBot 主人）"""
        try:
            sid = str(event.get_sender_id())
        except Exception:
            return False
        uids = str(self.param("ADMIN_UIDS") or "").strip()
        if uids:
            admin_set = {u.strip() for u in uids.replace("，", ",").split(",") if u.strip()}
            if sid in admin_set:
                return True
        try:
            sender = getattr(getattr(event, "message_obj", None), "sender", None)
            role = getattr(sender, "role", None) if sender is not None else None
            if role in ("owner", "admin"):
                return True
        except Exception:
            pass
        try:
            conf = self.context.astrbot_config_mgr.get_conf(None)
            for k in ("admin_qq", "master_qq", "owner_qq", "admin"):
                v = getattr(conf, k, None)
                if v is not None and str(v) == sid:
                    return True
        except Exception:
            pass
        return False

    def perm_of(self, sid):
        """聊天三态权限（2.3.0）：("allow"|"deny"|"partial", {被禁模块key,...})"""
        perm = self.data.get(LAN_DATA_KEY, {}).get("perms", {}).get(sid)
        if not isinstance(perm, dict):
            return "allow", set()
        mode = perm.get("mode", "allow")
        return mode, set(perm.get("deny") or [])

    async def dispatch(self, head, event):
        """指令处理入口（调用方需已持有 core.lock）：鉴权 → 功能开关 → 路由。
        返回回复协议值或 None。"""
        head = self.expand_alias(head)
        # 聊天三态权限：deny 由指令插件提前拦截；partial 命中被禁功能时提示
        mode, denied = self.perm_of(self.chat_sid(event))
        fkey = self._feature_of_head(head) or self._handlers.get(head, {}).get("feature")
        if mode == "partial" and fkey and fkey in denied:
            label = next((m["label"] for m in FEATURE_MODULES if m["key"] == fkey), fkey)
            return f"⚠️ 「{label}」功能已被管理员关闭，暂时无法使用。"
        h = self._handlers.get(head)
        if h is None:
            if self._fallback is not None:
                return self._fallback(head, event)
            return None
        if h["admin"] and not self.is_admin(event):
            return "🔒 该指令仅限管理员使用（可在 WebUI「设置 → 通用 → 管理员 UID」配置管理员）。"
        if h["feature"] and not self.feature_enabled(h["feature"]):
            label = next((m["label"] for m in FEATURE_MODULES if m["key"] == h["feature"]), h["feature"])
            return f"⚠️ 「{label}」功能已被管理员关闭，暂时无法使用。"
        fn = h["fn"]
        import inspect
        if inspect.iscoroutinefunction(fn):
            return await fn(event)
        return fn(event)

    def has_command(self, head):
        return head in self._handlers

    def all_heads(self):
        return set(self._handlers)

    # ================= 用户识别 =================
    @staticmethod
    def chat_sid(event):
        """聊天唯一标识（UMO）"""
        try:
            umo = event.unified_msg_origin
            if umo:
                return umo
        except Exception:
            pass
        gid = event.get_group_id()
        return f"group:{gid}" if gid else f"private:{event.get_sender_id()}"

    @staticmethod
    def user_key(event):
        """用户唯一 key（数据按用户维度存储，跨群共享）"""
        return event.get_sender_id()

    def user_name(self, event):
        """发送者昵称（自定义昵称在有效期内时优先）"""
        custom = self.custom_name_of(self.user_key(event))
        return custom or event.get_sender_name()

    def custom_name_of(self, key):
        u = self.data.get("users", {}).get(key)
        if not u:
            return ""
        ts = u.get("custom_name_ts")
        if not ts:
            return ""
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            return ""
        if datetime.now().timestamp() - ts > float(self.param("CUSTOM_NAME_TTL", CUSTOM_NAME_TTL)):
            return ""
        return str(u.get("custom_name") or "").strip()

    def ensure_user(self, key):
        return self.data.setdefault("users", {}).setdefault(key, {"coins": 0, "favorability": 0.0})

    def touch_chat(self, event):
        """聊天登记：首次出现的聊天进入权限管理列表（返回是否新登记）"""
        sid = self.chat_sid(event)
        umos = self.data.setdefault("umos", {})
        if sid in umos:
            return False
        try:
            typ = "group" if event.get_group_id() else "private"
        except Exception:
            typ = "private"
        umos[sid] = {"type": typ, "name": "", "ts": datetime.now().timestamp()}
        return True

    def touch_user_active(self, key):
        """记录用户最后活跃（5 分钟内不重复置脏）"""
        u = self.data.get("users", {}).get(key)
        if not isinstance(u, dict):
            return False
        ts = datetime.now().timestamp()
        if ts - float(u.get("last_active", 0) or 0) > 300:
            u["last_active"] = ts
            return True
        return False

    def mark_group_member(self, gid, uid, name):
        """本群成员标记（排行榜显示真名用，48h 有效期）"""
        if not gid:
            return False
        members = self.data.setdefault("group_members", {}).setdefault(str(gid), {})
        ttl = float(self.param("GROUP_MEMBER_TTL_HOURS", GROUP_MEMBER_TTL_HOURS)) * 3600
        now = datetime.now().timestamp()
        old = members.get(str(uid))
        if isinstance(old, dict) and now - float(old.get("ts", 0) or 0) < ttl \
                and old.get("name") == name:
            return False
        members[str(uid)] = {"name": name, "ts": now}
        return True

    def level_of(self, favorability):
        """好感度等级（好感度体系属于好感度插件的基础档位，核心保留共用计算）"""
        return min(MAX_LEVEL, int(float(favorability) // LEVEL_STEP))

    # ================= 数据管理 =================
    @staticmethod
    def _default_data():
        return {"users": {}, "roulette": {}, "pets": {}, "bank": {}, "farms": {}, "loans": {},
                "ledger": {}, "redpackets": [], "activities": {}, "activity_config": {},
                "group_members": {}, "group_names": {}, "alias_cmds": {**DEFAULT_ALIAS_CMDS},
                LAN_DATA_KEY: {}, "settle_dates": {}, "features": {}, "params": {}, "umos": {}}

    _DATA_DICT_KEYS = ("users", "roulette", "pets", "bank", "farms", "loans", "ledger",
                       "activities", "activity_config", "group_members", "group_names",
                       LAN_DATA_KEY, "settle_dates", "features", "params", "umos")

    def _load(self):
        if not os.path.exists(SYSTEM_FILE):
            # 新布局不存在：有旧布局（data.json/records.json）→ 一次性转译；否则全新安装
            try:
                from .plugins.format_convert import convert_legacy_to_new
                convert_legacy_to_new()
            except Exception as e:
                logger.error(f"[核心] 旧数据转译失败: {e}")
            if not os.path.exists(SYSTEM_FILE):
                return self._default_data()
        data = self._default_data()
        try:
            # 群共享运行数据
            data.update(_read_json(SYSTEM_FILE) or {})
            # 设置 / 运行参数 / 商店价格记录（各自单独文件）
            data.update(_read_json(SETTINGS_FILE) or {})
            params = _read_json(PARAMS_FILE)
            if isinstance(params, dict):
                data["params"] = params
            prices = _read_json(SHOP_PRICES_FILE)
            if isinstance(prices, list):
                data["shop_price_records"] = prices
            # 用户数据（每用户一个文件 → 聚合进内存命名空间）
            if os.path.isdir(USER_DATA_DIR):
                for fn in os.listdir(USER_DATA_DIR):
                    if not fn.endswith(".json"):
                        continue
                    uid = fn[:-5]
                    obj = _read_json(os.path.join(USER_DATA_DIR, fn))
                    if not isinstance(obj, dict):
                        continue
                    for ns, fkey in _USER_FILE_NS.items():
                        v = obj.get(fkey)
                        if v not in (None, {}, []):
                            data.setdefault(ns, {})[uid] = v
        except Exception as e:
            logger.error(f"[核心] 读取数据失败: {e}")
        for k in self._DATA_DICT_KEYS:
            data.setdefault(k, {} if k != "redpackets" else [])
        data.setdefault("redpackets", [])
        data.setdefault("shop_price_records", [])
        if not isinstance(data.get("alias_cmds"), dict):
            data["alias_cmds"] = {**DEFAULT_ALIAS_CMDS}
        return data

    def save(self, data=None):
        """写盘（3.0 布局）：群共享数据 runtime.json + 设置 settings.json +
        运行参数 params.json + 商店价格 shop_prices.json + 每用户 user_data/<uid>.json。
        ponytail: 用户文件全量重写、无脏跟踪——千级用户内无感，需要时改增量写。"""
        data = self.data if data is None else data
        try:
            os.makedirs(USER_DATA_DIR, exist_ok=True)
            # 系统文件
            _write_json(SYSTEM_FILE, {k: data.get(k) for k in _RUNTIME_NS})
            _write_json(SETTINGS_FILE, {k: data.get(k) for k in _SETTINGS_NS})
            _write_json(PARAMS_FILE, data.get("params") or {})
            if data.get("shop_price_records"):
                _write_json(SHOP_PRICES_FILE, data["shop_price_records"])
            # 用户文件：收集出现过的用户 → 每人一个文件
            keymap = {}  # 安全文件名 → 原始 key
            for ns in _USER_FILE_NS:
                for k in (data.get(ns) or {}):
                    keymap.setdefault(_safe_uid(k), k)
            for safe, orig in keymap.items():
                obj = {}
                for ns, fkey in _USER_FILE_NS.items():
                    v = (data.get(ns) or {}).get(orig)
                    if v not in (None, {}, []):
                        obj[fkey] = v
                _write_json(os.path.join(USER_DATA_DIR, safe + ".json"), obj)
            # 清理已消失的用户文件
            for fn in os.listdir(USER_DATA_DIR):
                if fn.endswith(".json") and fn[:-5] not in keymap:
                    try:
                        os.remove(os.path.join(USER_DATA_DIR, fn))
                    except OSError:
                        pass
        except Exception as e:
            logger.error(f"[核心] 保存数据失败: {e}")

    # ================= 运行参数 =================
    def param(self, key, default=None):
        """读运行参数：data["params"] 覆盖值 → core 模块全局常量 → AstrBot 配置 → default"""
        v = self.data.get("params", {}).get(key)
        if v is not None:
            return v
        import sys
        mod = sys.modules[__name__]
        if hasattr(mod, key):
            return getattr(mod, key)
        if key in self.config:
            return self.config[key]
        return default

    def set_param(self, key, val):
        """写运行参数：立即生效（覆盖值入 data["params"]，并同步 core 与已挂载插件的模块全局）"""
        import sys
        self.data.setdefault("params", {})[key] = val
        for m in [sys.modules[__name__]] + list(self._mounted):
            try:
                setattr(m, key, val)
            except Exception:
                pass

    # ================= 金币变化（唯一出入口） =================
    def coins_of(self, key):
        v = self.data.get("users", {}).get(key, {}).get("coins")
        return int(v) if isinstance(v, (int, float)) else 0

    def coin_line(self, key):
        return f"💰 当前金币：{self.coins_of(key)}"

    def add_coins(self, key, amount, reason="", skip_repay=False):
        """增加/扣除金币（正得负扣），返回变动后余额。
        联动点：加币前发 coins.pre_add（银行贷款插件可扣减还款）；
        变动后发 coins.gain；自动记流水（流水插件数据）。"""
        amount = int(amount)
        if amount > 0 and not skip_repay:
            payload = {"key": key, "amount": amount, "reason": reason, "skip_repay": skip_repay}
            for cb in self._events.get("coins.pre_add", []):
                try:
                    cb(payload)  # 订阅者可原地修改 payload["amount"]（如银行贷款扣减还款）
                except Exception as e:
                    logger.error(f"[核心] 联动事件 coins.pre_add 订阅者异常: {e}")
            amount = int(payload["amount"])
        user = self.ensure_user(key)
        cur = user.get("coins")
        if not isinstance(cur, (int, float)):
            cur = 0
        new_bal = max(0, int(cur) + amount)
        user["coins"] = new_bal
        if amount != 0 and reason:
            self.log_ledger(key, amount, reason, new_bal)
        self.emit("coins.gain", key=key, amount=amount, balance=new_bal, reason=reason)
        return new_bal

    def pay_coins(self, key, amount, reason=""):
        """消费金币：余额不足返回 None（不扣款），成功返回新余额"""
        amount = int(amount)
        if self.coins_of(key) < amount:
            return None
        return self.add_coins(key, -amount, reason)

    def log_ledger(self, key, amount, reason, balance):
        """记录金币流水（最多 200 条；流水插件读取展示）"""
        ledger = self.data.setdefault("ledger", {}).setdefault(key, [])
        ledger.append({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "reason": reason, "delta": amount, "balance": balance})
        if len(ledger) > 200:
            del ledger[: len(ledger) - 200]

    # ================= 数值配置（game_items.json：打工/玩耍/商店/作物/肥料/贷款套餐） =================
    def items(self):
        if self._items_cache is None:
            self._items_cache = _default_items()
        return self._items_cache

    def save_items(self, flat):
        """保存数值配置（dict 全量覆盖；键限制在 ITEMS_KEYS）"""
        clean = {k: flat.get(k) if isinstance(flat.get(k), list) else [] for k in ITEMS_KEYS}
        self._items_cache = clean
        try:
            tmp = ITEMS_JSON_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False, indent=2)
            os.replace(tmp, ITEMS_JSON_FILE)
            return True, "保存成功"
        except Exception as e:
            return False, f"保存失败: {e}"

    # ================= 定时任务（固定结算循环） =================
    def daily(self, flag, fn):
        """注册每日幂等任务：fn(data, now) → bool(全部成功)。
        core 巡检发现 data["settle_dates"][flag] != 今天 且 now.hour >= hour 时执行；
        fn 自行 core.save()；仅当返回 True 时写入幂等标记（失败次日巡检自动重试）。"""
        self._daily_jobs.append((flag, fn))

    def at(self, hour, flag, fn):
        """带触发时间的每日任务（hour: 0-23）"""
        self._daily_jobs.append((flag, fn, int(hour)))

    def interval(self, seconds, fn):
        """注册周期任务（秒）：fn()，自行加锁存盘。核心统一启动一条巡检协程。"""
        self._interval_jobs.append((int(seconds), fn))

    def start_ticker(self):
        """启动统一巡检循环（幂等；在事件循环内或外均可调用）"""
        if self._ticker_task is not None and not self._ticker_task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # 无运行中的事件循环（如导入自检），等首条消息触发再启动
        self._ticker_task = asyncio.create_task(self._ticker())

    async def _ticker(self):
        while True:
            try:
                await asyncio.sleep(1)
                now = datetime.now()
                today = date.today().isoformat()
                # 周期任务：fn 自行 `async with core.lock`（本循环不持锁，避免不可重入死锁）
                for secs, fn in self._interval_jobs:
                    last = getattr(fn, "_last_run", 0)
                    if now.timestamp() - last >= secs:
                        try:
                            fn._last_run = now.timestamp()
                        except Exception:
                            pass
                        try:
                            await fn() if asyncio.iscoroutinefunction(fn) else fn()
                        except Exception as e:
                            logger.error(f"[核心] 周期任务 {getattr(fn, '__name__', fn)} 异常: {e}")
                # 每日任务：核心持锁执行（fn 约定不自行加锁）
                async with self.lock:
                    sd = self.data.setdefault("settle_dates", {})
                    for job in self._daily_jobs:
                        flag, fn = job[0], job[1]
                        hour = int(job[2]) if len(job) > 2 else 0
                        if now.hour >= hour and sd.get(flag) != today:
                            try:
                                ok = fn(self.data, now)
                                if asyncio.iscoroutine(ok):
                                    ok = await ok
                            except Exception as e:
                                logger.error(f"[核心] 每日任务 {flag} 异常: {e}")
                                ok = False
                            if ok:
                                sd[flag] = today
                                self.save()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[核心] 巡检循环异常: {e}")
                await asyncio.sleep(5)

    # ================= 通用工具 =================
    @staticmethod
    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    @staticmethod
    def f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def parse_item_qty(message_str):
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

    @staticmethod
    def parse_kv_text(raw_lines, types=None):
        """解析「[类型:名称] + key=value」行列表 → [{"type","name","data"}]（旧版 txt 配置兼容）"""
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

    @classmethod
    def parse_kv_sections(cls, path, kind, types=None):
        """解析配置文件的「[类型:名称]+key=value」段（文件缺失返回 []）"""
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_lines = f.readlines()
        except Exception as e:
            logger.error(f"[核心] 读取{kind}配置失败: {e}")
            return []
        return cls.parse_kv_text(raw_lines, types)

    @classmethod
    def parse_kv_sections_text(cls, text, kind, types=None):
        """解析字符串形式的「[类型:名称] + key=value」配置"""
        return cls.parse_kv_text(text.splitlines(keepends=True), types)

    def farm_grades(self, raw=None):
        """土地等级表解析（WebUI 可改）→ [(名称, 产量加成小数, 时间减免小数)]。
        raw 缺省读运行参数；常量为列表时直接用；为字符串（WebUI 编辑后）时按「名=产量%,时间%」解析。"""
        if raw is None:
            raw = self.param("FARM_GRADES")
        if isinstance(raw, list) and raw and isinstance(raw[0], (list, tuple)):
            return [(str(a), float(b), float(c)) for a, b, c in raw]
        out = []
        for seg in str(raw or "").replace("；", "|").replace(";", "|").split("|"):
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
                out.append((name.strip(), nums[0] / 100.0, nums[1] / 100.0))
        return out or list(FARM_GRADES)
