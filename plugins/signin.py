# -*- coding: utf-8 -*-
"""签到插件（3.0.0）：每日签到 / 我的签到 / 修改昵称 / 签到帮助。

2.3.0 签到模块移植：奖励判定（金币/好感度/宠物经验/属性丸/农场经验球）、
签到累计与签到日历记录、签到成功后的活动钩子（经 core.service("thirdparty")）、
实时数据快照渲染（plugins/image/signin.py，经 core.image 调用）。
"""
import random
from datetime import date, datetime

from astrbot.api import logger

from ..core import (MIN_COINS, MAX_COINS, MIN_FAV, MAX_FAV, LEVEL_STEP,
                    PILL_NAME, EXP_BALL_NAME, ITEM_TO_COIN,
                    PILL_DROP_MIN, PILL_DROP_MAX,
                    SIGNIN_NO_REWARD_CHANCE, SIGNIN_PILL_CHANCE, SIGNIN_BALL_CHANCE,
                    PET_SIGNIN_EXP_MIN, PET_SIGNIN_EXP_MAX,
                    CUSTOM_NAME_TTL, CUSTOM_NAME_MAX_LEN)

NAME = "signin"


def _reward_chances(core):
    """签到额外奖励池概率（WebUI 可编辑）：返回 (无奖品, 属性丸, 经验球)；
    三者总和超过 1 时按比例归一，保证互斥奖池总和恒为 1。"""
    no_r = float(core.param("SIGNIN_NO_REWARD_CHANCE", SIGNIN_NO_REWARD_CHANCE))
    pill_r = float(core.param("SIGNIN_PILL_CHANCE", SIGNIN_PILL_CHANCE))
    ball_r = float(core.param("SIGNIN_BALL_CHANCE", SIGNIN_BALL_CHANCE))
    total = no_r + pill_r + ball_r
    if total <= 1e-9:
        return 0.40, 0.30, 0.30
    if total > 1.0:
        return no_r / total, pill_r / total, ball_r / total
    return no_r, pill_r, ball_r


def apply_signin_once(core, data, key, today):
    """执行一次完整签到奖励（金币/好感度/宠物经验/属性丸/农场经验球），返回提示行列表。

    供每日签到和「双倍签到」等活动复用；不更新 last_date，
    不处理宠物结算显示 / 活动钩子（避免递归）。
    金币范围 / 好感度范围 / 特殊道具获取率均经 core.param 运行参数读取。
    """
    user = core.ensure_user(key)
    coins_got = random.randint(int(core.param("MIN_COINS", MIN_COINS)),
                               int(core.param("MAX_COINS", MAX_COINS)))
    fav_got = round(random.uniform(float(core.param("MIN_FAV", MIN_FAV)),
                                   float(core.param("MAX_FAV", MAX_FAV))), 2)

    old_fav = float(user.get("favorability", 0.0))
    old_lv = core.level_of(old_fav)

    core.add_coins(key, coins_got, "每日签到")
    new_fav = round(old_fav + fav_got, 2)
    user["favorability"] = new_fav

    new_lv = core.level_of(new_fav)

    lines = [
        f"💰 获得金币：+{coins_got}（当前 {core.coins_of(key)}）",
        f"💗 好感度：+{fav_got:.2f}（当前 {new_fav:.2f}）",
    ]
    step = float(core.param("LEVEL_STEP", LEVEL_STEP) or 10.0)
    if new_lv > old_lv:
        lines.append(f"🎉 好感度突破 {int(new_lv * step)}，等级提升至 Lv.{new_lv}！")
    else:
        lines.append(f"🏅 当前好感等级：Lv.{new_lv}")

    no_ch, pill_ch, ball_ch = _reward_chances(core)

    pet = data.get("pets", {}).get(key)
    exp_got = 0.0
    if pet:
        # 宠物每日结算由固定结算循环统一执行，签到只加经验
        exp_got = round(random.uniform(float(core.param("PET_SIGNIN_EXP_MIN", PET_SIGNIN_EXP_MIN)),
                                       float(core.param("PET_SIGNIN_EXP_MAX", PET_SIGNIN_EXP_MAX))), 2)
        lvl_msg = ""
        pet_svc = core.service("pet")
        gain = getattr(pet_svc, "gain_exp", None) if pet_svc is not None else None
        if callable(gain):
            try:
                lvl_msg = gain(key, exp_got) or ""
            except Exception as e:
                logger.error(f"[签到] 宠物经验联动失败: {e}")
        else:
            # 宠物插件未挂载：降级为直接累加经验（不处理升级提示）
            pet["exp"] = round(float(pet.get("exp", 0.0)) + exp_got, 2)
        lines.append(f"🐾 宠物经验：+{exp_got:.1f}{lvl_msg}")

    # 额外奖励池（互斥）：概率运行时可编辑，总和恒为 1。
    # 归属：属性丸=宠物特殊道具（需解锁宠物）；农场经验球=农场特殊道具（需开通农场）。
    farm = data.get("farms", {}).get(key)
    pill_min = int(core.param("PILL_DROP_MIN", PILL_DROP_MIN))
    pill_max = int(core.param("PILL_DROP_MAX", PILL_DROP_MAX))
    r = random.random()
    if r < no_ch:
        pass  # 不送任何东西
    elif r < no_ch + pill_ch:
        if pet:
            pills = random.randint(pill_min, pill_max)
            inv = pet.setdefault("inventory", {})
            inv[PILL_NAME] = int(inv.get(PILL_NAME, 0)) + pills
            lines.append(f"💊 运气不错，获得 {pills} 个属性丸（发送「使用 属性丸」使用）！")
        else:
            cnt = random.randint(pill_min, pill_max)
            gain_coins = cnt * ITEM_TO_COIN
            core.add_coins(key, gain_coins, "签到奖励转金币")
            lines.append(f"🔄 抽到属性丸 ×{cnt}（未解锁宠物，自动转为 {gain_coins} 金币）")
    else:
        if farm:
            balls = random.randint(pill_min, pill_max)
            tools = farm.setdefault("tools", {})
            tools[EXP_BALL_NAME] = int(tools.get(EXP_BALL_NAME, 0)) + balls
            lines.append(f"🏵️ 运气不错，获得 {balls} 个农场经验球（发送「使用 {EXP_BALL_NAME}」使用）！")
        else:
            cnt = random.randint(pill_min, pill_max)
            gain_coins = cnt * ITEM_TO_COIN
            core.add_coins(key, gain_coins, "签到奖励转金币")
            lines.append(f"🔄 抽到农场经验球 ×{cnt}（未开通农场，自动转为 {gain_coins} 金币）")

    # 最近一次签到获得金币（_handle_sign_in 已清零；双倍签到多次运行累加 = 本次签到总额）
    user["signin_coins_total"] = int(user.get("signin_coins_total", 0) or 0) + coins_got
    user["signin_fav_total"] = round(float(user.get("signin_fav_total", 0) or 0) + fav_got, 2)
    if pet:
        user["signin_pet_exp_total"] = round(float(user.get("signin_pet_exp_total", 0) or 0) + exp_got, 2)
    # 额外获得道具（本次签到）：从 lines 里提取（属性丸 / 经验球 / 转金币）
    extra = [ln for ln in lines if ("属性丸" in ln or "经验球" in ln)]
    user["signin_extra"] = extra

    return lines


