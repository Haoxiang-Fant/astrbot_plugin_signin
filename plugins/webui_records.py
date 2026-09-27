# -*- coding: utf-8 -*-
"""WebUI 运行记录 Web API（3.0.0 从 2.3.0 modules/webui.py 的 records/* 端点移植）。

本模块是辅助模块（非挂载插件：无 NAME / register(core) 插件契约），由 webui 插件在
register 末尾调用 register(core) 完成 Web API 注册。暴露：
  - ENDPOINTS：[(path, method, handler, desc), ...]（端点清单，供 webui 检查/再包装）
  - register(core)：逐项 context.register_web_api(f"/astrbot_plugin_signin3/{path}", ...) 端点：
    records/pets           GET   全部宠物卡片（状态/活动/自动信息）          ← web_get_record_pets
    records/pets/auto      POST  切换用户自动照顾/自动打工                  ← web_toggle_record_auto
    records/pets/loan_waive POST 豁免用户自动化贷款（视为已还款扣基准金币）   ← web_waive_auto_loan
    records/pets/detail    POST  单只宠物完整信息（属性变化记录，按需）      ← web_get_record_pet_detail
    records/prices         GET   商店价格变动（shop_price_records）         ← web_get_record_prices
    records/users          GET   全部用户信息卡片（基础信息）               ← web_get_record_users
    records/users/detail   POST  单个用户完整信息（按需）                   ← web_get_record_user_detail

与 2.3.0 的差异（JSON 回复形状保持一致）：
  - self._load() → core.data；self._save(d) → core.save()；self._lock → core.lock；
    self._custom_name_of(data, uid) → core.custom_name_of(uid)；self._ensure_user → core.ensure_user；
    self._level_of → core.level_of。records 数据（attr_log/auto_feed_logs/auto_work_logs/
    signin_logs/shop_price_records）在内存中都在 core.data（records.json 拆分只发生在写盘），
    读取逻辑不变。
  - 未套 LAN 访问门（旧版在 main.py 注册处统一 _lan_gate_wrap；本模块直接注册裸处理器，
    由 webui 层决定是否再包装；门默认对本机回环放行，本地使用不受影响）。
  - 「切换自动照顾/打工」不再调用 _ensure_auto_work_loop / _ensure_daily_settle_loop
    （3.0 由 core 统一巡检协程承载，常驻运行）；「开启自动照顾立即触发一次照顾」改为经
    core.service("pet_auto_care")（服务未挂载时跳过立即照顾，不追加提示文案）。
  - 银行汇总 / 欠款统计分别经 core.service("bank_deposit").summary / core.service("bank_loan")
    .debt_summary（服务未挂载时对应字段为 None，替代旧版恒有值的模块内实现）。
  - 昵称首拼/笔画排序五件套（_GB2312_PINYIN_RANGES/_hanzi_pinyin_initial/_COMMON_STROKES/
    _hanzi_stroke/_record_nick_sort_key）自 2.3.0 modules/base.py 原样复制。
  - 运行参数一律 core.param(KEY, 缺省) 读取（本模块不挂载，set_param 不会同步本模块全局）。
"""
import random
from datetime import datetime, timedelta

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from ..core import (
    PLUGIN_NAME,
    PET_MAX_HEALTH, PET_MAX_LEVEL, PET_EXP_PER_LEVEL, PET_ATTR_MAX_RANGES,
    ATTR_SHORT,
    AUTO_FEED_ENABLED, AUTO_WORK_ENABLED,
    SHOP_PRICE_FLOAT_ENABLED, SHOP_PRICE_SPECIAL_HOURS,
    SHOP_PRICE_DISCOUNT_MIN, SHOP_PRICE_DISCOUNT_MAX,
    SHOP_PRICE_DISCOUNT_LO, SHOP_PRICE_DISCOUNT_HI, SHOP_PRICE_RECORD_MAX,
    PILL_DAILY_LIMIT, MONEY_EVENT_MAX_PER_DAY,
    CROP_LEVEL_RANGES, CROP_LEVEL_STAGES,
    CROP_FILE,
)

__all__ = ["ENDPOINTS", "register"]

_CORE = None  # register(core) 时注入（处理器经它访问 core.lock / core.data / core.service 等）


def _core():
    return _CORE


# ================= 常量：宠物属性档位（2.2.7 统一数值管理，同 plugins/pet.py） =================
# 一档下限 = 60% 上限、二档下限 = 35%、三档下限 = 15%，低于 15% 为四档。
_TIER_PCTS = (0.6, 0.35, 0.15)

_ATTR_MAX_DEFAULT = {140: (200.0, 200.0, 200.0, 120.0),
                     80: (120.0, 120.0, 120.0, 100.0),
                     40: (100.0, 100.0, 100.0, 100.0),
                     0: (80.0, 80.0, 60.0, 80.0)}


def _parse_attr_max_ranges(raw):
    """解析 PET_ATTR_MAX_RANGES（WebUI「设置 → 宠物 → 属性」可编辑）。
    格式：健康值下限=饱食,口渴,体力,心情，竖线分隔。返回 {健康下限: (饱食, 口渴, 体力, 心情)}；
    解析失败回退默认四档。（同 plugins/pet.py 实现）"""
    if not isinstance(raw, str) or not raw.strip():
        return dict(_ATTR_MAX_DEFAULT)
    out = {}
    for seg in str(raw).replace("；", "|").replace(";", "|").split("|"):
        seg = seg.strip()
        if not seg or "=" not in seg:
            continue
        floor_s, body = seg.split("=", 1)
        try:
            floor = float(floor_s.strip())
        except (TypeError, ValueError):
            continue
        nums = []
        for x in body.replace("，", ",").split(","):
            try:
                nums.append(float(x.strip()))
            except (TypeError, ValueError):
                nums = []
                break
        if len(nums) == 4:
            out[floor] = (nums[0], nums[1], nums[2], nums[3])
    return out or dict(_ATTR_MAX_DEFAULT)


def _attr_max(core, health: float):
    """返回 (饱食上限, 口渴上限, 体力上限, 心情上限)，由健康度决定（健康度最大值 PET_MAX_HEALTH）。"""
    ranges = _parse_attr_max_ranges(core.param("PET_ATTR_MAX_RANGES", PET_ATTR_MAX_RANGES))
    # 按健康值下限从高到低匹配（健康 >= 下限 即命中）
    for floor in sorted(ranges, reverse=True):
        if health >= floor:
            return ranges[floor]
    return (80.0, 80.0, 60.0, 80.0)


def _attr_tier(val: float, max_v: float) -> int:
    """按属性最大值推导档位：≥60% 一档、≥35% 二档、≥15% 三档、否则四档。"""
    try:
        max_v = float(max_v or 0)
        val = float(val or 0)
    except (TypeError, ValueError):
        return 1
    if max_v <= 0:
        return 1
    for i, p in enumerate(_TIER_PCTS):
        if val >= max_v * p:
            return i + 1
    return 4


