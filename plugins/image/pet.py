# -*- coding: utf-8 -*-
"""图片响应模块 · 宠物渲染器（2.3.0 modules/pet.py 渲染层移植）。

本模块移植四个渲染函数（布局与 2.3.0 完全一致，方法调用改为模块级函数）：
  render_pet_status  ← 老 _render_pet_status_image（「宠物」指令：标题/自动化信息/预留位/
                       宠物状态卡（五属性条）/ 底部双卡（属性变化+进度条 ｜ 当前项目+预计完成））
  render_work_play   ← 老 _render_work_play_image(+_inner)（打工/玩耍列表：宠物信息卡 + 内容卡片网格）
  render_shop        ← 老 _render_shop_image（宠物商店：分类卡片网格 + 底部特殊道具提示）
  render_bag         ← 老 _render_bag_image（背包：标题区/两级分类卡片网格/页尾大卡片）
返回 ("image", path)，渲染环境不可用或异常时返回 None（调用方回退纯文本）。
老插件实例方法/全局配置改为模块级等价实现：配置项仍优先读本模块 globals() 注入值，
缺省回退 core 常量（与 2.3.0 默认值一致）。
"""
import random
from datetime import datetime, date

from astrbot.api import logger

from ...core import (PET_MAX_HEALTH, PET_MAX_LEVEL, ATTR_SHORT, ATTR_LABELS,
                     PILL_NAME, EXP_BALL_NAME, PILL_DAILY_LIMIT, EXP_BALL_DAILY_LIMIT,
                     SIGNIN_NO_REWARD_CHANCE, SIGNIN_PILL_CHANCE, SIGNIN_BALL_CHANCE,
                     PET_ATTR_MAX_RANGES, AUTO_FEED_ENABLED, AUTO_WORK_ENABLED,
                     SHOP_PRICE_FLOAT_ENABLED, SHOP_PRICE_SPECIAL_HOURS,
                     SHOP_PRICE_DISCOUNT_MIN, SHOP_PRICE_DISCOUNT_MAX,
                     SHOP_PRICE_DISCOUNT_LO, SHOP_PRICE_DISCOUNT_HI,
                     RANK_PET_EXP_W, RANK_PET_HEALTH_W, RANK_PET_ATTR_W, FONT_FILE)
from .common import (DS_BG, DS_SURFACE, DS_BORDER, DS_BORDER_2, DS_TEXT, DS_TEXT_2,
                     DS_MUTED, DS_ACCENT, DS_GOLD, DS_GOLD_2, DS_DANGER, DS_SUCCESS,
                     DS_GREEN_SOFT, ensure_pillow, load_fonts, title_font, text_measurer,
                     make_wrapper, save_temp_image, dtext)

__all__ = ["render_pet_status", "render_work_play", "render_shop", "render_bag"]


# ================= 宠物数据小工具（老 PetMixin / base / farm / rank 同款实现） =================
# 2.2.7 统一数值管理：档位由「属性最大值数据」统一推导——
# 一档下限 = 60% 上限、二档下限 = 35%、三档下限 = 15%，低于 15% 为四档。
_TIER_PCTS = (0.6, 0.35, 0.15)


def _parse_attr_max_ranges():
    """解析 PET_ATTR_MAX_RANGES（WebUI「设置 → 宠物 → 属性」可编辑）。
    格式：健康值下限=饱食,口渴,体力,心情，竖线分隔。返回 {健康下限: (饱食, 口渴, 体力, 心情)}；
    解析失败回退默认四档。"""
    raw = globals().get("PET_ATTR_MAX_RANGES", PET_ATTR_MAX_RANGES) or ""
    if not isinstance(raw, str) or not raw.strip():
        return {140: (200.0, 200.0, 200.0, 120.0),
                80: (120.0, 120.0, 120.0, 100.0),
                40: (100.0, 100.0, 100.0, 100.0),
                0: (80.0, 80.0, 60.0, 80.0)}
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
    return out or {140: (200.0, 200.0, 200.0, 120.0),
                   80: (120.0, 120.0, 120.0, 100.0),
                   40: (100.0, 100.0, 100.0, 100.0),
                   0: (80.0, 80.0, 60.0, 80.0)}


def _attr_max(health: float):
    """返回 (饱食上限, 口渴上限, 体力上限, 心情上限)，由健康度决定。
    默认规则：健康 140-200 → 200/200/200/120；80-139 → 120/120/120/100；
    40-79 → 100/100/100/100；0-39 → 80/80/60/80。健康度最大值 200（PET_MAX_HEALTH）。"""
    ranges = _parse_attr_max_ranges()
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


def _worst_tier(satiety: float, thirst: float, mood: float, health: float = 0.0) -> int:
    """饱食/口渴/心情对健康的影响档位，每个属性单独定档后取最差档（4 最差）。
    health 用于确定当前属性上限。"""
    sat_max, thr_max, sta_max, mood_max = _attr_max(float(health or 0))
    return max(_attr_tier(satiety, sat_max),
               _attr_tier(thirst, thr_max),
               _attr_tier(mood, mood_max))


def _attr_is_red(label: str, val, health: float = 0.0) -> bool:
    """属性值是否应标红（进入第 3/4 档 → 红，即值 < 35% 上限）。
    兼容「饱食度/口渴值/心情值/体力值/健康度」与「饱食/口渴/心情/体力/健康」两种标签。"""
    try:
        val = float(val)
    except (TypeError, ValueError):
        return False
    sat_max, thr_max, sta_max, mood_max = _attr_max(float(health or 0))
    max_v = {"饱食": sat_max, "饱食度": sat_max,
             "口渴": thr_max, "口渴值": thr_max,
             "体力": sta_max, "体力值": sta_max,
             "心情": mood_max, "心情值": mood_max,
             "健康": PET_MAX_HEALTH, "健康度": PET_MAX_HEALTH}.get(label)
    if max_v is None:
        return False
    return val < max_v * _TIER_PCTS[1]


def _pet_level_from_exp(exp: float) -> int:
    """新经验体系：所需经验 = 当前等级 × 100（累计 100+200+...+（L-1）×100 升到 Lv.L）"""
    exp = max(0.0, float(exp))
    # 解 100*(L-1)*L/2 <= exp → L = floor((1+sqrt(1+8*exp/100))/2)
    L = int((1 + (1 + 8 * exp / 100.0) ** 0.5) / 2)
    return min(PET_MAX_LEVEL, L)


def _pet_exp_progress(exp: float) -> tuple:
    """返回 (当前等级, 本级已得经验, 本级所需经验)，新经验体系：所需经验 = 当前等级 × 100"""
    exp = max(0.0, float(exp))
    level = _pet_level_from_exp(exp)
    need_prev = 100.0 * (level - 1) * level / 2.0  # 升到当前等级的累计经验
    got = exp - need_prev
    need = float(level) * 100.0
    return level, got, need


def _pet_busy_until(pet: dict) -> float:
    """打工/玩耍共用冷却计时器：返回忙碌结束时间戳（兼容旧数据 work_until / play_until）"""
    busy = float(pet.get("busy_until", 0) or 0)
    old = max(float(pet.get("work_until", 0) or 0), float(pet.get("play_until", 0) or 0))
    return max(busy, old)


def _fmt_duration(sec):
    """剩余时长文本（老 farm._fmt_duration 同款）"""
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


def _fmt_score(v):
    """积分显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0（老 rank._fmt_score 同款）"""
    v = float(v)
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _fmt_price(v):
    """价格显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0（老 farm._fmt_price 同款）"""
    v = float(v)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _pet_rank_entries(data: dict):
    """宠物排行榜条目（老 rank._rank_entries("pet") 同款）：按积分降序 [(score, uid, name)]"""
    ew = float(globals().get("RANK_PET_EXP_W", RANK_PET_EXP_W))
    hw = float(globals().get("RANK_PET_HEALTH_W", RANK_PET_HEALTH_W))
    aw = float(globals().get("RANK_PET_ATTR_W", RANK_PET_ATTR_W))
    entries = []
    for puid, pet in (data.get("pets") or {}).items():
        if not isinstance(pet, dict):
            continue
        exp = float(pet.get("exp", 0) or 0)
        health = float(pet.get("health", 0) or 0)
        others = (float(pet.get("satiety", 0) or 0) + float(pet.get("thirst", 0) or 0)
                  + float(pet.get("stamina", 0) or 0) + float(pet.get("mood", 0) or 0))
        score = exp * ew + health * hw + others * aw
        pname = str(pet.get("name", "") or "").strip() or str(puid)
        entries.append((score, str(puid), pname))
    # 积分降序；同分按用户 ID 稳定排序
    entries.sort(key=lambda e: (-e[0], e[1]))
    return entries