def _render_snapshot(core, name, key, extra_lines=None, signed_today=False):
    """签到实时数据快照（图片响应模块 render_signin_snapshot；缺失/失败返回 None 回退纯文本）"""
    fn = getattr(core.image, "render_signin_snapshot", None)
    if fn is None:
        return None
    try:
        return fn(core, name, key, core.data, extra_lines=extra_lines, signed_today=signed_today)
    except Exception as e:
        logger.error(f"[签到] 快照渲染失败: {e}")
        return None


class _LegacyPluginBridge:
    """旧版活动组件适配器：活动经 attach(plugin) 拿到的主插件能力（保留 2.x 方法名）。
    data 形参仅为兼容旧签名，实际一律操作核心唯一数据 core.data。"""

    def __init__(self, core):
        self._core = core

    # ---- 数据存取 ----
    def _load(self):
        return self._core.data

    def _save(self, data=None):
        self._core.save()

    def _ensure_user(self, data, key):
        return self._core.ensure_user(key)

    def _coins_of(self, data, key):
        return self._core.coins_of(key)

    def _add_coins(self, data, key, amount, reason="", skip_repay=False):
        return self._core.add_coins(key, amount, reason, skip_repay=skip_repay)

    def _coin_line(self, data, key):
        return self._core.coin_line(key)

    # ---- 用户 / 等级 ----
    def _user_key(self, event):
        return self._core.user_key(event)

    def _level_of(self, fav):
        return self._core.level_of(fav)

    # ---- 签到（双倍签到等活动复用） ----
    def _apply_signin_once(self, data, key, today):
        return apply_signin_once(self._core, data, key, today)


