# -*- coding: utf-8 -*-
"""仓库背包（3.0.0 功能插件）。自 2.3.0 原样迁移：
「背包」← modules/pet.py _handle_bag（1.7.7 大改：标题区[用户名/好感等级/金币/负债/宠物/农场]
  + 分类卡片[宠物道具/作物/种子/肥料] + 页尾大卡[仓库总价值/今日净收益/三榜排名]，渲染失败回退文本列表）；
「农场仓库」← modules/farm.py _handle_farm_warehouse（农场仓库视图，图片优先、回退文本清单）；
「今日净收益」零点基线 ← modules/base.py _ensure_bag_base / _rank_score_coins
  （金币排行积分 = 金币×RANK_COIN_COIN_W + 存款本金×RANK_COIN_BANK_W），
  并订阅 coins.pre_add：金币变动前固定当日基线——该事件回调签名为 cb(payload)
  （payload 为单一带位置 dict，核心不经 ** 展开 调用，勿写成 cb(**payload)）。
跨插件能力一律 core.service() None-safe 降级：农场 farm.farm_of / 欠款 bank_loan.debt_summary /
三榜 rank.entries；宠物道具/作物/肥料数值读 core.items()；
图片经 core.image.render_bag / render_warehouse（未挂载或失败回退纯文本）。"""
from datetime import date, datetime

from astrbot.api import logger

from ..core import (ATTR_SHORT, PET_MAX_LEVEL, PILL_NAME, EXP_BALL_NAME,
                    PILL_ATTR_COUNT, PILL_BOOST_MIN, PILL_BOOST_MAX, PILL_DAILY_LIMIT,
                    EXP_BALL_MIN_PCT, EXP_BALL_MAX_PCT, EXP_BALL_DAILY_LIMIT,
                    RANK_COIN_COIN_W, RANK_COIN_BANK_W, FARM_UNLOCK_COST)

NAME = "warehouse"

_CORE = None  # register(core) 时注入的核心框架实例


# ================= 通用小工具（2.3.0 同款） =================
def _find_item(items, name):
    return next((x for x in items if x["name"] == name), None)


def _fmt_price(v):
    """价格显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0"""
    v = float(v)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _fmt_hours(h):
    """化肥库存显示：小时数（保留最多 2 位小数去尾 0）"""
    try:
        h = float(h)
    except (TypeError, ValueError):
        h = 0.0
    if h == int(h):
        return str(int(h))
    return f"{h:.2f}".rstrip("0").rstrip(".")


def _effect_desc(effects):
    """宠物道具效果文案（饱食/口渴/体力/心情/健康 ±N）"""
    parts = []
    for k, short in ATTR_SHORT.items():
        v = effects.get(k, 0)
        if v > 0:
            parts.append(f"{short}+{v:.0f}")
        elif v < 0:
            parts.append(f"{short}{v:.0f}")
    return " ".join(parts) if parts else "无效果"


def _fert_max_accel(fert):
    """化肥可加速次数（-1 = 不限）。兼容旧字段 max_uses"""
    v = fert.get("max_accel", fert.get("max_uses", -1))
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return -1


def _plot_free(plot):
    return plot is None or plot.get("crop") is None


# ================= 宠物经验（2.3.0 pet.py 同款；新经验体系：所需经验 = 当前等级 × 100） =================
def _pet_level_from_exp(exp):
    exp = max(0.0, float(exp))
    # 解 100*(L-1)*L/2 <= exp → L = floor((1+sqrt(1+8*exp/100))/2)
    lv = int((1 + (1 + 8 * exp / 100.0) ** 0.5) / 2)
    return min(PET_MAX_LEVEL, lv)


def _pet_exp_progress(exp):
    """返回 (当前等级, 本级已得经验, 本级所需经验)"""
    exp = max(0.0, float(exp))
    level = _pet_level_from_exp(exp)
    need_prev = 100.0 * (level - 1) * level / 2.0  # 升到当前等级的累计经验
    got = exp - need_prev
    need = float(level) * 100.0
    return level, got, need


# ================= 欠款（2.3.0 loans.py 同款；bank_loan 服务不可用时的降级计算） =================
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


def _loan_owed(loan, now_ts):
    return round(loan.get("remaining", 0) + _loan_accrued(loan, now_ts), 2)


