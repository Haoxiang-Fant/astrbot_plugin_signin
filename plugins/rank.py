# -*- coding: utf-8 -*-
"""排行榜（3.0.0 功能插件）。自 2.3.0 modules/rank.py 原样迁移：
金币排行 / 宠物排行 / 农场排行；积分权重（RANK_*_W）、展示名次、群成员有效期等均为运行参数（core.param，常量作缺省）。
图片渲染走图片响应模块 render_rank（模块缺失时回退文本，附进度百分比）；
本群判定以 WebUI 同步名单（group_names）为主，兼容 48h 活跃标记（group_members）；
每次查询先经平台 API 刷新在榜用户的本群昵称（仅已标记用户，不改 48h 时间戳）。
积分条目经 core.expose("rank") 暴露（签到快照等渲染依赖 entries(kind, data) 的返回形状）。"""
import asyncio
import random
from datetime import datetime

from astrbot.api import logger

from ..core import (RANK_KINDS, RANK_DISPLAY, RANK_HIGHLIGHT_COLOR,
                    RANK_COIN_COIN_W, RANK_COIN_BANK_W,
                    RANK_PET_EXP_W, RANK_PET_HEALTH_W, RANK_PET_ATTR_W,
                    RANK_FARM_EXP_W, RANK_FARM_PLOT_W, RANK_FARM_GRADE_W,
                    RANK_PLOT_SCORES, GROUP_MEMBER_TTL_HOURS)

NAME = "rank"

_CORE = None  # register(core) 时注入的核心框架实例


def _param(key, default):
    """读运行参数（register 前调用时回退常量缺省）"""
    if _CORE is not None:
        return _CORE.param(key, default)
    return default


# ================= 基础工具（与 2.3.0 一致） =================
def _dep_amount(d) -> int:
    """安全读取存单本金，防御异常/空数据（自 2.3.0 bank._dep_amount 迁移）"""
    v = d.get("amount") if isinstance(d, dict) else None
    return int(v) if isinstance(v, (int, float)) else 0


def _user_name(data, uid):
    """存档昵称（users[uid].name / nickname；与 2.3.0 _user_name 一致）"""
    u = data.get("users", {}).get(uid, {})
    return u.get("name") or u.get("nickname") or ""


def _fmt_score(v):
    """积分显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0"""
    v = float(v)
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _mask_name(name, in_group):
    """排行榜展示名：非本群用户脱敏为「第一个字 * 最后一个字」（≥2 字时，如 李四明 → 李*明），
    获得到昵称信息后即用首尾字，不整行打星；本群用户显示原名。"""
    if in_group:
        return name
    s = str(name)
    if len(s) <= 1:
        return s  # 单字名无法遮掩
    return s[0] + "*" + s[-1]


# ================= 本群成员标记（48h 有效期） =================
def _is_group_member(data: dict, gid, uid) -> bool:
    """用户是否被标记为当前群成员（有效期 GROUP_MEMBER_TTL_HOURS 小时；无群上下文视为非本群）。
    兼容旧数据：标记可能是裸时间戳（float），也可能是 {"ts": ..., "name": ...}。"""
    if not gid:
        return False
    g = data.get("group_members", {}).get(str(gid)) or {}
    ts = g.get(str(uid)) if isinstance(g, dict) else None
    if isinstance(ts, dict):
        ts = ts.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    ttl = float(_param("GROUP_MEMBER_TTL_HOURS", GROUP_MEMBER_TTL_HOURS) or 48) * 3600
    return datetime.now().timestamp() - float(ts) <= ttl


def _group_member_name(data: dict, gid, uid) -> str:
    """取用户在本群的昵称（标记时记录）；旧数据（裸时间戳）或未标记时返回空串。"""
    if not gid:
        return ""
    g = data.get("group_members", {}).get(str(gid)) or {}
    info = g.get(str(uid)) if isinstance(g, dict) else None
    if isinstance(info, dict):
        return str(info.get("name", "") or "").strip()
    return ""


