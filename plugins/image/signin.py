# -*- coding: utf-8 -*-
"""图片响应模块 · 签到响应图渲染器（3.0.1 参考效果图复刻版）。

布局复刻《签到页面参考效果图》（5274x3182，按 1/3 缩放输出 1758 宽）：
- 左侧墨绿信息面板（#184a31，白字）：签到时间块 + 底部问候语/昵称（OPPOSans-H）
  + 异常徽章（页底色块、红字红框，自动换行）；
- 右侧白底墨绿边框方角卡片流：签到信息｜好感度信息（并排）、银行存款信息｜排行榜
  （并排，中缝分隔线）、宠物信息、农场信息；
- 进度条：墨绿边框白轨道墨绿填充，条内文字默认墨绿色、与墨绿填充重叠部分白色（两层合成）；
- 状态：宠物虚弱=红框卡片红字；未解锁=居中解锁提示；模块被管理员禁用=灰色居中提示
  （DISABLED_MODULES 配置，全部禁用时整版仅剩面板+空白卡）。
字体：正文 OPPOSans-M；问候语/昵称 OPPOSans-H（缺失回退 M+描边）。
"""
import os
from datetime import datetime

from ...core import (LEVEL_STEP, PET_MAX_HEALTH, PET_ATTR_MAX_RANGES, WEAK_HEAL_COST,
                     PET_UNLOCK_COST, FARM_UNLOCK_COST, FONT_FILE)
from .common import ensure_pillow, load_fonts, save_temp_image, dtext, text_measurer, clean_img_text

__all__ = ["render_signin_snapshot"]

# ---- 参考效果图实测配色 ----
C_BG = (246, 244, 236)      # 页面底 #f6f4ec
C_GREEN = (24, 74, 49)      # 墨绿（面板/边框/进度填充）#184a31
C_GOLD = (200, 145, 31)     # 金 #c8911f
C_GRAY = (89, 89, 89)       # 次要文字 #595959
C_DGRAY = (127, 127, 127)   # 禁用提示 #7f7f7f
C_RED = (192, 0, 0)         # 异常红 #c00000
C_BLACK = (0, 0, 0)
C_WHITE = (255, 255, 255)

HEAVY_FONT_FILE = os.path.join(os.path.dirname(FONT_FILE), "OPPOSans-H.ttf")

BORDER = 2          # 卡片/徽章描边（参考图 5px）
GAP_V = 31          # 卡片纵向间距（94px）
ROW_PITCH = 36      # 正文行距（108px）
BANK_PITCH = 37     # 银行/排行榜行距（110px）


def _u(v):
    """参考图坐标（5274 宽）→ 输出坐标（1/3）。"""
    return int(round(v / 3.0))


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
    """积分显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0"""
    v = float(v)
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _fmt_num(v):
    """千位分隔符：整数 20,000；小数 12,345.67（积分等数值展示）"""
    s = _fmt_score(v)
    if "." in s:
        i, _, f = s.partition(".")
        try:
            return f"{int(i):,}.{f}"
        except ValueError:
            return s
    try:
        return f"{int(s):,}"
    except ValueError:
        return s


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


# ---- 银行/贷款只读小工具 ----
def _dep_amount(d):
    v = d.get("amount") if isinstance(d, dict) else None
    return int(v) if isinstance(v, (int, float)) else 0


def _loan_is_overdue(loan, now_ts):
    # 3.0.2：due_ts/remaining 为 None 的脏数据按 0 处理（原样比较会炸整图回退旧版渲染）
    return now_ts > (loan.get("due_ts") or 0) and (loan.get("remaining") or 0) > 0


def _disabled_modules(core):
    """管理员禁用模块集合（DISABLED_MODULES / disabled_modules 配置：逗号分隔 signin,fav,bank,rank,pet,farm）"""
    raw = core.param("DISABLED_MODULES", "") or core.param("disabled_modules", "") or ""
    return {s.strip().lower() for s in str(raw).replace("，", ",").split(",") if s.strip()}


