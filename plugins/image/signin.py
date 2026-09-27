# -*- coding: utf-8 -*-
"""图片响应模块 · 签到实时数据快照渲染器（2.3.0 _render_signin_snapshot 移植）。

布局与 2.3.0 一致：标题为按时段问候语（早上好/上午好/中午好/下午好/晚上好！/夜深了）+ 用户名；
签到信息｜好感度信息（同一行左右并排）、银行与征信｜排行榜信息（同一行左右并排）、
宠物信息 / 农场信息 各占整行；标题右上角显示当前金币数量；
好感度进度条为矩形边框+百分比填充；含「｜」的文本按段整体换行、不切开；
高亮规范：高亮不变动卡片填充颜色，只改变边框颜色 + 字体颜色。
渲染统一走通用快照瀑布渲染器 generic.snapshot（老 _render_snapshot_image）。
"""
from datetime import datetime

from ...core import LEVEL_STEP, PET_MAX_HEALTH, PET_ATTR_MAX_RANGES, WEAK_HEAL_COST
from .common import DS_MUTED, DS_TEXT_2, DS_SUCCESS, DS_GOLD, DS_DANGER, DS_BLUE
from .generic import snapshot

__all__ = ["render_signin_snapshot"]


def _time_greeting(now=None):
    """按时段返回问候语（早上好/上午好/中午好/下午好/晚上好！/夜深了）"""
    h = (now or datetime.now()).hour
    if 5 <= h < 8:
        return "早上好！"
    if 8 <= h < 11:
        return "上午好！"
    if 11 <= h < 13:
        return "中午好！"
    if 13 <= h < 17:
        return "下午好！"
    if 17 <= h < 23:
        return "晚上好！"
    return "夜深了"


def _fmt_score(v):
    """积分显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0（老 rank._fmt_score 同款）"""
    v = float(v)
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _parse_attr_max_ranges(raw):
    """解析 PET_ATTR_MAX_RANGES → {健康下限: (饱食, 口渴, 体力, 心情)}；解析失败回退默认四档"""
    raw = raw or ""
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


def _attr_max_of(core, health):
    """(饱食, 口渴, 体力, 心情) 上限：宠物插件服务优先，未挂载/失败回退本地解析"""
    pet_svc = core.service("pet")
    if pet_svc is not None:
        fn = getattr(pet_svc, "attr_max", None)
        if callable(fn):
            try:
                return fn(health)
            except Exception:
                pass
    ranges = _parse_attr_max_ranges(core.param("PET_ATTR_MAX_RANGES", PET_ATTR_MAX_RANGES))
    for floor in sorted(ranges, reverse=True):
        if health >= floor:
            return ranges[floor]
    return (80.0, 80.0, 60.0, 80.0)


def _busy_until_of(core, pet):
    """打工/玩耍共用冷却时间戳：宠物插件服务优先，未挂载/失败回退本地读取"""
    pet_svc = core.service("pet")
    if pet_svc is not None:
        fn = getattr(pet_svc, "busy_until", None)
        if callable(fn):
            try:
                return float(fn(pet))
            except Exception:
                pass
    busy = float(pet.get("busy_until", 0) or 0)
    old = max(float(pet.get("work_until", 0) or 0), float(pet.get("play_until", 0) or 0))
    return max(busy, old)


# ---- 银行/贷款只读小工具（老 bank._dep_amount / loans._loan_accrued·_loan_owed·_loan_is_overdue 同款） ----
def _dep_amount(d):
    v = d.get("amount") if isinstance(d, dict) else None
    return int(v) if isinstance(v, (int, float)) else 0


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


def _loan_is_overdue(loan, now_ts):
    return now_ts > loan.get("due_ts", 0) and loan.get("remaining", 0) > 0


