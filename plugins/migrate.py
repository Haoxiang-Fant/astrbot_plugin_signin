# -*- coding: utf-8 -*-
"""数据迁移插件：旧版 2.x 数据接入 3.0。

3.0 的数据目录与 2.x 同为 data/plugin_data/astrbot_plugin_signin（核心 _DATA_DIR），
升级后数据原地可用，migrate_once 通常直接跳过（同目录守卫）；
仅当旧数据位于其他位置（如旧版把数据放进插件目录内）时才执行逐文件复制。
随后依次补跑旧格式转译（跨群合并 / 宠物等级重算 / 自动化贷款合并，幂等）
与数据格式检查（records 分文件，format_convert 插件）。
"""
import os
import shutil

from astrbot.api import logger

NAME = "migrate"

from ..core import (_DATA_DIR, DATA_FILE, RECORDS_FILE, PET_MAX_LEVEL, PET_EXP_PER_LEVEL,  # noqa: E402
                    _read_json, _write_json)

_OLD_PLUGIN_NAME = "astrbot_plugin_signin"
_COPY_FILES = ("data.json", "records.json", "game_items.json", "config_draft.json",
               "后台.txt", "宠物商店.txt", "宠物商店-食物.txt", "宠物商店-饮料.txt",
               "宠物商店-药物.txt", "宠物商店-玩具.txt", "作物.txt", "肥料.txt", "贷款套餐.txt")
_COPY_DIRS = ("historydata",)

# ================= 3.0.2 数据净化（修复跨群迁移每启重跑洗掉的用户字段） =================
_SANITIZE_MARK = "sanitize_3_0_2.done"
# 现值永远优先的三个字段（迁移合并时唯一保留的字段，绝不被旧档覆盖）
_LIVE_USER_KEYS = ("coins", "favorability", "last_date")
# 记录类字段：2.2.2 布局存于 records.json，回填时拼接合并
_LOG_KEYS = ("auto_feed_logs", "auto_work_logs", "farm_logs", "signin_logs")


def register(core):
    migrate_once()
    migrate_legacy(core)
    sanitize_wiped_fields(core)
    try:
        from .format_convert import check_and_convert
        check_and_convert()
    except Exception as e:
        logger.error(f"[迁移] 数据格式检查失败: {e}")


def sanitize_wiped_fields(core):
    """把被「跨群迁移每启重跑」bug 洗掉的用户字段从旧档找回来（一次性，标记文件幂等）。

    3.0.2 之前 users 每次启动被重建为仅 coins/favorability/last_date 且已写进
    user_data；旧版 data.json（用户全字段）+ records.json（四类日志）按约定保留
    在数据目录未删，由此回填：
      - coins/favorability/last_date 现值优先，绝不动；
      - 用户其余字段仅在现记录缺失时回填（不覆盖用户升级后重设的值）；
      - 四类日志拼接合并（旧档在前），signin_logs 按 date 去重避免日历重复；
      - 旧档独有的用户整体恢复。
    宠物（含 attr_log）/银行/农场/流水未被该 bug 波及，不在此处理。
    """
    marker = os.path.join(_DATA_DIR, _SANITIZE_MARK)
    if os.path.exists(marker):
        return
    legacy = _read_json(DATA_FILE)
    records = _read_json(RECORDS_FILE)
    legacy_users = legacy.get("users") if isinstance(legacy, dict) else None
    if not isinstance(legacy_users, dict) or not legacy_users:
        _write_json(marker, None)  # 无旧档可回填，直接标记完成
        return
    rec_users = records.get("users") if isinstance(records, dict) else {}
    cur_users = core.data.setdefault("users", {})
    restored = merged = 0
    try:
        for key, old in legacy_users.items():
            if not isinstance(old, dict):
                continue
            uid = key.rsplit(":", 1)[-1] if ":" in key else key
            cur = cur_users.get(uid)
            if not isinstance(cur, dict):
                cur = dict(old)  # 旧档独有用户：整体恢复（随后同样走日志回填）
                cur_users[uid] = cur
                restored += 1
            for k, v in old.items():
                if k not in _LIVE_USER_KEYS and k not in cur:
                    cur[k] = v
            rec = rec_users.get(uid) or rec_users.get(key) or {}
            for k in _LOG_KEYS:
                old_log = rec.get(k) if isinstance(rec, dict) else None
                if not isinstance(old_log, list) or not old_log:
                    continue
                have = cur.get(k)
                have = have if isinstance(have, list) else []
                if k == "signin_logs":
                    # 签到日历每天一条：旧档在前，现档同日期条目跳过（保留旧档真实数据）
                    dates = {e.get("date") for e in have if isinstance(e, dict)}
                    cur[k] = [e for e in old_log if isinstance(e, dict) and e.get("date") not in dates] + have
                else:
                    cur[k] = old_log + [e for e in have if e not in old_log]
                merged += 1
        core.save()
    except Exception as e:
        logger.error(f"[迁移] 数据净化失败（下次启动重试）: {e}")
        return
    _write_json(marker, None)  # 成功后标记，不再重跑
    logger.info(f"[迁移] 数据净化完成：整体恢复用户 {restored} 个，回填日志 {merged} 项"
                f"（coins/favorability/last_date 现值未动）")