def _update_group_member_name(data: dict, gid, uid, name) -> None:
    """只更新【已标记】群成员的昵称字段：
    - 不改 48h 时间戳（活跃以本人触发功能为准，刷新昵称不等同于本人活跃）；
    - 旧裸时间戳数据补齐为 {"ts","name"}；未标记用户不创建（非本群身份不变）。"""
    members = data.setdefault("group_members", {})
    g = members.setdefault(str(gid), {})
    old = g.get(str(uid))
    if isinstance(old, dict):
        old["name"] = str(name or "").strip()
    elif isinstance(old, (int, float)):
        g[str(uid)] = {"ts": float(old), "name": str(name or "").strip()}


def _group_default_name(data: dict, gid, uid) -> str:
    """排行榜默认昵称：WebUI「同步全部群聊昵称」得到的数据（group_names[gid][uid]）。
    实时标记昵称（group_members）优先于它；未同步到该群/用户时返回空串。"""
    if not gid:
        return ""
    g = data.get("group_names", {}).get(str(gid)) or {}
    return str(g.get(str(uid), "") or "").strip()


def _is_in_group(data: dict, gid, uid) -> bool:
    """本群判定：以 WebUI 同步的全群名单（group_names[gid]）为准，
    并兼容 48h 活跃标记（group_members[gid]，未同步/刚加入时也能识别）；无群上下文视为非本群。"""
    if not gid:
        return False
    g = data.get("group_names", {}).get(str(gid)) or {}
    if isinstance(g, dict) and str(uid) in g:
        return True
    return _is_group_member(data, gid, uid)


def _pick_any_group_name(data: dict, uid) -> str:
    """取用户在【任意群】的一个昵称（用于非本群用户的脱敏显示）：
    若其在多个群的昵称不一致，随机选择一个；全部一致则直接使用；任何群都没有则返回空串。"""
    names = {}
    for g in (data.get("group_names") or {}).values():
        if not isinstance(g, dict):
            continue
        n = str(g.get(str(uid), "") or "").strip()
        if n:
            names[n] = True
    if not names:
        return ""
    if len(names) > 1:
        return random.choice(sorted(names))
    return next(iter(names))


async def _refresh_rank_names(event, data: dict, gid, uids) -> int:
    """每次查询排行榜时，通过平台 API（aiocqhttp get_group_member_info）刷新在榜用户的本群昵称。
    仅更新已标记（group_members[gid][uid]）用户的昵称；未标记用户不会被创建；
    平台不支持（无 api.call_action）时返回 0；单个用户失败静默跳过。返回成功刷新人数。"""
    call_action = getattr(getattr(getattr(event, "bot", None), "api", None), "call_action", None)
    if call_action is None:
        return 0
    _gid = int(gid) if str(gid).isdigit() else gid
    updated = 0

    async def _one(uid):
        nonlocal updated
        try:
            _uid = int(uid) if str(uid).isdigit() else uid
            info = await call_action("get_group_member_info", group_id=_gid, user_id=_uid)
            if isinstance(info, dict):
                nick = str(info.get("card") or info.get("nickname") or "").strip()
                if nick:
                    _update_group_member_name(data, gid, uid, nick)
                    updated += 1
        except Exception as e:
            logger.debug(f"[rank] 刷新群成员昵称失败 uid={uid}: {e}")

    await asyncio.gather(*[_one(u) for u in uids])
    return updated


# ================= 排行积分 =================
def _rank_plot_score_total(plots):
    """土地等级分合计：每块地块按累计升级花费计分（贫瘠 0 / 红 1000 / 普通 2500 / 肥沃 4500 / 黑 7500，
    分数表为运行参数 RANK_PLOT_SCORES 可调），地块超界按最高级计分。"""
    raw = _param("RANK_PLOT_SCORES", RANK_PLOT_SCORES)
    try:
        if isinstance(raw, str):
            scores = [float(x) for x in raw.replace("，", ",").split(",") if str(x).strip() != ""]
        else:
            scores = [float(x) for x in raw]
    except (TypeError, ValueError):
        scores = [0.0, 1000.0, 2500.0, 4500.0, 7500.0]
    total = 0.0
    for plot in plots:
        g = int(plot.get("grade", 0)) if isinstance(plot, dict) else 0
        total += scores[g] if 0 <= g < len(scores) else scores[-1]
    return total


