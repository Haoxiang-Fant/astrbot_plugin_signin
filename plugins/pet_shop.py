# -*- coding: utf-8 -*-
"""宠物商店插件（3.0.0）。自 2.3.0 modules/pet.py 迁移：「商店」「购买」两个指令。

「商店」：按类型分组展示宠物商店商品（组内按实时价升序、分类间按组内最低价升序），
图片经 core.image.render_shop 统一渲染（未挂载或渲染失败时回退 2.3.0 同款纯文本）。
「购买」：先按宠物商店商品（core.items()["shop"]）购买；非宠物商品按 2.3.0 规则
委托农场商店服务 core.service("farm_shop")（「种子」后缀 → buy_seed，化肥名 → buy_fert），
服务缺失时回退旧版报错文案。价格浮动（2.0.4：偶数点窗口 + 特价时段随机折扣）逻辑原样迁移，
运行参数经 core.param 读取（WebUI 可调，常量仅作缺省）。
"""
import random
from datetime import datetime

from astrbot.api import logger

from ..core import (
    ATTR_SHORT,
    PILL_NAME, EXP_BALL_NAME,
    PILL_DAILY_LIMIT, EXP_BALL_DAILY_LIMIT,
    SHOP_PRICE_FLOAT_ENABLED, SHOP_PRICE_SPECIAL_HOURS,
    SHOP_PRICE_DISCOUNT_MIN, SHOP_PRICE_DISCOUNT_MAX,
    SHOP_PRICE_DISCOUNT_LO, SHOP_PRICE_DISCOUNT_HI,
    SIGNIN_NO_REWARD_CHANCE, SIGNIN_PILL_CHANCE, SIGNIN_BALL_CHANCE,
)

NAME = "pet_shop"