def _pet_rank_text(uid, data):
    """宠物排行榜的排行积分文本（标题右侧，老 PetMixin._pet_rank_text 同款）"""
    try:
        entries = _pet_rank_entries(data)
    except Exception:
        entries = []
    for i, (score, euid, _) in enumerate(entries, 1):
        if str(euid) == str(uid):
            return f"🐾 宠物榜 第{i}名 · {_fmt_score(score)}分"
    return "🐾 宠物榜未上榜"


def _effect_desc(effects: dict) -> str:
    """道具效果描述（老 base._effect_desc 同款）：饱食+10 口渴-5 形式"""
    parts = []
    for k, short in ATTR_SHORT.items():
        v = effects.get(k, 0)
        if v > 0:
            parts.append(f"{short}+{v:.0f}")
        elif v < 0:
            parts.append(f"{short}{v:.0f}")
    return " ".join(parts) if parts else "无效果"


# ================= 商店实时价格（老 _shop_price_window / _shop_discount_map / _pet_shop_price） =================
def _shop_price_window(now=None):
    """当前价格窗口：刷新时间固定偶数点（0/2/4/…/22 整点），同一窗口（2 小时）内价格稳定。
    返回 (窗口起始小时, 窗口标识串)；窗口标识形如 YYYYMMDD|HH，用于固定随机种子。"""
    now = now or datetime.now()
    start_hour = now.hour - (now.hour % 2)
    wid = "%s|%02d" % (now.strftime("%Y%m%d"), start_hour)
    return start_hour, wid


def _shop_discount_map(wid, names):
    """指定窗口的折扣映射 {商品名: 倍率}：特价时段（窗口起始 10/12/18/0 时）随机选取
    SHOP_PRICE_DISCOUNT_MIN~MAX 种商品按 二~八折（倍率 LO~HI）；其余窗口全部原价。
    按「窗口 id + 商品名」固定随机 → 同一窗口内价格稳定。"""
    start_hour = int(wid.split("|")[1])
    special = start_hour in tuple(globals().get("SHOP_PRICE_SPECIAL_HOURS", SHOP_PRICE_SPECIAL_HOURS))
    if not special:
        return {}
    lo = float(globals().get("SHOP_PRICE_DISCOUNT_LO", SHOP_PRICE_DISCOUNT_LO) or 0.2)
    hi = float(globals().get("SHOP_PRICE_DISCOUNT_HI", SHOP_PRICE_DISCOUNT_HI) or 0.8)
    cnt_lo = int(globals().get("SHOP_PRICE_DISCOUNT_MIN", SHOP_PRICE_DISCOUNT_MIN) or 2)
    cnt_hi = int(globals().get("SHOP_PRICE_DISCOUNT_MAX", SHOP_PRICE_DISCOUNT_MAX) or 5)
    n = max(0, min(cnt_hi, len(names)))
    if n < 1 or cnt_lo > n:
        cnt_lo = min(cnt_lo, n)
    if n < 1:
        return {}
    rng = random.Random("pet_shop_window_discount|%s" % wid)
    count = rng.randint(cnt_lo, n)
    chosen = rng.sample(sorted(names), count)
    out = {}
    for nm in chosen:
        out[nm] = round(rng.uniform(lo, hi), 2)
    return out


def _pet_shop_price(item, discount):
    """宠物商店实时价格：返回 (原价, 实时价, 是否打折)。默认关闭（SHOP_PRICE_FLOAT_ENABLED=False）。"""
    base = int(item.get("price", 0))
    if not bool(globals().get("SHOP_PRICE_FLOAT_ENABLED", SHOP_PRICE_FLOAT_ENABLED)):
        return base, base, False
    mult = discount.get(item["name"], 1.0)
    if mult >= 1.0 - 1e-9:
        return base, base, False
    price = max(1, int(round(base * mult)))
    return base, price, price != base


def _signin_reward_chances():
    """签到额外奖励池概率：返回 (无奖品, 属性丸, 经验球)；
    三者总和超过 1 时按比例归一，保证互斥奖池总和恒为 1。"""
    no_r = float(globals().get("SIGNIN_NO_REWARD_CHANCE", SIGNIN_NO_REWARD_CHANCE))
    pill_r = float(globals().get("SIGNIN_PILL_CHANCE", SIGNIN_PILL_CHANCE))
    ball_r = float(globals().get("SIGNIN_BALL_CHANCE", SIGNIN_BALL_CHANCE))
    total = no_r + pill_r + ball_r
    if total <= 1e-9:
        return 0.40, 0.30, 0.30
    if total > 1.0:
        return no_r / total, pill_r / total, ball_r / total
    return no_r, pill_r, ball_r