def render_signin_snapshot(core, name, key, data, extra_lines=None, signed_today=False):
    """签到响应图（参考效果图复刻版）。返回 ("image", path)；渲染不可用返回 None（回退纯文本）。"""
    Image, ImageDraw = ensure_pillow()
    if Image is None:
        return None
    tw = text_measurer()
    if tw is None:
        return None
    # 字号（参考图 48/50/70/80/88/90/105/200px ÷3）
    fonts = load_fonts(16, 17, 23, 27, 29, 30, 35, 67)
    if fonts is None:
        return None
    f_bar, f_barlab, f_small, f_body, f_msg2, f_title, f_msg, f_greet_fb = fonts
    heavy = None
    if os.path.exists(HEAVY_FONT_FILE):
        heavy = load_fonts(67, font_file=HEAVY_FONT_FILE)
    f_greet = heavy[0] if heavy else f_greet_fb
    greet_stroke = 0 if heavy else 2  # 无 Heavy 字体时以描边加粗近似

    user = data.get("users", {}).get(key) or {}
    pet = data.get("pets", {}).get(key)
    if pet:
        # 3.0.2：宠物属性缺失/字符串/None 等脏数据统一归一为 float（原样取值会炸整图回退旧版渲染）
        pet = dict(pet)
        for k in ("satiety", "thirst", "stamina", "mood", "health"):
            try:
                pet[k] = float(pet.get(k) or 0)
            except (TypeError, ValueError):
                pet[k] = 0.0
    farm = data.get("farms", {}).get(key)
    bank = data.get("bank", {}).get(key)
    loan = data.get("loans", {}).get(key)
    now = datetime.now()
    now_ts = now.timestamp()
    dis = _disabled_modules(core)
    all_disabled = {"signin", "fav", "bank", "rank", "pet", "farm"} <= dis

    # ---------- 数据准备 ----------
    total = int(user.get("signin_total", 0) or 0)
    coins_gain = int(user.get("signin_coins_total", 0) or 0)
    fav_gain = float(user.get("signin_fav_total", 0) or 0)
    exp_gain = float(user.get("signin_pet_exp_total", 0) or 0)

    fav = float(user.get("favorability", 0) or 0)
    lv = core.level_of(fav)
    step = float(core.param("LEVEL_STEP", LEVEL_STEP) or 10.0)
    progress = (fav - lv * step) / step if step > 0 else 0.0
    progress = max(0.0, min(1.0, progress))
    deposits = bank.get("deposits", []) if isinstance(bank, dict) else []
    loans = loan.get("loans", []) if isinstance(loan, dict) else []
    matured_sum = sum(_dep_amount(d) for d in deposits if d.get("status") == "matured")
    locked_sum = sum(_dep_amount(d) for d in deposits if d.get("status") == "locked")
    ti = float(bank.get("total_interest", 0) or 0) if isinstance(bank, dict) else 0.0
    overdue_days = max([int((now_ts - (l.get("due_ts") or 0)) // 86400)
                        for l in loans if _loan_is_overdue(l, now_ts)], default=0)

    steal_thieves, steal_loss = set(), 0
    if farm:
        for it in (farm.get("steal_infos", []) or []):
            if it.get("thief_uid"):
                steal_thieves.add(str(it["thief_uid"]))
            for item in it.get("items", []) or []:
                steal_loss += int(item.get("loss", 0) or 0)

    weak = bool(pet and pet.get("weak"))
    weak_streak = max(1, int(pet.get("weak_streak", 0) or 0)) if pet else 0
    heal_cost = int(core.param("WEAK_HEAL_COST", WEAK_HEAL_COST))
    pet_cost = int(core.param("PET_UNLOCK_COST", PET_UNLOCK_COST))
    farm_cost = int(core.param("FARM_UNLOCK_COST", FARM_UNLOCK_COST))

    # ---------- 卡片内容（(文本, 颜色) 或 [(段, 颜色), ...] 富文本） ----------
    # 签到信息：标题行 + 「本次签到获得」 + 获得项（label | value 两列）
    # 重复签到由调用方保证不发放奖励：signin_*_total 仍为当日首次签到获得量，原样展示
    gains = [("金币", str(coins_gain)), ("好感度", f"{fav_gain:.2f}")]
    if pet:
        gains.append(("宠物经验", f"{exp_gain:.2f}"))
    signin_rows = [(f"这是你的第 {total} 次签到", C_BLACK),
                   ("本次签到获得", C_BLACK)]
    signin_rows += [(lbl, C_BLACK) for lbl, _ in gains]
    signin_values = dict(gains)

    # 好感度信息：(label, value) 行 + 进度条（比例, 百分比文案）
    fav_rows = [("当前好感度", f"{fav:.2f}"), (progress, f"{int(round(progress * 100))}%"),
                ("当前好感度等级", str(lv))]

    # 银行与征信
    bank_rows = [(f"存款到期总额 {matured_sum}｜累计利息收益 +{ti:.0f}", C_GOLD)]
    if locked_sum > 0:
        nxt = min((float(d.get("unlock_ts", 0) or 0) for d in deposits
                   if d.get("status") == "locked"), default=0)
        nxt_txt = datetime.fromtimestamp(nxt).strftime("%m-%d %H:%M") if nxt else "—"
        bank_rows.append((f"剩余未到期存款 {locked_sum}", C_BLACK))
        bank_rows.append((f"距离最早到期时间 {nxt_txt}", C_BLACK))
    if loans:
        segs = [(f"你有 {len(loans)} 笔贷款", C_BLACK)]
        if overdue_days:
            segs.append((f"｜已逾期 {overdue_days} 天", C_RED))
        else:
            nxt = min((int((float(l.get("due_ts", 0) or 0) - now_ts) // 86400) for l in loans), default=0)
            segs.append((f"｜最早 {max(0, nxt)} 天后逾期", C_BLACK))
        bank_rows.append(segs)
    if len(bank_rows) == 1:
        bank_rows.append(("暂无未到期存款与负债记录", C_GRAY))

    # 排行榜
    rank_rows = []
    for kind, label in (("coins", "金币"), ("pet", "宠物"), ("farm", "农场")):
        pos = score = None
        try:
            rank_svc = core.service("rank")
            if rank_svc is not None:
                entries = rank_svc.entries(kind, data)
                pos = next((i for i, (_, euid, _) in enumerate(entries, 1) if str(euid) == str(key)), None)
                if pos:
                    score = entries[pos - 1][0]
        except Exception:
            pos = None
        if pos:
            rank_rows.append((f"{label}排行榜", f"第 {pos} 名", _fmt_num(score), C_GOLD))
        else:
            rank_rows.append((f"{label}排行榜", "未上榜", "", C_GRAY))
    rank_rows.append(("更新时间", "刚刚", "", C_GRAY))

    # 宠物信息
    pet_rows = []
    pet_right = {}
    if pet and not weak:
        sat_max, thr_max, sta_max, mood_max = _attr_max_of(core, pet["health"])
        attrs = [("饱食度", float(pet["satiety"]), sat_max), ("口渴值", float(pet["thirst"]), thr_max),
                 ("体力值", float(pet["stamina"]), sta_max), ("心情值", float(pet["mood"]), mood_max),
                 ("健康值", float(pet["health"]), float(PET_MAX_HEALTH))]
        segs = []
        for i, (lbl, val, mx) in enumerate(attrs):
            if i:
                segs.append(("｜", C_BLACK))
            segs.append((f"{lbl} {val:.0f}/{mx:.0f}", C_RED if val < mx * 0.1 else C_BLACK))
        pet_rows.append(segs)
        busy_until = _busy_until_of(core, pet)
        if now_ts < busy_until:
            pet_rows.append((f"当前正在{pet.get('busy_activity', '打工')} {pet.get('busy_item', '')}".rstrip(),
                             C_BLACK))
            remain = int(busy_until - now_ts)
            pet_right[1] = (f"剩余{remain // 3600}小时{remain % 3600 // 60}分钟", C_BLACK)
        else:
            pet_rows.append(("当前空闲", C_GRAY))
        pet_rows.append((f"自动化：自动照顾{'开' if user.get('auto_feed_enabled') else '关'}｜"
                         f"自动打工{'开' if user.get('auto_work_enabled') else '关'}｜"
                         f"基准金币 {int(user.get('work_base', 0) or 0)}", C_BLACK))
        fl = (user.get("auto_feed_logs") or [])
        wl = (user.get("auto_work_logs") or [])
        if fl:
            last = fl[-1]
            items = "、".join(f"{it.get('name', '')}×{it.get('qty', 0)}" for it in last.get("items", []))
            pet_rows.append((f"最近自动照顾：{last.get('date', '')} {items}（花 {last.get('total', 0)} 金币）", C_BLACK))
        elif wl:
            last = wl[-1]
            pet_rows.append((f"最近自动打工：{last.get('date', '')}「{last.get('job', '')}」+{last.get('coins', 0)} 金币",
                             C_BLACK))
        else:
            pet_rows.append(("暂无自动化记录", C_GRAY))

    # 农场信息
    farm_rows = []
    farm_right = {}
    if farm:
        plots = farm.get("plots", []) or []
        idle = sum(1 for p in plots if p.get("crop") is None)
        mature = sum(1 for p in plots if p.get("crop") is not None and now_ts >= float(p.get("mature_ts", 0) or 0))
        growing = len(plots) - idle - mature
        farm_rows.append((f"土地：空闲 {idle}｜种植中 {growing}｜已成熟 {mature}（共 {len(plots)} 块）", C_BLACK))
        farm_right[0] = (f"已解锁 {len(plots)} 块地块", C_BLACK)
        planting = {}
        earliest = None
        for p in plots:
            crop = p.get("crop")
            if not crop:
                continue
            planting[crop.get("name", "作物")] = planting.get(crop.get("name", "作物"), 0) + 1
            mt = float(p.get("mature_ts", 0) or 0)
            if now_ts < mt:
                earliest = mt if earliest is None else min(earliest, mt)
        if planting:
            farm_rows.append(("当前种植：" + "、".join(f"{k}×{v}" for k, v in planting.items()), C_BLACK))
            if earliest:
                farm_rows.append((f"最早的成熟需要 {int((earliest - now_ts) // 3600)} 小时", C_BLACK))
            else:
                farm_rows.append(("暂无成熟预估", C_GRAY))
        else:
            farm_rows.append(("当前种植：无", C_GRAY))
            farm_rows.append(("暂无成熟预估", C_GRAY))
        farm_rows.append([(f"偷菜人数 {len(steal_thieves)}｜被偷损失金额 ", C_BLACK),
                          (f"{steal_loss}", C_RED if steal_loss else C_BLACK)])

    # ---------- 版面几何（参考图坐标 ÷3；右侧加宽 WIDEN 防溢出） ----------
    WIDEN = _u(750)                        # 卡片列加宽量（右侧列长文本预留）
    TOP, M_BOT = _u(180), _u(179)
    PX, PW = _u(151), _u(1489)            # 左侧面板
    CX, CR = _u(1736), _u(5109) + WIDEN   # 右侧卡片列
    CW = CR - CX
    AW = _u(992)                           # 签到信息卡宽
    BX = CX + _u(1084)                     # 好感度卡 x
    BW = _u(1686) - 16                     # 银行存款信息卡宽（与排行榜卡间距 32）
    RX = CX + _u(1686) + 16                # 排行榜卡 x

    h_top = _u(228 + (len(signin_rows) - 1) * 108)
    if "bank" in dis and "rank" in dis:
        h_mid = _u(663)
    else:
        h_mid = _u(336 + (max(len(bank_rows), len(rank_rows)) - 1) * 110)
    h_pet = h_farm = _u(663)

    page_w = CR + _u(166)
    y = TOP
    top_y = None
    if "signin" not in dis or "fav" not in dis:
        top_y = y
        y += h_top + GAP_V
    mid_y = y
    y += h_mid + GAP_V
    pet_y = y
    y += h_pet + GAP_V
    farm_y = y
    y += h_farm
    page_h = y + M_BOT
    cards_bottom = y

    img = Image.new("RGB", (page_w, page_h), C_BG)
    d = ImageDraw.Draw(img)

    # ---------- 绘制小工具 ----------
    def card(x0, y0, x1, y1, outline=C_GREEN):
        d.rectangle([x0, y0, x1, y1], fill=C_WHITE, outline=outline, width=BORDER)

    def txt(x, y, s, font, color):
        dtext(d, (int(x), int(y)), s, font=font, fill=color)

    def rtxt(xr, y, s, font, color):
        txt(xr - tw(s, font), y, s, font, color)

    def rich(x, y, segs, font):
        cx = x
        for s, color in segs:
            txt(cx, y, s, font, color)
            cx += tw(s, font)

    def fit(s, font, maxw):
        if tw(s, font) <= maxw:
            return s
        while s and tw(s + "…", font) > maxw:
            s = s[:-1]
        return s + "…"

    def center_line(y, s, font, color, maxw):
        s = fit(s, font, maxw)
        txt(CX + (CW - tw(s, font)) / 2, y, s, font, color)

    def pair(y, lx, label, vx, value, maxw=None):
        """标签 + 数值两列：数值墨迹与标签墨迹垂直居中（参考图 y268 标签 / y276 数值关系）"""
        txt(lx, y, label, f_body, C_BLACK)
        bb = d.textbbox((int(lx), int(y)), clean_img_text(label), font=f_body)
        dtext(d, (int(vx), int((bb[1] + bb[3]) / 2)), fit(str(value), f_body, maxw) if maxw else str(value),
              font=f_body, fill=C_BLACK, anchor="lm")

    def two_tone(xy, s, font, fill_box):
        """进度条双层文字：默认墨绿色，与墨绿填充重叠部分白色。"""
        txt(xy[0], xy[1], s, font, C_GREEN)
        from PIL import ImageChops
        fx0, fy0, fx1, fy1 = fill_box
        w = tw(s, font)
        asc, desc = font.getmetrics()
        px0 = max(0, int(xy[0]) - 4)
        py0 = max(0, int(xy[1]) - 4)
        px1 = min(img.width, int(xy[0] + w) + 4)
        py1 = min(img.height, int(xy[1] + asc + desc) + 4)
        if px1 <= px0 or py1 <= py0:
            return
        region = img.crop((px0, py0, px1, py1))
        mask = Image.new("L", region.size, 0)
        ImageDraw.Draw(mask).text((int(xy[0]) - px0, int(xy[1]) - py0), clean_img_text(s), font=font, fill=255)
        clip = Image.new("L", region.size, 0)
        ImageDraw.Draw(clip).rectangle([fx0 - px0, fy0 - py0, fx1 - px0, fy1 - py0], fill=255)
        region.paste(C_WHITE, (0, 0), ImageChops.multiply(mask, clip))
        img.paste(region, (px0, py0))

    def ghost(x0, y0, x1, y1):
        s = "管理员禁用了本模块的功能。"
        txt((x0 + x1 - tw(s, f_msg)) / 2, (y0 + y1 - 35) / 2, s, f_msg, C_DGRAY)

    def draw_greet(x, y, s):
        dtext(d, (int(x), int(y)), s, font=f_greet, fill=C_WHITE,
              **({"stroke_width": greet_stroke, "stroke_fill": C_WHITE} if greet_stroke else {}))

    # ---------- 左侧墨绿面板 ----------
    d.rectangle([PX, TOP, PX + PW, cards_bottom], fill=C_GREEN)
    tx = PX + _u(135)
    txt(tx, TOP + _u(185), "签到时间", f_body, C_WHITE)
    txt(tx, TOP + _u(185) + 35, "中国标准时间", f_body, C_WHITE)
    txt(tx, TOP + _u(185) + 70, f"{now.year}/{now.month}/{now.day} {now:%H:%M}", f_body, C_WHITE)

    # 底部堆叠：异常徽章 / 昵称 / 问候语 / 全禁用消息
    badges = []
    if pet and not weak:
        if float(pet["satiety"]) < _attr_max_of(core, pet["health"])[0] * 0.1:
            badges.append("你的宠物饿了")
    if steal_loss > 0:
        badges.append("菜被偷了")
    if overdue_days:
        badges.append("有借款逾期")

    pb = cards_bottom
    if all_disabled:
        msg_top = pb - _u(75) - 29
        txt(PX + _u(165), msg_top, "管理员禁用了所有功能。", f_msg2, C_WHITE)
        name_top = msg_top - _u(44) - 67
    else:
        name_top = None
        if badges:
            # 小号徽章：矩形宽高由文字决定，文字垂直居中（anchor=lm）
            fnt = f_body
            pad_h, pad_v, gap = _u(42), _u(24), _u(36)
            asc, desc = fnt.getmetrics()
            bh = asc + desc + pad_v * 2
            bx0 = PX + _u(125)
            right_lim = PX + PW - _u(60)
            rows, cur, cx = [], [], bx0
            for b in badges:
                bw = tw(b, fnt) + pad_h * 2
                if cur and cx + bw > right_lim:
                    rows.append(cur)
                    cur, cx = [], bx0
                cur.append((cx, b, bw))
                cx += bw + gap
            if cur:
                rows.append(cur)
            yb = pb - _u(125) - len(rows) * (bh + gap) + gap
            for row in rows:
                for cx, b, bw in row:
                    d.rectangle([cx, yb, cx + bw, yb + bh], fill=C_BG, outline=C_RED, width=BORDER)
                    dtext(d, (int(cx + pad_h), int(yb + bh / 2)), b, font=fnt, fill=C_RED, anchor="lm")
                yb += bh + gap
            badge_top = pb - _u(125) - len(rows) * (bh + gap) + gap
            name_top = badge_top - _u(44) - 67
        if name_top is None:
            name_top = pb - _u(176) - 67
    # 昵称（3.0.2）：上限由 10 放宽到 12 个全角字符宽，按像素宽计（半角折半）；
    # 恰好 12 全角不省略，超过 12 才以…截断；仍按面板宽度自动换行，不出墨绿矩形
    disp = fit(str(name), f_greet, tw("一" * 12, f_greet))
    name_w = PW - _u(135) - _u(60)
    name_lines, cur = [], ""
    for ch in disp:
        if cur and tw(cur + ch, f_greet) > name_w:
            name_lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        name_lines.append(cur)
    for i, ln in enumerate(reversed([_time_greeting(now)] + name_lines)):
        draw_greet(PX + _u(135), name_top - i * _u(240), ln)

    # ---------- 右侧卡片 ----------
    # 顶部行：签到信息 | 好感度信息
    if top_y is not None:
        if "signin" not in dis:
            card(CX, top_y, CX + AW, top_y + h_top)
            ry = top_y + _u(73)
            for i, (s, color) in enumerate(signin_rows):
                if s in signin_values:
                    pair(ry + i * ROW_PITCH, CX + _u(101), s, CX + _u(557), signin_values[s],
                         maxw=CX + AW - CX - _u(557) - 12)
                else:
                    txt(CX + _u(101), ry + i * ROW_PITCH, s, f_body, color)
        if "fav" not in dis:
            card(BX, top_y, CR, top_y + h_top)
            pair(top_y + _u(88), BX + _u(98), fav_rows[0][0], BX + _u(756), fav_rows[0][1])
            # 进度条（墨绿边框 + 白轨道 + 墨绿填充 + 双层文字；右缘随加宽后的卡片对齐）
            bx0, bx1 = BX + _u(90), CR - _u(132)
            by0, by1 = top_y + _u(214), top_y + _u(298)
            d.rectangle([bx0, by0, bx1, by1], fill=C_WHITE, outline=C_GREEN, width=BORDER)
            fw = int(((bx1 - bx0) - BORDER * 2) * fav_rows[1][0])
            fill_box = (bx0 + BORDER, by0 + BORDER, bx0 + BORDER + fw, by1 - BORDER)
            if fw > 0:
                d.rectangle(fill_box, fill=C_GREEN)
            two_tone((bx0 + _u(38), by0 + (_u(84) - 17) // 2), "升级进度", f_barlab, fill_box)
            two_tone((bx0 + _u(38) + tw("升级进度", f_barlab) + _u(40), by0 + (_u(84) - 16) // 2),
                     fav_rows[1][1], f_bar, fill_box)
            rtxt(bx1, by0 + _u(116), f"{fav:.2f}/{(lv + 1) * step:.2f}", f_bar, C_BLACK)
            pair(top_y + _u(369), BX + _u(98), fav_rows[2][0], BX + _u(756), fav_rows[2][1])

    # 中部行：银行存款信息 | 排行榜（两张独立卡片）
    if "bank" in dis and "rank" in dis:
        ghost(CX, mid_y, CX + BW, mid_y + h_mid)
        ghost(RX, mid_y, CR, mid_y + h_mid)
    else:
        if "bank" in dis:
            ghost(CX, mid_y, CX + BW, mid_y + h_mid)
        else:
            card(CX, mid_y, CX + BW, mid_y + h_mid)
            txt(CX + _u(89), mid_y + _u(68), "银行存款信息", f_title, C_BLACK)
            for i, row in enumerate(bank_rows):
                if isinstance(row, list):
                    rich(CX + _u(89), mid_y + _u(187) + i * BANK_PITCH, row, f_body)
                else:
                    txt(CX + _u(89), mid_y + _u(187) + i * BANK_PITCH,
                        fit(row[0], f_body, BW - _u(89) - _u(40)), f_body, row[1])
        if "rank" in dis:
            ghost(RX, mid_y, CR, mid_y + h_mid)
        else:
            card(RX, mid_y, CR, mid_y + h_mid)
            # 排行榜内容相对本卡左缘（参考图 3546-3469=77 起）
            txt(RX + _u(77), mid_y + _u(75), "排行榜", f_title, C_BLACK)
            for i, (label, pos, score, color) in enumerate(rank_rows[:-1]):
                y_r = mid_y + _u(194) + i * BANK_PITCH
                txt(RX + _u(77), y_r, label, f_body, color)
                txt(RX + _u(545), y_r, pos, f_body, color)
                if score:
                    txt(RX + _u(1065), y_r, "积分", f_body, color)
                    txt(RX + _u(1261), y_r, score, f_body, color)
            y_u = mid_y + _u(194) + (len(rank_rows) - 1) * BANK_PITCH
            txt(RX + _u(77), y_u + 3, rank_rows[-1][0], f_small, C_GRAY)
            txt(RX + _u(1065), y_u + 3, rank_rows[-1][1], f_small, C_GRAY)

    # 宠物信息
    if "pet" in dis:
        ghost(CX, pet_y, CR, pet_y + h_pet)
    elif not pet:
        card(CX, pet_y, CR, pet_y + h_pet)
        center_line(pet_y + _u(155), "没有找到你的宠物", f_msg, C_BLACK, CW - _u(80))
        center_line(pet_y + _u(155) + _u(142), "发送指令「解锁宠物」领养一只吧", f_msg, C_BLACK, CW - _u(80))
        center_line(pet_y + _u(155) + _u(284), f"需要消耗 {pet_cost} 金币", f_msg2, C_BLACK, CW - _u(80))
    elif weak:
        card(CX, pet_y, CR, pet_y + h_pet, outline=C_RED)
        center_line(pet_y + _u(155), f"你已经有 {weak_streak} 天没有照顾宠物了 宠物处于虚弱状态",
                    f_msg, C_RED, CW - _u(80))
        center_line(pet_y + _u(155) + _u(142), "发送指令「治疗宠物」救治", f_msg, C_RED, CW - _u(80))
        center_line(pet_y + _u(155) + _u(284), f"需要消耗 {heal_cost} 金币", f_msg2, C_BLACK, CW - _u(80))
    else:
        card(CX, pet_y, CR, pet_y + h_pet)
        txt(CX + _u(97), pet_y + _u(68), "宠物信息", f_title, C_BLACK)
        for i, row in enumerate(pet_rows):
            if isinstance(row, list):
                rich(CX + _u(95), pet_y + _u(188) + i * ROW_PITCH, row, f_body)
            else:
                txt(CX + _u(95), pet_y + _u(188) + i * ROW_PITCH, row[0], f_body, row[1])
        if 1 in pet_right:
            s, color = pet_right[1]
            txt(CX + _u(2405), pet_y + _u(188) + ROW_PITCH, s, f_body, color)

    # 农场信息
    if "farm" in dis:
        ghost(CX, farm_y, CR, farm_y + h_farm)
    elif not farm:
        card(CX, farm_y, CR, farm_y + h_farm)
        center_line(farm_y + _u(155), "没有获取到农场信息", f_msg, C_BLACK, CW - _u(80))
        center_line(farm_y + _u(155) + _u(142), "发送指令「解锁农场」开始种地吧", f_msg, C_BLACK, CW - _u(80))
        center_line(farm_y + _u(155) + _u(284), f"需要消耗 {farm_cost} 金币", f_msg2, C_BLACK, CW - _u(80))
    else:
        card(CX, farm_y, CR, farm_y + h_farm)
        txt(CX + _u(94), farm_y + _u(68), "农场信息", f_title, C_BLACK)
        for i, row in enumerate(farm_rows):
            if isinstance(row, list):
                rich(CX + _u(97), farm_y + _u(188) + i * ROW_PITCH, row, f_body)
            else:
                txt(CX + _u(97), farm_y + _u(188) + i * ROW_PITCH, row[0], f_body, row[1])
        if 0 in farm_right:
            s, color = farm_right[0]
            txt(CX + _u(2741), farm_y + _u(188), s, f_body, color)

    # 全部禁用：整版空白卡（参考图 6）
    if all_disabled:
        card(CX, TOP, CR, cards_bottom)

    return save_temp_image(img, "_snap_", "签到快照")