# ================= 商店数值（game_items.json 扁平结构 → 2.3.0 规范结构） =================
def _f(x):
    """宽松转 float（失败回 0，与 2.3.0 self._f 一致）"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def shop_items(core):
    """商店商品列表（2.3.0 规范结构：name/type/desc/price/effects{五属性}）"""
    out = []
    for s in core.items().get("shop") or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name", "")).strip()
        if not name:
            continue
        typ = str(s.get("type", "") or "").strip()
        if typ == "食品":  # 旧类型统一归一为「食物」
            typ = "食物"
        out.append({
            "name": name, "type": typ, "desc": str(s.get("desc", "") or ""),
            "price": _f(s.get("price", 0)),
            "effects": {
                "satiety": _f(s.get("satiety", 0)),
                "thirst": _f(s.get("thirst", 0)),
                "stamina": _f(s.get("stamina", 0)),
                "mood": _f(s.get("mood", 0)),
                "health": _f(s.get("health", 0)),
            },
        })
    return out


def find_shop_item(core, name):
    """按名称查找商店商品（无则 None）"""
    return next((it for it in shop_items(core) if it["name"] == name), None)


# ================= 实时价格（2.0.4 原样迁移） =================
def _shop_price_window(now=None):
    """当前价格窗口（2.0.4）：刷新时间固定偶数点（0/2/4/…/22 整点），同一窗口（2 小时）内价格稳定。
    返回 (窗口起始小时, 窗口标识串)；窗口标识形如 YYYYMMDD|HH，用于固定随机种子。"""
    now = now or datetime.now()
    start_hour = now.hour - (now.hour % 2)
    wid = "%s|%02d" % (now.strftime("%Y%m%d"), start_hour)
    return start_hour, wid


# 按窗口缓存折扣映射（同一窗口内价格稳定，避免每次查价都重读配置）
_DISCOUNT_CACHE = [None]  # [(wid, {商品名: 倍率})]


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
    names = [it["name"] for it in shop_items(core)]
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


def _pet_shop_price(core, item):
    """宠物商店实时价格（2.0.4，默认关闭）：价格固定偶数点（0/2/…/22）刷新，同一 2 小时窗口稳定；
    特价时段（10/12/18/0 时窗口）随机选取 2~5 件商品打 二~八折（倍率 0.2~0.8）；其余时段全部原价。
    返回 (原价, 实时价, 是否打折)。"""
    base = int(item.get("price", 0))
    if not bool(core.param("SHOP_PRICE_FLOAT_ENABLED", SHOP_PRICE_FLOAT_ENABLED)):
        return base, base, False
    _start, wid = _shop_price_window()
    mult = _shop_discount_map_cached(core, wid).get(item["name"], 1.0)
    if mult >= 1.0 - 1e-9:
        return base, base, False
    price = max(1, int(round(base * mult)))
    return base, price, price != base


# ================= 文案辅助（2.3.0 同款） =================
def _effect_desc(effects: dict) -> str:
    """道具效果描述（「饱食+10 口渴-5」形式；无效果时「无效果」）"""
    parts = []
    for k, short in ATTR_SHORT.items():
        v = effects.get(k, 0)
        if v > 0:
            parts.append(f"{short}+{v:.0f}")
        elif v < 0:
            parts.append(f"{short}{v:.0f}")
    return " ".join(parts) if parts else "无效果"


def _signin_reward_chances(core):
    """签到额外奖励池概率（WebUI 可编辑）：返回 (无奖品, 属性丸, 经验球)；
    三者总和超过 1 时按比例归一，保证互斥奖池总和恒为 1（2.3.0 原样迁移）。"""
    no_r = float(core.param("SIGNIN_NO_REWARD_CHANCE", SIGNIN_NO_REWARD_CHANCE))
    pill_r = float(core.param("SIGNIN_PILL_CHANCE", SIGNIN_PILL_CHANCE))
    ball_r = float(core.param("SIGNIN_BALL_CHANCE", SIGNIN_BALL_CHANCE))
    total = no_r + pill_r + ball_r
    if total <= 1e-9:
        return 0.40, 0.30, 0.30
    if total > 1.0:
        return no_r / total, pill_r / total, ball_r / total
    return no_r, pill_r, ball_r


def _weak_lock(core, key):
    """虚弱宠物守卫（2.2.1）：宠物虚弱期间返回锁定提示，否则 None。
    守卫文案由 pet 插件 weak_guard 提供；pet 插件未挂载时不拦截。"""
    svc = core.service("pet")
    fn = getattr(svc, "weak_guard", None) if svc is not None else None
    if fn is None:
        return None
    try:
        return fn(key)
    except Exception as e:
        logger.error(f"[{NAME}] 虚弱守卫调用失败: {e}")
        return None


# ================= 商店视图 =================
def _shop_categories(core):
    """商店分类视图（2.3.0 原样迁移）：按类型分组（保持配置顺序），组内商品按实时价升序
    （价格越高位置越靠后）；分类之间也按「组内最低价」升序，便宜的类别靠前。无商品时返回 None。"""
    items = shop_items(core)
    if not items:
        return None
    categories = []
    seen = {}
    for it in items:
        typ = it["type"] or "其他"
        if typ not in seen:
            seen[typ] = len(categories)
            categories.append((typ, []))
        categories[seen[typ]][1].append(it)
    for _typ, its in categories:
        its.sort(key=lambda it: (_pet_shop_price(core, it)[1], it["name"]))
    categories.sort(key=lambda cat: (min((_pet_shop_price(core, it)[1] for it in cat[1]), default=0), cat[0]))
    return categories


def _render_shop_image(core, name, categories, inventory, coins=None):
    """宠物商店图片（图片响应模块统一渲染）：render_shop 未挂载或渲染失败时返回 None（回退纯文本）"""
    fn = getattr(core.image, "render_shop", None)
    if fn is None:
        return None
    try:
        return fn(name, categories, inventory, coins)
    except Exception as e:
        logger.error(f"[{NAME}] 渲染商店图片失败: {e}")
        return None


def _handle_shop(core, event):
    categories = _shop_categories(core)
    if categories is None:
        return "商店暂无商品（请管理员编辑 后台.txt）。"
    # 当前用户持有数量
    key = core.user_key(event)
    pet = core.data.get("pets", {}).get(key)
    inventory = pet.get("inventory", {}) if pet else {}
    img = _render_shop_image(core, core.user_name(event), categories, inventory, core.coins_of(key))
    if img is not None:
        return img
    lines = ["🛒 宠物商店（发送「购买 <道具名> [数量]」购买，发送「使用 <道具名> [数量]」使用）："]
    for typ, items in categories:
        lines.append(f"【{typ}】")
        for it in items:
            have = int(inventory.get(it["name"], 0))
            _o, cur, _f2 = _pet_shop_price(core, it)
            ln = f"· {it['name']} ×{have}｜{cur}金币"
            if _f2:
                ln += f"（原价 {_o} 金币）"
            if it.get("desc"):
                ln += f"（{it['desc']}）"
            ln += f"：{_effect_desc(it['effects'])}"
            lines.append(ln)
    _no, _pill_p, _ball_p = _signin_reward_chances(core)
    pill_limit = int(core.param("PILL_DAILY_LIMIT", PILL_DAILY_LIMIT))
    ball_limit = int(core.param("EXP_BALL_DAILY_LIMIT", EXP_BALL_DAILY_LIMIT))
    lines.append(f"· {PILL_NAME}（特殊）：随机 2 个属性 +5~20（每日最多 {pill_limit} 次，签到 {_pill_p * 100:.0f}% 概率获得）")
    lines.append(f"· {EXP_BALL_NAME}（农场特殊道具）：获得升级经验 5%~20%（每日最多 {ball_limit} 次，签到 {_ball_p * 100:.0f}% 概率获得）")
    return "\n".join(lines)


# ================= 购买 =================
def _buy_pet_item(core, event, item_name, qty, item):
    """购买宠物商店商品（调用方已解析参数并匹配到商品）。返回回复文本。"""
    name = core.user_name(event)
    key = core.user_key(event)
    pet = core.data.get("pets", {}).get(key)
    if not pet:
        return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"

    # 2.0.3：购买按实时价（价格浮动）结算
    _orig_p, price, _floated = _pet_shop_price(core, item)
    total = price * qty
    coins = core.coins_of(key)
    if coins < total:
        return f"金币不足：{qty} × {price} = {total} 金币，当前 {coins}。"

    core.add_coins(key, -total, f"购买道具·{item_name}")
    inv = pet.setdefault("inventory", {})
    inv[item_name] = int(inv.get(item_name, 0)) + qty
    core.save()
    price_note = f"（实时价 {price} 金币，原价 {_orig_p}）" if _floated else ""
    return (f"🛒 {name} 花费 {total} 金币购买了「{item_name}」×{qty}{price_note}。发送「使用 {item_name}」使用。\n"
            f"{core.coin_line(key)}")


def _handle_buy(core, event):
    """购买 <名称> [数量]：宠物商店道具 / 农场种子 / 农场化肥"""
    key = core.user_key(event)
    # 虚弱宠物守卫（与 2.3.0 main 派发前拦截同序：先于参数格式检查）
    lock = _weak_lock(core, key)
    if lock:
        return lock
    item_name, qty, err = core.parse_item_qty(event.message_str)
    if err:
        return f"格式：购买 <道具名> [数量]。{err}"

    # 宠物商店商品优先
    item = find_shop_item(core, item_name)
    if item:
        return _buy_pet_item(core, event, item_name, qty, item)

    # 非宠物商品 → 农场商店委托（2.3.0 规则：「种子」后缀买种子，否则按化肥名买化肥）
    svc = core.service("farm_shop")
    buy_seed = getattr(svc, "buy_seed", None) if svc is not None else None
    buy_fert = getattr(svc, "buy_fert", None) if svc is not None else None
    if buy_seed is not None or buy_fert is not None:
        flat = core.items()
        if item_name.endswith("种子") and buy_seed is not None:
            crop = next((x for x in (flat.get("crops") or [])
                         if isinstance(x, dict) and x.get("name") == item_name[:-2]), None)
            if crop:
                return buy_seed(event)
        fert = next((x for x in (flat.get("ferts") or [])
                     if isinstance(x, dict) and x.get("name") == item_name), None)
        if fert and buy_fert is not None:
            return buy_fert(event)
    return f"没有「{item_name}」这个商品（宠物商店 / 农场商店都没有），发送「商店」或「农场商店」查看。"


# ================= 服务暴露（其它插件经 core.service("pet_shop") 调用） =================
class PetShopApi:
    """宠物商店服务：商品读取与购买（被「购买」指令及自动化等插件联动）"""

    def __init__(self, core):
        self._core = core

    def shop_items(self):
        """商店商品列表（2.3.0 规范结构：name/type/desc/price/effects）"""
        return shop_items(self._core)

    def find_item(self, name):
        """按名称查找商店商品（无则 None）"""
        return find_shop_item(self._core, name)

    def live_price(self, item):
        """商品实时价格 → (原价, 实时价, 是否打折)（价格浮动未开启时为原价）"""
        return _pet_shop_price(self._core, item)

    def buy(self, event, item_name, qty):
        """购买宠物商店商品（返回回复文本）；非本店商品返回 None（调用方可继续委托农场商店）"""
        item = find_shop_item(self._core, item_name)
        if item is None:
            return None
        return _buy_pet_item(self._core, event, str(item_name), int(qty), item)


def register(core):
    def handle_shop(event):
        """商店：查看宠物商店（按类型分组、实时价排序）"""
        return _handle_shop(core, event)

    def handle_buy(event):
        """购买 <道具名> [数量]：宠物商店道具；非宠物商品委托农场商店（种子/化肥）"""
        return _handle_buy(core, event)

    core.command("商店", feature="pet_shop")(handle_shop)
    core.command("购买", feature="pet_shop")(handle_buy)

    core.expose("pet_shop", PetShopApi(core))