# ================= 跨插件数据（服务优先，None-safe 降级共享 data） =================
def _farm_of(data, key):
    """读农场记录：farm 服务可用时优先，否则读共享 data"""
    svc = _CORE.service("farm")
    fn = getattr(svc, "farm_of", None) if svc is not None else None
    if callable(fn):
        try:
            farm = fn(key)
            if farm is not None:
                return farm
        except Exception:
            pass
    return data.get("farms", {}).get(key)


def _pet_of(data, key):
    """读宠物记录：pet 服务可用时优先，否则读共享 data"""
    svc = _CORE.service("pet")
    fn = getattr(svc, "pet_of", None) if svc is not None else None
    if callable(fn):
        try:
            pet = fn(key)
            if pet is not None:
                return pet
        except Exception:
            pass
    return data.get("pets", {}).get(key)


def _debt_of(data, key):
    """生效欠款含息总额：bank_loan 服务优先，降级按 2.3.0 口径本地计算"""
    svc = _CORE.service("bank_loan")
    fn = getattr(svc, "debt_summary", None) if svc is not None else None
    if callable(fn):
        try:
            s = fn(key)
            if isinstance(s, dict):
                return int(s.get("total", 0) or 0)
        except Exception:
            pass
    rec = data.get("loans", {}).get(key)
    if not rec:
        return 0
    now_ts = datetime.now().timestamp()
    return int(sum(_loan_owed(l, now_ts) for l in rec.get("loans", []) if l.get("remaining", 0) > 0))


# ================= 背包「今日净收益」零点基线（2.3.0 base.py 同款） =================
def _dep_amount(d):
    """安全读取存单本金，防御异常/空数据"""
    v = d.get("amount") if isinstance(d, dict) else None
    return int(v) if isinstance(v, (int, float)) else 0


def _rank_score_coins(data, key):
    """金币排行积分（与金币排行榜一致）：金币 × 权重 + 存款本金 × 权重"""
    cw = float(_CORE.param("RANK_COIN_COIN_W", RANK_COIN_COIN_W))
    bw = float(_CORE.param("RANK_COIN_BANK_W", RANK_COIN_BANK_W))
    bank = data.get("bank", {}).get(key)
    dep = sum(_dep_amount(d) for d in (bank.get("deposits", []) if isinstance(bank, dict) else []))
    return int(_CORE.coins_of(key) * cw + dep * bw)


def _ensure_bag_base(data, key):
    """背包「今日净收益」的零点基线（金币排行积分）。
    积分只在金币/存款变动时变化，因此在「当日第一笔金币变动前」（coins.pre_add）或
    「当日首次查看背包时」记录的积分即等于当日零点的积分。返回 (基线值, 是否新建基线)。"""
    user = _CORE.ensure_user(key)
    today = date.today().isoformat()
    base = user.get("bag_base") or {}
    if base.get("date") == today:
        return int(base.get("score", 0)), False
    score = _rank_score_coins(data, key)
    user["bag_base"] = {"date": today, "score": score}
    return score, True


# ================= 三榜排名（rank 服务；未挂载/异常 → None，显示 未上榜） =================
def _my_rank(kind, key):
    """本人在 kind 榜（coins/pet/farm）的名次；未上榜或榜服务不可用返回 None"""
    svc = _CORE.service("rank")
    fn = getattr(svc, "entries", None) if svc is not None else None
    if callable(fn):
        try:
            entries = fn(kind)
            if entries:
                keys = [str(e[1]) for e in entries]
                try:
                    return keys.index(str(key)) + 1
                except ValueError:
                    return None
        except Exception as e:
            logger.warning(f"[warehouse] rank.entries({kind}) 调用失败: {e}")
    return None


def _resolve_name(key, name=None):
    """视图显示名：指令路径带 event 昵称；服务调用路径回退自定义昵称 / key"""
    if name:
        return name
    try:
        return _CORE.custom_name_of(key) or str(key)
    except Exception:
        return str(key)