def render_pet_status(name, uid, data, pet, slot_msg=None,
                      changes=None, reason=None, coins_delta=None, exp_delta=None,
                      changes_fresh=False, progress_done=False, card2_hl=False):
    """「宠物」指令图片（2.0.0 新模板）：
    标题(<用户昵称>的宠物 + 右侧宠物排行数据) / 自动化信息（自动照顾/自动打工 开关状态+累计金币，
    2.1.1 起移至标题下方单行展示，无卡片边框，删除切换指令提示） / 预留位(红 #C00000，无内容则忽略) / 分割线 /
    宠物状态卡片（名称/状态/等级/经验/升级进度条 + 五项属性条）/ 底部双卡：
    - 底部卡片1（宽）：最近一次五大状态属性变化 + 变化原因 + 原打工/玩耍进度条模块
    - 底部卡片2（窄）：宠物正在进行的工作/玩耍项目 + 金币/经验变化 + 预计完成时间
    高亮规则：
    - 卡片1：仅在本次响应产生了新的状态变化（changes_fresh）或 进度条满（空闲）后的第一次响应
      （progress_done，内部自动判定）时黄色高亮
    - 卡片2：仅在本次响应发生了 打工/玩耍 变动（card2_hl）时黄色高亮
    预留位消息为即时消息：有效显示次数 = 1，仅产生新变化的那次响应显示。"""
    Image, ImageDraw = ensure_pillow()
    if Image is None:
        return None
    # 字号语义：标题 36（衬线）/ 宠物名 26（大两号）/ 状态 18 / 正文 20 / 经验 16 / 属性名 18 / 提示 16 / 描述 18
    # 附加 14/12 小字号：底部卡片单行自适应（内容超宽时逐级缩小，保证不触发自动换行）
    fonts = load_fonts(26, 18, 20, 16, 18, 16, 18, 14, 12)
    if fonts is None:
        return None
    (pet_name_font, status_font, lv_font,
     exp_font, attr_font, hint_font, desc_font) = fonts[:7]
    small_font, tiny_font = fonts[7], fonts[8]
    title_font_ = title_font(kind="pet")
    if title_font_ is None:
        return None

    now_ts = datetime.now().timestamp()
    busy_until = _pet_busy_until(pet)
    busy = now_ts < busy_until
    act = pet.get("busy_activity", "")
    status = "忙碌中" if busy else "空闲中"

    sat_max, thr_max, sta_max, mood_max = _attr_max(pet["health"])
    # 属性条数据：(标签, 当前值, 最大值, 提示词)
    attrs = [
        ("饱食度", pet["satiety"], sat_max, "宠物饿了"),
        ("口渴值", pet["thirst"], thr_max, "宠物渴了"),
        ("心情值", pet["mood"], mood_max, "宠物不开心"),
        ("体力值", pet["stamina"], sta_max, "宠物累了"),
        ("健康度", pet["health"], PET_MAX_HEALTH, "宠物生病了"),
    ]

    pad = 20
    title_h = 56  # 2.1.1：减小标题与下方自动化信息的间距
    inner = 10
    name_row_h = 34
    lv_row_h = 28
    bar_h = 26            # 普通进度条行高（条高 16 = 升级/空闲进度条）
    bar_h_px = 16
    attr_label_h = 22     # 属性名行高
    attr_bar_h = 16       # 属性条行高（条高 = 普通进度条的 30% ≈ 5px）
    attr_bar_px = max(4, int(bar_h_px * 0.3))
    rule_h = 18
    line_h = 26
    bottom_gap = 14       # 宠物状态卡片 与 底部双卡 之间的间距

    # 2.0.0：整张图片加宽为原宽度（640）的 110%，各模块布局与卡片 1/2 宽度比例不变
    width = 704
    content_w = width - pad * 2

    tw = text_measurer()
    if tw is None:
        return None
    wrap = make_wrapper(tw, content_w)

    # 升级进度（新经验体系）
    lv, got_exp, need_exp = _pet_exp_progress(float(pet.get("exp", 0.0)))
    exp_ratio = min(1.0, got_exp / need_exp) if need_exp > 0 else 1.0
    exp_text = f"经验 {got_exp:.0f}/{need_exp:.0f}"

    # ---------- 预留位（红 #C00000，无内容忽略） ----------
    slot_lines = []
    slot_h = 0
    if slot_msg:
        for ln in str(slot_msg).split("\n"):
            for wl in wrap(ln, desc_font, content_w):
                slot_lines.append(wl)
        slot_h = len(slot_lines) * line_h + 6

    # ---------- 最近变化 / 原因（底部卡片1） ----------
    # 只有显式未提供变化（None）时才回退到每日结算；显式空变化（如 经验球）保持「无变化」
    if changes is None:
        ls = pet.get("last_settle") or {}
        changes = {}
        for k, a in (("satiety_d", "satiety"), ("thirst_d", "thirst"),
                     ("stamina_d", "stamina"), ("mood_d", "mood"), ("health_d", "health")):
            if ls.get(k):
                changes[a] = ls[k]
    if not reason:
        reason = "昨晚结算" if pet.get("last_settle") else "暂无变化记录"
    # 五属性变化片段：饱食/口渴/体力/心情/健康 同时显示；
    # 有变化用深色，无变化用灰色隐去；尽量一行（提示字号+半角空格），超宽自动换行
    chg_segs = []
    for i, a in enumerate(("satiety", "thirst", "stamina", "mood", "health")):
        try:
            v = float(changes.get(a, 0) or 0)
        except (TypeError, ValueError):
            v = 0.0
        txt = f"{ATTR_SHORT[a]}{v:+.1f}"
        if i < 4:
            txt += " "  # 半角空格分隔（节省宽度，尽量一行）
        color = DS_TEXT_2 if abs(v) > 1e-9 else DS_MUTED
        chg_segs.append((txt, color))

    # 空闲进度条（原打工/玩耍进度条模块，移入底部卡片1）
    idle_ratio = 1.0
    if busy:
        start = float(pet.get("busy_start", 0) or 0)
        total = busy_until - start if busy_until > start else 1.0
        idle_ratio = min(0.9, max(0.05, 1.0 - (busy_until - now_ts) / total))  # 忙碌中进度不满
    idle_color = DS_GREEN_SOFT if not busy else DS_GOLD_2

    # ---------- 底部卡片2 数据（当前项目 / 金币经验变化 / 预计完成时刻 / 状态档位） ----------
    if busy:
        item = pet.get("busy_item", "")
        cur_txt = f"{('打工' if act == '打工' else '玩耍')}「{item}」" if item else ("打工中" if act == "打工" else "玩耍中")
        try:
            _done = datetime.fromtimestamp(busy_until)
            if _done.date() == date.today():
                eta_txt = f"预计 {_done.strftime('%H:%M')} 完成"
            else:
                eta_txt = f"预计 {_done.strftime('%m-%d %H:%M')} 完成"
        except Exception:
            eta_txt = "预计稍后完成"
    else:
        cur_txt = "空闲中"
        eta_txt = "—"
    reward_txt = "金币/经验 无变化"
    if coins_delta or exp_delta:
        parts = []
        if coins_delta:
            parts.append(f"金币{int(coins_delta):+}")
        if exp_delta:
            parts.append(f"经验{exp_delta:+.1f}")
        reward_txt = " ".join(parts)  # 紧凑格式（保证卡片2 一行放下，不触发自动换行）
    # 进度条满（空闲）后的第一次响应：内部自动判定并标记（调用方随后 _save）
    if not progress_done:
        _bu = float(pet.get("busy_until", 0) or 0)
        if _bu > 0 and now_ts >= _bu and not pet.get("_progress_done_notified"):
            progress_done = True
            pet["_progress_done_notified"] = True
    # 当前宠物所处的状态档位（饱食/口渴/心情 取最差档，1~4）
    tier = _worst_tier(pet["satiety"], pet["thirst"], pet["mood"], pet["health"])
    tier_txt = f"状态档位：{tier}/4"
    tier_color = DS_DANGER if tier >= 3 else DS_MUTED

    # ---------- 描述行（沿用原底部文案，显示在卡片1进度条下方） ----------
    if busy:
        act_label = "打工" if act == "打工" else "玩耍"
        desc = f"{pet.get('name', '宠物')}正在{act_label}"
    elif any(attrs[i][1] < (50 if i == 0 else (60 if i == 1 else (40 if i == 2 else (20 if i == 3 else 40))))
             for i in range(5)):
        desc = random.choice([
            f"{pet.get('name', '宠物')}看起来不太舒服，快照料一下吧～",
            f"{pet.get('name', '宠物')}有点不舒服，喂食 / 饮水 / 陪伴一下吧～",
            f"{pet.get('name', '宠物')}状态不佳，需要你的照顾～",
        ])
    elif pet.get("guard"):
        desc = random.choice([
            f"闲来没事，{pet.get('name', '宠物')}正在巡逻你的农场",
            f"{pet.get('name', '宠物')}尽职尽责，正在农场周围巡视～",
        ])
    else:
        desc = random.choice([
            f"{pet.get('name', '宠物')}正在悠闲地晒太阳～",
            f"{pet.get('name', '宠物')}精神饱满，随时可以出发！",
            f"{pet.get('name', '宠物')}正在开心地打盹～",
        ])

    # ---------- 布局与高度 ----------
    pet_card_h = inner * 2 + name_row_h + lv_row_h + bar_h + len(attrs) * (attr_label_h + attr_bar_h)

    # 卡片 1/2 宽度比例与原布局保持一致（按 110% 加宽后等比换算）：
    # 原布局 content=600：卡片1=423 / 卡片2=165 / 间距=12 → 等比放大到 content=664
    card_gap = 13
    card2_w = 183                      # 卡片2 收窄（等比）
    card1_w = content_w - card2_w - card_gap  # 卡片1 加宽（等比）
    c1_inner = 8

    # 多色分段文本：按片段测量换行行数 / 逐段绘制（支持自动换行）
    def seg_lines_n(segs, max_w, font):
        n = 1
        row_w = 0.0
        for t, _ in segs:
            w = tw(t, font)
            if row_w > 0 and row_w + w > max_w:
                n += 1
                row_w = w
            else:
                row_w += w
        return n

    def draw_segs(d, x0, y0, max_w, segs, font, lh):
        y = y0
        row = []
        row_w = 0.0
        for t, c in segs:
            w = tw(t, font)
            if row and row_w + w > max_w:
                x = x0
                for tt, cc in row:
                    dtext(d, (int(x), y), tt, font=font, fill=cc)
                    x += tw(tt, font)
                y += lh
                row = []
                row_w = 0.0
            row.append((t, c))
            row_w += w
        if row:
            x = x0
            for tt, cc in row:
                dtext(d, (int(x), y), tt, font=font, fill=cc)
                x += tw(tt, font)
            y += lh
        return y

    # 剩余时间：显示在进度条右下方（进度条外部）；空闲时不显示
    remain_txt = f"剩余 {_fmt_duration(busy_until - now_ts)}" if busy else ""
    desc_avail = card1_w - c1_inner * 2
    remain_same_row = bool(remain_txt) and (tw(desc, hint_font) + 16 + tw(remain_txt, hint_font) <= desc_avail)
    tail_rows = 1 if (not remain_txt or remain_same_row) else 2  # 描述行（+剩余时间行）

    # 单行自适应字号：内容超宽时逐级缩小字号，保证不触发自动换行；
    # 最小字号仍放不下（超长自定义名等）→ 截断加省略号，始终单行
    def fit_rows(txt, fonts_chain, avail):
        for f in fonts_chain:
            if tw(txt, f) <= avail:
                return [(txt, f)]
        smallest = fonts_chain[-1]
        cut = txt
        while cut and tw(cut + "…", smallest) > avail:
            cut = cut[:-1]
        return [(cut + "…", smallest)] if cut else [(txt, smallest)]

    # 卡片1：变化行(多色,逐级缩字号保证一行) + 原因行(自适应) + 进度条 + 描述/剩余行
    c1_avail = card1_w - c1_inner * 2
    c1_chg_font = hint_font
    for f in (hint_font, small_font, tiny_font):
        if seg_lines_n(chg_segs, c1_avail, f) <= 1:
            c1_chg_font = f
            break
    c1_chg_n = seg_lines_n(chg_segs, c1_avail, c1_chg_font)  # 正常=1（保证不换行）
    c1_reason_rows = fit_rows(f"原因：{reason}", (hint_font, small_font, tiny_font), c1_avail)
    c1_h = c1_inner * 2 + (c1_chg_n + len(c1_reason_rows) + tail_rows) * line_h + bar_h
    # 卡片2：项目行 + 奖励行 + 预计完成行 + 状态档位行（均单行自适应字号，不自动换行）
    c2_avail = card2_w - c1_inner * 2
    c2_cur_rows = fit_rows(cur_txt, (desc_font, hint_font, small_font, tiny_font), c2_avail)
    c2_reward_rows = fit_rows(reward_txt, (hint_font, small_font, tiny_font), c2_avail)
    c2_eta_rows = fit_rows(eta_txt, (hint_font, small_font, tiny_font), c2_avail)
    c2_tier_rows = fit_rows(tier_txt, (hint_font, small_font, tiny_font), c2_avail)
    c2_h = c1_inner * 2 + (len(c2_cur_rows) + len(c2_reward_rows)
                           + len(c2_eta_rows) + len(c2_tier_rows)) * line_h
    bottom_h = max(c1_h, c2_h)

    # ---------- 自动化功能提示（2.1.1：从五属性条右侧移至标题下方；删除切换指令提示行） ----------
    u_auto = data.get("users", {}).get(uid) or {}
    feed_on = bool(u_auto.get("auto_feed_enabled")) and bool(globals().get("AUTO_FEED_ENABLED", AUTO_FEED_ENABLED))
    work_on = bool(u_auto.get("auto_work_enabled")) and bool(globals().get("AUTO_WORK_ENABLED", AUTO_WORK_ENABLED)) and feed_on
    feed_cost = sum(int(lg.get("total", 0) or 0) for lg in (u_auto.get("auto_feed_logs") or []))
    work_gain = sum(int(lg.get("coins", 0) or 0) for lg in (u_auto.get("auto_work_logs") or []))
    auto_st1 = "开" if feed_on else "关"
    auto_c1 = DS_SUCCESS if feed_on else DS_MUTED
    auto_line1 = f"自动照顾 {auto_st1}｜消耗 {feed_cost} 金币"
    auto_st2 = "开" if work_on else "关"
    auto_c2 = DS_SUCCESS if work_on else DS_MUTED
    auto_line2 = f"自动打工 {auto_st2}｜赚取 {work_gain} 金币"
    auto_h = line_h + 6  # 单行（开关状态+累计金币）；指令提示已删除，无卡片边框

    height = pad * 2 + title_h + auto_h + slot_h + rule_h + pet_card_h + bottom_gap + bottom_h

    img = Image.new("RGB", (width, height), DS_BG)
    d = ImageDraw.Draw(img)
    y = pad

    # 标题：<用户昵称>的宠物 + 右侧 宠物排行数据
    # 2.0.1：用户名过长时用 … 截断，保证排行榜信息始终在同一行右对齐（避免错乱/换行）
    rank_text = _pet_rank_text(uid, data) if data else ""
    _rank_w = (24 + tw(rank_text, desc_font)) if rank_text else 0
    _title_suffix = " 的宠物"
    _name_max = content_w - tw(_title_suffix, title_font_) - _rank_w
    disp_name = name
    if tw(disp_name, title_font_) > max(20, _name_max):
        cut = disp_name
        while cut and tw(cut + "…", title_font_) > max(20, _name_max):
            cut = cut[:-1]
        disp_name = (cut + "…") if cut else disp_name[:1] + "…"
    title_line = f"{disp_name}{_title_suffix}"
    dtext(d, (pad, y), title_line, font=title_font_, fill=DS_ACCENT)
    if rank_text:
        dtext(d, (int(width - pad - tw(rank_text, desc_font)), y + 16), rank_text,
              font=desc_font, fill=DS_MUTED)
    y += title_h

    # 自动化信息（标题下方，单行展示，无卡片边框；保留分割线）
    dtext(d, (int(pad + inner), y + 2), auto_line1, font=tiny_font, fill=auto_c1)
    x_auto = int(pad + inner + tw(auto_line1, tiny_font) + tw("　", tiny_font))
    dtext(d, (x_auto, y + 2), auto_line2, font=tiny_font, fill=auto_c2)
    y += auto_h

    # 预留位（红 #C00000）
    if slot_lines:
        for wl in slot_lines:
            dtext(d, (pad, y), wl, font=desc_font, fill=DS_DANGER)
            y += line_h
        y += 6

    # 分割线
    d.line([(pad, y), (width - pad, y)], fill=DS_BORDER, width=2)
    y += rule_h

    # ---------- 宠物状态卡片（横跨整行） ----------
    d.rectangle([pad, y, width - pad, y + pet_card_h], outline=DS_BORDER, width=1)
    yy = y + inner
    # 行1：宠物名称(大两号,左) + 状态(小一号,右)
    dtext(d, (int(pad + inner), yy), pet.get("name", "宠物"), font=pet_name_font, fill=DS_TEXT)
    dtext(d, (int(width - pad - inner - tw(status, status_font)), yy + 10),
          status, font=status_font, fill=DS_MUTED)
    yy += name_row_h
    # 行2：等级(左) + 经验(右,小两号)
    dtext(d, (int(pad + inner), yy), f"Lv.{lv}", font=lv_font, fill=DS_TEXT)
    dtext(d, (int(width - pad - inner - tw(exp_text, exp_font)), yy + 6),
          exp_text, font=exp_font, fill=DS_MUTED)
    yy += lv_row_h
    # 行3：升级进度条（普通进度条高度，含百分比）
    bar_y = yy + (bar_h - bar_h_px) // 2
    d.rectangle([pad + inner, bar_y, width - pad - inner, bar_y + bar_h_px], outline=DS_BORDER, width=1)
    if exp_ratio > 0:
        d.rectangle([pad + inner + 1, bar_y + 1,
                     int(pad + inner + 1 + (content_w - 2 * inner - 2) * exp_ratio), bar_y + bar_h_px - 1],
                    fill=DS_SUCCESS)
    pct_text = f"{int(exp_ratio * 100)}%"
    dtext(d, (int(width - pad - inner - tw(pct_text, exp_font) - 4), bar_y - 4),
          pct_text, font=exp_font, fill=DS_TEXT)
    yy += bar_h
    # 行4+：宠物属性条区
    attr_w = int(content_w * 0.55)  # 属性条宽度（剩余右侧放状态解释）
    for label, val, amax, hint in attrs:
        # 属性名 + 当前值/最大值
        t = f"{label} {val:.0f}/{amax:.0f}"
        dtext(d, (int(pad + inner), yy), t, font=attr_font, fill=DS_TEXT_2)
        yy += attr_label_h
        # 属性条颜色判定（2.0.2：饱/渴/心 进入第3/4档位 → 红 #C00000，档位阈值 WebUI 可编辑）
        red = _attr_is_red(label, val)
        green = (amax - val) < 20 and pet["health"] >= 41
        bar_color = DS_DANGER if red else (DS_GREEN_SOFT if green else DS_TEXT)
        # 属性条（高度 = 普通进度条的 30%）
        ay = yy + (attr_bar_h - attr_bar_px) // 2
        ratio = max(0.0, min(1.0, val / amax)) if amax > 0 else 0.0
        d.rectangle([pad + inner, ay, pad + inner + attr_w, ay + attr_bar_px],
                    outline=DS_BORDER, width=1)
        if ratio > 0:
            d.rectangle([pad + inner + 1, ay + 1,
                         int(pad + inner + 1 + (attr_w - 2) * ratio), ay + attr_bar_px - 1],
                        fill=bar_color)
        # 状态解释：仅红色时固定显示在整个属性条区域的右侧（不随填充比例移动）
        if red:
            dtext(d, (int(pad + inner + attr_w + 6), yy + 1), hint, font=hint_font, fill=DS_DANGER)
        yy += attr_bar_h
    y += pet_card_h
    y += bottom_gap  # 状态卡片与底部双卡保持间距

    # ---------- 底部双卡：卡片1(宽) + 卡片2(窄) ----------
    # 2.1.1 高亮规范：高亮不变动卡片填充颜色，只改变 边框颜色 + 字体颜色
    HL_BORDER = DS_GOLD    # 高亮边框（金）
    HL_TEXT = DS_GOLD      # 高亮字体（深金）
    NORMAL_BORDER = DS_BORDER
    # 卡片1：五属性变化(多色分段,自动换行) + 原因 + 打工/玩耍进度条 + 描述/剩余时间
    # 高亮条件：本次产生了新的状态变化，或 进度条满（空闲）后的第一次响应
    c1_hl = bool(changes_fresh or progress_done)
    d.rectangle([pad, y, pad + card1_w, y + bottom_h],
                fill=None, outline=(HL_BORDER if c1_hl else NORMAL_BORDER), width=(2 if c1_hl else 1))
    yy = y + c1_inner
    yy = draw_segs(d, pad + c1_inner, yy, c1_avail, chg_segs, c1_chg_font, line_h)
    c1_reason_color = HL_TEXT if c1_hl else DS_MUTED
    for wl, f in c1_reason_rows:
        dtext(d, (int(pad + c1_inner), yy), wl, font=f, fill=c1_reason_color)
        yy += line_h
    # 打工/玩耍进度条（原模块）
    by = yy + (bar_h - bar_h_px) // 2
    d.rectangle([pad + c1_inner, by, pad + card1_w - c1_inner, by + bar_h_px],
                outline=DS_BORDER, width=1)
    if idle_ratio > 0:
        d.rectangle([pad + c1_inner + 1, by + 1,
                     int(pad + c1_inner + 1 + (card1_w - 2 * c1_inner - 2) * idle_ratio), by + bar_h_px - 1],
                    fill=idle_color)
    yy += bar_h
    # 描述（左）+ 剩余时间（进度条右下方、进度条外部，右对齐）
    desc_color = HL_TEXT if c1_hl else DS_MUTED
    if remain_txt and remain_same_row:
        dtext(d, (int(pad + c1_inner), yy), desc, font=hint_font, fill=desc_color)
        dtext(d, (int(pad + card1_w - c1_inner - tw(remain_txt, hint_font)), yy),
              remain_txt, font=hint_font, fill=DS_MUTED)
    else:
        dtext(d, (int(pad + c1_inner), yy), desc, font=hint_font, fill=desc_color)
        if remain_txt:
            dtext(d, (int(pad + card1_w - c1_inner - tw(remain_txt, hint_font)), yy + line_h),
                  remain_txt, font=hint_font, fill=DS_MUTED)
    # 卡片2：当前项目 + 金币/经验 + 预计完成时刻 + 状态档位
    # 高亮条件：本次响应发生了 打工/玩耍 变动
    c2_x = pad + card1_w + card_gap
    c2_hl = bool(card2_hl)
    d.rectangle([c2_x, y, width - pad, y + bottom_h],
                fill=None, outline=(HL_BORDER if c2_hl else NORMAL_BORDER), width=(2 if c2_hl else 1))
    yy = y + c1_inner
    c2_cur_color = HL_TEXT if c2_hl else DS_TEXT_2
    c2_reward_color = HL_TEXT if c2_hl else DS_GOLD
    c2_eta_color = HL_TEXT if c2_hl else DS_MUTED
    for wl, f in c2_cur_rows:
        dtext(d, (int(c2_x + c1_inner), yy), wl, font=f, fill=c2_cur_color)
        yy += line_h
    for wl, f in c2_reward_rows:
        dtext(d, (int(c2_x + c1_inner), yy), wl, font=f, fill=c2_reward_color)
        yy += line_h
    for wl, f in c2_eta_rows:
        dtext(d, (int(c2_x + c1_inner), yy), wl, font=f, fill=c2_eta_color)
        yy += line_h
    for wl, f in c2_tier_rows:
        dtext(d, (int(c2_x + c1_inner), yy), wl, font=f, fill=tier_color)
        yy += line_h

    return save_temp_image(img, "_pet_", "宠物状态")


