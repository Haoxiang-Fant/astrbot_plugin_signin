# -*- coding: utf-8 -*-
"""自动照顾（3.0.0 功能插件）。自 2.3.0 modules/pet.py「自动照顾」部分原样迁移（2.2.7 重写版）：
「自动照顾 开/关」指令 + 每日结算照顾触发（core.daily("auto_care_daily")）。
- 检查机制：任一属性处于第 3/4 档 → 需要照顾（档位由属性最大值数据统一推导，虚弱期间不触发）；
- 照顾流程：健康优先补到 最大健康 × 目标百分比（AUTO_FEED_TARGET_HEALTH_PCT，默认 80%），
  随后 饱食(食物) → 口渴(饮料) → 心情(玩具) → 体力(体力丸) 逐项按档位处理；
- 道具来源：优先使用持有道具（效果与手动使用一致、套用好感加成），
  仓库没有 → 购买并立即使用（价格 = 手动购买价 × AUTO_BUY_PRICE_MULT，默认 1.1）；
  金币不足 → 自动化贷款（单一余额 user["auto_loan"]，无上限无逾期无利息，仅自动照顾可贷）；
- 金币变化一律走 core.add_coins（唯一出入口）；照顾消耗计入基准金币 user["work_base"]，
  日志写入 user["auto_feed_logs"]（≤ AUTO_FEED_LOG_MAX 条）。
其他插件经 core.service("pet_auto_care") 调用 care_due(key) / care_run(key, trigger)
（调用方需持有 core.lock，如指令处理与 core 巡检协程；care_run 发生照顾时自行 core.save()）。"""
from datetime import date, datetime

from astrbot.api import logger

from ..core import (AUTO_BUY_PRICE_MULT, AUTO_FEED_ENABLED, AUTO_FEED_LOG_MAX,
                    AUTO_FEED_TARGET_HEALTH_PCT, ATTR_LABELS, PET_MAX_HEALTH)

NAME = "pet_auto_care"

_CORE = None  # register(core) 时注入的核心框架实例

# 体力丸：仅自动化程序可购买/使用的道具（不进商店，手动「购买/使用」无效），
# 价格 50 金币，效果为将体力值补满。
STAMINA_PILL_NAME = "体力丸"
STAMINA_PILL_PRICE = 50
# 照顾补属性顺序：健康 → 饱食 → 口渴 → 心情 → 体力（对应道具类别）
_CARE_TYPE_MAP = {"health": "药物", "satiety": "食物", "thirst": "饮料", "mood": "玩具"}
# 2.2.7 统一数值管理：档位不再单独配置，由「属性最大值数据」统一推导——
# 一档下限 = 60% 上限、二档下限 = 35%、三档下限 = 15%，低于 15% 为四档。
_TIER_PCTS = (0.6, 0.35, 0.15)
# 每只宠物的「属性变化记录」上限（与宠物插件口径一致，超出丢弃最旧）
_ATTR_LOG_MAX = 300


