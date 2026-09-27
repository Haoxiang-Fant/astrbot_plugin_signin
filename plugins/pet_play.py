# -*- coding: utf-8 -*-
"""玩耍插件（3.0.0）：玩耍 / 玩耍 <名称> / 玩耍 全部。

2.3.0 宠物模块「玩耍」部分移植：
- 项目数值读 game_items.json（core.items()["plays"]，WebUI 可编辑），兼容扁平 cost_xxx
  与已归一化 cost dict 两种存储形态；收益含经验/心情（可负体力/健康收益按净变化展示）；
- 宠物忙碌计时器（busy_until/busy_activity/busy_item，打工/玩耍共用）；
- 等级/健康/心情要求与属性消耗检查 → 扣消耗（健康并入净变化防双扣）、加经验（优先宠物
  插件 gain_exp）与心情/体力/健康收益、进入冷却；
- 「捡到钱了」事件：MONEY_EVENT_CHANCE 概率（每日上限 MONEY_EVENT_MAX_PER_DAY，
  计数存 pet["money_event_count"]），捡到 MONEY_EVENT_GAIN 金币（经 core.add_coins）；
- 虚弱守卫经 core.service("pet").weak_guard（服务未挂载时不拦截）；
- 玩耍列表智能筛选（策略一：可进行最高 PLAY_SHOW_DOABLE 种 + 不可进行最低
  PLAY_SHOW_LOCKED 种）与图片渲染（render_work_play / image.text 缺失或失败时回退纯文本）。

完成逻辑抽为模块级函数 start_play(data, key, play_name, trigger="手动", name=None)，
经 core.service("pet_play").start / .start_play 复用。
"""
import random
from datetime import datetime

from astrbot.api import logger

from ..core import (ATTR_LABELS, PET_MAX_HEALTH, PET_MAX_LEVEL,
                    PLAY_SHOW_DOABLE, PLAY_SHOW_LOCKED,
                    MONEY_EVENT_CHANCE, MONEY_EVENT_GAIN, MONEY_EVENT_MAX_PER_DAY)

NAME = "pet_play"

_CORE = None  # register 时注入的核心框架引用（start_play 无 core 形参时经此取用）

# 2.2.1：每只宠物的「属性变化记录」上限（超出丢弃最旧）
_ATTR_LOG_MAX = 300


# ================= 数值配置（game_items.json plays → 2.3.0 归一化结构） =================
def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _norm_play(p):
    """单条玩耍项目归一化：兼容扁平 cost_stamina/... 形态与已归一化 cost dict 形态"""
    cost = p.get("cost")
    if not isinstance(cost, dict):
        cost = {"stamina": _f(p.get("cost_stamina", 0)),
                "satiety": _f(p.get("cost_satiety", 0)),
                "thirst": _f(p.get("cost_thirst", 0)),
                "health": _f(p.get("cost_health", 0))}
    return {"name": str(p.get("name", "") or ""),
            "desc": str(p.get("desc", "") or ""),
            "min_level": _f(p.get("min_level", 0)),
            "min_health": _f(p.get("min_health", 0)),
            "min_mood": _f(p.get("min_mood", 0)),
            "cost": {k: _f(v) for k, v in cost.items()},
            "time": _f(p.get("time", 0)),
            "exp": _f(p.get("exp", 0)),
            "mood": _f(p.get("mood", 0)),
            "stamina": _f(p.get("stamina", 0)),   # 玩耍收益体力（可负）
            "health": _f(p.get("health", 0))}     # 玩耍收益健康（可为负）


def _plays(core):
    """玩耍项目列表（core.items()["plays"] 归一化；缺名条目丢弃）"""
    return [_norm_play(p) for p in (core.items().get("plays") or []) if isinstance(p, dict) and str(p.get("name") or "").strip()]


# ================= 宠物能力降级封装（core.service("pet") 未挂载时本地等价实现） =================
def _parse_attr_max_ranges(raw):
    """解析 PET_ATTR_MAX_RANGES（健康值下限=饱食,口渴,体力,心情，竖线分隔）；失败回退默认四档"""
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