def _sign_in_activity_hooks(core, event, data, key, lines):
    """签到成功后的活动钩子分发（2.3.0 _sign_in_activity_hooks 移植）：
    经 core.service("thirdparty") 取活动列表，仅调用「已启用 + 时间有效 + 满足参与要求」
    活动的 on_sign_in(event, data, key, lines)；thirdparty 未挂载时静默跳过。
    钩子内异常只记日志，不中断签到。"""
    tp = core.service("thirdparty")
    if tp is None:
        return
    try:
        loader = getattr(tp, "load_activities", None)
        if callable(loader):
            acts = loader()
        else:
            acts = getattr(tp, "_activities", None) or getattr(tp, "activities", None) or []
    except Exception as e:
        logger.error(f"[签到] 读取活动列表失败: {e}")
        return
    if not acts:
        return
    enabled = data.get("activities", {})
    bridge = _LegacyPluginBridge(core)
    for act in acts:
        try:
            if not enabled.get(getattr(act, "id", ""), False):
                continue
            if not act.is_active_now():
                continue
            ok, missing = True, ""
            check = getattr(act, "check_requirements", None)
            if callable(check):
                ok, missing = check(bridge, data, key)
            if not ok:
                lines.append("")
                lines.append(f"⚠️ 活动「{getattr(act, 'name', '')}」未满足参与要求（{missing}），本次签到不触发。")
                continue
            fn = getattr(act, "on_sign_in", None)
            if fn:
                # 兼容：活动实例尚未 attach 主插件实例时挂上适配器
                # （老活动经 self.plugin 调 _apply_signin_once / _add_coins 等）
                try:
                    if getattr(act, "plugin", None) is None and hasattr(act, "attach"):
                        act.attach(bridge)
                except Exception:
                    pass
                fn(event, data, key, lines)
        except Exception as e:
            logger.error(f"[签到] 活动 {getattr(act, 'id', '?')} 签到钩子处理异常: {e}")


class SigninApi:
    """签到服务接口（其它插件经 core.service("signin") 调用）"""

    def __init__(self, core):
        self._core = core

    def signin_snapshot_lines(self, key):
        """用户最近一次签到的数据行（签到信息卡片文本；无记录返回空列表）"""
        core = self._core
        data = core.data
        user = data.get("users", {}).get(key) or {}
        pet = data.get("pets", {}).get(key)
        total = int(user.get("signin_total", 0) or 0)
        coins_total = int(user.get("signin_coins_total", 0) or 0)
        fav_total = float(user.get("signin_fav_total", 0) or 0)
        lines = [f"累计签到 {total} 次｜获得金币：{coins_total}"]
        if pet:
            exp_total = float(user.get("signin_pet_exp_total", 0) or 0)
            lines.append(f"获得好感度：{fav_total:.1f}｜获得宠物经验：{exp_total:.1f}")
        else:
            lines.append(f"获得好感度：{fav_total:.1f}")
        extra = [ln for ln in (user.get("signin_extra") or []) if ("属性丸" in ln or "经验球" in ln)]
        lines.append(("额外道具：" + "；".join(extra)) if extra else "额外道具：无")
        return lines

    def apply_signin_once(self, data, key, today):
        """运行一次完整签到奖励（供「双倍签到」等活动复用），返回提示行列表"""
        return apply_signin_once(self._core, data, key, today)

    def snapshot(self, name, key, extra_lines=None, signed_today=False):
        """渲染签到实时数据快照（返回 ("image", path) 或 None）"""
        return _render_snapshot(self._core, name, key, extra_lines=extra_lines, signed_today=signed_today)


