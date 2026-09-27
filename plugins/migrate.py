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

from ..core import _DATA_DIR, DATA_FILE, PET_MAX_LEVEL, PET_EXP_PER_LEVEL  # noqa: E402

_OLD_PLUGIN_NAME = "astrbot_plugin_signin"
_COPY_FILES = ("data.json", "records.json", "game_items.json", "config_draft.json",
               "后台.txt", "宠物商店.txt", "宠物商店-食物.txt", "宠物商店-饮料.txt",
               "宠物商店-药物.txt", "宠物商店-玩具.txt", "作物.txt", "肥料.txt", "贷款套餐.txt")
_COPY_DIRS = ("historydata",)


def register(core):
    migrate_once()
    migrate_legacy(core)
    try:
        from .format_convert import check_and_convert
        check_and_convert()
    except Exception as e:
        logger.error(f"[迁移] 数据格式检查失败: {e}")


# ================= 旧格式数据转译（直升 2.2.x → 3.0 时补跑 2.3.0 的三次迁移；全部幂等） =================

def migrate_legacy(core):
    """旧版数据格式转译入口：跨群合并 → 宠物等级重算 → 自动化贷款合并。"""
    _migrate_legacy_cross_group(core)
    _migrate_pet_levels(core)
    _migrate_auto_loans(core)


def _migrate_legacy_cross_group(core):
    """一次性迁移旧数据：按群存储（gid:uid / private:uid）→ 跨群（uid）。
    金币求和；好感度取最大；签到日期取最新；宠物保留等级/经验最高的一只；左轮战绩求和合并。
    （2.3.0 base.py _migrate_legacy_data 原样移植；_migrated_cross_group 标记幂等）"""
    data = core.data
    if data.get("_migrated_cross_group"):
        return

    def _uid(key: str) -> str:
        return key.rsplit(":", 1)[-1] if ":" in key else key

    new_users = {}
    for key, u in data.get("users", {}).items():
        if not isinstance(u, dict):
            new_users[key] = u
            continue
        uid = _uid(key)
        d = new_users.setdefault(uid, {"coins": 0, "favorability": 0.0, "last_date": ""})
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