def _f(x):
    """安全转 float（失败回退 0.0）"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ================= 宠物插件能力（core.service("pet")，全部 None-safe） =================
def _pet_fn(name):
    """取宠物插件接口方法；插件未挂载/无该方法返回 None（调用方自行降级）"""
    svc = _CORE.service("pet") if _CORE is not None else None
    fn = getattr(svc, name, None)
    return fn if callable(fn) else None


def _bring_up_to_date(pet, today):
    """self._bring_pet_up_to_date → core.service("pet").bring_up_to_date（None-safe：插件未挂载时跳过）"""
    fn = _pet_fn("bring_up_to_date")
    if fn is None:
        return
    try:
        fn(pet, today)
    except Exception as e:
        logger.error(f"[pet_auto_care] bring_up_to_date 调用失败: {e}")


def _attr_max(health):
    """self._attr_max(h) → core.service("pet").attr_max(h)；宠物插件未挂载/调用失败返回 None"""
    fn = _pet_fn("attr_max")
    if fn is None:
        return None
    try:
        return fn(health)
    except Exception as e:
        logger.error(f"[pet_auto_care] attr_max 调用失败: {e}")
        return None


# ================= 档位与属性工具（2.2.7 统一数值管理，与 2.3.0 一致） =================
def _attr_tier(val, max_v):
    """按属性最大值推导档位：≥60% 一档、≥35% 二档、≥15% 三档、否则四档。"""
    max_v = _f(max_v or 0)
    val = _f(val or 0)
    if max_v <= 0:
        return 1
    for i, p in enumerate(_TIER_PCTS):
        if val >= max_v * p:
            return i + 1
    return 4


def _pet_attr_tiers(pet):
    """五属性当前档位 {attr: tier}（统一由属性最大值数据推导；健康上限 = PET_MAX_HEALTH）；
    宠物插件未挂载（无属性上限数据）时返回 {}。"""
    maxes = _attr_max(pet.get("health", 0))
    if maxes is None:
        return {}
    sat_max, thr_max, sta_max, mood_max = maxes
    return {"health": _attr_tier(pet.get("health", 0), PET_MAX_HEALTH),
            "satiety": _attr_tier(pet.get("satiety", 0), sat_max),
            "thirst": _attr_tier(pet.get("thirst", 0), thr_max),
            "mood": _attr_tier(pet.get("mood", 0), mood_max),
            "stamina": _attr_tier(pet.get("stamina", 0), sta_max)}


def _clamp_attrs(pet):
    """属性裁剪到当前健康度对应的各属性上限（2.3.0 _clamp_attrs 原样移植）；
    宠物插件未挂载时跳过（无上限数据可裁）。"""
    maxes = _attr_max(pet.get("health", 0))
    if maxes is None:
        return
    sat_max, thr_max, sta_max, mood_max = maxes
    pet["satiety"] = round(_CORE.clamp(pet["satiety"], 0, sat_max), 2)
    pet["thirst"] = round(_CORE.clamp(pet["thirst"], 0, thr_max), 2)
    pet["stamina"] = round(_CORE.clamp(pet["stamina"], 0, sta_max), 2)
    pet["mood"] = round(_CORE.clamp(pet["mood"], 0, mood_max), 2)
    pet["health"] = round(_CORE.clamp(pet["health"], 0, PET_MAX_HEALTH), 2)


def _fav_multipliers(fav_level):
    """返回 (正面效果倍率, 负面效果倍率)（与手动使用道具一致的好感等级加成）"""
    if fav_level <= 1:
        return 1.0, 1.0
    if fav_level <= 5:
        return 1.1, 1.0
    if fav_level <= 9:
        return 1.1, 0.9
    return 1.2, 0.8


# ================= 数值配置（core.items()["shop"] 扁平结构 → 2.3.0 内部结构） =================
def _normalize_shop(flat_shop):
    """game_items.json 扁平结构 → [{name, type, desc, price, effects{五属性}}]；
    旧类型「食品」兼容为「食物」（与 2.3.0 _items_flat_to_normalized 同口径）。"""
    out = []
    for s in flat_shop or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name", "") or "").strip()
        if not name:
            continue
        typ = str(s.get("type", "") or "").strip()
        if typ == "食品":
            typ = "食物"
        out.append({"name": name, "type": typ, "desc": str(s.get("desc", "") or ""),
                    "price": _f(s.get("price", 0)),
                    "effects": {"satiety": _f(s.get("satiety", 0)),
                                "thirst": _f(s.get("thirst", 0)),
                                "stamina": _f(s.get("stamina", 0)),
                                "mood": _f(s.get("mood", 0)),
                                "health": _f(s.get("health", 0))}})
    return out


def _shop_buy_price(item):
    """道具手动购买价：2.0.4 实时浮动价格默认关闭（SHOP_PRICE_FLOAT_ENABLED=False），
    浮动/特价定价属宠物商店插件职责，自动化取商品原价为手动价基准。"""
    return int(_f(item.get("price", 0)))


# ================= 自动化贷款（2.2.7：单一余额，无上限无逾期无利息，仅自动照顾可贷） =================
def _auto_loan_balance_of(data, key):
    """自动化贷款余额（user["auto_loan"]）"""
    u = data.get("users", {}).get(key)
    if not isinstance(u, dict):
        return 0.0
    try:
        return round(float(u.get("auto_loan", 0) or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _auto_loan_borrow(data, key, need):
    """自动化贷款：金额无上限、无逾期、无利息，仅自动照顾流程调用（不能手动贷款）。
    贷款全额入账（core.add_coins 唯一出入口）。返回实际发放金额。"""
    need = int(need or 0)
    if need <= 0:
        return 0.0
    u = _CORE.ensure_user(key)
    u["auto_loan"] = round(_auto_loan_balance_of(data, key) + need, 2)
    _CORE.add_coins(key, need, "自动化贷款")
    return float(need)


# ================= 开关与检查机制（2.2.7） =================
def _auto_care_enabled_for(data, key):
    """自动照顾是否对该用户生效：总开关 + 主人开启 + 有宠物且未虚弱"""
    if not bool(_CORE.param("AUTO_FEED_ENABLED", AUTO_FEED_ENABLED)):
        return False
    u = data.get("users", {}).get(key)
    if not (u and u.get("auto_feed_enabled")):
        return False
    pet = data.get("pets", {}).get(key)
    return bool(pet) and not pet.get("weak")


def _auto_care_due(data, key):
    """检查机制：任一属性处于第 3/4 档 → 需要自动照顾
    （档位统一由属性最大值数据推导，见 _TIER_PCTS；虚弱期间不触发）。"""
    if not _auto_care_enabled_for(data, key):
        return False
    pet = data.get("pets", {}).get(key)
    return any(t >= 3 for t in _pet_attr_tiers(pet).values())


# ================= 照顾执行（2.2.7 道具使用机制） =================
def _care_apply_item(data, key, pet, item, attr, target, pos_mult, neg_mult):
    """照顾使用道具：五条属性效果全部生效并套用好感等级加成（与手动使用一致）；
    目标属性按目标值封顶，其余属性全额生效，超出上限由 _clamp_attrs 统一裁剪。"""
    for a in ("satiety", "thirst", "stamina", "mood", "health"):
        try:
            v = float((item.get("effects") or {}).get(a, 0) or 0)
        except (TypeError, ValueError):
            continue
        if abs(v) < 1e-9:
            continue
        v = round(v * pos_mult, 2) if v > 0 else round(v * neg_mult, 2)
        if a == attr:
            pet[a] = round(min(target, float(pet.get(a, 0) or 0) + v), 2)
        else:
            pet[a] = round(float(pet.get(a, 0) or 0) + v, 2)


def _care_fill_attr(data, key, pet, attr, target, shop, inv, pos_mult, neg_mult, spends, state):
    """把属性补到目标值（2.2.7 道具使用机制）：
    - 优先使用用户持有的道具（免费，效果与手动使用一致）；
    - 仓库没有 → 购买并立即使用（价格 = 手动购买价 × AUTO_BUY_PRICE_MULT，默认 1.1）；
    - 金币不足 → 自动申请自动化贷款（无上限无逾期）后继续；
    每轮选「效果与缺口最接近」的同类道具（健康=药物 / 饱食=食物 / 口渴=饮料 / 心情=玩具）。
    返回是否补到目标值。"""
    typ = _CARE_TYPE_MAP.get(attr, "")
    for _ in range(30):  # 防御：最多 30 轮，防无解死循环
        cur = float(pet.get(attr, 0) or 0)
        if cur >= target - 0.5:
            return True
        need = target - cur
        # 1) 仓库优先：效果最接近缺口的持有道具
        best = None
        for it in shop:
            if str(it.get("type") or "").strip() != typ:
                continue
            if int(inv.get(it["name"], 0) or 0) <= 0:
                continue
            try:
                eff = float((it.get("effects") or {}).get(attr, 0) or 0)
            except (TypeError, ValueError):
                continue
            if eff <= 0:
                continue
            k = (abs(eff - need), int(it.get("price", 0) or 0), it["name"])
            if best is None or k < best[0]:
                best = (k, it)
        if best is not None:
            it = best[1]
            inv[it["name"]] = int(inv.get(it["name"], 0) or 0) - 1
            if inv[it["name"]] <= 0:
                inv.pop(it["name"], None)
            _care_apply_item(data, key, pet, it, attr, target, pos_mult, neg_mult)
            spends.append((it["name"], 1, 0, "使用"))
            continue
        # 2) 仓库没有 → 购买并立即使用
        best = None
        for it in shop:
            if str(it.get("type") or "").strip() != typ:
                continue
            try:
                eff = float((it.get("effects") or {}).get(attr, 0) or 0)
            except (TypeError, ValueError):
                continue
            if eff <= 0:
                continue
            k = (abs(eff - need), int(it.get("price", 0) or 0), it["name"])
            if best is None or k < best[0]:
                best = (k, it)
        if best is None:
            return False  # 商店没有该类别道具 → 放弃
        it = best[1]
        cost = max(1, int(round(_shop_buy_price(it)
                                * float(_CORE.param("AUTO_BUY_PRICE_MULT", AUTO_BUY_PRICE_MULT) or 1.1))))
        if _CORE.coins_of(key) < cost:
            _auto_loan_borrow(data, key, cost - _CORE.coins_of(key))
        if _CORE.coins_of(key) < cost:
            return False  # 贷款异常失败 → 放弃
        _CORE.add_coins(key, -cost, f"自动照顾·{it['name']}")
        state["total"] += cost
        spends.append((it["name"], 1, cost, "购买"))
        _care_apply_item(data, key, pet, it, attr, target, pos_mult, neg_mult)
    return False


def _pet_attr_log(pet, cat, behavior, changes, extra="", ts=None, items=None):
    """追加一条宠物属性变化记录（与 2.3.0 _pet_attr_log 同结构，WebUI 宠物详情展示用）。
    cat = 行为分类（自动照顾/自动打工等）；changes = {属性key: 变化量}；
    items = [(道具名, 数量, 效果dict), ...]（本次使用的道具及其效果快照）。
    宠物插件未挂载（无属性上限数据）时跳过记录。"""
    if not changes:
        return
    if ts is None:
        ts = datetime.now().timestamp()
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = datetime.now().timestamp()
    maxes = _attr_max(pet.get("health", 0))
    if maxes is None:
        return
    sat_max, thr_max, sta_max, mood_max = maxes
    log = pet.setdefault("attr_log", [])
    entry = {
        "ts": ts,
        "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
        "cat": cat,
        "behavior": behavior,
        "changes": {k: round(float(v), 2) for k, v in changes.items() if abs(float(v)) > 1e-9},
        "extra": extra or "",
        # 变动后的属性绝对值快照（属性条可视化：基础=变动前=after-changes）
        "after": {k: round(float(pet.get(k, 0)), 2) for k in ATTR_LABELS},
        # 变动发生时的各属性上限快照（上限随健康度动态变化）
        "max": {"satiety": sat_max, "thirst": thr_max, "stamina": sta_max,
                "mood": mood_max, "health": PET_MAX_HEALTH},
    }
    if items:
        # 道具效果快照（每条记录冻结使用当时的数值）
        arr = []
        for n, q, eff in items:
            fx = {}
            for k, v in (eff or {}).items():
                fv = round(float(v), 2)
                if abs(fv) > 1e-9:
                    fx[str(k)] = fv
            try:
                iq = int(q)
            except (TypeError, ValueError):
                iq = 1
            arr.append({"name": str(n), "qty": max(1, iq), "effects": fx})
        if arr:
            entry["items"] = arr
    log.append(entry)
    if len(log) > _ATTR_LOG_MAX:
        del log[:len(log) - _ATTR_LOG_MAX]


# ================= 自动照顾主流程（2.2.7 重写） =================
def _auto_care_run(data, key, trigger="属性变化", initial=False):
    """自动照顾。流程：
    1) 健康优先补到 最大健康 × 目标百分比（WebUI 可调，默认 80%）；
    2) 随后按 饱食(食物) → 口渴(饮料) → 心情(玩具) → 体力(体力丸) 逐项检查：
       处于第 1/2 档 → 本次忽略；第 3/4 档 → 补满（开启初始照顾 initial=True 时改为提升到第 1 档）；
    3) 道具来源：优先使用用户持有的道具（效果与手动使用一致，套用好感加成），
       仓库没有 → 购买并立即使用（价格 = 手动购买价 × 购买价格倍率）；
       金币不足 → 自动化贷款（无上限无逾期）；
    4) 消耗道具导致的金币消耗计入基准金币（work_base），并记录照顾日志与属性变化。
    trigger：触发来源（开启照顾/属性变化/每日结算/自动打工后）。
    返回本次日志条目（无金币消耗时返回 None）。调用方负责 core.save()
    （服务入口 care_run 已内置 core.save()）。"""
    pet = data.get("pets", {}).get(key)
    if not pet:
        return None
    if _attr_max(pet.get("health", 0)) is None:
        logger.warning("[pet_auto_care] 宠物插件未挂载（attr_max 不可用），跳过自动照顾。")
        return None
    today = date.today().isoformat()
    _bring_up_to_date(pet, today)
    before = {a: pet[a] for a in ATTR_LABELS}
    shop = _normalize_shop((_CORE.items() or {}).get("shop"))
    inv = pet.setdefault("inventory", {})
    user = data.get("users", {}).get(key, {})
    fav_level = _CORE.level_of(float(user.get("favorability", 0.0)))
    pos_mult, neg_mult = _fav_multipliers(fav_level)
    tiers = _pet_attr_tiers(pet)
    sat_max, thr_max, sta_max, mood_max = _attr_max(pet["health"])
    health_target = round(PET_MAX_HEALTH * float(_CORE.param("AUTO_FEED_TARGET_HEALTH_PCT", AUTO_FEED_TARGET_HEALTH_PCT) or 0.8), 2)
    spends = []
    state = {"total": 0}
    # 1) 健康优先：低于目标值即补到目标（80% 最大健康）
    _care_fill_attr(data, key, pet, "health", health_target, shop, inv,
                    pos_mult, neg_mult, spends, state)
    # 2) 饱食 → 口渴 → 心情：第 1/2 档忽略，第 3/4 档补满（初始照顾提升到第 1 档即可）
    for attr, max_v in (("satiety", sat_max), ("thirst", thr_max), ("mood", mood_max)):
        target = (max_v * _TIER_PCTS[0]) if initial else max_v
        if not initial and tiers.get(attr, 1) <= 2:
            continue  # 第 1/2 档 → 本次忽略
        _care_fill_attr(data, key, pet, attr, round(float(target), 2), shop, inv,
                        pos_mult, neg_mult, spends, state)
    # 3) 体力丸：第 3/4 档（初始照顾未达第 1 档）→ 50 金币购买并使用，直接补满体力
    sta_target = sta_max if not initial else sta_max * _TIER_PCTS[0]
    if float(pet.get("stamina", 0) or 0) < sta_target - 0.5 and (initial or tiers.get("stamina", 1) >= 3):
        cost = STAMINA_PILL_PRICE
        if _CORE.coins_of(key) < cost:
            _auto_loan_borrow(data, key, cost - _CORE.coins_of(key))
        if _CORE.coins_of(key) >= cost:
            _CORE.add_coins(key, -cost, f"自动照顾·{STAMINA_PILL_NAME}")
            state["total"] += cost
            spends.append((STAMINA_PILL_NAME, 1, cost, "购买"))
            pet["stamina"] = round(float(sta_max), 2)
    _clamp_attrs(pet)
    if not spends:
        return None
    total = int(state["total"])
    # 聚合同道具同来源的明细（日志显示 ×N 数量标记）
    agg = {}
    for nm, q, c, s in spends:
        k = (nm, s)
        if k in agg:
            agg[k][1] += q
            agg[k][2] += c
        else:
            agg[k] = [nm, q, c, s]
    u = data.setdefault("users", {}).setdefault(key, {})
    entry = {"date": today,
             "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
             "trigger": trigger,
             "items": [{"name": n, "qty": q, "cost": c, "src": s} for n, q, c, s in agg.values()],
             "total": total,
             "mult": round(float(_CORE.param("AUTO_BUY_PRICE_MULT", AUTO_BUY_PRICE_MULT) or 1.1), 2),
             "loan": 0}
    logs = u.setdefault("auto_feed_logs", [])
    logs.append(entry)
    cap = int(_CORE.param("AUTO_FEED_LOG_MAX", AUTO_FEED_LOG_MAX) or 30)
    if len(logs) > cap:
        del logs[:len(logs) - cap]
    # 记录本次自动照顾造成的属性变化（附带使用的道具/数量/效果快照）
    changes = {a: round(pet[a] - before[a], 2) for a in ATTR_LABELS if abs(pet[a] - before[a]) > 1e-9}
    eff_map = {it["name"]: dict(it.get("effects") or {}) for it in shop}
    eff_map[STAMINA_PILL_NAME] = {"stamina": round(float(sta_max), 2)}  # 体力丸效果 = 补满体力
    _pet_attr_log(pet, "自动照顾", f"自动照顾（{trigger}）", changes,
                  extra=f"花费 {total} 金币" + ("（初始照顾）" if initial else ""),
                  items=[(it["name"], it["qty"], eff_map.get(it["name"], {})) for it in entry["items"]])
    # 产生基准金币：消耗道具导致的金币消耗（含贷款支付的购买）
    u["work_base"] = int(u.get("work_base", 0) or 0) + total
    # 照顾产生基准金币 → 自动打工由 pet_auto_work 插件的 core.interval(60) 常驻巡检自动接管
    return entry


# ================= 每日结算照顾触发（core.daily 承载） =================
def _auto_care_daily(data, now):
    """自动照顾「每日结算」触发（2.3.0 固定结算循环内该段原样移植，幂等标记 auto_care_daily）：
    所有开启了自动照顾且宠物处于 3/4 档的用户（每日结算发生的属性变化属于触发情形之一）
    各执行一次照顾（trigger=每日结算）。返回 True 仅当全程无异常
    （core 据此写幂等标记，失败次日自动重试）；数据变更由本函数自行 core.save()。"""
    ok = True
    for key, u in list((data.get("users") or {}).items()):
        if not isinstance(u, dict):
            continue
        try:
            if _auto_care_due(data, key):
                _auto_care_run(data, key, trigger="每日结算")
        except Exception as e:
            ok = False
            logger.error(f"[pet_auto_care] 自动照顾每日结算异常 uid={key}: {e}")
    if _CORE is not None:
        _CORE.save()
    return ok


# ================= 服务暴露（宠物/打工/玩耍插件属性变化后联动） =================
class PetAutoCareApi:
    """自动照顾服务：care_due(key) 检查 + care_run(key, trigger) 执行
    （调用方需持有 core.lock，如指令处理与 core 巡检协程）"""

    def care_due(self, key):
        """该用户当前是否需要自动照顾（开启条件满足 + 任一属性处于第 3/4 档）"""
        return _auto_care_due(_CORE.data, key)

    def care_run(self, key, trigger="属性变化", initial=False):
        """执行一次自动照顾；照顾发生（有道具消耗/使用）时自行 core.save()。
        返回本次日志条目（无消耗时返回 None）。"""
        entry = _auto_care_run(_CORE.data, key, trigger=trigger, initial=initial)
        if entry is not None and _CORE is not None:
            _CORE.save()
        return entry


def register(core):
    global _CORE
    _CORE = core

    # ================= 自动照顾 开/关 =================
    @core.command("自动照顾", feature="pet_auto")
    def handle_auto_feed_switch(event):
        """自动照顾 <开/关>（2.2.7 重写）：
        开 → 开启自动照顾并同步开启自动打工，随后执行开启时的初始照顾：
             检查宠物状态，使用道具将所有属性提升到第 1 档（健康补到目标值），记录金币消耗；
        关 → 关闭自动照顾并同步关闭自动打工。"""
        name = core.user_name(event)
        key = core.user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or parts[1].strip() not in ("开", "关"):
            return f"{name} 请指定：自动照顾 开 / 自动照顾 关"
        data = core.data
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"
        on = parts[1].strip() == "开"
        u = core.ensure_user(key)
        u["auto_feed_enabled"] = on
        extra = ""
        if on:
            u["auto_work_enabled"] = True  # 开启自动照顾 → 同步开启自动打工
            # 开启操作：初始照顾 —— 所有属性提升至第 1 档（健康补到目标值），记录使用了多少金币
            entry = _auto_care_run(data, key, trigger="开启照顾", initial=True)
            if entry:
                items = "、".join(f"{'🛒' if it.get('src') == '购买' else '📦'}{it['name']}×{it['qty']}"
                                  for it in entry.get("items", []))
                extra = f"\n🛒 初始照顾完成：{items}（花费 {entry.get('total', 0)} 金币，已计入基准金币）"
            else:
                extra = "\n✅ 已完成开启检查：宠物全部属性已处于第 1 档，无需照顾。"
        else:
            u["auto_work_enabled"] = False  # 关闭自动照顾 → 同步关闭自动打工
        core.save()
        if on:
            mult = core.param("AUTO_BUY_PRICE_MULT", AUTO_BUY_PRICE_MULT)
            return (f"✅ {name} 已开启自动照顾（自动打工已同步开启）：五属性每次变化"
                    f"（打工/玩耍/使用道具/治疗后）与每日结算时自动检查，任一属性处于第 3/4 档即自动照顾："
                    f"健康补到最大健康×目标百分比（WebUI 可调，默认 80%）；饱食/口渴/心情/体力按档位处理——"
                    f"第 1/2 档忽略、第 3/4 档补满（体力用体力丸 50 金币/个直接补满）；"
                    f"优先使用持有的道具（效果与手动使用一致），没有再购买（手动价 × {mult} 倍）；"
                    f"照顾花费计入基准金币，由自动打工填补；金币不足自动申请自动化贷款"
                    f"（无上限无逾期，可用「还款」指令或打工报酬偿还）。发送「结算日志」查看记录。{extra}")
        return f"⏹️ {name} 已关闭自动照顾（自动打工已同步关闭）。"

    # 每日结算照顾触发（core 统一巡检承载，幂等标记 auto_care_daily）
    core.daily("auto_care_daily", _auto_care_daily)

    # 服务暴露：宠物/打工/玩耍插件经 core.service("pet_auto_care") 联动
    core.expose("pet_auto_care", PetAutoCareApi())