def render_work_play(kind, name, pet, items, coins=None):
    """打工/玩耍列表图片（1.7.1 布局）：
    标题(用户名称) + 宠物信息卡片(横跨整行) + 分割线 + 内容卡片（每行 WORK/PLAY_CARD_COLS 个）。
    宠物信息卡：宠物名称(大两号)+状态(小一号) / 等级+经验值(小两号,两端对齐) / 升级进度条(含百分比) / 属性(过低红色)。
    内容卡：名称(大三号,居左)+时间(居右) / 描述 / 条件(如有) / 消耗 / 卡片内分割线 / 报酬或变更(红 #C00000,右,大一号)。
    卡片高度自适应（按换行后行数），同行取最高；不能打工/玩耍的卡片灰(#D9D9D9)。
    pet 可为 None（无宠物）：宠物信息卡显示「还没有宠物」提示，内容卡全部灰卡。
    coins（1.7.6）：不为 None 时在标题下方显示金币余额行。"""
    try:
        return _render_work_play_inner(kind, name, pet, items, coins)
    except Exception as e:
        logger.error(f"[插件] 渲染{kind}列表图片异常: {e}")
        return None


def _render_work_play_inner(kind, name, pet, items, coins=None):
    """打工/玩耍列表图片实际渲染（异常由外层捕获并回退文本）"""
    Image, ImageDraw = ensure_pillow()
    if Image is None:
        raise ImportError("Pillow 不可用")
    # 字号语义：标题 36（衬线）/ 宠物名 26 / 状态 18 / 正文 20 / 经验 16 / 属性 18 / 内容名 28 / 描述 20 / 报酬 22
    fonts = load_fonts(26, 18, 20, 16, 18, 28, 20, 22)
    if fonts is None:
        raise RuntimeError(f"字体加载失败: {FONT_FILE}")
    (pet_name_font, status_font, lv_font, exp_font,
     attr_font, item_name_font, body_font, price_font) = fonts
    title_font_ = title_font(kind="work")
    if title_font_ is None:
        raise RuntimeError(f"标题字体加载失败: {FONT_FILE}")

    has_pet = pet is not None
    now_ts = datetime.now().timestamp()
    if has_pet:
        # 宠物忙碌状态：打工/玩耍共用冷却计时器，任一忙碌即忙碌（与灰卡判定一致）
        busy = now_ts < _pet_busy_until(pet)
        status = "虚弱中" if pet.get("weak") else ("忙碌中" if busy else "空闲中")
        sat_max, thr_max, sta_max, mood_max = _attr_max(pet["health"])
        # 属性展示（过低红色高亮，与「宠物」指令属性条一致，2.0.2 档位阈值第3/4档）
        attrs = [
            ("饱食", pet["satiety"], sat_max, _attr_is_red("饱食", pet["satiety"], pet["health"])),
            ("口渴", pet["thirst"], thr_max, _attr_is_red("口渴", pet["thirst"], pet["health"])),
            ("体力", pet["stamina"], sta_max, _attr_is_red("体力", pet["stamina"], pet["health"])),
            ("心情", pet["mood"], mood_max, _attr_is_red("心情", pet["mood"], pet["health"])),
            ("健康", pet["health"], PET_MAX_HEALTH, _attr_is_red("健康", pet["health"], pet["health"])),
        ]
    else:
        status = "未解锁"

    pad = 20
    title_h = 76
    coin_h = 28  # 1.7.6：标题下方金币余额行高度
    rule_h = 22          # 宠物卡与内容卡之间的分割线
    gap = 12
    inner = 10
    cols = int(globals().get("WORK_CARD_COLS" if kind == "打工" else "PLAY_CARD_COLS", 2))
    cols = max(1, min(4, cols))
    card_w = int(globals().get("WORK_PLAY_CARD_WIDTH", 522))  # 默认 522 = 原 290 的 180%
    card_w = max(290, min(800, card_w))
    width = pad * 2 + card_w * cols + gap * (cols - 1)
    pet_w = width - pad * 2  # 宠物信息卡宽度 = 内容卡一行布局总宽度

    name_row_h = 34
    lv_row_h = 28
    bar_h = 26
    attr_row_h = 24
    line_h = 26
    item_name_h = 40
    rule_card_h = 10
    price_h = 30
    n_pad = int(globals().get("SHOP_PRICE_PAD", 4))

    tw = text_measurer()
    if tw is None:
        raise RuntimeError("Pillow 不可用，无法测量文本宽度")

    wrap = make_wrapper(tw, 0)

    # ---------- 宠物信息卡片 ----------
    pet_content_w = pet_w - inner * 2
    if has_pet:
        # 属性行：每行 3 个（最后一行 2 个）
        attr_rows = [attrs[:3], attrs[3:]]
        pet_card_h = inner * 2 + name_row_h + lv_row_h + bar_h + len(attr_rows) * attr_row_h
        # 升级进度：新经验体系（所需经验 = 当前等级 × 100）
        lv, got_exp, need_exp = _pet_exp_progress(float(pet.get("exp", 0.0)))
        exp_ratio = min(1.0, got_exp / need_exp) if need_exp > 0 else 1.0
        exp_text = f"经验 {got_exp:.0f}/{need_exp:.0f}"
    else:
        attr_rows = []
        # 无宠物：名称行 + 提示行
        pet_card_h = inner * 2 + name_row_h + lv_row_h
        exp_ratio = 0.0
        exp_text = ""

    # ---------- 内容卡片预计算 ----------
    def can_do(item):
        if not has_pet:
            return False
        if now_ts < _pet_busy_until(pet):  # 打工/玩耍共用冷却计时器
            return False
        if pet["level"] < item["min_level"]:
            return False
        if pet["health"] < item["min_health"]:
            return False
        if pet["mood"] < item["min_mood"]:
            return False
        for attr, cost in item["cost"].items():
            if cost > 0 and pet[attr] < cost:
                return False
        return True

    def cond_text(item):
        parts = []
        if item["min_level"] > 0:
            parts.append(f"Lv.{int(item['min_level'])}+")
        if item["min_health"] > 0:
            parts.append(f"健康 {item['min_health']:.0f}+")
        if item["min_mood"] > 0:
            parts.append(f"心情 {item['min_mood']:.0f}+")
        return "条件：" + " / ".join(parts) if parts else ""

    def cost_text(item):
        parts = []
        for attr, cost in item["cost"].items():
            if cost > 0:
                parts.append(f"{ATTR_SHORT[attr]}-{cost:.0f}")
        return "消耗：" + " ".join(parts) if parts else "消耗：无"

    def need_text(item):
        # 玩耍卡「需要」行：条件下一行显示需要的 饱食度/口渴值/体力（固定顺序中文全称）
        parts = []
        for attr in ("satiety", "thirst", "stamina", "health"):
            cost = item["cost"].get(attr, 0)
            if cost > 0:
                parts.append(f"{ATTR_LABELS[attr]} {cost:.0f}")
        return "需要：" + " ".join(parts) if parts else "需要：无"

    def reward_text(item):
        if kind == "打工":
            s = f"金币 +{int(item['coins'])}"
            if item["exp"] > 0:
                s += f" · 经验 +{item['exp']:.0f}"
            return s
        else:
            s = f"经验 +{item['exp']:.0f}"
            if item["mood"] > 0:
                s += f" · 心情 +{item['mood']:.0f}"
            # 红字不含体力（体力需求在「需要」行体现）；健康按净变化显示（可负）
            net_health = item.get("health", 0) - item["cost"].get("health", 0)
            if net_health != 0:
                s += f" · 健康 {net_health:+.0f}"
            return s

    plans = []  # (item, ok, [行], 高度)
    for it in items:
        ok = can_do(it)
        content_w = card_w - inner * 2
        rows = []
        rows.append(("pair", it["name"], f"{int(it['time'])} 分钟"))
        for wl in wrap(it["desc"], body_font, content_w):
            rows.append(("plain", wl, ""))
        ct = cond_text(it)
        if ct:
            for wl in wrap(ct, body_font, content_w):
                rows.append(("plain", wl, ""))
        if kind == "打工":
            for wl in wrap(cost_text(it), body_font, content_w):
                rows.append(("plain", wl, ""))
        else:
            # 玩耍：要求（条件）下一行显示需要的 饱食度/口渴值/体力（中文全称）
            for wl in wrap(need_text(it), body_font, content_w):
                rows.append(("plain", wl, ""))
        rows.append(("rule", "", ""))
        rows.append(("price", reward_text(it), ""))
        h_before = inner * 2 + item_name_h + (len(rows) - 3) * line_h  # 去掉 rule/price 后的文本行数
        h = h_before + rule_card_h + price_h + n_pad
        plans.append((it, ok, rows, h))

    # ---------- 总高度 ----------
    rows_n = (len(plans) + cols - 1) // cols if plans else 0
    cards_h = 0
    for r in range(rows_n):
        group = plans[r * cols:(r + 1) * cols]
        cards_h += max(p[3] for p in group) + gap
    if cards_h > 0:
        cards_h -= gap
    height = pad * 2 + title_h + (coin_h if coins is not None else 0) + pet_card_h + rule_h + cards_h

    img = Image.new("RGB", (width, height), DS_BG)
    d = ImageDraw.Draw(img)
    y = pad

    # 标题：<用户名称>
    dtext(d, (pad, y), name, font=title_font_, fill=DS_ACCENT)
    y += title_h
    # 1.7.6：标题下方金币余额
    if coins is not None:
        dtext(d, (pad, y), f"💰 金币余额：{coins}", font=attr_font, fill=DS_GOLD)
        y += coin_h

    # ---------- 宠物信息卡片（横跨整行） ----------
    d.rectangle([pad, y, pad + pet_w, y + pet_card_h], outline=DS_BORDER, width=1)
    yy = y + inner
    # 行1：宠物名称(大两号,左) + 状态(小一号,右)
    pet_disp = pet.get("name", "宠物") if has_pet else "还没有宠物"
    dtext(d, (int(pad + inner), yy), pet_disp, font=pet_name_font, fill=DS_TEXT)
    dtext(d, (int(pad + pet_w - inner - tw(status, status_font)), yy + 10),
          status, font=status_font, fill=DS_MUTED)
    yy += name_row_h
    if has_pet:
        # 行2：等级(左) + 经验值(小两号,右)，两端对齐（本行宽 = 内容宽）
        dtext(d, (int(pad + inner), yy), f"Lv.{pet['level']}", font=lv_font, fill=DS_TEXT)
        dtext(d, (int(pad + pet_w - inner - tw(exp_text, exp_font)), yy + 6),
              exp_text, font=exp_font, fill=DS_MUTED)
        yy += lv_row_h
        # 行3：升级进度条（宽度与上一行相等 = 内容宽，含百分比）
        bar_w = pet_content_w
        bar_y = yy + (bar_h - 14) // 2
        d.rectangle([pad + inner, bar_y, pad + inner + bar_w, bar_y + 14], outline=DS_BORDER, width=1)
        if exp_ratio > 0:
            d.rectangle([pad + inner + 1, bar_y + 1,
                         int(pad + inner + 1 + (bar_w - 2) * exp_ratio), bar_y + 13],
                        fill=DS_SUCCESS)
        pct_text = f"{int(exp_ratio * 100)}%"
        dtext(d, (int(pad + pet_w - inner - tw(pct_text, exp_font) - 4), bar_y - 4),
              pct_text, font=exp_font, fill=DS_TEXT)
        yy += bar_h
        # 行4+：宠物属性（过低红色高亮）
        for group in attr_rows:
            gx = pad + inner
            for an, av, amax, low in group:
                t = f"{an} {av:.0f}/{amax:.0f}"
                dtext(d, (int(gx), yy), t, font=attr_font,
                      fill=DS_DANGER if low else DS_TEXT_2)
                gx += tw(t, attr_font) + 22
            yy += attr_row_h
    else:
        # 无宠物：提示行
        dtext(d, (int(pad + inner), yy), "发送「解锁宠物」领养一只吧", font=lv_font, fill=DS_GOLD)
        yy += lv_row_h
    y += pet_card_h

    # 分割线
    d.line([(pad, y), (width - pad, y)], fill=DS_BORDER, width=2)
    y += rule_h

    # ---------- 内容卡片 ----------
    for r in range(rows_n):
        group = plans[r * cols:(r + 1) * cols]
        gh = max(p[3] for p in group)
        for j, (it, ok, rows, _) in enumerate(group):
            x0 = pad + j * (card_w + gap)
            bg = DS_BORDER_2 if not ok else DS_SURFACE
            d.rectangle([x0, y, x0 + card_w, y + gh], fill=bg, outline=DS_BORDER, width=1)
            yy = y + inner
            for row in rows:
                kind_row = row[0]
                if kind_row == "pair":
                    dtext(d, (int(x0 + inner), yy), row[1], font=item_name_font, fill=DS_TEXT)
                    dtext(d, (int(x0 + card_w - inner - tw(row[2], body_font)), yy + 12),
                          row[2], font=body_font, fill=DS_MUTED)
                    yy += item_name_h
                elif kind_row == "plain":
                    for wl in wrap(row[1], body_font, card_w - inner * 2):
                        dtext(d, (int(x0 + inner), yy), wl, font=body_font, fill=DS_TEXT_2)
                        yy += line_h
                elif kind_row == "rule":
                    yy += rule_card_h // 2
                    d.line([(x0 + 8, yy), (x0 + card_w - 8, yy)], fill=DS_BORDER, width=1)
                    yy += rule_card_h // 2
                elif kind_row == "price":
                    py = y + gh - n_pad - price_h
                    dtext(d, (int(x0 + card_w - inner - tw(row[1], price_font)), py),
                          row[1], font=price_font, fill=DS_DANGER)
                    break
        y += gh + gap

    return save_temp_image(img, "_wp_", f"{kind}列表")