def _attr_maxes(core, health):
    """返回 (饱食上限, 口渴上限, 体力上限, 心情上限)：优先宠物插件 attr_max，未挂载时本地解析"""
    svc = core.service("pet")
    fn = getattr(svc, "attr_max", None) if svc is not None else None
    if callable(fn):
        try:
            r = fn(health)
            if r and len(r) == 4:
                return r
        except Exception as e:
            logger.error(f"[宠物玩耍] 读取属性上限失败: {e}")
    ranges = _parse_attr_max_ranges(core.param("PET_ATTR_MAX_RANGES"))
    for floor in sorted(ranges, reverse=True):
        if health >= floor:
            return ranges[floor]
    return (80.0, 80.0, 60.0, 80.0)


def _clamp_attrs(core, pet):
    sat_max, thr_max, sta_max, mood_max = _attr_maxes(core, pet["health"])
    pet["satiety"] = round(max(0.0, min(pet["satiety"], sat_max)), 2)
    pet["thirst"] = round(max(0.0, min(pet["thirst"], thr_max)), 2)
    pet["stamina"] = round(max(0.0, min(pet["stamina"], sta_max)), 2)
    pet["mood"] = round(max(0.0, min(pet["mood"], mood_max)), 2)
    pet["health"] = round(max(0.0, min(pet["health"], PET_MAX_HEALTH)), 2)


def _busy_until(core, pet):
    """打工/玩耍共用冷却计时器（优先宠物插件 busy_until；未挂载时兼容旧数据 work_until/play_until）"""
    svc = core.service("pet")
    fn = getattr(svc, "busy_until", None) if svc is not None else None
    if callable(fn):
        try:
            return float(fn(pet))
        except Exception as e:
            logger.error(f"[宠物玩耍] 读取忙碌计时器失败: {e}")
    busy = float(pet.get("busy_until", 0) or 0)
    old = max(float(pet.get("work_until", 0) or 0), float(pet.get("play_until", 0) or 0))
    return max(busy, old)


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


def _weak_block(core, key):
    """虚弱守卫（宠物插件 weak_guard）：返回提示文本或 None（服务未挂载/异常时不拦截）"""
    svc = core.service("pet")
    wg = getattr(svc, "weak_guard", None) if svc is not None else None
    if not callable(wg):
        return None
    try:
        return wg(key)
    except Exception as e:
        logger.error(f"[宠物玩耍] 虚弱守卫调用失败: {e}")
        return None


def _state_snippet(core, pet):
    """宠物当前状态摘要（优先宠物插件 state_snippet；未挂载时 2.3.0 同款文本降级）"""
    svc = core.service("pet")
    fn = getattr(svc, "state_snippet", None) if svc is not None else None
    if callable(fn):
        try:
            return fn(pet)
        except Exception as e:
            logger.error(f"[宠物玩耍] 读取状态摘要失败: {e}")
    sat_max, thr_max, sta_max, mood_max = _attr_maxes(core, pet["health"])
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


def _pet_level_from_exp(exp):
    """所需经验 = 当前等级 × 100（累计 100+200+...+（L-1）×100 升到 Lv.L）"""
    exp = max(0.0, float(exp))
    L = int((1 + (1 + 8 * exp / 100.0) ** 0.5) / 2)
    return min(PET_MAX_LEVEL, L)


def _apply_exp_local(pet):
    """本地经验结算（宠物插件未挂载时降级）：返回升级提示行"""
    new_level = _pet_level_from_exp(float(pet.get("exp", 0.0)))
    old = int(pet.get("level", 1))
    pet["level"] = new_level
    if new_level > old:
        return f"\n🎊 宠物升级！Lv.{old} → Lv.{new_level}"
    return ""


def _gain_pet_exp(core, key, pet, exp):
    """加宠物经验并返回升级提示行（优先宠物插件 gain_exp(key, exp)；未挂载时本地公式）"""
    svc = core.service("pet")
    fn = getattr(svc, "gain_exp", None) if svc is not None else None
    if callable(fn):
        try:
            return fn(key, exp) or ""
        except Exception as e:
            logger.error(f"[宠物玩耍] 宠物经验联动失败: {e}")
    pet["exp"] = round(float(pet.get("exp", 0.0)) + exp, 2)
    return _apply_exp_local(pet)