# ================= 背包视图（2.3.0 pet._handle_bag 同款） =================
def _bag_view(key, name=None):
    """背包：标题区（用户名/好感等级/金币/负债/宠物/农场）+ 分类卡片（宠物道具/农场道具）
    + 页尾大卡（仓库总价值/今日净收益/三榜排名）。渲染失败回退文本列表。"""
    name = _resolve_name(key, name)
    data = _CORE.data
    pet = _pet_of(data, key)
    farm = _farm_of(data, key)
    user = data.get("users", {}).get(key) or {}

    items = _CORE.items()
    shop_items = items.get("shop") or []
    crops = items.get("crops") or []
    ferts = items.get("ferts") or []

    # ---- 物品卡片分类：两级（1.7.7）----
    # 第一级：宠物道具 / 农场道具；
    # 第二级（宠物）：食物/饮料/玩具/药物/特殊；（农场）：种子/收获物/化肥
    # groups = [(一级名, [(二级名, [card])])]；card = {name,count,desc,effect,sell_unit}
    groups = []
    if pet:
        by_type = {"食物": [], "饮料": [], "玩具": [], "药物": [], "特殊": []}
        other = []
        for nm, cnt in pet.get("inventory", {}).items():
            cnt = int(cnt)
            if cnt <= 0:
                continue
            it = _find_item(shop_items, nm)
            if it:
                card = {"name": nm, "count": cnt, "desc": it.get("desc", ""),
                        "effect": _effect_desc(it.get("effects") or {}), "sell_unit": None}
                typ = (it.get("type") or "").strip()
                if typ == "食品":  # 旧类型兼容：食品 → 食物
                    typ = "食物"
                if typ in by_type:
                    by_type[typ].append(card)
                else:
                    other.append(card)
            elif nm == PILL_NAME:
                pa = int(_CORE.param("PILL_ATTR_COUNT", PILL_ATTR_COUNT) or 2)
                bmin = float(_CORE.param("PILL_BOOST_MIN", PILL_BOOST_MIN))
                bmax = float(_CORE.param("PILL_BOOST_MAX", PILL_BOOST_MAX))
                pdl = int(_CORE.param("PILL_DAILY_LIMIT", PILL_DAILY_LIMIT))
                by_type["特殊"].append({"name": nm, "count": cnt, "desc": "随机提升宠物属性",
                                        "effect": f"随机{pa}属性 +{bmin:.0f}~{bmax:.0f}（每日{pdl}次）",
                                        "sell_unit": None})
            else:
                other.append({"name": nm, "count": cnt, "desc": "", "effect": "", "sell_unit": None})
        subs = [(t, by_type[t]) for t in ("食物", "饮料", "玩具", "药物", "特殊") if by_type[t]]
        if other:
            subs.append(("其它", other))
        if subs:
            groups.append(("宠物道具", subs))
    if farm:
        wh = farm.get("warehouse", {})
        seed_cards, crop_cards, fert_cards = [], [], []
        for nm, cnt in wh.get("seeds", {}).items():
            c = _find_item(crops, nm)
            seed_cards.append({"name": nm, "count": int(cnt),
                               "desc": c.get("desc", "") if c else "",
                               "effect": "", "sell_unit": _CORE.f(c["seed_sell_price"]) if c else 0.0})
        for nm, cnt in wh.get("crops", {}).items():
            c = _find_item(crops, nm)
            crop_cards.append({"name": nm, "count": int(cnt),
                               "desc": c.get("desc", "") if c else "",
                               "effect": "", "sell_unit": _CORE.f(c["crop_price"]) if c else 0.0})
        for nm, cnt in wh.get("fertilizers", {}).items():
            f = _find_item(ferts, nm)
            maxa = "不限" if (f and _fert_max_accel(f) < 0) else (f"{_fert_max_accel(f)}次" if f else "？")
            effect = f"每株可加速 {maxa}"
            if f and _CORE.f(f.get("yield_add", 0) or 0) > 0:
                effect += f" 增产{_CORE.f(f['yield_add']):.0f}%/次"
            fert_cards.append({"name": nm, "count": _fmt_hours(cnt),
                               "desc": f.get("desc", "") if f else "",
                               "effect": effect,
                               "sell_unit": None})
        # 2.0.1：农场特殊道具（如 农场经验球）
        tool_cards = []
        for nm, cnt in (farm.get("tools") or {}).items():
            effect = ""
            if nm == EXP_BALL_NAME:
                lo = float(_CORE.param("EXP_BALL_MIN_PCT", EXP_BALL_MIN_PCT))
                hi = float(_CORE.param("EXP_BALL_MAX_PCT", EXP_BALL_MAX_PCT))
                edl = int(_CORE.param("EXP_BALL_DAILY_LIMIT", EXP_BALL_DAILY_LIMIT))
                effect = f"获得升级经验 {lo * 100:.0f}%~{hi * 100:.0f}%（每日{edl}次）"
            tool_cards.append({"name": nm, "count": int(cnt), "desc": "提升农场经验" if nm == EXP_BALL_NAME else "",
                               "effect": effect, "sell_unit": None})
        subs = []
        if seed_cards:
            subs.append(("种子", seed_cards))
        if crop_cards:
            subs.append(("收获物", crop_cards))
        if fert_cards:
            subs.append(("化肥", fert_cards))
        if tool_cards:
            subs.append(("特殊", tool_cards))
        if subs:
            groups.append(("农场道具", subs))
    if not groups:
        return f"{name} 的背包是空的。"

    # ---- 页尾：仓库总价值 / 今日净收益 / 三榜排名 ----
    wh_total = 0
    if farm:
        wh = farm.get("warehouse", {})
        for nm, cnt in wh.get("crops", {}).items():
            c = _find_item(crops, nm)
            wh_total += int(round(int(cnt) * (_CORE.f(c["crop_price"]) if c else 0.0)))
        for nm, cnt in wh.get("seeds", {}).items():
            c = _find_item(crops, nm)
            wh_total += int(round(int(cnt) * (_CORE.f(c["seed_sell_price"]) if c else 0.0)))
    # 1.7.8：今日净收益 = 当前金币排行积分 − 当日零点基线（覆盖红包/利息/存款/左轮等所有金币变化）
    base, base_new = _ensure_bag_base(data, key)
    net = _rank_score_coins(data, key) - base
    if base_new:
        _CORE.save()  # 持久化当日零点基线

    footer = {
        "wh_total": wh_total, "net": net,
        "coins_rank": _my_rank("coins", key), "pet_rank": _my_rank("pet", key), "farm_rank": _my_rank("farm", key),
    }

    # ---- 标题区：好感等级 / 金币 / 负债 / 宠物 / 农场 ----
    fav_lv = _CORE.level_of(float(user.get("favorability", 0.0)))
    debt = _debt_of(data, key)
    pet_line = None
    if pet:
        lv, _, _ = _pet_exp_progress(float(pet.get("exp", 0.0)))
        abnormal = bool(pet.get("weak")) or float(pet.get("health", 100)) <= 39
        pet_line = {"name": pet.get("name", "宠物"), "level": lv, "abnormal": abnormal}
    farm_line = None
    if farm:
        now_ts = datetime.now().timestamp()
        plots = farm.get("plots", [])
        idle = sum(1 for pl in plots if _plot_free(pl))
        mature = sum(1 for pl in plots
                     if not _plot_free(pl) and float(pl.get("mature_ts", 0)) <= now_ts)
        occupied = len(plots) - idle - mature
        farm_line = {"level": int(farm.get("level", 0)), "idle": idle,
                     "occupied": occupied, "mature": mature}
    header = {"fav_lv": fav_lv, "coins": _CORE.coins_of(key), "debt": debt,
              "pet": pet_line, "farm": farm_line}

    render = getattr(_CORE.image, "render_bag", None)
    if callable(render):
        try:
            img = render(name, header, groups, footer)
        except Exception as e:
            logger.error(f"[warehouse] render_bag 渲染失败，回退文本: {e}")
            img = None
        if img is not None:
            return img

    # ---- 文本回退（2.3.0 同款） ----
    lines = [f"🎒 {name} 的背包（好感 Lv.{fav_lv}）"]
    coin_line = f"金币 {header['coins']}"
    if debt > 0:
        coin_line += f"｜负债 {debt}"
    lines.append(coin_line)
    if pet_line:
        lines.append(f"宠物 {pet_line['name']} Lv.{pet_line['level']}"
                     f"（{'异常' if pet_line['abnormal'] else '正常'}）")
    else:
        lines.append("未领养宠物")
    if farm_line:
        lines.append(f"农场 Lv.{farm_line['level']}：空闲 {farm_line['idle']} 块｜"
                     f"占用 {farm_line['occupied']} 块｜成熟 {farm_line['mature']} 块")
    else:
        lines.append("未解锁农场")
    for l1, subs in groups:
        lines.append(f"【{l1}】")
        for l2, cards in subs:
            lines.append(f"　【{l2}】")
            for c in cards:
                ln = f"　· {c['name']} ×{c['count']}"
                if c.get("desc"):
                    ln += f"（{c['desc']}）"
                if c.get("effect"):
                    ln += f"：{c['effect']}"
                if c.get("sell_unit") is not None:
                    ln += f" 可售 {_fmt_price(c['sell_unit'])} |{_fmt_price(c['sell_unit'] * c['count'])}"
                lines.append(ln)
    lines.append(f"仓库总价值 {wh_total}｜今日净收益 {'+' if net >= 0 else ''}{net}")

    def _rk(r):
        return f"第{r}名" if r else "未上榜"
    lines.append(f"金币排行 {_rk(footer['coins_rank'])}｜宠物排行 {_rk(footer['pet_rank'])}｜农场排行 {_rk(footer['farm_rank'])}")
    return "\n".join(lines)


