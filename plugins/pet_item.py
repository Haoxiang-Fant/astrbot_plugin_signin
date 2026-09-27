# -*- coding: utf-8 -*-
"""宠物道具插件（3.0.0）。自 2.3.0 modules/pet.py 迁移：「使用」指令的宠物背包分支。

属性丸（PILL_NAME：随机 N 个属性 +5~20，每日限用）与商店道具的使用效果原样迁移
（好感等级倍率 → 属性累加 → 上限裁剪 → attr_log 记录 → last_activity → 自动照顾联动）；
非宠物道具（化肥 / 农场经验球等农场道具）委托农场道具服务 core.service("farm_item")
的 use_item(key, name, qty)，服务未挂载或返回 None 时回退旧版「没有这个道具」文案。
属性上限 / 状态摘要优先经 pet 服务获取（pet 插件未挂载时回退本地同款计算），运行参数经
core.param 读取（WebUI 可调，常量仅作缺省）。
"""
import random
from datetime import date, datetime

from astrbot.api import logger

from ..core import (
    ATTR_LABELS, ATTR_SHORT, PET_MAX_HEALTH, PET_ATTR_MAX_RANGES,
    PILL_NAME, PILL_DAILY_LIMIT, PILL_ATTR_COUNT, PILL_BOOST_MIN, PILL_BOOST_MAX,
    AUTO_BUY_SHORT_ENABLED, AUTO_BUY_SHORT_MULT,
)

NAME = "pet_item"

# 2.2.1：每只宠物的「属性变化记录」上限（超出丢弃最旧；WebUI 宠物记录详情页展示）
_ATTR_LOG_MAX = 300