def rank_score_coins(data: dict, key: str) -> int:
    """金币排行积分（与金币排行榜一致）：金币 × 权重 + 存款本金 × 权重"""
    cw = float(_param("RANK_COIN_COIN_W", RANK_COIN_COIN_W))
    bw = float(_param("RANK_COIN_BANK_W", RANK_COIN_BANK_W))
    bank = data.get("bank", {}).get(key)
    dep = sum(_dep_amount(d) for d in (bank.get("deposits", []) if isinstance(bank, dict) else []))
    return int(_CORE.coins_of(key) * cw + dep * bw)


def _rank_entries(kind: str, data: dict):
    """计算全部玩家的排行条目，返回按积分降序的 [(score, uid, name), ...]。
    kind: "coins" 金币排行 / "pet" 宠物排行 / "farm" 农场排行。"""
    entries = []
    if kind == "coins":
        cw = float(_param("RANK_COIN_COIN_W", RANK_COIN_COIN_W))
        bw = float(_param("RANK_COIN_BANK_W", RANK_COIN_BANK_W))
        for uid, user in (data.get("users") or {}).items():
            if not isinstance(user, dict):
                continue
            bank = data.get("bank", {}).get(uid)
            dep = sum(_dep_amount(d) for d in (bank.get("deposits", []) if isinstance(bank, dict) else []))
            score = _CORE.coins_of(uid) * cw + dep * bw
            entries.append((score, str(uid), _user_name(data, uid) or str(uid)))
    elif kind == "pet":
        ew = float(_param("RANK_PET_EXP_W", RANK_PET_EXP_W))
        hw = float(_param("RANK_PET_HEALTH_W", RANK_PET_HEALTH_W))
        aw = float(_param("RANK_PET_ATTR_W", RANK_PET_ATTR_W))
        for uid, pet in (data.get("pets") or {}).items():
            if not isinstance(pet, dict):
                continue
            exp = float(pet.get("exp", 0) or 0)
            health = float(pet.get("health", 0) or 0)
            others = (float(pet.get("satiety", 0) or 0) + float(pet.get("thirst", 0) or 0)
                      + float(pet.get("stamina", 0) or 0) + float(pet.get("mood", 0) or 0))
            score = exp * ew + health * hw + others * aw
            pname = str(pet.get("name", "") or "").strip() or str(uid)
            entries.append((score, str(uid), pname))
    else:  # farm
        ew = float(_param("RANK_FARM_EXP_W", RANK_FARM_EXP_W))
        pw = float(_param("RANK_FARM_PLOT_W", RANK_FARM_PLOT_W))
        gw = float(_param("RANK_FARM_GRADE_W", RANK_FARM_GRADE_W))
        for uid, farm in (data.get("farms") or {}).items():
            if not isinstance(farm, dict):
                continue
            exp = float(farm.get("exp", 0) or 0)
            plots = farm.get("plots") or []
            score = exp * ew + max(0, len(plots) - 2) * pw + _rank_plot_score_total(plots) * gw
            entries.append((score, str(uid), _user_name(data, uid) or str(uid)))
    # 积分降序；同分按用户 ID 稳定排序
    entries.sort(key=lambda e: (-e[0], e[1]))
    return entries