def _worst_tier(core, satiety: float, thirst: float, mood: float, health: float = 0.0) -> int:
    """饱食/口渴/心情对健康的影响档位，每个属性单独定档后取最差档（4 最差）。
    health 用于确定当前属性上限。"""
    sat_max, thr_max, sta_max, mood_max = _attr_max(core, float(health or 0))
    return max(_attr_tier(satiety, sat_max),
               _attr_tier(thirst, thr_max),
               _attr_tier(mood, mood_max))


def _attr_is_red(core, label: str, val, health: float = 0.0) -> bool:
    """属性值是否应标红（进入第 3/4 档 → 红，即值 < 35% 上限）。
    兼容「饱食度/口渴值/心情值/体力值/健康度」与「饱食/口渴/心情/体力/健康」两种标签。"""
    try:
        val = float(val)
    except (TypeError, ValueError):
        return False
    sat_max, thr_max, sta_max, mood_max = _attr_max(core, float(health or 0))
    health_max = float(core.param("PET_MAX_HEALTH", PET_MAX_HEALTH) or PET_MAX_HEALTH)
    max_v = {"饱食": sat_max, "饱食度": sat_max,
             "口渴": thr_max, "口渴值": thr_max,
             "体力": sta_max, "体力值": sta_max,
             "心情": mood_max, "心情值": mood_max,
             "健康": health_max, "健康度": health_max}.get(label)
    if max_v is None:
        return False
    return val < max_v * _TIER_PCTS[1]


def _pet_busy_until(pet: dict) -> float:
    """打工/玩耍共用冷却计时器：返回忙碌结束时间戳（兼容旧数据 work_until / play_until）"""
    busy = float(pet.get("busy_until", 0) or 0)
    old = max(float(pet.get("work_until", 0) or 0), float(pet.get("play_until", 0) or 0))
    return max(busy, old)


def _pet_level_from_exp(core, exp: float) -> int:
    """新经验体系：所需经验 = 当前等级 × PET_EXP_PER_LEVEL（累计 100+200+...+（L-1）×100 升到 Lv.L）"""
    exp = max(0.0, float(exp))
    per = float(core.param("PET_EXP_PER_LEVEL", PET_EXP_PER_LEVEL) or 100.0)
    max_lv = int(core.param("PET_MAX_LEVEL", PET_MAX_LEVEL) or PET_MAX_LEVEL)
    # 解 per*(L-1)*L/2 <= exp → L = floor((1+sqrt(1+8*exp/per))/2)
    L = int((1 + (1 + 8 * exp / per) ** 0.5) / 2)
    return min(max_lv, L)


def _pet_exp_progress(core, exp: float) -> tuple:
    """返回 (当前等级, 本级已得经验, 本级所需经验)，新经验体系：所需经验 = 当前等级 × PET_EXP_PER_LEVEL"""
    exp = max(0.0, float(exp))
    level = _pet_level_from_exp(core, exp)
    per = float(core.param("PET_EXP_PER_LEVEL", PET_EXP_PER_LEVEL) or 100.0)
    need_prev = per * (level - 1) * level / 2.0  # 升到当前等级的累计经验
    got = exp - need_prev
    need = float(level) * per
    return level, got, need