# ================= 商店数值（game_items.json 扁平结构 → 2.3.0 规范结构） =================
def _f(x):
    """宽松转 float（失败回 0，与 2.3.0 self._f 一致）"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _shop_items(core):
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


def _find_shop_item(core, name):
    """按名称查找商店商品（无则 None）"""
    return next((it for it in _shop_items(core) if it["name"] == name), None)


def _live_price(core, item):
    """商店道具实时价 (原价, 实时价, 是否打折)：优先 pet_shop 服务，未挂载时按原价"""
    svc = core.service("pet_shop")
    fn = getattr(svc, "live_price", None) if svc is not None else None
    if fn is not None:
        try:
            r = fn(item)
            if r:
                return r
        except Exception as e:
            logger.error(f"[{NAME}] live_price 调用失败: {e}")
    base = int(item.get("price", 0))
    return base, base, False


# ================= 宠物数值辅助（优先 pet 服务，未挂载时回退 2.3.0 本地计算） =================
def _fav_multipliers(fav_level: int):
    """返回 (正面效果倍率, 负面效果倍率)（2.3.0 原样迁移）"""
    if fav_level <= 1:
        return 1.0, 1.0
    if fav_level <= 5:
        return 1.1, 1.0
    if fav_level <= 9:
        return 1.1, 0.9
    return 1.2, 0.8


def _parse_attr_max_ranges(raw):
    """解析「140=200,200,200,120|80=…」属性上限数据（2.0.2：阈值可在 WebUI 编辑；
    解析失败回退 2.3.0 默认档）。"""
    out = {}
    for seg in str(raw or "").replace("；", "|").replace(";", "|").split("|"):
        seg = seg.strip()
        if "=" not in seg:
            continue
        floor, body = seg.split("=", 1)
        vals = []
        for x in body.replace("，", ",").split(","):
            try:
                vals.append(float(x.strip()))
            except (TypeError, ValueError):
                vals = []
                break
        if len(vals) == 4:
            try:
                out[float(floor.strip())] = tuple(vals)
            except (TypeError, ValueError):
                pass
    return out or {140.0: (200.0, 200.0, 200.0, 120.0), 80.0: (120.0, 120.0, 120.0, 100.0),
                   40.0: (100.0, 100.0, 100.0, 100.0), 0.0: (80.0, 80.0, 60.0, 80.0)}


def _attr_max(core, pet_svc, health):
    """返回 (饱食上限, 口渴上限, 体力上限, 心情上限)，由健康度决定（优先 pet 服务 attr_max）"""
    fn = getattr(pet_svc, "attr_max", None) if pet_svc is not None else None
    if fn is not None:
        try:
            r = fn(health)
            if r:
                return tuple(float(x) for x in r)
        except Exception as e:
            logger.error(f"[{NAME}] attr_max 调用失败: {e}")
    ranges = _parse_attr_max_ranges(core.param("PET_ATTR_MAX_RANGES", PET_ATTR_MAX_RANGES))
    # 按健康值下限从高到低匹配（健康 >= 下限 即命中）
    for floor in sorted(ranges, reverse=True):
        if health >= floor:
            return ranges[floor]
    return (80.0, 80.0, 60.0, 80.0)


def _clamp_attrs(core, pet_svc, pet: dict) -> None:
    """属性上限裁剪（0 ~ 各自上限；健康度上限 PET_MAX_HEALTH）"""
    sat_max, thr_max, sta_max, mood_max = _attr_max(core, pet_svc, pet["health"])
    pet["satiety"] = round(max(0.0, min(sat_max, pet["satiety"])), 2)
    pet["thirst"] = round(max(0.0, min(thr_max, pet["thirst"])), 2)
    pet["stamina"] = round(max(0.0, min(sta_max, pet["stamina"])), 2)
    pet["mood"] = round(max(0.0, min(mood_max, pet["mood"])), 2)
    pet["health"] = round(max(0.0, min(PET_MAX_HEALTH, pet["health"])), 2)


def _state_snippet_fallback(core, pet_svc, pet: dict) -> str:
    """宠物当前状态摘要（pet 服务未挂载时的 2.3.0 同款本地文案；
    1.7.6：状态低判定采用第三档标准——饱食 <50 或 口渴 <60 或 心情 <40 时附加提示）"""
    sat_max, thr_max, sta_max, mood_max = _attr_max(core, pet_svc, pet["health"])
    weak = "，😷 虚弱（发送「治疗宠物」）" if pet.get("weak") else ""
    line = (f"🐾 {pet.get('name', '宠物')}：饱食 {pet['satiety']:.0f}/{sat_max:.0f}，"
            f"口渴 {pet['thirst']:.0f}/{thr_max:.0f}，体力 {pet['stamina']:.0f}/{sta_max:.0f}，"
            f"心情 {pet['mood']:.0f}/{mood_max:.0f}，健康 {pet['health']:.0f}/{PET_MAX_HEALTH:.0f}{weak}")
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


def _pet_state_snippet(core, pet_svc, pet: dict) -> str:
    """宠物当前状态摘要：优先 pet 服务 state_snippet（未挂载时回退本地同款文案）"""
    fn = getattr(pet_svc, "state_snippet", None) if pet_svc is not None else None
    if fn is not None:
        try:
            s = fn(pet)
            if s:
                return s
        except Exception as e:
            logger.error(f"[{NAME}] state_snippet 调用失败: {e}")
    return _state_snippet_fallback(core, pet_svc, pet)


def _pet_attr_log(core, pet_svc, pet: dict, cat: str, behavior: str, changes: dict,
                  extra: str = "", ts=None, items=None):
    """2.2.1：追加一条宠物属性变化记录（pet["attr_log"]，写盘剥离到 records.json 由
    format_convert 完成）。cat = 行为分类；behavior = 具体行为描述；
    changes = {属性key: 变化量}（仅记录非零项）；extra = 附加信息；
    items = [(道具名, 数量, 效果dict), ...]（2.2.7：本次使用的道具及其效果快照）。"""
    if not changes:
        return
    if ts is None:
        ts = datetime.now().timestamp()
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = datetime.now().timestamp()
    log = pet.setdefault("attr_log", [])
    sat_max, thr_max, sta_max, mood_max = _attr_max(core, pet_svc, pet["health"])
    entry = {
        "ts": ts,
        "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
        "cat": cat,
        "behavior": behavior,
        "changes": {k: round(float(v), 2) for k, v in changes.items() if abs(float(v)) > 1e-9},
        "extra": extra or "",
        # 2.2.2：变动后的属性绝对值快照（本函数总在属性变更后立即调用，pet 当前值即为变动后状态）
        "after": {k: round(float(pet.get(k, 0)), 2) for k in ATTR_LABELS},
        # 2.2.2：变动发生时的各属性上限快照（上限随健康度动态变化，旧记录无此字段）
        "max": {"satiety": sat_max, "thirst": thr_max, "stamina": sta_max,
                "mood": mood_max, "health": PET_MAX_HEALTH},
    }
    if items:
        # 2.2.7：道具效果快照（每条记录冻结使用当时的数值，商店后续改价/改效果不影响历史展示）
        arr = []
        for n, q, eff in items:
            fx = {}
            for k, v in (eff or {}).items():
                try:
                    fv = round(float(v), 2)
                except (TypeError, ValueError):
                    continue
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


# ================= 联动服务（None 安全） =================
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


def _auto_care_after_change(core, key) -> None:
    """2.2.7：宠物属性变化后（使用道具）触发自动照顾检查——任一属性处于第 3/4 档且
    开启了自动照顾 → 自动照顾插件立即照顾（触发来源 = 属性变化）。
    需在 core.lock 内、调用方 core.save() 之后调用（与 2.3.0 钩子时序一致）。"""
    svc = core.service("pet_auto_care")
    fn = getattr(svc, "care_run", None) if svc is not None else None
    if fn is None:
        return
    try:
        fn(key, "属性变化")
    except Exception as e:
        logger.error(f"[{NAME}] 自动照顾联动失败: {e}")


def _render_pet_status(core, name, key, data, pet, slot_msg=None, changes=None, reason=None):
    """宠物状态图（图片响应模块统一渲染）：render_pet_status 未挂载或渲染失败时返回 None。
    优先按 2.3.0 完整参数调用（变动高亮等），渲染器不支持时回退最小参数。"""
    fn = getattr(core.image, "render_pet_status", None)
    if fn is None:
        return None
    try:
        return fn(name, key, data, pet, slot_msg=slot_msg, changes=changes, reason=reason,
                  coins_delta=None, exp_delta=None,
                  changes_fresh=bool(changes), card2_hl=False)
    except TypeError:
        try:
            return fn(name, key, data, pet, slot_msg=slot_msg)
        except Exception as e:
            logger.error(f"[{NAME}] 渲染宠物状态图失败: {e}")
            return None
    except Exception as e:
        logger.error(f"[{NAME}] 渲染宠物状态图失败: {e}")
        return None


# ================= 使用道具（宠物背包分支） =================
def _use_pet_item(core, key, item_name, qty, display_name):
    """使用宠物背包道具（属性丸 / 商店道具）。非宠物道具返回 None（调用方决定后续委托）。"""
    data = core.data
    if item_name != PILL_NAME and _find_shop_item(core, item_name) is None:
        return None
    pet = data.get("pets", {}).get(key)
    if not pet:
        return f"{display_name} 还没有宠物，发送「解锁宠物」领养一只吧。"

    pet_svc = core.service("pet")
    user = data.get("users", {}).get(key, {})
    fav_level = core.level_of(float(user.get("favorability", 0.0)))
    pos_mult, neg_mult = _fav_multipliers(fav_level)

    inv = pet.setdefault("inventory", {})

    # 属性丸（特殊道具：随机 N 个属性 +5~20，每日最多 PILL_DAILY_LIMIT 次）
    if item_name == PILL_NAME:
        today = date.today().isoformat()
        if pet.get("pill_used_date") != today:
            pet["pill_used_date"] = today
            pet["pill_used_count"] = 0
        used = int(pet.get("pill_used_count", 0))
        pill_limit = int(core.param("PILL_DAILY_LIMIT", PILL_DAILY_LIMIT))
        if used + qty > pill_limit:
            return f"属性丸每天最多使用 {pill_limit} 次（今天已用 {used} 次）。"
        have = int(inv.get(PILL_NAME, 0))
        if have < qty:
            return f"属性丸不足：需要 {qty} 个，当前 {have} 个（签到有几率获得）。"
        before = {a: pet[a] for a in ATTR_LABELS}  # 2.2.2：变动前快照（记录实际生效的变化量）
        boosts = {}
        attr_count = int(core.param("PILL_ATTR_COUNT", PILL_ATTR_COUNT) or 2)
        boost_lo = core.param("PILL_BOOST_MIN", PILL_BOOST_MIN)
        boost_hi = core.param("PILL_BOOST_MAX", PILL_BOOST_MAX)
        for _ in range(qty):
            for attr in random.sample(list(ATTR_LABELS), attr_count):  # 随机 N 个属性
                v = round(random.uniform(boost_lo, boost_hi) * pos_mult, 2)
                boosts[attr] = round(boosts.get(attr, 0) + v, 2)
                pet[attr] = round(pet[attr] + v, 2)
        inv[PILL_NAME] = have - qty
        if inv[PILL_NAME] <= 0:
            inv.pop(PILL_NAME, None)
        pet["pill_used_count"] = used + qty
        _clamp_attrs(core, pet_svc, pet)
        # 2.2.2：实际生效的变化量（clamp 后 after−before，超出上限的部分不虚记）
        changes = {a: round(pet[a] - before[a], 2) for a in ATTR_LABELS if abs(pet[a] - before[a]) > 1e-9}
        # 2.2.1：记录使用属性丸造成的属性变化（2.2.7：附带道具与实际生效效果快照）
        _pet_attr_log(core, pet_svc, pet, "使用道具", f"使用「{PILL_NAME}」×{qty}", changes,
                      items=[(PILL_NAME, qty, boosts)])
        msg = f"{pet['name']} 使用「属性丸」×{qty} 成功！"
        pet["last_activity"] = {"msg": msg, "changes": changes,
                                "reason": f"使用「{PILL_NAME}」×{qty}",
                                "coins": 0, "exp": 0, "act": "使用",
                                "ts": datetime.now().timestamp(),
                                "shown": False}
        core.save()
        _auto_care_after_change(core, key)  # 2.2.7：使用属性丸属性变化 → 自动照顾检查
        desc = "，".join(f"{ATTR_SHORT[a]}+{v:.1f}" for a, v in boosts.items())
        text = (f"💊 {display_name} 使用了属性丸×{qty}：{desc}\n"
                f"（今日已用 {pet['pill_used_count']}/{pill_limit} 次）\n"
                f"{_pet_state_snippet(core, pet_svc, pet)}")
        # 2.0.0 消息合并：使用道具结果合入宠物指令图片的预留位（即时消息显示一次）
        # 本次产生新的状态变化 → 卡片1高亮；无工作/玩耍变动 → 卡片2不高亮
        img = _render_pet_status(
            core, display_name, key, data, pet,
            slot_msg=f"{msg}（今日已用 {pet['pill_used_count']}/{pill_limit} 次）",
            changes=changes, reason=f"使用「{PILL_NAME}」×{qty}")
        if pet.get("last_activity"):
            pet["last_activity"]["shown"] = True
            core.save()
        return img if img is not None else text

    # 商店道具
    shop_item = _find_shop_item(core, item_name)
    effects = shop_item.get("effects") or {}
    have = int(inv.get(item_name, 0))
    auto_buy_note = None
    if have < qty:
        # 2.0.3：缺货自动购买（默认关闭）——仓库不足时自动购买足额并使用
        if bool(core.param("AUTO_BUY_SHORT_ENABLED", AUTO_BUY_SHORT_ENABLED)):
            short = qty - have
            mult = float(core.param("AUTO_BUY_SHORT_MULT", AUTO_BUY_SHORT_MULT) or 1.0)
            cur_price = _live_price(core, shop_item)[1]
            cost = int(round(cur_price * mult * short))
            coins = core.coins_of(key)
            if coins >= cost:
                core.add_coins(key, -cost, f"自动购买·{item_name}")
                inv[item_name] = qty
                have = qty
                auto_buy_note = (short, cost)
            else:
                return (f"你没有足够的「{item_name}」：需要 {qty} 个，当前 {have} 个；"
                        f"缺货自动购买 {short} 个需 {cost} 金币，但当前金币不足（{coins}）。")
        else:
            return f"你没有足够的「{item_name}」：需要 {qty} 个，当前 {have} 个。发送「购买 {item_name} {qty}」购买。"

    before = {a: pet[a] for a in ATTR_LABELS}  # 2.2.2：变动前快照（记录实际生效的变化量）
    for _ in range(qty):
        for attr in ATTR_LABELS:
            val = effects.get(attr, 0)
            if val > 0:
                applied = round(val * pos_mult, 2)
            elif val < 0:
                applied = round(val * neg_mult, 2)
            else:
                continue
            pet[attr] = round(pet[attr] + applied, 2)

    inv[item_name] = have - qty
    if inv[item_name] <= 0:
        inv.pop(item_name, None)
    _clamp_attrs(core, pet_svc, pet)
    # 2.2.2：实际生效的变化量（clamp 后 after−before，超出 0/上限的部分不虚记）
    changes = {a: round(pet[a] - before[a], 2) for a in ATTR_LABELS if abs(pet[a] - before[a]) > 1e-9}
    # 2.2.1：记录使用商店道具造成的属性变化（2.2.7：附带道具与效果快照）
    if changes:
        _pet_attr_log(core, pet_svc, pet, "使用道具", f"使用「{item_name}」×{qty}", changes,
                      extra=(f"缺货自动购买 {auto_buy_note[0]} 个，花费 {auto_buy_note[1]} 金币" if auto_buy_note else ""),
                      items=[(item_name, qty, effects)])
    msg = f"{pet['name']} 使用「{item_name}」×{qty} 成功！"
    if auto_buy_note:
        msg += f"（缺货自动购买 {auto_buy_note[0]} 个，花费 {auto_buy_note[1]} 金币）"
    if not changes:
        msg += "（无效果）"
    pet["last_activity"] = {"msg": msg, "changes": changes,
                            "reason": f"使用「{item_name}」×{qty}",
                            "coins": 0, "exp": 0, "act": "使用",
                            "ts": datetime.now().timestamp(),
                            "shown": False}
    core.save()
    _auto_care_after_change(core, key)  # 2.2.5：手动使用道具后属性变化 → 自动照顾检查

    if not changes:
        text = (f"✅ {display_name} 使用了「{item_name}」×{qty}（无效果）。\n"
                f"{_pet_state_snippet(core, pet_svc, pet)}")
    else:
        desc = "，".join(f"{ATTR_SHORT[a]}{v:+.1f}" for a, v in changes.items())
        text = (f"✅ {display_name} 使用了「{item_name}」×{qty}：{desc}\n"
                f"{_pet_state_snippet(core, pet_svc, pet)}")
    if auto_buy_note:
        _short, _cost = auto_buy_note
        text += f"（缺货自动购买 {_short} 个，花费 {_cost} 金币）"
    # 2.0.0 消息合并：使用道具结果合入宠物指令图片的预留位（即时消息显示一次）
    # 本次有属性变化 → 卡片1高亮；无工作/玩耍变动 → 卡片2不高亮
    img = _render_pet_status(core, display_name, key, data, pet, slot_msg=msg,
                             changes=changes, reason=f"使用「{item_name}」×{qty}")
    if pet.get("last_activity"):
        pet["last_activity"]["shown"] = True
        core.save()
    return img if img is not None else text


def _handle_use_item(core, event):
    name = core.user_name(event)
    key = core.user_key(event)
    # 虚弱宠物守卫（与 2.3.0 main 派发前拦截同序：先于参数格式检查）
    lock = _weak_lock(core, key)
    if lock:
        return lock
    item_name, qty, err = core.parse_item_qty(event.message_str)
    if err:
        return f"格式：使用 <道具名> [数量]。{err}"

    # 宠物背包道具优先（属性丸 / 商店道具；非宠物道具返回 None）
    r = _use_pet_item(core, key, item_name, qty, name)
    if r is not None:
        return r

    # 非宠物道具 → 农场道具服务（化肥：使用 <化肥> <分钟数>；农场经验球等，2.0.0/2.0.1 起归农场）
    svc = core.service("farm_item")
    fn = getattr(svc, "use_item", None) if svc is not None else None
    if fn is not None:
        try:
            r = fn(key, item_name, qty)
        except Exception as e:
            logger.error(f"[{NAME}] farm_item.use_item 调用失败: {e}")
            r = None
        if r is not None:
            return r
    return f"没有「{item_name}」这个道具，发送「商店」查看。"


# ================= 服务暴露（其它插件经 core.service("pet_item") 调用） =================
class PetItemApi:
    """宠物道具服务：宠物背包道具使用（「使用」指令联动入口）"""

    def __init__(self, core):
        self._core = core

    def use_item(self, key, name, qty, display_name=None):
        """使用宠物背包道具（属性丸 / 商店道具）：key=用户 key，name=道具名，qty=数量。
        返回回复文本；非宠物道具返回 None（调用方可继续委托 farm_item 服务）。
        display_name 缺省时用自定义昵称 / 用户 key（仅供回复文案显示）。"""
        core = self._core
        if display_name is None:
            display_name = core.custom_name_of(key) or str(key)
        return _use_pet_item(core, key, str(name), int(qty), display_name)


def register(core):
    def handle_use(event):
        """使用 <道具名> [数量]：宠物道具（属性丸/商店道具）；农场道具交由 farm_item 服务"""
        return _handle_use_item(core, event)

    core.command("使用", feature="pet_item")(handle_use)

    core.expose("pet_item", PetItemApi(core))