def render_shop(name, categories, inventory, coins=None):
    """宠物商店：每行 SHOP_CARD_COLS 个卡片；名称(大三号)/持有数 / 效果(空格优先换行) / 分割线 / 价格(红、右、大一号、分割线与底边之间 N 像素)
    卡片高度自适应，同行取最高；被拉伸的低卡片忽略价格与分割线的 N 约束。
    coins（1.7.6）：不为 None 时在标题下方显示金币余额行。"""
    Image, ImageDraw = ensure_pillow()
    if Image is None:
        return None
    # 字号语义：标题 36（衬线）/ 分类 26 / 名称 26（大三号）/ 正文 18 / 价格 20（大一号）/ 原价 14（小一号）
    fonts = load_fonts(26, 26, 18, 20, 14)
    if fonts is None:
        return None
    cat_font, name_font, body_font, price_font, small_price_font = fonts
    title_font_ = title_font(kind="shop")
    if title_font_ is None:
        return None

    pad = 20
    title_h = 76
    coin_h = 28  # 1.7.6：标题下方金币余额行高度
    cat_h = 30
    rule_h = 18
    cols = int(globals().get("SHOP_CARD_COLS", 3))
    n_pad = int(globals().get("SHOP_PRICE_PAD", 4))  # N：价格距分割线/底边
    card_w = 246
    gap = 12
    inner = 10
    name_h = 34
    line_h = 26
    price_h = 26

    tw = text_measurer()
    if tw is None:
        return None

    content_w = card_w - inner * 2

    wrap = make_wrapper(tw, content_w)
    word_wrap = make_wrapper(tw, content_w, mode="word")  # 效果描述按单元整体换行

    # 实时价格（2.0.4）：同一 2 小时窗口内价格稳定；本渲染共用一个窗口折扣映射
    _start, wid = _shop_price_window()
    _shop_names = [it["name"] for _typ, _items in categories for it in _items]
    discount = _shop_discount_map(wid, _shop_names)

    def _card_plan(it):
        """返回 (行列表, 高度, 分割线前高度)。行 = (kind, text, extra)"""
        have = int(inventory.get(it["name"], 0))
        cnt_text = f"×{have}"
        rows = []
        if tw(it["name"], name_font) + tw(cnt_text, body_font) + 8 <= content_w:
            rows.append(("pair", it["name"], cnt_text))
        else:
            for ln in wrap(it["name"], name_font):
                rows.append(("plain", ln, ""))
            rows.append(("plain", cnt_text, ""))
        # 商品描述：名称下方、效果上方
        if it.get("desc"):
            for ln in wrap(it["desc"], body_font):
                rows.append(("plain", ln, ""))
        for ln in word_wrap(f"效果：{_effect_desc(it['effects'])}", body_font):
            rows.append(("plain", ln, ""))
        h_before = inner * 2 + sum(name_h if (i == 0 and r[0] == "pair") else line_h for i, r in enumerate(rows))
        rows.append(("rule", "", ""))
        # 2.0.3：价格浮动 → 原价（灰小字）+ 实时价（红正常字）
        _o, cur, _f = _pet_shop_price(it, discount)
        rows.append(("price", f"{cur} 金币", (f"{_o} 金币" if _f else "")))
        # 总高 = 分割线前内容 + 分割线距价格上方 N + 线 1px + 价格区（价格高 + 底边 N）
        h = h_before + n_pad + 1 + price_h + n_pad
        return rows, h, h_before

    # 预计算每个类别的卡片排版
    cat_plans = []
    for typ, items in categories:
        item_plans = []
        for it in items:
            rows, h, hb = _card_plan(it)
            item_plans.append((it, rows, h, hb))
        cat_plans.append((typ, item_plans))

    width = pad * 2 + card_w * cols + gap * (cols - 1)

    # 总高度（卡片行高度取同行最大值）+ 底部特殊道具提示（自动换行防溢出）
    height = pad * 2 + title_h + (coin_h if coins is not None else 0)
    for typ, item_plans in cat_plans:
        height += cat_h + rule_h
        if item_plans:
            for g in range(0, len(item_plans), cols):
                group = item_plans[g:g + cols]
                height += max(p[2] for p in group) + gap
            height -= gap  # 去掉最后一组后的多余间距
    _no, _pill_p, _ball_p = _signin_reward_chances()
    pill_limit = int(globals().get("PILL_DAILY_LIMIT", PILL_DAILY_LIMIT))
    ball_limit = int(globals().get("EXP_BALL_DAILY_LIMIT", EXP_BALL_DAILY_LIMIT))
    pill_txt = f"· {PILL_NAME}（特殊）：随机 2 个属性 +5~20（每日最多 {pill_limit} 次，签到 {_pill_p * 100:.0f}% 概率获得）"
    ball_txt = f"· {EXP_BALL_NAME}（农场特殊道具）：获得升级经验 5%~20%（每日最多 {ball_limit} 次，签到 {_ball_p * 100:.0f}% 概率获得）"
    foot_max_w = width - pad * 2

    def wrap_foot(text, font):
        lines = []
        cur = ""
        for ch in text:
            if tw(cur + ch, font) <= foot_max_w:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines or [""]

    foot_lines = wrap_foot(pill_txt, body_font) + wrap_foot(ball_txt, body_font)
    height += len(foot_lines) * 26 + 14

    img = Image.new("RGB", (width, height), DS_BG)
    d = ImageDraw.Draw(img)
    y = pad
    dtext(d, (pad, y), f"{name} 的宠物商店", font=title_font_, fill=DS_ACCENT)
    y += title_h
    # 1.7.6：标题下方金币余额
    if coins is not None:
        dtext(d, (pad, y), f"💰 金币余额：{coins}", font=body_font, fill=DS_GOLD)
        y += coin_h

    for typ, item_plans in cat_plans:
        # 类别名（居中；坐标必须转 int）
        cx = int(pad + (width - 2 * pad - tw(typ, cat_font)) / 2)
        dtext(d, (cx, y), typ, font=cat_font, fill=DS_TEXT_2)
        y += cat_h
        # 分隔线（居中）
        d.line([(pad + 20, y), (width - pad - 20, y)], fill=DS_BORDER, width=2)
        y += rule_h
        for g in range(0, len(item_plans), cols):
            group = item_plans[g:g + cols]
            gh = max(p[2] for p in group)
            for j, (it, rows, _, _) in enumerate(group):
                x0 = pad + j * (card_w + gap)
                d.rectangle([x0, y, x0 + card_w, y + gh], outline=DS_BORDER, width=1)
                yy = y + inner
                rule_y = None
                price_y = y + gh - n_pad - price_h  # 价格基线（贴底边 N）
                for row in rows:
                    kind = row[0]
                    if kind == "pair":
                        dtext(d, (int(x0 + inner), yy), row[1], font=name_font, fill=DS_TEXT)
                        dtext(d, (int(x0 + card_w - inner - tw(row[2], body_font)), yy + 4),
                              row[2], font=body_font, fill=DS_GOLD)
                        yy += name_h
                    elif kind == "plain":
                        for wl in wrap(row[1], body_font):
                            dtext(d, (int(x0 + inner), yy), wl, font=body_font, fill=DS_TEXT_2)
                            yy += line_h
                    elif kind == "rule":
                        rule_y = price_y - n_pad  # 分割线固定在价格上方 N 距离
                    elif kind == "price":
                        # 价格贴底边 N（2.0.3：原价灰色小字 + 实时价红色正常字）
                        real_text = row[1]
                        orig_text = row[2] or ""
                        right_x = int(x0 + card_w - inner - tw(real_text, price_font))
                        if orig_text:
                            dtext(d, (int(right_x - 6 - tw(orig_text, small_price_font)), price_y + 4),
                                  orig_text, font=small_price_font, fill=DS_MUTED)
                        dtext(d, (right_x, price_y), real_text, font=price_font, fill=DS_DANGER)
                        break
                if rule_y is not None:
                    d.line([(x0 + 8, rule_y), (x0 + card_w - 8, rule_y)], fill=DS_BORDER, width=1)
            y += gh + gap

    # 底部：特殊道具提示（自动换行，不溢出图片）
    y += 8
    for ln in foot_lines:
        dtext(d, (pad, y), ln, font=body_font, fill=DS_MUTED)
        y += 26

    return save_temp_image(img, "_shop_", "商店")