# ================= 旧格式数据转译（直升 2.2.x → 3.0 时补跑 2.3.0 的三次迁移；全部幂等） =================

def migrate_legacy(core):
    """旧版数据格式转译入口：跨群合并 → 宠物等级重算 → 自动化贷款合并。"""
    _migrate_legacy_cross_group(core)
    _migrate_pet_levels(core)
    _migrate_auto_loans(core)


def _migrate_legacy_cross_group(core):
    """一次性迁移旧数据：按群存储（gid:uid / private:uid）→ 跨群（uid）。
    金币求和；好感度取最大；签到日期取最新；宠物保留等级/经验最高的一只；左轮战绩求和合并。
    （2.3.0 base.py _migrate_legacy_data 原样移植；_migrated_cross_group 标记幂等）
    3.0.2 修复：标记不随 3.0 分文件布局落盘，导致本函数每次启动都重跑并重建 users，
    只保留金币/好感度/签到日期，洗掉 signin_total / signin_logs / custom_name 等其余字段
    （签到累计次数失效的根因）。现无旧版「群:uid」键即跳过，且合并时整条保留首条记录。"""
    data = core.data
    if data.get("_migrated_cross_group"):
        return
    users = data.get("users", {})
    if not any(":" in str(k) for k in users):
        return  # 已是跨群（uid）数据，无需迁移

    def _uid(key: str) -> str:
        return key.rsplit(":", 1)[-1] if ":" in key else key

    new_users = {}
    for key, u in users.items():
        if not isinstance(u, dict):
            new_users[key] = u
            continue
        uid = _uid(key)
        d = new_users.get(uid)
        if not isinstance(d, dict):
            new_users[uid] = dict(u)  # 首条有效记录整体保留（含签到累计/自定义昵称/自动化设置等）
            continue
        d["coins"] = int(d.get("coins", 0)) + int(u.get("coins", 0))
        d["favorability"] = max(float(d.get("favorability", 0.0)), float(u.get("favorability", 0.0)))
        d["last_date"] = max(d.get("last_date", "") or "", u.get("last_date", "") or "")
    data["users"] = new_users

    new_r = {}
    for key, s in data.get("roulette", {}).items():
        if not isinstance(s, dict):
            new_r[key] = s
            continue
        uid = _uid(key)
        d = new_r.setdefault(uid, {"wins": 0, "losses": 0, "net": 0, "lost_to": {}, "won_from": {}})
        d["wins"] += int(s.get("wins", 0))
        d["losses"] += int(s.get("losses", 0))
        d["net"] += int(s.get("net", 0))
        for side in ("lost_to", "won_from"):
            for opp, e in s.get(side, {}).items():
                if not isinstance(e, dict):
                    continue
                dd = d[side].setdefault(opp, {"name": e.get("name", ""), "amount": 0})
                dd["amount"] += int(e.get("amount", 0))
                if e.get("name"):
                    dd["name"] = e["name"]
    data["roulette"] = new_r

    new_pets = {}
    for key, p in data.get("pets", {}).items():
        if not isinstance(p, dict):
            new_pets[key] = p
            continue
        uid = _uid(key)
        existing = new_pets.get(uid)
        if existing is None or (p.get("level", 0), p.get("exp", 0)) > (existing.get("level", 0), existing.get("exp", 0)):
            new_pets[uid] = p
    data["pets"] = new_pets

    data["_migrated_cross_group"] = True
    core.save()
    logger.info("[迁移] 旧格式数据已合并为跨群（uid）存储")