def _pet_attr_log(core, pet, cat, behavior, changes, extra=""):
    """2.2.1：追加一条宠物属性变化记录（pet["attr_log"]，随 records.json 存储）"""
    if not changes:
        return
    ts = datetime.now().timestamp()
    sat_max, thr_max, sta_max, mood_max = _attr_maxes(core, pet["health"])
    log = pet.setdefault("attr_log", [])
    log.append({
        "ts": ts,
        "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
        "cat": cat,
        "behavior": behavior,
        "changes": {k: round(float(v), 2) for k, v in changes.items() if abs(float(v)) > 1e-9},
        "extra": extra or "",
        "after": {k: round(float(pet.get(k, 0)), 2) for k in ATTR_LABELS},
        "max": {"satiety": sat_max, "thirst": thr_max, "stamina": sta_max,
                "mood": mood_max, "health": PET_MAX_HEALTH},
    })
    if len(log) > _ATTR_LOG_MAX:
        del log[: len(log) - _ATTR_LOG_MAX]


def _auto_care_after_change(core, key, trigger="手动"):
    """2.2.5 联动：玩耍后属性变化 → 通知自动照顾插件检查（服务未挂载时跳过）"""
    svc = core.service("pet_auto_care")
    fn = getattr(svc, "care_run", None) if svc is not None else None
    if not callable(fn):
        return
    try:
        fn(key, trigger)
    except Exception as e:
        logger.error(f"[宠物玩耍] 自动照顾联动失败: {e}")


# ================= 列表筛选与渲染 =================
def _item_can_do(pet, item, now_ts, busy):
    """该项目当前是否可以进行（与列表灰卡判定一致：忙碌/等级/健康/心情/消耗不足均不可）"""
    if pet is None:
        return False
    if busy:
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


def _select_play_items(core, pet, items, now_ts):
    """玩耍列表·策略一（2.0.2）：可进行项目中等级要求最高的 PLAY_SHOW_DOABLE 种
    + 不可进行项目中等级要求最低的 PLAY_SHOW_LOCKED 种，合并后按等级要求升序排列。
    个数由运行参数控制（core.param("PLAY_SHOW_*")）。"""
    doable_n = max(1, int(core.param("PLAY_SHOW_DOABLE", PLAY_SHOW_DOABLE)))
    locked_n = max(0, int(core.param("PLAY_SHOW_LOCKED", PLAY_SHOW_LOCKED)))
    busy = pet is not None and now_ts < _busy_until(core, pet)
    doable = [it for it in items if _item_can_do(pet, it, now_ts, busy)]
    locked = [it for it in items if not _item_can_do(pet, it, now_ts, busy)]
    top = sorted(doable, key=lambda it: (it["min_level"], it["name"]), reverse=True)[:doable_n]
    low = sorted(locked, key=lambda it: (it["min_level"], it["name"]))[:locked_n]
    return sorted(top + low, key=lambda it: (it["min_level"], it["name"]))


def _render_work_play(core, name, pet, items, coins):
    """玩耍列表专用渲染（image/pet.py render_work_play；缺失或失败返回 None 回退文本）"""
    fn = getattr(core.image, "render_work_play", None)
    if fn is None:
        return None
    try:
        return fn("玩耍", name, pet, items, coins=coins)
    except Exception as e:
        logger.error(f"[宠物玩耍] 渲染玩耍列表图片失败: {e}")
        return None


def _render_text(core, title, lines):
    """纯文本转图片（image.text；缺失或失败返回 None）"""
    fn = getattr(core.image, "text", None)
    if fn is None:
        return None
    try:
        return fn(title, lines)
    except Exception as e:
        logger.error(f"[宠物玩耍] 渲染文本图片失败: {e}")
        return None


def play_list(core, event=None, mode="smart"):
    """玩耍列表（event=None 时不含个人状态；mode="all" 全显，否则智能筛选）"""
    plays = _plays(core)
    if not plays:
        return "后台还没有配置玩耍项目（请管理员编辑 后台.txt）。"
    pet = None
    coins = None
    name = "玩家"
    if event is not None:
        key = core.user_key(event)
        pet = core.data.get("pets", {}).get(key)
        coins = core.coins_of(key)
        name = core.user_name(event)
    # 属性缺省 → 策略一（智能筛选）；属性=全部 → 策略二（全显）
    items = plays
    if mode == "smart":
        items = _select_play_items(core, pet, items, datetime.now().timestamp())
    img = _render_work_play(core, name, pet, items, coins)
    if img is not None:
        return img
    lines = ["发送「玩耍 <名称>」开始（发送「玩耍 全部」查看全部）", ""]
    for p in items:
        lines.append(f"· {p['name']}：{p['desc']}｜要求 Lv.{int(p['min_level'])}+ / 健康 {p['min_health']:.0f}+ / 心情 {p['min_mood']:.0f}+｜耗时 {p['time']:.0f}分｜经验 +{p['exp']:.0f} 心情 +{p['mood']:.0f}")
    txt_img = _render_text(core, "玩耍列表", lines)
    if txt_img is not None:
        return txt_img
    return "\n".join(["🎾 玩耍列表（发送「玩耍 <名称>」开始）："] + lines)