def render_signin_snapshot(core, name, key, data, extra_lines=None, signed_today=False):
    """签到实时数据快照（信息流瀑布平铺，布局与 2.3.0 一致）。
    返回 ("image", path)；渲染环境不可用时返回 None（调用方回退纯文本）。"""
    user = data.get("users", {}).get(key) or {}
    pet = data.get("pets", {}).get(key)
    farm = data.get("farms", {}).get(key)
    bank = data.get("bank", {}).get(key)
    loan = data.get("loans", {}).get(key)
    now_ts = datetime.now().timestamp()
    # WebUI 设计系统语义色（与 style.css 对齐）
    GRAY = DS_MUTED        # --muted #65715f
    TEXT = DS_TEXT_2       # --text-2 #3f4a40
    GREEN = DS_SUCCESS     # --success #2c7a50
    GOLD = DS_GOLD         # --gold #d6a11a
    RED = DS_DANGER        # --danger #b3392e
    BLUE = DS_BLUE         # 提示蓝（深调）
    lines_layout = []

    # ---------- 1. 签到信息（合并行：累计签到+获得金币 一行、获得好感度+宠物经验 一行） ----------
    rows = []
    if signed_today:
        rows.append(("✅ 今日已签到", GREEN))
    total = int(user.get("signin_total", 0) or 0)
    double_on = False
    try:
        tp = core.service("thirdparty")
        if tp is not None and tp.is_active("double_signin"):
            double_on = True
    except Exception:
        double_on = False
    coins_total = int(user.get("signin_coins_total", 0) or 0)
    rows.append((f"累计签到 {total} 次" + ("（双倍签到活动进行中）" if double_on else "") + f"｜获得金币：{coins_total}", TEXT))
    fav_total = float(user.get("signin_fav_total", 0) or 0)
    if pet:
        exp_total = float(user.get("signin_pet_exp_total", 0) or 0)
        rows.append((f"获得好感度：{fav_total:.1f}｜获得宠物经验：{exp_total:.1f}", TEXT))
    else:
        rows.append((f"获得好感度：{fav_total:.1f}", TEXT))
    extra = user.get("signin_extra") or (extra_lines or [])
    extra = [ln for ln in extra if ("属性丸" in ln or "经验球" in ln)]
    rows.append((("额外道具：" + "；".join(extra)) if extra else "额外道具：无", GRAY))
    signin_card = ("签到信息", rows, False)

    # ---------- 2. 好感度信息（进度条 = 矩形边框 + 百分比填充） ----------
    fav = float(user.get("favorability", 0) or 0)
    lv = core.level_of(fav)
    step = float(core.param("LEVEL_STEP", LEVEL_STEP) or 10.0)
    progress = (fav - lv * step) / step if step > 0 else 0.0
    progress = max(0.0, min(1.0, progress))
    pct = int(progress * 100)
    next_need = (lv + 1) * step - fav
    fav_rows = [
        (f"当前好感度总值：{fav:.1f}", TEXT),
        ("__bar__", progress, GREEN, f"{pct}%"),
        (f"距离下一级还需 {next_need:.1f} 好感度", GRAY),
    ]
    fav_card = ("好感度信息", fav_rows, False)

    # 行1：签到信息 | 好感度信息（左右并排）
    lines_layout.append([signin_card, fav_card])

    # ---------- 3. 银行与征信（无存款无欠款则不显示） ----------
    deposits = bank.get("deposits", []) if isinstance(bank, dict) else []
    loans = loan.get("loans", []) if isinstance(loan, dict) else []
    bank_card = None
    if deposits or loans:
        rows = []
        matured_sum = sum(_dep_amount(d) for d in deposits if d.get("status") == "matured")
        locked_sum = sum(_dep_amount(d) for d in deposits if d.get("status") == "locked")
        # 2.2.0：银行存款改为固定时间自动结算，快照只显示结算结果
        ti = float(bank.get("total_interest", 0) or 0) if isinstance(bank, dict) else 0.0
        rows.append((f"存款到期总额：{matured_sum}｜累计利息收益：+{ti:.0f}", GOLD))
        if locked_sum > 0:
            nxt = min((float(d.get("unlock_ts", 0) or 0) for d in deposits
                       if d.get("status") == "locked"), default=0)
            nxt_txt = datetime.fromtimestamp(nxt).strftime("%m-%d %H:%M") if nxt else "—"
            # 2.1.1：存款到期时间移动到「未到期存款」下方单独一行
            rows.append((f"剩余未到期存款：{locked_sum}", TEXT))
            rows.append((f"最早 {nxt_txt} 到期", GRAY))
        if loans:
            owed = sum(_loan_owed(l, now_ts) for l in loans)
            overdue_days = max([int((now_ts - l.get("due_ts", 0)) // 86400)
                                for l in loans if _loan_is_overdue(l, now_ts)], default=0)
            rows.append((f"负债信息：欠款总额 {owed:.0f} 金币", RED if overdue_days else TEXT))
            if overdue_days:
                rows.append((f"逾期 {overdue_days} 天", RED))
            rows.append(("还款提示：发送「还款 <套餐> [金额]」还款", BLUE))
        bank_card = ("银行与征信", rows, bool(overdue_days) if loans else False)

    # ---------- 4. 排行榜信息（与银行与征信 同一行右侧） ----------
    rank_rows = []
    for kind, label in (("coins", "金币"), ("pet", "宠物"), ("farm", "农场")):
        entries = None
        pos = None
        try:
            rank_svc = core.service("rank")
            if rank_svc is not None:
                entries = rank_svc.entries(kind, data)
                pos = next((i for i, (_, euid, _) in enumerate(entries, 1) if str(euid) == str(key)), None)
        except Exception:
            pos = None
        if pos and entries:
            score = entries[pos - 1][0]
            rank_rows.append((f"{label}排行榜：第 {pos} 名（积分 {_fmt_score(score)}）", GOLD))
        else:
            rank_rows.append((f"{label}排行榜：未上榜", GRAY))
    rank_card = ("排行榜信息", rank_rows, False)

    # 行2：银行与征信 | 排行榜信息（银行无数据显示时排行榜独占整行）
    # 银行列收窄但不换行；权重按内容宽度折算：银行 372 : 排行榜 391
    if bank_card:
        lines_layout.append([bank_card, rank_card, (372, 391)])
    else:
        lines_layout.append([rank_card])

    # ---------- 5. 宠物信息（未开通则不显示） ----------
    if pet:
        weak = bool(pet.get("weak"))
        rows = []
        if weak:
            rows.append(("宠物健康值归零，进入虚弱状态！！！", RED))
            rows.append((f"操作提示：发送「治疗宠物」（花 {int(core.param('WEAK_HEAL_COST', WEAK_HEAL_COST))} 金币）恢复", RED))
            # 未照顾天数：连续两天结算健康为 0 进入虚弱
            streak = int(pet.get("weak_streak", 0) or 0)
            rows.append((f"未照顾天数：{max(1, streak)} 天", RED))
            rows.append(("自动化提示：自动照顾/自动打工已暂停，治疗恢复后自动继续", GRAY))
        else:
            rows.append((f"结算信息：{pet.get('last_settle', {}).get('date', '暂无')}", GRAY))
            sat_max, thr_max, sta_max, mood_max = _attr_max_of(core, pet["health"])
            rows.append((f"饱食 {pet['satiety']:.0f}/{sat_max:.0f}｜口渴 {pet['thirst']:.0f}/{thr_max:.0f}"
                         f"｜体力 {pet['stamina']:.0f}/{sta_max:.0f}｜心情 {pet['mood']:.0f}/{mood_max:.0f}"
                         f"｜健康 {pet['health']:.0f}/{PET_MAX_HEALTH:.0f}", TEXT))
            busy_until = _busy_until_of(core, pet)
            if now_ts < busy_until:
                rows.append((f"忙碌中：{pet.get('busy_activity', '')}「{pet.get('busy_item', '')}」", BLUE))
            else:
                rows.append(("当前空闲", GRAY))
            u_auto = user
            rows.append((f"自动化：自动照顾{'开' if u_auto.get('auto_feed_enabled') else '关'}｜"
                         f"自动打工{'开' if u_auto.get('auto_work_enabled') else '关'}｜"
                         f"基准金币 {int(u_auto.get('work_base', 0) or 0)}", TEXT))
            fl = (u_auto.get("auto_feed_logs") or [])
            wl = (u_auto.get("auto_work_logs") or [])
            if fl:
                last = fl[-1]
                items = "、".join(f"{it.get('name', '')}×{it.get('qty', 0)}" for it in last.get("items", []))
                rows.append((f"最近自动照顾：{last.get('date', '')} {items}（花 {last.get('total', 0)} 金币）", GOLD))
            if wl:
                last = wl[-1]
                rows.append((f"最近自动打工：{last.get('date', '')}「{last.get('job', '')}」+{last.get('coins', 0)} 金币", GREEN))
            if not fl and not wl:
                rows.append(("暂无自动化记录", GRAY))
        lines_layout.append(("宠物信息", rows, weak))

    # ---------- 6. 农场信息（未开通则不显示） ----------
    if farm:
        rows = []
        plots = farm.get("plots", []) or []
        idle = sum(1 for p in plots if p.get("crop") is None)
        mature = sum(1 for p in plots if p.get("crop") is not None and now_ts >= float(p.get("mature_ts", 0) or 0))
        growing = len(plots) - idle - mature
        rows.append((f"土地：空闲 {idle}｜种植中 {growing}｜已成熟 {mature}（共 {len(plots)} 块）", TEXT))
        # 被偷菜统计
        infos = farm.get("steal_infos", []) or []
        thieves = set()
        loss = 0
        for it in infos:
            tid = it.get("thief_uid")
            if tid:
                thieves.add(str(tid))
            for item in it.get("items", []) or []:
                loss += int(item.get("loss", 0) or 0)
        rows.append((f"被偷菜人数：{len(thieves)}｜被偷损失总金额：{loss}", RED if loss else GRAY))
        lines_layout.append(("农场信息", rows, bool(loss)))

    if not lines_layout:
        lines_layout.append([("签到信息", [("暂无数据", GRAY)], False)])
    # 右上角：当前金币数量
    coins = int(user.get("coins", 0) or 0)
    return snapshot(f"{_time_greeting()} {name}", lines_layout,
                    header_right=(f"金币：{coins}", GOLD))