def _migrate_pet_levels(core):
    """按经验体系重算所有宠物等级（旧数据经验总值匹配 3.0 等级公式，幂等）"""
    for pet in (core.data.get("pets") or {}).values():
        if not isinstance(pet, dict):
            continue
        exp = float(pet.get("exp", 0.0) or 0.0)
        pet["level"] = min(PET_MAX_LEVEL, int(exp // PET_EXP_PER_LEVEL))


def _migrate_auto_loans(core):
    """旧版自动化贷款账单（loans 中 auto=True 的账单）→ 用户 auto_loan 余额
    （新体系无利息无逾期，按剩余本金并入；发现旧账单才执行，迁移后删除账单，天然幂等）。
    键同步归一化：跨群合并后用户以 uid 为键，此处 loans 记录键一并归一。"""
    data = core.data
    for key in list((data.get("loans") or {}).keys()):
        rec = data["loans"].get(key)
        if not isinstance(rec, dict):
            continue
        bills = rec.get("loans") or []
        auto_bills = [b for b in bills if isinstance(b, dict) and b.get("auto") and b.get("remaining", 0) > 0]
        uid = key.rsplit(":", 1)[-1] if ":" in key else key
        if uid != key:
            # 键归一化：uid 键不存在时才搬移，避免覆盖
            if uid not in data["loans"]:
                data["loans"][uid] = rec
            del data["loans"][key]
            rec = data["loans"].get(uid) or rec
        if not auto_bills:
            continue
        total = int(round(sum(float(b.get("remaining", 0) or 0) for b in auto_bills)))
        u = core.ensure_user(uid)
        u["auto_loan"] = round(float(u.get("auto_loan", 0) or 0) + total, 2)
        rec["loans"] = [b for b in bills if not (isinstance(b, dict) and b.get("auto"))]
        logger.info(f"[迁移] 自动化贷款账单 {total} 金币并入用户 {uid} 的 auto_loan 余额")
    core.save()


def _old_data_dir():
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
        return os.path.join(get_astrbot_plugin_data_path(), _OLD_PLUGIN_NAME)
    except Exception:
        return None


def migrate_once() -> bool:
    """旧版数据目录 → 本插件数据目录（目标缺失才复制；返回是否发生迁移）。"""
    old = _old_data_dir()
    if not old or not os.path.isdir(old) or os.path.abspath(old) == os.path.abspath(_DATA_DIR):
        return False
    moved = []
    try:
        for fn in _COPY_FILES:
            src = os.path.join(old, fn)
            dst = os.path.join(_DATA_DIR, fn)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
                moved.append(fn)
        for dn in _COPY_DIRS:
            src = os.path.join(old, dn)
            dst = os.path.join(_DATA_DIR, dn)
            if os.path.isdir(src) and not os.path.exists(dst):
                shutil.copytree(src, dst)
                moved.append(dn)
        if moved:
            logger.info(f"[迁移] 已从 {_OLD_PLUGIN_NAME} 迁移数据: {', '.join(moved)}")
            return True
    except Exception as e:
        logger.error(f"[迁移] 旧数据迁移失败: {e}")
    return False