# ================= 玩耍完成逻辑（手动 / 自动化共用） =================
def start_play(data, key, play_name, trigger="手动", name=None):
    """开始一次玩耍（模块级复用函数：手动「玩耍 <名称>」与自动化插件共用）。

    data = core.data；key = 用户 key；play_name = 玩耍项目名；
    trigger = 触发来源（"手动" / "自动玩耍" 等，仅用于属性记录分类）；name = 显示昵称
    （缺省回退自定义昵称 / 用户 key）。返回回复文本（str）；数据变更后由本函数写盘。
    注意：调用方若在事务中，请自行持有 core.lock。
    """
    core = _CORE
    if core is None:
        return "玩耍功能尚未就绪，请稍后再试。"
    if name is None:
        name = core.custom_name_of(key) or str(key)

    play = next((p for p in _plays(core) if p["name"] == play_name), None)
    if not play:
        return f"没有名为「{play_name}」的玩耍项目，发送「玩耍」或「玩耍 全部」查看列表。"

    pet = data.get("pets", {}).get(key)
    if not pet:
        return f"{name} 还没有宠物，发送「解锁宠物」领养一只吧。"

    # 虚弱守卫（宠物插件 weak_guard；服务未挂载时不拦截）
    weak_msg = _weak_block(core, key)
    if weak_msg:
        return weak_msg

    # 冷却检查：打工/玩耍共用一个计时器，冷却期内不能进行新的玩耍或打工
    now_ts = datetime.now().timestamp()
    busy_until = _busy_until(core, pet)
    if now_ts < busy_until:
        return f"{name} 的宠物还在忙碌中（冷却剩余 {_fmt_duration(busy_until - now_ts)}），暂时不能打工或玩耍。"

    # 要求检查
    if pet["level"] < play["min_level"]:
        return f"宠物等级不足（需要 Lv.{int(play['min_level'])}，当前 Lv.{pet['level']}）。"
    if pet["health"] < play["min_health"]:
        return f"宠物健康度不足（需要 {play['min_health']:.0f}，当前 {pet['health']:.1f}）。"
    if pet["mood"] < play["min_mood"]:
        return f"宠物心情不足（需要 {play['min_mood']:.0f}，当前 {pet['mood']:.1f}）。"

    # 消耗检查
    for attr, cost in play["cost"].items():
        if cost > 0 and pet[attr] < cost:
            return f"{ATTR_LABELS[attr]}不足，无法玩耍（需要 {cost:.0f}，当前 {pet[attr]:.1f}）。"

    # 记录变化前属性（供属性变化记录）
    before = {a: pet[a] for a in ATTR_LABELS}
    for attr, cost in play["cost"].items():
        if attr == "health":
            continue  # 健康消耗并入下方净变化计算（避免双扣）
        pet[attr] = round(max(0.0, pet[attr] - cost), 2)

    pet["mood"] = round(pet["mood"] + play["mood"], 2)
    if play.get("stamina", 0) > 0:
        pet["stamina"] = round(pet["stamina"] + play["stamina"], 2)
    # 健康净变化：收益《健康度》(可负) − 消耗《消耗健康度》
    pet["health"] = round(
        pet["health"] + play.get("health", 0) - play["cost"].get("health", 0), 2)
    lvl_msg = _gain_pet_exp(core, key, pet, play["exp"])
    _clamp_attrs(core, pet)

    # 「捡到钱了」事件：概率触发（每日上限，计数存宠物记录）
    chance = float(core.param("MONEY_EVENT_CHANCE", MONEY_EVENT_CHANCE))
    gain_coins = int(core.param("MONEY_EVENT_GAIN", MONEY_EVENT_GAIN))
    max_per_day = int(core.param("MONEY_EVENT_MAX_PER_DAY", MONEY_EVENT_MAX_PER_DAY))
    bonus = ""
    money_gain = 0
    if pet.get("money_event_count", 0) < max_per_day and random.random() < chance:
        core.add_coins(key, gain_coins, "玩耍捡到钱")
        pet["money_event_count"] = int(pet.get("money_event_count", 0)) + 1
        money_gain = gain_coins
        bonus = f"\n🍀 触发「捡到钱了」事件，金币 +{gain_coins}！\n{core.coin_line(key)}"

    # 进入冷却（打工/玩耍共用计时器）
    pet["busy_until"] = now_ts + int(play["time"]) * 60
    pet["busy_start"] = now_ts
    pet["busy_activity"] = "玩耍"
    pet["busy_item"] = play["name"]
    pet["_progress_done_notified"] = False  # 新一轮进度条开始，重置「满后第一次响应」标记
    changes = {a: round(pet[a] - before[a], 2) for a in ATTR_LABELS if abs(pet[a] - before[a]) > 1e-9}
    # 2.2.1：记录本次玩耍造成的属性变化
    _pet_attr_log(core, pet, "玩耍" if trigger == "手动" else trigger,
                  f"玩耍「{play['name']}」", changes,
                  extra=f"经验+{play['exp']:.1f}" + (f"，捡到钱+{money_gain}金币" if money_gain else ""))
    pet["last_activity"] = {
        "msg": f"{pet['name']} 去「{play['name']}」玩耍成功！",
        "changes": changes,
        "reason": f"玩耍「{play['name']}」",
        "coins": money_gain,
        "exp": play["exp"],
        "act": "玩耍",
        "ts": now_ts,
        "shown": True,   # 本次文本回复即视为已显示一次
    }
    core.save()
    _auto_care_after_change(core, key, trigger)  # 2.2.5：玩耍后属性变化 → 自动照顾检查

    cd = f"（冷却 {int(play['time'])} 分钟）" if play["time"] > 0 else ""
    gain = f"经验 +{play['exp']:.1f}，😊 心情 +{play['mood']:.1f}"
    net_stamina = play.get("stamina", 0) - play["cost"].get("stamina", 0)
    if net_stamina != 0:
        gain += f"，💪 体力 {net_stamina:+.1f}"
    net_health = play.get("health", 0) - play["cost"].get("health", 0)
    if net_health != 0:
        gain += f"，❤️ 健康 {net_health:+.1f}"
    return (f"🎾 {name} 的宠物去「{play['name']}」玩耍完成！{cd}\n"
            f"🐾 {gain}{lvl_msg}{bonus}\n"
            f"{_state_snippet(core, pet)}")