# ================= 响应组装 =================
def _build_rank(kind: str, event):
    """组装排行榜响应：图片优先，渲染失败回退为文本。
    全局榜 + 本群判定（WebUI 同步名单 group_names，兼容 48h 活跃标记）：
    非本群用户脱敏为「首字*尾字」（绝不用 ***，多群昵称不一致时随机取一个群的昵称）。"""
    titles = {"coins": "💰 金币排行榜", "pet": "🐾 宠物排行榜", "farm": "🌾 农场排行榜"}
    data = _CORE.data
    me = str(_CORE.user_key(event))
    gid = event.get_group_id()          # 当前群（私聊无群 → 全员按非本群处理）
    show = int(_param("RANK_DISPLAY", RANK_DISPLAY) or 20)
    hl = _param("RANK_HIGHLIGHT_COLOR", RANK_HIGHLIGHT_COLOR)
    entries = _rank_entries(kind, data)
    if not entries:
        return "还没有玩家上榜，快发送「签到」「解锁宠物」「解锁农场」参与吧～"
    top_score = entries[0][0]           # 第一名积分：进度条基准（第一名恒 100%）
    rows = []
    for i, (score, uid, name) in enumerate(entries[:show], start=1):
        # 本群判定：WebUI 同步名单（group_names）为主，兼容 48h 活跃标记
        in_group = _is_in_group(data, gid, uid)
        # 名字来源（优先级）：
        #   本群 → 实时记录昵称 / 同步本群昵称 / 存档昵称（宠物名）
        #   非本群 → 任意群昵称（多群不一致随机）/ 存档昵称（宠物名）/ uid 兜底
        #           → 一律脱敏「首字*尾字」，绝不用 ***
        if in_group:
            gname = _group_member_name(data, gid, uid)
            gdef = _group_default_name(data, gid, uid)
            base = gname or gdef or name
        else:
            base = _pick_any_group_name(data, uid) or name
        # 没有任何昵称（兜底就是用户 ID）时按用户 ID 做「首字*尾字」脱敏，同样不使用 ***
        disp = _mask_name(base, in_group)
        ratio = 1.0 if i == 1 else (min(1.0, score / top_score) if top_score > 0 else 0.0)
        rows.append((i, disp, _fmt_score(score), uid == me, ratio, not in_group))
    render = getattr(_CORE.image, "render_rank", None)
    img = render(titles[kind], rows, hl) if render is not None else None
    if img is not None:
        return img
    # 文本回退（附进度百分比）
    lines = [titles[kind] + f"（前 {show} 名）", ""]
    for rank, name, score_str, is_me, ratio, masked in rows:
        tag = "（本群之外）" if masked else ""
        lines.append(f"{rank}. {name}{tag} {score_str} [{int(ratio * 100)}%]" + (" ← 你" if is_me else ""))
    return "\n".join(lines)


def register(core):
    global _CORE
    _CORE = core

    # ================= 金币排行 / 宠物排行 / 农场排行 =================
    async def handle_rank(event):
        head = core.expand_alias((event.message_str or "").strip().split(maxsplit=1)[0])
        kind = RANK_KINDS.get(head, "coins")
        data = core.data
        # 每次查询时通过平台 API 刷新在榜用户的本群昵称（仅已标记用户，不改 48h 时间戳）
        gid = event.get_group_id()
        if gid:
            show = int(core.param("RANK_DISPLAY", RANK_DISPLAY) or 20)
            _entries = _rank_entries(kind, data)
            updated = await _refresh_rank_names(event, data, gid, [e[1] for e in _entries[:show]])
            if updated:
                core.save()
        return _build_rank(kind, event)

    core.command("金币排行", feature="rank")(handle_rank)
    core.command("宠物排行", feature="rank")(handle_rank)
    core.command("农场排行", feature="rank")(handle_rank)

    core.add_help("排行榜", [
        ("金币排行", "金币排行榜（金币 + 银行存款积分）"),
        ("宠物排行", "宠物排行榜（经验/健康/属性积分）"),
        ("农场排行", "农场排行榜（经验/土地积分）"),
    ])

    # ================= 服务暴露（签到快照渲染 / 其他插件查询用） =================
    class RankApi:
        """排行榜服务：entries(kind, data) 返回按积分降序的 [(score, uid, name), ...]（形状被签到快照渲染依赖）"""

        def entries(self, kind, data=None):
            """全部玩家排行条目（kind: coins/pet/farm）；data 缺省取 core.data"""
            return _rank_entries(kind, _CORE.data if data is None else data)

        def rank_score_coins(self, key, data=None):
            """金币排行积分（与金币排行榜一致）；data 缺省取 core.data"""
            return rank_score_coins(_CORE.data if data is None else data, key)

    core.expose("rank", RankApi())