# ================= 农场仓库视图（2.3.0 farm._handle_farm_warehouse 同款） =================
def _farm_warehouse_view(key, name=None):
    name = _resolve_name(key, name)
    data = _CORE.data
    farm = _farm_of(data, key)
    if not farm:
        return f"{name} 还没有农场，发送「解锁农场」（需 {FARM_UNLOCK_COST} 金币）解锁。"
    items = _CORE.items()
    crops = items.get("crops") or []
    ferts = items.get("ferts") or []
    render = getattr(_CORE.image, "render_warehouse", None)
    if callable(render):
        try:
            img = render(farm, crops, ferts)
        except Exception as e:
            logger.error(f"[warehouse] render_warehouse 渲染失败，回退文本: {e}")
            img = None
        if img is not None:
            return img
    wh = farm.get("warehouse", {})
    lines = ["农场仓库："]
    for k, label in [("crops", "作物"), ("seeds", "种子"), ("fertilizers", "肥料")]:
        lines.append(f"----{label}----")
        for nm, cnt in wh.get(k, {}).items():
            lines.append(f"{nm} ×{cnt}")
    return "\n".join(lines)


# ================= 挂载入口 =================
def register(core):
    global _CORE
    _CORE = core

    # ================= 联动事件：金币变动前固定「今日净收益」当日零点基线 =================
    def on_coins_pre_add(payload):
        """注意签名：coins.pre_add 的回调是 cb(payload)（单一带位置 dict，核心直接传 dict 调用，
        不经 ** 展开——写成 cb(**payload) 会把 key/amount 拆成 kwargs 而报错）。
        在金币变动前为 payload["key"] 记录当日基线（当日已有基线则原样返回，不落盘）。"""
        try:
            _ensure_bag_base(core.data, payload.get("key"))
        except Exception as e:
            logger.error(f"[warehouse] coins.pre_add 基线记录异常: {e}")

    core.on("coins.pre_add", on_coins_pre_add)

    # ================= 指令：背包 / 农场仓库 =================
    def handle_bag(event):
        return _bag_view(core.user_key(event), core.user_name(event))

    def handle_farm_warehouse(event):
        return _farm_warehouse_view(core.user_key(event), core.user_name(event))

    core.command("背包", feature="warehouse")(handle_bag)
    core.command("农场仓库", feature="warehouse")(handle_farm_warehouse)

    core.add_help("仓库", [
        ("背包", "查看背包"),
        ("农场仓库", "查看农场仓库库存"),
    ])

    # ================= 服务暴露（其他插件复用视图；返回回复协议值） =================
    class WarehouseApi:
        """仓库服务：背包视图 / 农场仓库视图"""

        def bag_view(self, key, name=None):
            """背包视图（name 缺省回退自定义昵称 / key）"""
            return _bag_view(key, name)

        def farm_warehouse_view(self, key, name=None):
            """农场仓库视图（未开通农场返回提示文本）"""
            return _farm_warehouse_view(key, name)

    core.expose("warehouse", WarehouseApi())