def render_bag(name, header, groups, footer):
    """背包图片（1.7.7）：
    标题区：「<用户名>的背包」（居左大字号）+「好感 Lv.X」（居右）；金币行（负债 #FF6D6D）；
    宠物行（名字+等级+状态：正常/异常）；农场行（等级+空闲/占用/成熟地块数）。
    内容区（两级分类）：一级分类（宠物道具/农场道具，居中+居中分割线）→
    二级分类（食物/饮料/玩具/药物/特殊 或 种子/收获物/化肥，居中+居中分割线）→ 卡片网格
    （每行 BAG_CARD_COLS 个）。
    卡片：物品名(左)+数量(右) → 描述 → 使用效果 → 卡片内分割线 → 可售卖金额(右，#C00000，
    格式「单价 |总价」）；无数据的模块自动隐藏（无描述/无效果/不可售时对应模块不显示）。
    页尾：通宽大卡片 = 内容区卡片总宽 + 行内间隙总和；第一行 仓库总价值(大字号)+今日净收益(小字号)，
    第二行 金币/宠物/农场三榜排名。"""
    Image, ImageDraw = ensure_pillow()
    if Image is None:
        return None
    # 字号语义：标题 36（衬线）/ 信息 20 / 一级分类 26 / 名称 22 / 正文 18 / 页尾大字 26 / 二级分类 20
    fonts = load_fonts(20, 26, 22, 18, 26)
    if fonts is None:
        return None
    info_font, l1_font, name_font, body_font, big_font = fonts
    title_font_ = title_font(kind="bag")
    if title_font_ is None:
        return None
    l2_font = info_font

    tw = text_measurer()
    if tw is None:
        return None

    pad = 20
    title_h = 74
    info_h = 30
    head_gap = 12      # 标题区与内容区之间的距离
    l1_h = 38
    l1_rule_h = 18
    l2_h = 28
    l2_rule_h = 14
    cols = max(3, min(7, int(globals().get("BAG_CARD_COLS", 5))))
    card_w = 200
    gap = 12
    inner = 10
    name_h = 30
    line_h = 24
    sell_h = 26
    n_pad = 4          # 卡片内分割线距可售金额上方 N

    content_w = card_w - inner * 2
    wrap = make_wrapper(tw, content_w)
    word_wrap = make_wrapper(tw, content_w, mode="word")

    DEBT_COLOR = (255, 109, 109)   # #FF6D6D 负债红（暖调）
    SELL_COLOR = DS_DANGER       # #C00000

    def _card_plan(card):
        """返回 (行列表, 卡片高度)。行 = (kind, text, extra)"""
        cnt_text = f"×{card['count']}"
        rows = []
        if tw(card["name"], name_font) + tw(cnt_text, body_font) + 8 <= content_w:
            rows.append(("pair", card["name"], cnt_text))
        else:
            for ln in wrap(card["name"], name_font):
                rows.append(("plain_name", ln, ""))
            rows.append(("plain_name", cnt_text, ""))
        if card.get("desc"):
            for ln in wrap(card["desc"], body_font):
                rows.append(("plain", ln, ""))
        if card.get("effect"):
            for ln in word_wrap(f"效果：{card['effect']}", body_font):
                rows.append(("plain", ln, ""))
        sellable = card.get("sell_unit") is not None
        if sellable:
            rows.append(("rule", "", ""))
            unit = _fmt_price(card["sell_unit"])
            total = _fmt_price(card["sell_unit"] * card["count"])
            sell_txt = f"{unit} |{total}"
            if tw(sell_txt, info_font) <= content_w:
                rows.append(("sell", sell_txt, ""))
            else:
                # 极端长金额 → 在「|」断行（单价一行、总价一行）
                rows.append(("sell", f"{unit} |", ""))
                rows.append(("sell", total, ""))
        h = inner * 2
        for r in rows:
            if r[0] == "pair":
                h += name_h
            elif r[0] == "plain_name":
                h += name_h - 4
            elif r[0] == "rule":
                h += n_pad + 1
            elif r[0] == "sell":
                h += sell_h
            else:
                h += line_h
        return rows, h

    # 预计算排版：[(一级名, [(二级名, [(card, rows, h)])])]
    group_plans = []
    for l1, subs in groups:
        sub_plans = [(l2, [(card, *_card_plan(card)) for card in cards]) for l2, cards in subs]
        group_plans.append((l1, sub_plans))

    width = pad * 2 + card_w * cols + gap * (cols - 1)
    page_w = width - pad * 2  # 页尾大卡片宽 = 内容卡片总宽 + 间隙总和

    # ---- 高度 ----
    height = pad
    height += title_h
    height += info_h  # 金币/负债行
    height += info_h  # 宠物行
    height += info_h  # 农场行
    height += head_gap
    for l1, sub_plans in group_plans:
        height += l1_h + l1_rule_h
        for l2, plans in sub_plans:
            height += l2_h + l2_rule_h
            for g in range(0, len(plans), cols):
                height += max(p[2] for p in plans[g:g + cols]) + gap
            height -= gap
            height += gap  # 二级分类间距
        height += gap      # 一级分类间距
    # 页尾卡片：内边距 + 大字行 + 小字行 + 排名行；底部额外留白（页尾卡片不贴底边）
    foot_h = inner * 2 + 36 + 6 + 26
    height += gap + foot_h + pad + 12

    img = Image.new("RGB", (width, height), DS_BG)
    d = ImageDraw.Draw(img)
    y = pad

    # ---- 标题区 ----
    dtext(d, (pad, y), f"{name} 的背包", font=title_font_, fill=DS_ACCENT)
    fav_txt = f"好感 Lv.{header['fav_lv']}"
    dtext(d, (int(width - pad - tw(fav_txt, info_font)), y + 10), fav_txt,
          font=info_font, fill=DS_GOLD)
    y += title_h
    # 金币 + 负债
    x = pad
    coin_txt = f"金币 {header['coins']}"
    dtext(d, (x, y), coin_txt, font=info_font, fill=DS_TEXT)
    x += int(tw(coin_txt, info_font))
    if header["debt"] > 0:
        debt_txt = f"｜负债 {header['debt']}"
        dtext(d, (x, y), debt_txt, font=info_font, fill=DEBT_COLOR)
    y += info_h
    # 宠物行
    if header["pet"]:
        p = header["pet"]
        pet_txt = f"宠物 {p['name']} Lv.{p['level']}"
        dtext(d, (pad, y), pet_txt, font=info_font, fill=DS_TEXT)
        st_txt = "异常" if p["abnormal"] else "正常"
        st_color = DEBT_COLOR if p["abnormal"] else DS_GREEN_SOFT
        dtext(d, (pad + int(tw(pet_txt, info_font)) + 10, y), st_txt, font=info_font, fill=st_color)
    else:
        dtext(d, (pad, y), "未领养宠物", font=info_font, fill=DS_MUTED)
    y += info_h
    # 农场行
    if header["farm"]:
        f = header["farm"]
        dtext(d, (pad, y),
              f"农场 Lv.{f['level']}　空闲 {f['idle']} 块｜占用 {f['occupied']} 块｜成熟 {f['mature']} 块",
              font=info_font, fill=DS_TEXT)
    else:
        dtext(d, (pad, y), "未解锁农场", font=info_font, fill=DS_MUTED)
    y += info_h + head_gap

    # ---- 内容区（两级分类） ----
    for l1, sub_plans in group_plans:
        # 一级分类名（居中）+ 分割线（居中）
        cx = int(pad + (page_w - tw(l1, l1_font)) / 2)
        dtext(d, (cx, y), l1, font=l1_font, fill=DS_TEXT)
        y += l1_h
        d.line([(pad, y), (width - pad, y)], fill=DS_MUTED, width=2)
        y += l1_rule_h
        for l2, plans in sub_plans:
            # 二级分类名（居中）+ 分割线（居中，更细更浅）
            cx = int(pad + (page_w - tw(l2, l2_font)) / 2)
            dtext(d, (cx, y), l2, font=l2_font, fill=DS_MUTED)
            y += l2_h
            d.line([(pad + 20, y), (width - pad - 20, y)], fill=DS_BORDER, width=1)
            y += l2_rule_h
            for g in range(0, len(plans), cols):
                group = plans[g:g + cols]
                gh = max(p[2] for p in group)
                for j, (card, rows, _) in enumerate(group):
                    x0 = pad + j * (card_w + gap)
                    d.rectangle([x0, y, x0 + card_w, y + gh], outline=DS_BORDER, width=1)
                    yy = y + inner
                    for row in rows:
                        kind = row[0]
                        if kind == "pair":
                            dtext(d, (int(x0 + inner), yy), row[1], font=name_font, fill=DS_TEXT)
                            dtext(d, (int(x0 + card_w - inner - tw(row[2], body_font)), yy + 4),
                                  row[2], font=body_font, fill=DS_GOLD)
                            yy += name_h
                        elif kind == "plain_name":
                            dtext(d, (int(x0 + inner), yy), row[1], font=name_font, fill=DS_TEXT)
                            yy += name_h - 4
                        elif kind == "plain":
                            dtext(d, (int(x0 + inner), yy), row[1], font=body_font, fill=DS_TEXT_2)
                            yy += line_h
                        elif kind == "rule":
                            yy += n_pad
                            d.line([(x0 + 8, yy), (x0 + card_w - 8, yy)], fill=DS_BORDER, width=1)
                            yy += 1
                        elif kind == "sell":
                            dtext(d, (int(x0 + card_w - inner - tw(row[1], info_font)), yy + 2),
                                  row[1], font=info_font, fill=SELL_COLOR)
                            yy += sell_h
                y += gh + gap
            y += gap  # 二级分类间距
        y += gap      # 一级分类间距

    # ---- 页尾大卡片 ----
    d.rectangle([pad, y, pad + page_w, y + foot_h], outline=DS_BORDER_2, width=2)
    yy = y + inner
    total_txt = f"仓库总价值 {footer['wh_total']}"
    dtext(d, (pad + inner, yy), total_txt, font=big_font, fill=DS_TEXT)
    net = footer["net"]
    net_txt = f"｜今日净收益 {'+' if net >= 0 else ''}{net}"
    net_color = DEBT_COLOR if net < 0 else DS_TEXT_2
    dtext(d, (pad + inner + int(tw(total_txt, big_font)) + 8, yy + 6), net_txt,
          font=body_font, fill=net_color)
    yy += 36 + 6

    def _rk(r):
        return f"第{r}名" if r else "未上榜"
    rank_txt = (f"金币排行 {_rk(footer['coins_rank'])}｜宠物排行 {_rk(footer['pet_rank'])}"
                f"｜农场排行 {_rk(footer['farm_rank'])}")
    dtext(d, (pad + inner, yy), rank_txt, font=info_font, fill=DS_TEXT_2)

    return save_temp_image(img, "_bag_", "背包")