# ================= 服务接口（自动化等插件经 core.service("pet_play") 调用） =================
class PetPlayApi:
    """玩耍服务接口：start / start_play / play_list"""

    def __init__(self, core):
        self._core = core

    def start(self, data, key, play=None, trigger="手动", name=None):
        """开始一次玩耍（play=None 返回 None，由调用方先选定项目）"""
        if not play:
            return None
        return start_play(data, key, str(play), trigger=trigger, name=name)

    def start_play(self, data, key, play_name, trigger="手动", name=None):
        """开始一次指定玩耍（与模块级 start_play 等价）"""
        return start_play(data, key, play_name, trigger=trigger, name=name)

    def play_list(self):
        """归一化玩耍项目列表（自动化选项目用）"""
        return _plays(self._core)


def register(core):
    global _CORE
    _CORE = core

    @core.command("玩耍", feature="pet_play")
    def handle_play(event):
        key = core.user_key(event)
        parts = event.message_str.split(maxsplit=1)

        if len(parts) < 2:
            return play_list(core, event)          # 策略一：可进行最高6 + 不可进行最低2
        play_name = parts[1].strip()
        if play_name == "全部":
            return play_list(core, event, mode="all")   # 策略二：全部显示

        return start_play(core.data, key, play_name, trigger="手动", name=core.user_name(event))

    core.expose("pet_play", PetPlayApi(core))

    core.add_help("宠物玩耍", [
        ("玩耍", "查看玩耍项目列表（智能筛选可进行与即将解锁的项目）"),
        ("玩耍 <名称>", "陪宠物玩耍：获得经验和心情，概率触发「捡到钱了」事件"),
        ("玩耍 全部", "查看全部玩耍项目"),
    ])