def _auto_loan_balance_of(data: dict, key: str) -> float:
    """自动化贷款余额（2.2.7：user.auto_loan 单一余额，无上限无逾期无利息）"""
    u = data.get("users", {}).get(key)
    if not isinstance(u, dict):
        return 0.0
    try:
        return round(float(u.get("auto_loan", 0) or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _record_account_name(data: dict, uid) -> str:
    """账户昵称（2.1.0）：取用户任意群聊里记录的昵称（WebUI 同步的 group_names 优先，
    实时标记的 group_members 次之）；与自定义昵称 core.custom_name_of 组成回退链：
    自定义昵称 → 账户昵称 → uid。"""
    uid = str(uid)
    for src in ("group_names", "group_members"):
        groups = data.get(src) or {}
        if not isinstance(groups, dict):
            continue
        for g in groups.values():
            if not isinstance(g, dict):
                continue
            info = g.get(uid)
            if isinstance(info, dict):
                nm = str(info.get("name", "") or "").strip()
            elif isinstance(info, str):
                nm = str(info).strip()
            else:
                nm = ""
            if nm:
                return nm
    return ""


# ================= 昵称排序五件套（2.1.0，自 2.3.0 modules/base.py 1421-1506 原样复制） =================
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


# ================= 商店实时价格（2.0.4，同 plugins/pet_shop.py 模块级实现） =================
def _shop_price_window(now=None):
    """当前价格窗口（2.0.4）：刷新时间固定偶数点（0/2/4/…/22 整点），同一窗口（2 小时）内价格稳定。
    返回 (窗口起始小时, 窗口标识串)；窗口标识形如 YYYYMMDD|HH，用于固定随机种子。"""
    now = now or datetime.now()
    start_hour = now.hour - (now.hour % 2)
    wid = "%s|%02d" % (now.strftime("%Y%m%d"), start_hour)
    return start_hour, wid


_DISCOUNT_CACHE = [None]  # [(wid, {商品名: 倍率})]


def _shop_raw_items(core):
    """商店原始商品列表（game_items.json 的 shop 段；name/type/price/desc 键）"""
    items = (core.items() or {}).get("shop")
    return items if isinstance(items, list) else []


def _shop_discount_map(core, wid):
    """指定窗口的折扣映射 {商品名: 倍率}（2.0.4）：特价时段（窗口起始 10/12/18/0 时）随机选取
    SHOP_PRICE_DISCOUNT_MIN~MAX 种商品按 二~八折（倍率 LO~HI）；其余窗口全部原价。
    按「窗口 id + 商品名」固定随机 → 同一窗口内价格稳定；取整后的价格可能使倍率略有偏差，这里只存随机倍率。"""
    start_hour = int(wid.split("|")[1])
    special = start_hour in tuple(core.param("SHOP_PRICE_SPECIAL_HOURS", SHOP_PRICE_SPECIAL_HOURS))
    if not special:
        return {}
    lo = float(core.param("SHOP_PRICE_DISCOUNT_LO", SHOP_PRICE_DISCOUNT_LO) or 0.2)
    hi = float(core.param("SHOP_PRICE_DISCOUNT_HI", SHOP_PRICE_DISCOUNT_HI) or 0.8)
    cnt_lo = int(core.param("SHOP_PRICE_DISCOUNT_MIN", SHOP_PRICE_DISCOUNT_MIN) or 2)
    cnt_hi = int(core.param("SHOP_PRICE_DISCOUNT_MAX", SHOP_PRICE_DISCOUNT_MAX) or 5)
    names = [it["name"] for it in _shop_raw_items(core) if isinstance(it, dict) and it.get("name")]
    n = max(0, min(cnt_hi, len(names)))
    if n < 1 or cnt_lo > n:
        cnt_lo = min(cnt_lo, n)
    rng = random.Random("pet_shop_window_discount|%s" % wid)
    if n < 1:
        return {}
    count = rng.randint(cnt_lo, n)
    chosen = rng.sample(sorted(names), count)
    out = {}
    for nm in chosen:
        out[nm] = round(rng.uniform(lo, hi), 2)
    return out


def _shop_discount_map_cached(core, wid):
    """按窗口缓存折扣映射（仅在窗口变化时重算；极端情况下跨天窗口号不同必然重算）"""
    cache = _DISCOUNT_CACHE[0]
    if cache and cache[0] == wid:
        return cache[1]
    m = _shop_discount_map(core, wid)
    _DISCOUNT_CACHE[0] = (wid, m)
    return m


def _record_shop_price_window(core, data):
    """把从上次记录到当前的全部价格窗口变动写入 data['shop_price_records']（每次窗口开始时记一条），
    返回是否新增。2.0.4：价格修改可在 WebUI「运行记录 → 商店价格」查看；
    即使期间没打开过页面，重新打开时也会按固定随机把错过的窗口补齐。（2.3.0 pet._record_shop_price_window 移植）"""
    if not bool(core.param("SHOP_PRICE_FLOAT_ENABLED", SHOP_PRICE_FLOAT_ENABLED)):
        return False
    cap = int(core.param("SHOP_PRICE_RECORD_MAX", SHOP_PRICE_RECORD_MAX) or 60)
    _start_hour, wid = _shop_price_window()

    def _wid_to_dt(wid_str):
        try:
            d, hh = str(wid_str).split("|")
            return datetime.strptime(d, "%Y%m%d").replace(hour=int(hh))
        except (ValueError, TypeError):
            return None

    cur_dt = _wid_to_dt(wid)
    if cur_dt is None:
        return False
    last = data.get("_shop_price_last_window")
    cur = None
    if last:
        cand = _wid_to_dt(last)
        if cand is not None:
            cur = cand + timedelta(hours=2)
    if cur is None:
        # 首次无记录时回填最近 cap 个窗口（补齐整个记录容量），便于首次打开页面直接看到历史价格变动
        cur = cur_dt - timedelta(hours=max(0, cap - 1) * 2)
    # 防御：异常数据（窗口时间戳在未来/跨度过大）可能导致死循环 → 最多补 cap 个窗口
    guard = 0
    recs = data.setdefault("shop_price_records", [])
    added = False
    while cur <= cur_dt and guard < cap:
        w = "%s|%02d" % (cur.strftime("%Y%m%d"), cur.hour)
        special = cur.hour in tuple(core.param("SHOP_PRICE_SPECIAL_HOURS", SHOP_PRICE_SPECIAL_HOURS))
        disc = _shop_discount_map(core, w)
        items = []
        # 2.2.7：记录整个商店快照（含原价商品），WebUI 点击窗口卡片可展开"当时商店信息"；
        # 打折状态由前端按 price != base 区分（旧记录只有打折商品，展开时按已有数据显示）
        for it in _shop_raw_items(core):
            mult = disc.get(it.get("name"), 1.0)
            base = int(it.get("price", 0) or 0)
            # 2.3.0：附带商品类型，WebUI 快照按「商店」指令同样的分类展示（旧记录无 type，前端回退当前商店配置）
            items.append({"name": it.get("name"), "type": it.get("type") or "其他", "base": base,
                          "price": max(1, int(round(base * mult))), "mult": round(mult, 2)})
        recs.append({"window": w,
                     "ts": cur.strftime("%Y-%m-%d %H:%M"),
                     "special": special,
                     "items": items})
        added = True
        cur += timedelta(hours=2)
        guard += 1
    if len(recs) > cap:
        del recs[:len(recs) - cap]
    data["_shop_price_last_window"] = wid
    return added


# ================= 农场小工具（同 plugins/farm.py 模块级实现；供用户详情的地块/仓库卡片） =================
def _f(x):
    """宽松转 float（失败回 0，与 2.3.0 self._f 一致）"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _norm_crop_entry(d):
    """规范化一条作物配置（扁平 dict，键与 game_items.json 一致）"""
    if not isinstance(d, dict):
        return None
    name = str(d.get("name", "")).strip()
    if not name:
        return None
    return {
        "name": name,
        "desc": str(d.get("desc", "") or ""),
        "seed_price": _f(d.get("seed_price", 0)),
        "seed_sell_price": _f(d.get("seed_sell_price", 0)),
        "yield": int(_f(d.get("yield", 0))),
        "crop_price": _f(d.get("crop_price", 0)),
        "exp": int(_f(d.get("exp", 0))),
        "min_level": int(_f(d.get("min_level", 0))),
        "grow_minutes": int(_f(d.get("grow_minutes", 0))),
    }


def _load_crops(core):
    """作物配置：优先 game_items.json（core.items()）；为空时回退解析 作物.txt（同 farm 插件口径）"""
    flat = core.items() or {}
    crops = [c for c in (_norm_crop_entry(d) for d in (flat.get("crops") or [])) if c]
    if crops:
        return crops
    try:
        return core.parse_kv_sections(CROP_FILE, "作物")
    except Exception:
        return []


def _find_item(items, name):
    return next((x for x in items if x["name"] == name), None)


def _plot_grade(core, grade):
    """土地等级 → (名称, 产量加成小数, 时间减免小数)（core.farm_grades 解析，WebUI 可编辑）"""
    grades = core.farm_grades()
    return grades[grade] if 0 <= grade < len(grades) else grades[0]


def _crop_level_ranges(core):
    """各级作物成熟分钟上限（CROP_LEVEL_RANGES，如 (0,240,480,720,1440)）"""
    raw = core.param("CROP_LEVEL_RANGES", CROP_LEVEL_RANGES)
    try:
        if isinstance(raw, str):
            vals = [int(_f(x)) for x in raw.replace("，", ",").split(",") if str(x).strip() != ""]
        else:
            vals = [int(_f(x)) for x in raw]
    except (TypeError, ValueError):
        vals = [0, 240, 480, 720, 1440]
    if not vals:
        vals = [0, 240, 480, 720, 1440]
    return vals


def _crop_stage_counts(core):
    """各级作物成长阶段数（默认 一~四级 = 4/5/5/6）"""
    raw = core.param("CROP_LEVEL_STAGES", CROP_LEVEL_STAGES)
    try:
        if isinstance(raw, str):
            vals = [int(_f(x)) for x in raw.replace("，", ",").split(",") if str(x).strip() != ""]
        else:
            vals = [int(_f(x)) for x in raw]
    except (TypeError, ValueError):
        vals = [4, 5, 5, 6]
    if not vals:
        vals = [4, 5, 5, 6]
    return vals


def _crop_level_of(core, crop):
    """按贫瘠土地上的成熟分钟数划分作物等级（1~N 级）"""
    grow = int(_f(crop.get("grow_minutes", 0)))
    ranges = _crop_level_ranges(core)
    if len(ranges) <= 1:
        return 1
    for i in range(1, len(ranges)):
        if grow <= ranges[i]:
            return i
    return len(ranges)


def _crop_stage_count(core, crop):
    """该作物在总生长周期内划分的成长阶段数"""
    lv = _crop_level_of(core, crop)
    stages = _crop_stage_counts(core)
    idx = min(lv - 1, len(stages) - 1)
    return max(1, int(stages[idx]))


def _plot_growth(core, plot, crop, now):
    """计算一块种植地的生长状态（2.0.0 阶段模型，同 plugins/farm.py _plot_growth）。
    本模块只使用返回值中的 advance_sec（化肥已推进时间）。"""
    base_sec = int(plot.get("base_time", 0)) or (crop["grow_minutes"] * 60 if crop else 0)
    _, _, gt = _plot_grade(core, int(plot.get("grade", 0)))
    total_sec = base_sec * (1 - gt)
    elapsed_sec = max(0.0, float(now) - float(plot.get("plant_ts", 0)))
    advance_sec = float(plot.get("fert_advance", 0.0))
    if not plot.get("fert_advance") and plot.get("fert_time"):
        # 旧数据（1.7.9 及以前：减时比例）→ 折算为推进秒数
        advance_sec = float(plot.get("fert_time", 0.0)) * base_sec
    progress_sec = elapsed_sec + advance_sec
    stage_count = _crop_stage_count(core, crop)
    stage_sec = base_sec / max(1, stage_count)
    stage_idx = min(stage_count - 1, int(progress_sec // stage_sec)) if stage_sec > 0 else 0
    stage_left_sec = stage_sec - (progress_sec % stage_sec)
    remain_sec = total_sec - progress_sec
    return {
        "base_sec": base_sec, "total_sec": total_sec,
        "stage_count": stage_count, "stage_sec": stage_sec,
        "elapsed_sec": elapsed_sec, "advance_sec": advance_sec,
        "progress_sec": progress_sec, "stage_idx": stage_idx,
        "stage_left_sec": stage_left_sec, "remain_sec": remain_sec,
        "mature": remain_sec <= 0,
    }


# ================= Web API 端点（2.3.0 webui.py 移植；JSON 回复形状保持一致） =================
async def web_get_record_pets():
    """运行记录·宠物记录：全部宠物卡片（当前状态 / 正在进行的活动 / 自动照顾·自动打工信息）。
    2.2.0：宠物每日结算由固定结算循环统一执行，此处只读取已结算结果。"""
    core = _core()
    if core is None:
        return error_response("运行记录模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        data = core.data
        now_ts = datetime.now().timestamp()
        pets = []
        for uid, pet in (data.get("pets") or {}).items():
            if not isinstance(pet, dict):
                continue
            u = data.get("users", {}).get(uid) or {}
            custom = core.custom_name_of(uid)
            sat_max, thr_max, sta_max, mood_max = _attr_max(core, pet["health"])
            busy_until = _pet_busy_until(pet)
            busy = None
            if busy_until > now_ts:
                busy = {
                    "activity": pet.get("busy_activity"),
                    "item": pet.get("busy_item"),
                    "until": busy_until,
                    "remaining_min": int((busy_until - now_ts) // 60),
                }
            tickets = {
                "sat": pet.get("satiety", 0), "thr": pet.get("thirst", 0),
                "sta": pet.get("stamina", 0), "mood": pet.get("mood", 0),
                "health": pet.get("health", 0),
                "sat_max": sat_max, "thr_max": thr_max, "sta_max": sta_max,
                "mood_max": mood_max,
                "health_max": core.param("PET_MAX_HEALTH", PET_MAX_HEALTH),
                "sat_red": _attr_is_red(core, "饱食", pet.get("satiety", 0), pet.get("health", 0)),
                "thr_red": _attr_is_red(core, "口渴", pet.get("thirst", 0), pet.get("health", 0)),
                "mood_red": _attr_is_red(core, "心情", pet.get("mood", 0), pet.get("health", 0)),
                "health_red": _attr_is_red(core, "健康", pet.get("health", 0), pet.get("health", 0)),
            }
            pets.append({
                "uid": uid,
                # 2.2.2：主人ID显示平台昵称（自定义昵称 → 账户昵称 → uid）
                "nick": custom or _record_account_name(data, uid) or uid,
                "name": pet.get("name", "宠物"),
                "level": pet.get("level", 0),
                "exp": round(float(pet.get("exp", 0) or 0), 1),
                "tier": _worst_tier(core, pet.get("satiety", 0), pet.get("thirst", 0), pet.get("mood", 0), pet.get("health", 0)),
                "weak": bool(pet.get("weak")),
                "attrs": tickets,
                "busy": busy,
                "auto": {
                    "purchase_on": bool(u.get("auto_feed_enabled")),
                    "purchase_global": bool(core.param("AUTO_FEED_ENABLED", AUTO_FEED_ENABLED)),
                    "work_on": bool(u.get("auto_work_enabled")),
                    "work_global": bool(core.param("AUTO_WORK_ENABLED", AUTO_WORK_ENABLED)),
                    "work_base": int(u.get("work_base", 0) or 0),
                    # 2.2.7：自动化贷款余额（无上限无逾期，仅自动照顾可贷）
                    "auto_loan_owed": _auto_loan_balance_of(data, uid),
                    "feed_logs": (u.get("auto_feed_logs") or [])[-5:],
                    "work_logs": (u.get("auto_work_logs") or [])[-5:],
                },
            })
        return json_response({"pets": pets})


async def web_toggle_record_auto():
    """运行记录·宠物记录：管理员在 WebUI 直接切换某个用户的 自动照顾/自动打工 开关。
    入参 {uid, key: purchase|work, on: bool}；同一用户维度，跨群共享。
    规则与群聊指令一致：开启自动照顾 → 自动开启自动打工；不开启自动照顾则不允许开启自动打工。
    2.1.0：管理员开启自动照顾时立即触发一次自动照顾（清空失败冷却，宠物处于第 3/4 档则立即补满，
    触发来源 = 管理员开启；3.0 经 core.service("pet_auto_care") 联动）。"""
    core = _core()
    if core is None:
        return error_response("运行记录模块未初始化（register 未调用）", status_code=500)
    try:
        payload = await request.json(default={})
    except Exception:
        payload = {}
    uid = str(payload.get("uid") or "").strip()
    key = str(payload.get("key") or "").strip()
    on = bool(payload.get("on"))
    if not uid:
        return error_response("uid 不能为空", status_code=400)
    if key not in ("purchase", "work"):
        return error_response("key 只能是 purchase 或 work", status_code=400)
    async with core.lock:
        data = core.data
        if uid not in (data.get("pets") or {}):
            return error_response("该用户还没有宠物", status_code=400)
        u = core.ensure_user(uid)
        if key == "work":
            if on and not u.get("auto_feed_enabled"):
                return error_response("该用户未开启自动照顾，无法开启自动打工（请先开启自动照顾）", status_code=400)
            u["auto_work_enabled"] = on
            if on:
                u["auto_work_next"] = 0  # 兼容旧字段（2.2.7 起打工循环按忙碌状态直接判定）
                # 3.0：自动打工/每日结算循环由 core 统一巡检协程常驻承载，无需懒启动
            msg = "已开启自动打工" if on else "已关闭自动打工"
        else:  # purchase
            u["auto_feed_enabled"] = on
            if on:
                u["auto_work_enabled"] = True  # 用户开启自动照顾 → 自动开启自动打工
                msg = "已开启自动照顾（自动打工同步开启）"
                # 管理员开启自动照顾 → 立即触发一次照顾检查（触发来源 = 管理员开启）
                svc = core.service("pet_auto_care")
                due = getattr(svc, "care_due", None) if svc is not None else None
                run = getattr(svc, "care_run", None) if svc is not None else None
                if callable(due) and due(uid) and callable(run):
                    entry = run(uid, trigger="管理员开启")
                    if entry and entry.get("items"):
                        items = "、".join(
                            f"{'🛒' if it.get('src') == '购买' else '📦'}{it['name']}×{it['qty']}"
                            for it in entry["items"])
                        msg += f"；已立即触发自动照顾：{items}（花费 {entry.get('total', 0)} 金币）"
                    else:
                        msg += "；已立即触发自动照顾，但金币不足/无可用道具，未能补满（稍后自动重试）"
                elif svc is not None:
                    msg += "；已立即检查：宠物状态良好，无需照顾"
                # 自动化服务未挂载时跳过立即照顾（不追加提示）
            else:
                u["auto_work_enabled"] = False
                msg = "已关闭自动照顾（自动打工同步关闭）"
        core.save()
        return json_response({
            "ok": True, "msg": msg, "uid": uid, "key": key,
            "on": bool(u["auto_work_enabled"] if key == "work" else u["auto_feed_enabled"]),
            "purchase_on": bool(u.get("auto_feed_enabled")),
            "work_on": bool(u.get("auto_work_enabled")),
            "work_base": int(u.get("work_base", 0) or 0),
        })


async def web_waive_auto_loan():
    """运行记录·宠物记录：管理员豁免某个用户的自动化贷款（2.2.7）。
    入参 {uid}。豁免 = 视为该用户已完成还款：自动化贷款余额清零，
    并从基准金币中扣除相应金额（豁免的照顾缺口一并抹平，可为负）。"""
    core = _core()
    if core is None:
        return error_response("运行记录模块未初始化（register 未调用）", status_code=500)
    try:
        payload = await request.json(default={})
    except Exception:
        payload = {}
    uid = str(payload.get("uid") or "").strip()
    if not uid:
        return error_response("uid 不能为空", status_code=400)
    async with core.lock:
        data = core.data
        u = core.ensure_user(uid)
        waived = _auto_loan_balance_of(data, uid)
        if waived <= 0:
            return error_response("该用户没有未还清的自动化贷款", status_code=400)
        # 余额清零（视为已还款）并扣除相应的基准金币（豁免的照顾缺口随之消除）
        u["auto_loan"] = 0
        u["work_base"] = int(u.get("work_base", 0) or 0) - int(round(waived))
        core.save()
        return json_response({
            "ok": True, "msg": f"已豁免自动化贷款 {waived:.2f} 金币（视为已还款，基准金币已同步扣除）",
            "uid": uid, "waived": round(waived, 2),
            "work_base": int(u.get("work_base", 0) or 0),
            "auto_loan_owed": 0,
        })


def _record_pet_detail_payload(core, uid: str, pet: dict) -> dict:
    """（同步，锁内调用）单只宠物的完整详情数据（2.2.1）：
    分类展示宠物各项信息（基本档案/当前状态/每日结算/自动化/特殊记录/仓库道具），
    以及每种行为（每日结算/打工/玩耍/使用道具/治疗/自动照顾/自动打工）造成的属性变化记录。"""
    data = core.data
    u = data.get("users", {}).get(uid) or {}
    custom = core.custom_name_of(uid)
    now_ts = datetime.now().timestamp()
    sat_max, thr_max, sta_max, mood_max = _attr_max(core, pet["health"])
    busy_until = _pet_busy_until(pet)
    busy = None
    if busy_until > now_ts:
        busy = {
            "activity": pet.get("busy_activity"),
            "item": pet.get("busy_item"),
            "until": busy_until,
            "remaining_min": int((busy_until - now_ts) // 60),
        }
    tickets = {
        "sat": pet.get("satiety", 0), "thr": pet.get("thirst", 0),
        "sta": pet.get("stamina", 0), "mood": pet.get("mood", 0),
        "health": pet.get("health", 0),
        "sat_max": sat_max, "thr_max": thr_max, "sta_max": sta_max,
        "mood_max": mood_max,
        "health_max": core.param("PET_MAX_HEALTH", PET_MAX_HEALTH),
        "sat_red": _attr_is_red(core, "饱食", pet.get("satiety", 0), pet.get("health", 0)),
        "thr_red": _attr_is_red(core, "口渴", pet.get("thirst", 0), pet.get("health", 0)),
        "mood_red": _attr_is_red(core, "心情", pet.get("mood", 0), pet.get("health", 0)),
        "health_red": _attr_is_red(core, "健康", pet.get("health", 0), pet.get("health", 0)),
    }
    level, exp_got, exp_need = _pet_exp_progress(core, float(pet.get("exp", 0) or 0))
    # 属性变化记录（attr_log，最新在前）
    logs = []
    for it in (pet.get("attr_log") or []):
        if not isinstance(it, dict):
            continue
        logs.append({
            "time": str(it.get("time", "") or ""),
            "ts": float(it.get("ts", 0) or 0),
            "cat": str(it.get("cat", "其他") or "其他"),
            "behavior": str(it.get("behavior", "") or ""),
            "changes": it.get("changes", {}) or {},
            "extra": str(it.get("extra", "") or ""),
            # 2.2.2：变动后属性快照（属性条可视化用；旧记录可能没有）
            "after": it.get("after", {}) or {},
            # 2.2.2：变动发生时的属性上限快照（旧记录可能没有）
            "max": it.get("max", {}) or {},
            # 2.2.7：本次使用的道具（名称/数量/效果快照；旧记录可能没有）
            "items": it.get("items") or [],
        })
    logs.reverse()
    ls = pet.get("last_settle") or {}
    return {
        "uid": uid,
        # 2.2.2：主人ID显示平台昵称（自定义昵称 → 账户昵称 → uid）
        "nick": custom or _record_account_name(data, uid) or uid,
        "name": pet.get("name", "宠物"),
        "level": level,
        "exp": round(float(pet.get("exp", 0) or 0), 1),
        "exp_got": round(exp_got, 1),
        "exp_need": round(exp_need, 1),
        "tier": _worst_tier(core, pet.get("satiety", 0), pet.get("thirst", 0), pet.get("mood", 0), pet.get("health", 0)),
        "weak": bool(pet.get("weak")),
        "guard": bool(pet.get("guard")),
        "attrs": tickets,
        "busy": busy,
        "last_settle_date": str(pet.get("last_settle_date", "") or ""),
        "last_settle": {
            "date": str(ls.get("date", "") or ""),
            "satiety_d": ls.get("satiety_d", 0),
            "thirst_d": ls.get("thirst_d", 0),
            "stamina_d": ls.get("stamina_d", 0),
            "mood_d": ls.get("mood_d", 0),
            "health_d": ls.get("health_d", 0),
            "tier": ls.get("tier", 0),
            "rested_well": bool(ls.get("rested_well")),
            "sick": bool(ls.get("sick")),
        } if ls else None,
        "pill": {
            "used": int(pet.get("pill_used_count", 0) or 0),
            "limit": int(core.param("PILL_DAILY_LIMIT", PILL_DAILY_LIMIT) or 3),
            "today": str(pet.get("pill_used_date", "") or ""),
        },
        "money_event": {
            "count": int(pet.get("money_event_count", 0) or 0),
            "max": int(core.param("MONEY_EVENT_MAX_PER_DAY", MONEY_EVENT_MAX_PER_DAY) or 5),
            "today": str(pet.get("money_event_date", "") or ""),
        },
        "bag": [{"name": str(k), "qty": int(v)} for k, v in (pet.get("inventory") or {}).items()],
        "auto": {
            "purchase_on": bool(u.get("auto_feed_enabled")),
            "purchase_global": bool(core.param("AUTO_FEED_ENABLED", AUTO_FEED_ENABLED)),
            "work_on": bool(u.get("auto_work_enabled")),
            "work_global": bool(core.param("AUTO_WORK_ENABLED", AUTO_WORK_ENABLED)),
            "work_base": int(u.get("work_base", 0) or 0),
            # 2.2.7：自动化贷款余额（无上限无逾期，仅自动照顾可贷）
            "auto_loan_owed": _auto_loan_balance_of(data, uid),
            "feed_logs": (u.get("auto_feed_logs") or [])[-5:],
            "work_logs": (u.get("auto_work_logs") or [])[-5:],
        },
        "attr_log": logs,
        "attr_labels": dict(ATTR_SHORT),
    }


async def web_get_record_pet_detail():
    """运行记录·宠物记录·详情（2.2.1）：POST {uid} → 单只宠物的完整详情
    （当前状态/基本档案/每日结算/自动化/特殊记录 + 每种行为的属性变化记录 attr_log），
    宠物记录页点击宠物卡片进入详情页时按需拉取。"""
    core = _core()
    if core is None:
        return error_response("运行记录模块未初始化（register 未调用）", status_code=500)
    try:
        payload = await request.json(default={})
    except Exception:
        payload = {}
    uid = str(payload.get("uid") or "").strip()
    if not uid:
        return error_response("uid 不能为空", status_code=400)
    try:
        async with core.lock:
            data = core.data
            pet = data.get("pets", {}).get(uid)
            if not isinstance(pet, dict):
                return json_response({"pet": None})
            return json_response({"pet": _record_pet_detail_payload(core, uid, pet)})
    except Exception as e:
        logger.error(f"[插件] 运行记录·宠物详情 读取失败: {e}")
        return json_response({"pet": None, "error": str(e)})


async def web_get_record_prices():
    """运行记录·商店价格：最近的价格变动记录 + 当前窗口折扣信息。"""
    core = _core()
    if core is None:
        return error_response("运行记录模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        data = core.data
        added = _record_shop_price_window(core, data)
        if added:
            core.save()
        enabled = bool(core.param("SHOP_PRICE_FLOAT_ENABLED", SHOP_PRICE_FLOAT_ENABLED))
        now = datetime.now()
        _start, wid = _shop_price_window(now)
        disc = _shop_discount_map_cached(core, wid) if enabled else {}
        current = [
            {"name": it.get("name"), "base": int(it.get("price", 0) or 0),
             "price": max(1, int(round(int(it.get("price", 0) or 0) * disc.get(it.get("name"), 1.0)))),
             "mult": round(disc.get(it.get("name"), 1.0), 2)}
            for it in _shop_raw_items(core) if isinstance(it, dict)
        ] if enabled else []
        special = _start in tuple(core.param("SHOP_PRICE_SPECIAL_HOURS", SHOP_PRICE_SPECIAL_HOURS))
        return json_response({
            "enabled": enabled,
            "now": now.strftime("%Y-%m-%d %H:%M"),
            "window": wid,
            "special": special,
            "current": current,
            "records": (data.get("shop_price_records") or [])[
                -int(core.param("SHOP_PRICE_RECORD_MAX", SHOP_PRICE_RECORD_MAX) or 60):],
        })


def _record_user_nick(core, data: dict, uid: str) -> str:
    """用户昵称回退链：自定义昵称 → 账户昵称 → uid。"""
    custom = core.custom_name_of(uid)
    if custom:
        return custom
    acc = _record_account_name(data, uid)
    return acc or str(uid)


def _record_users_payload(core):
    """（同步，锁内调用）组装全部用户信息卡片的基础数据（2.2.0：详情字段按需拉取）。"""
    data = core.data
    now_ts = datetime.now().timestamp()
    users = []
    for uid, u in (data.get("users") or {}).items():
        if not isinstance(u, dict):
            continue
        pet = data.get("pets", {}).get(uid)
        farm = data.get("farms", {}).get(uid)
        bank = data.get("bank", {}).get(uid)
        custom = core.custom_name_of(uid)
        acc_name = _record_account_name(data, uid)
        nick = custom or acc_name or uid
        la = float(u.get("last_active", 0) or 0)
        # 登录活跃时间：显示相对时间（如 3 分钟前 / 昨天 14:30）
        la_text = "-"
        if la > 0:
            diff = now_ts - la
            if diff < 60:
                la_text = "刚刚"
            elif diff < 3600:
                la_text = f"{int(diff // 60)} 分钟前"
            elif diff < 86400:
                la_text = f"{int(diff // 3600)} 小时前"
            else:
                la_text = f"{int(diff // 86400)} 天前"
        # ---- 卡片级汇总字段（详情字段由 records/users/detail 按需拉取） ----
        pet_summary = None
        if isinstance(pet, dict):
            pet_summary = {
                "level": int(pet.get("level", 0) or 0),
                "weak": bool(pet.get("weak")),
            }
        farm_summary = None
        if isinstance(farm, dict):
            farm_summary = {"level": int(farm.get("level", 0) or 0)}
        bank_summary = None
        if isinstance(bank, dict):
            # 2.2.3：银行汇总统计取自银行插件（3.0 经 core.service("bank_deposit").summary）
            svc = core.service("bank_deposit")
            bank_summary = svc.summary(uid) if svc is not None else None
        users.append({
            "uid": uid,
            "nick": nick,
            "nick_source": "custom" if custom else ("account" if acc_name else "uid"),
            "coins": int(u.get("coins", 0) or 0),
            "fav": round(float(u.get("favorability", 0) or 0), 1),
            "fav_level": core.level_of(float(u.get("favorability", 0) or 0)),
            "pet": pet_summary,
            "farm": farm_summary,
            "bank": bank_summary,
            "last_active": la,
            "last_active_text": la_text,
            "auto": {
                "purchase_on": bool(u.get("auto_feed_enabled")),
                "work_on": bool(u.get("auto_work_enabled")),
                "work_base": int(u.get("work_base", 0) or 0),
            },
            # 2.1.0：前端本地排序键（昵称首拼 / 昵称首字笔画 由后端计算好，
            # 前端切换排序方式/升降序时不再重新请求，规避带 query 的 API 兼容性问题）
            "sort_pinyin": list(_record_nick_sort_key(nick, "pinyin")),
            "sort_stroke": list(_record_nick_sort_key(nick, "stroke")),
        })
    # 后端默认按最后活跃时间降序（排序交给前端，这里仅做基础稳定序）
    users.sort(key=lambda x: x["last_active"], reverse=True)
    return json_response({"users": users})


async def web_get_record_users():
    """运行记录·用户信息（2.1.0）：全部用户的信息卡片（基础信息）。
    2.2.0：列表只返回卡片级基础字段（昵称/金币/好感等级/宠物·农场等级/银行汇总/活跃时间/排序键），
    仓库/农场地块/存单明细等完整详情由「records/users/detail」按需拉取，避免全量下发给终端。
    排序键（昵称首拼/首字笔画）由后端计算随用户数据返回，前端本地排序（不依赖 query）。"""
    core = _core()
    if core is None:
        return error_response("运行记录模块未初始化（register 未调用）", status_code=500)
    try:
        async with core.lock:
            return _record_users_payload(core)
    except Exception as e:
        logger.error(f"[插件] 运行记录·用户信息 读取失败: {e}")
        return json_response({"users": [], "error": str(e)})


def _record_user_detail_payload(core, uid: str, u: dict) -> dict:
    """（同步，锁内调用）单个用户的完整详情数据（2.2.0：按需拉取，替代全量下发的详情字段）。"""
    data = core.data
    pet = data.get("pets", {}).get(uid)
    farm = data.get("farms", {}).get(uid)
    bank = data.get("bank", {}).get(uid)
    # ---- 宠物状态（详情独立附属卡片用） ----
    pet_info = None
    if isinstance(pet, dict):
        pet_info = {
            "name": pet.get("name", "宠物"),
            "level": pet.get("level", 0),
            "weak": bool(pet.get("weak")),
            "exp": round(float(pet.get("exp", 0) or 0), 1),
            "busy_activity": pet.get("busy_activity"),
            "busy_item": pet.get("busy_item"),
            "busy_until": float(pet.get("busy_until", 0) or 0),
        }
    # ---- 农场实时状态（详情独立附属卡片用：每块土地） ----
    farm_info = None
    if isinstance(farm, dict):
        now_ts = datetime.now().timestamp()
        crops_all = _load_crops(core)
        plots = []
        for i, p in enumerate((farm.get("plots") or []), start=1):
            if not isinstance(p, dict):
                continue
            crop = p.get("crop")
            grade_name = _plot_grade(core, int(p.get("grade", 0) or 0))[0]
            if crop is None:
                plots.append({"no": i, "grade": int(p.get("grade", 0) or 0), "grade_name": grade_name,
                              "crop": None, "mature": False, "remain_min": 0,
                              "yield": 0, "income": 0, "advance_min": 0})
            else:
                c = _find_item(crops_all, crop)
                g = _plot_growth(core, p, c, now_ts) if c else None
                mature = now_ts >= float(p.get("mature_ts", 0) or 0)
                remain = max(0, float(p.get("mature_ts", 0) or 0) - now_ts)
                yield_n = int(p.get("yield", 0) or 0)
                plots.append({"no": i, "grade": int(p.get("grade", 0) or 0), "grade_name": grade_name,
                              "crop": str(crop), "mature": mature,
                              "remain_min": int(remain // 60),
                              "yield": yield_n,
                              "income": int(round(yield_n * float(c["crop_price"]))) if c else 0,
                              "advance_min": (int(g["advance_sec"] // 60) if g and g["advance_sec"] > 60 else 0)})
        # 2.2.3：农场仓库（作物/种子/肥料，按指令响应「农场仓库」的版式：名称 ×N 可售 X金币 / 小时不可售）
        wh = farm.get("warehouse", {}) if isinstance(farm.get("warehouse"), dict) else {}
        warehouse = {}
        for gkey, label in [("crops", "作物"), ("seeds", "种子"), ("fertilizers", "肥料")]:
            items = []
            for nm, cnt in ((wh.get(gkey) or {}).items()):
                if gkey == "fertilizers":
                    items.append({"name": str(nm), "qty": round(float(cnt or 0), 2),
                                  "hours": True, "price": None})
                    continue
                c = _find_item(crops_all, nm)
                if c:
                    price = float(c["crop_price"] if gkey == "crops" else c["seed_sell_price"])
                else:
                    price = 0.0
                items.append({"name": str(nm), "qty": int(cnt or 0), "hours": False, "price": price})
            warehouse[label] = items
        # 2.2.3：被偷记录（未收割 或 被偷批次收割后 24h 内有效，最新在前）
        steals = []
        for it in (farm.get("steal_infos") or []):
            if not isinstance(it, dict):
                continue
            ht = it.get("harvest_ts")
            if ht is not None and now_ts - float(ht or 0) > 86400:
                continue
            steals.append({
                "time": datetime.fromtimestamp(float(it.get("ts", 0) or 0)).strftime("%Y-%m-%d %H:%M"),
                "thief_name": str(it.get("thief_name", "") or ""),
                "items": it.get("items", []) or [],
                "harvested": ht is not None,
            })
        steals.reverse()
        farm_info = {
            "level": int(farm.get("level", 0) or 0),
            "exp": round(float(farm.get("exp", 0) or 0), 1),
            "plots": plots,
            "total_profit": int(farm.get("total_profit", 0) or 0),
            "warehouse": warehouse,
            "steal_infos": steals[:30],
        }
    # ---- 仓库（宠物背包，详情独立附属卡片用） ----
    bag = []
    if isinstance(pet, dict):
        inv = pet.get("inventory") or {}
        if isinstance(inv, dict):
            for nm, cnt in inv.items():
                n = int(cnt or 0)
                if n > 0:
                    bag.append({"name": str(nm), "qty": n})
            bag.sort(key=lambda x: (-x["qty"], x["name"]))
    # ---- 银行（详情独立附属卡片用：存单列表；统计数值取自银行插件 summary，WebUI 不另行统计） ----
    bank_info = None
    if isinstance(bank, dict) and bank.get("deposits"):
        deposits = []
        for d in (bank["deposits"] or []):
            if not isinstance(d, dict):
                continue
            deposits.append({
                "amount": int(d.get("amount", 0) or 0),
                "interest": int(d.get("interest", 0) or 0),
                "status": d.get("status", "locked"),
                "hours": int(d.get("hours", 0) or 0),
                "deposit_time": d.get("deposit_time", ""),
                "base_rate": float(d.get("base_rate", 0) or 0),
                "bonus_rate": float(d.get("bonus_rate", 0) or 0),
            })
        svc = core.service("bank_deposit")
        bank_info = svc.summary(uid) if svc is not None else None
        if isinstance(bank_info, dict):
            bank_info["deposits"] = deposits[:8]  # 最近 8 笔，前端折叠展示
    # ---- 2.2.3：详情页扩展（宠物属性/最近变动、农场记录与统计、签到日历、金币流水、欠款、最后活跃） ----
    la = float(u.get("last_active", 0) or 0)
    la_text = "-"
    if la > 0:
        diff = datetime.now().timestamp() - la
        if diff < 60:
            la_text = "刚刚"
        elif diff < 3600:
            la_text = f"{int(diff // 60)} 分钟前"
        elif diff < 86400:
            la_text = f"{int(diff // 3600)} 小时前"
        else:
            la_text = f"{int(diff // 86400)} 天前"
    # 宠物属性 + 最近一次属性变动（attr_log 最新一条）
    if pet_info is not None:
        sat_max, thr_max, sta_max, mood_max = _attr_max(core, pet["health"])
        health_max = core.param("PET_MAX_HEALTH", PET_MAX_HEALTH)
        pet_info["attrs"] = {
            "satiety": round(float(pet.get("satiety", 0) or 0), 1),
            "thirst": round(float(pet.get("thirst", 0) or 0), 1),
            "stamina": round(float(pet.get("stamina", 0) or 0), 1),
            "mood": round(float(pet.get("mood", 0) or 0), 1),
            "health": round(float(pet.get("health", 0) or 0), 1),
            "satiety_max": sat_max, "thirst_max": thr_max,
            "stamina_max": sta_max, "mood_max": mood_max, "health_max": health_max,
            "satiety_red": _attr_is_red(core, "饱食", pet.get("satiety", 0), pet.get("health", 0)),
            "thirst_red": _attr_is_red(core, "口渴", pet.get("thirst", 0), pet.get("health", 0)),
            "mood_red": _attr_is_red(core, "心情", pet.get("mood", 0), pet.get("health", 0)),
            "health_red": _attr_is_red(core, "健康", pet.get("health", 0), pet.get("health", 0)),
        }
        pet_info["last_change"] = None
        for it in reversed(pet.get("attr_log") or []):
            if isinstance(it, dict):
                pet_info["last_change"] = {
                    "time": str(it.get("time", "") or ""),
                    "cat": str(it.get("cat", "") or ""),
                    "behavior": str(it.get("behavior", "") or ""),
                    "changes": it.get("changes", {}) or {},
                    "extra": str(it.get("extra", "") or ""),
                }
                break
    # 农场变化记录（farm_logs，最新在前，最近 60 条）+ 累计统计（farm_stats）
    farm_log_list = [x for x in (u.get("farm_logs") or []) if isinstance(x, dict)][-60:]
    farm_log_list.reverse()
    farm_stat = u.get("farm_stats") if isinstance(u.get("farm_stats"), dict) else {}
    # 签到日历（signin_logs，每天一条）+ 累计签到天数 / 累计签到金币
    sign_logs = [x for x in (u.get("signin_logs") or []) if isinstance(x, dict)][-120:]
    # 金币流水（ledger，最新在前，最近 60 条）
    ledger = [x for x in reversed(data.get("ledger", {}).get(uid) or []) if isinstance(x, dict)][:60]
    # 欠款账单统计取自贷款插件 debt_summary（生效账单数 + 含息总额），WebUI 不另行统计
    loan_svc = core.service("bank_loan")
    debt = loan_svc.debt_summary(uid) if loan_svc is not None else None
    return {
        "uid": uid,
        "nick": _record_user_nick(core, data, uid),
        "fav": round(float(u.get("favorability", 0) or 0), 1),
        "fav_level": core.level_of(float(u.get("favorability", 0) or 0)),
        "coins": int(u.get("coins", 0) or 0),
        "pet": pet_info,
        "farm": farm_info,
        "bag": bag,
        "bank": bank_info,
        "auto": {
            "purchase_on": bool(u.get("auto_feed_enabled")),
            "work_on": bool(u.get("auto_work_enabled")),
            "work_base": int(u.get("work_base", 0) or 0),
            # 2.2.1：自动化贷款当前未还清欠款总额（0 = 无欠款）
            "auto_loan_owed": _auto_loan_balance_of(data, uid),
        },
        # 2.2.3 扩展字段
        "last_active_text": la_text,
        "farm_logs": farm_log_list,
        "farm_stat": {
            "plant": int(farm_stat.get("plant", 0) or 0),
            "fertilize": int(farm_stat.get("fertilize", 0) or 0),
            "harvest": int(farm_stat.get("harvest", 0) or 0),
            "harvest_yield": int(farm_stat.get("harvest_yield", 0) or 0),
            "steal": int(farm_stat.get("steal", 0) or 0),
            "steal_gain": int(farm_stat.get("steal_gain", 0) or 0),
            "cost": int(farm_stat.get("cost", 0) or 0),
        },
        "sign_logs": sign_logs,
        "sign_total": int(u.get("signin_total", 0) or 0),
        "sign_coins_all": int(u.get("signin_coins_all", 0) or 0),
        "ledger": ledger,
        "debt": debt,
    }


async def web_get_record_user_detail():
    """运行记录·用户信息·详情（2.2.0）：POST {uid} → 单个用户的完整详情
    （仓库/农场地块/宠物/银行存单/自动化），页面展开卡片时才拉取。"""
    core = _core()
    if core is None:
        return error_response("运行记录模块未初始化（register 未调用）", status_code=500)
    try:
        payload = await request.json(default={})
    except Exception:
        payload = {}
    uid = str(payload.get("uid") or "").strip()
    if not uid:
        return error_response("uid 不能为空", status_code=400)
    try:
        async with core.lock:
            data = core.data
            u = data.get("users", {}).get(uid)
            if not isinstance(u, dict):
                return json_response({"user": None})
            return json_response({"user": _record_user_detail_payload(core, uid, u)})
    except Exception as e:
        logger.error(f"[插件] 运行记录·用户详情 读取失败: {e}")
        return json_response({"user": None, "error": str(e)})


# ================= 端点清单与注册 =================
# 与 2.3.0 main.py 的 _web_apis 中 records/* 条目一一对应（顺序一致）。
# 注意：本清单为裸处理器（未套 LAN 访问门）；webui 层如需统一包门可基于本清单再包装。
ENDPOINTS = [
    ("records/pets", "GET", web_get_record_pets, "运行记录：全部宠物卡片（状态/活动/自动信息）"),
    ("records/pets/auto", "POST", web_toggle_record_auto, "运行记录：切换用户自动照顾/自动打工开关"),
    ("records/pets/loan_waive", "POST", web_waive_auto_loan, "运行记录：豁免用户自动化贷款（视为已还款并扣除基准金币）"),
    # 2.2.1：宠物详情按需拉取（点击宠物记录卡片进入详情页，含每种行为的属性变化记录）
    ("records/pets/detail", "POST", web_get_record_pet_detail, "运行记录：单只宠物完整信息（属性变化记录，按需）"),
    ("records/prices", "GET", web_get_record_prices, "运行记录：商店价格变动"),
    # 2.1.0：WebUI 运行记录页 · 用户信息
    ("records/users", "GET", web_get_record_users, "运行记录：全部用户信息卡片（基础信息）"),
    # 2.2.0：用户详情按需拉取（页面展开卡片时才请求，避免全量下发）
    ("records/users/detail", "POST", web_get_record_user_detail, "运行记录：单个用户完整信息（按需）"),
]


def register(core):
    """注册运行记录相关 Web API（由 webui 插件在 register 末尾调用）"""
    global _CORE
    _CORE = core
    for path, method, handler, desc in ENDPOINTS:
        core.context.register_web_api(f"/{PLUGIN_NAME}/{path}", handler, [method], desc)
    return [(path, method) for path, method, _h, _d in ENDPOINTS]