def register(core):
    @core.command("签到", feature="signin")
    def handle_sign_in(event):
        name = core.user_name(event)
        key = core.user_key(event)
        today = date.today().isoformat()

        data = core.data
        user = data.get("users", {}).get(key)

        if user and user.get("last_date") == today:
            reply = (f"{name}，你今天已经签到过啦～\n"
                     f"💰 当前金币：{user.get('coins', 0)}\n"
                     f"💗 当前好感度：{float(user.get('favorability', 0.0)):.2f}（Lv.{core.level_of(float(user.get('favorability', 0.0)))}）")
            # 银行存单已在固定时间自动结算，此处仅展示结算结果
            img = _render_snapshot(core, name, key, signed_today=True)
            return img if img is not None else reply

        if user is None:
            user = core.ensure_user(key)

        # 「获得金币」改为最近一次签到获得的金币总额，每次签到先清零（双倍签到各次再累加）
        # 获得好感度 / 获得宠物经验同样为最近一次签到的变更量
        user["signin_coins_total"] = 0
        user["signin_fav_total"] = 0
        user["signin_pet_exp_total"] = 0
        lines = [f"✅ {name} 签到成功！"]
        lines += apply_signin_once(core, data, key, today)
        user["last_date"] = today
        # 累计签到次数（只在主签到路径累加，双倍活动不重复计次）
        user["signin_total"] = int(user.get("signin_total", 0) or 0) + 1

        pet = data.get("pets", {}).get(key)
        if pet:
            settle_lines = None
            pet_svc = core.service("pet")
            if pet_svc is not None:
                fn = getattr(pet_svc, "settle_display_lines", None)
                if callable(fn):
                    try:
                        settle_lines = fn(pet)
                    except Exception as e:
                        logger.error(f"[签到] 读取宠物结算展示失败: {e}")
            if settle_lines:
                lines.append("")
                lines.extend(settle_lines)

        # ---- 活动钩子：已启用且时间有效的活动可在签到后追加内容（如双倍签到） ----
        _sign_in_activity_hooks(core, event, data, key, lines)

        # 签到日历记录（每天一条，随 records.json 存储；WebUI 用户详情·银行 tab 日历用）
        logs = user.setdefault("signin_logs", [])
        logs.append({
            "date": today,
            "coins": int(user.get("signin_coins_total", 0) or 0),
            "fav": round(float(user.get("signin_fav_total", 0) or 0), 2),
            "exp": round(float(user.get("signin_pet_exp_total", 0) or 0), 2),
            "extra": list(user.get("signin_extra") or []),
        })
        if len(logs) > 120:
            del logs[: len(logs) - 120]
        user["signin_coins_all"] = int(user.get("signin_coins_all", 0) or 0) + int(user.get("signin_coins_total", 0) or 0)

        core.save()
        # 签到响应为用户实时数据快照（瀑布平铺）
        img = _render_snapshot(core, name, key, extra_lines=lines)
        return img if img is not None else "\n".join(lines)

    @core.command("我的签到", feature="signin")
    def handle_my_info(event):
        name = core.user_name(event)
        key = core.user_key(event)

        data = core.data
        user = data.get("users", {}).get(key)
        if not user:
            return f"{name} 还没有签到记录，发送「签到」开始吧～"
        coins = user.get("coins", 0)
        fav = float(user.get("favorability", 0.0))
        lv = core.level_of(fav)
        last = user.get("last_date", "无")
        lines = [
            f"{name} 的签到信息：",
            f"💰 金币：{coins}",
            f"💗 好感度：{fav:.2f}",
            f"🏅 好感等级：Lv.{lv}",
            f"📅 上次签到：{last}",
        ]
        return "\n".join(lines)

    @core.command("修改昵称", feature="signin")
    def handle_change_name(event):
        """修改昵称 <任意字符>：设置自定义昵称，有效期 90 天，优先级高于获取的昵称"""
        name = core.user_name(event)
        key = core.user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return f"{name} 请指定要设置的昵称：修改昵称 <任意字符>（如：修改昵称 小明）"
        custom = parts[1].strip()
        max_len = int(core.param("CUSTOM_NAME_MAX_LEN", CUSTOM_NAME_MAX_LEN))
        if len(custom) > max_len:
            return f"昵称过长（最多 {max_len} 个字符）。"
        u = core.ensure_user(key)
        u["custom_name"] = custom
        u["custom_name_ts"] = datetime.now().timestamp()
        core.save()
        ttl_days = int(float(core.param("CUSTOM_NAME_TTL", CUSTOM_NAME_TTL)) // 86400)
        return (f"✅ {name} 已设置自定义昵称为「{custom}」（有效期 {ttl_days} 天，"
                f"优先级高于获取的昵称，后续响应将使用该昵称）。")

    @core.command("签到帮助", feature="signin")
    def handle_help_signin(event):
        sections = [
            ("签到", [
                ("签到", "每日签到，获得金币 / 好感度 / 宠物经验 / 属性丸 / 农场经验球"),
                ("我的签到", "查看金币与好感度"),
                ("修改昵称 <任意字符>", "设置自定义昵称（90 天有效，优先级高于获取的昵称）"),
                ("签到帮助", "查看签到模块指令"),
                ("游戏帮助", "查看全部模块指令"),
            ]),
        ]
        return core.image.build_help("签到帮助", sections)

    core.expose("signin", SigninApi(core))

    core.add_help("签到", [
        ("签到", "每日签到，获得金币 / 好感度 / 宠物经验 / 属性丸 / 农场经验球"),
        ("我的签到", "查看金币与好感度"),
        ("修改昵称 <任意字符>", "设置自定义昵称（90 天有效，优先级高于获取的昵称）"),
        ("签到帮助", "查看签到模块指令"),
        ("游戏帮助", "查看全部模块指令（本菜单）"),
    ])
    core.add_help("数据说明", [
        ("数据存储", "全部数据存于 data.json（users/pets/farms/bank/loans 等命名空间），与 2.x 存档完全兼容"),
        ("签到字段", "signin_total 累计次数；signin_coins_total / signin_fav_total / signin_pet_exp_total 为最近一次签到获得量；signin_extra 为最近一次额外道具"),
        ("签到日志", "signin_logs 每天一条（金币/好感度/宠物经验/额外道具），随 records.json 存储，最多保留 120 条"),
        ("金币流水", "所有金币变动经核心统一入口，自动记入 ledger 金币账单（每用户最多 200 条）"),
        ("自定义昵称", "custom_name 自定义昵称有效期 90 天，到期自动失效，优先级高于获取的昵称"),
    ])
