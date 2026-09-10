# -*- coding: utf-8 -*-
"""数据系统升级模块（2.2.2）。

数据存放标准：
- 所有运行数据文件位于 AstrBot\\data\\plugin_data\\astrbot_plugin_signin\\ 目录（base 的 _DATA_DIR）。
- 用户数据（users/pets/farms/bank/... 等状态）存 data.json；
  WebUI 记录数据（attr_log / auto_feed_logs / auto_work_logs / shop_price_records）单独存 records.json，
  两者分文件存储，避免运行记录无限增长拖累存档读写。

每次插件启动时由 main 调用 check_and_migrate_data()：
检查磁盘数据是否符合新标准，不符合则调用本模块把旧数据升级到新标准（幂等，可重复执行）。

内存中 data 字典仍然内嵌记录字段（所有业务代码无感知）；base 的 _load_disk/_save
通过 merge_records / split_records 在读写时回填 / 剥离，磁盘上始终分文件。
"""
import copy
import json
import os

try:
    from astrbot.api import logger
except Exception:  # 独立自检环境（_tmp/data_migrate_selftest.py）
    import logging
    logger = logging.getLogger("signin")

try:
    from .base import DATA_FILE
except Exception:  # 独立自检环境：DATA_FILE 由环境变量注入
    DATA_FILE = os.environ.get("SIGNIN_DATA_FILE", "data.json")

# 记录数据文件（与用户数据 data.json 分开存储）
RECORDS_FILE = os.path.join(os.path.dirname(DATA_FILE), "records.json")

# data 顶层记录字段 / 每只宠物内的记录字段 / 每个用户内的记录字段
_DATA_RECORD_FIELDS = ("shop_price_records",)
_PET_RECORD_FIELDS = ("attr_log",)
_USER_RECORD_FIELDS = ("auto_feed_logs", "auto_work_logs")


def _read_json(path):
    """读取 JSON 对象；文件缺失或非字典返回 None。"""
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
                return obj if isinstance(obj, dict) else None
    except Exception as e:
        logger.error(f"[插件] 读取 {os.path.basename(path)} 失败: {e}")
    return None


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def has_embedded_records(data) -> bool:
    """磁盘 data（原文）是否仍内嵌记录字段（旧标准：记录与用户数据混存）。"""
    if not isinstance(data, dict):
        return False
    if any(k in data for k in _DATA_RECORD_FIELDS):
        return True
    for pet in (data.get("pets") or {}).values():
        if isinstance(pet, dict) and any(k in pet for k in _PET_RECORD_FIELDS):
            return True
    for u in (data.get("users") or {}).values():
        if isinstance(u, dict) and any(k in u for k in _USER_RECORD_FIELDS):
            return True
    return False


def split_records(data: dict):
    """深拷贝并剥离记录字段 → (用户数据副本, 记录数据字典)。不修改入参。"""
    user_data = copy.deepcopy(data)
    records = {}
    if isinstance(user_data.get("shop_price_records"), list):
        records["shop_price_records"] = user_data.pop("shop_price_records")
    for uid, pet in (user_data.get("pets") or {}).items():
        if not isinstance(pet, dict):
            continue
        for f in _PET_RECORD_FIELDS:
            if f in pet:
                records.setdefault("pets", {}).setdefault(uid, {})[f] = pet.pop(f)
    for uid, u in (user_data.get("users") or {}).items():
        if not isinstance(u, dict):
            continue
        rec = {}
        for f in _USER_RECORD_FIELDS:
            if f in u:
                rec[f] = u.pop(f)
        if rec:
            records.setdefault("users", {})[uid] = rec
    return user_data, records


def merge_records(data: dict, records) -> None:
    """把 records.json 的记录字段回填进内存 data（原地修改）。records 为 None/非字典时忽略。"""
    if not isinstance(records, dict) or not isinstance(data, dict):
        return
    if isinstance(records.get("shop_price_records"), list):
        data["shop_price_records"] = records["shop_price_records"]
    pets = data.get("pets")
    if isinstance(pets, dict):
        for uid, rec in (records.get("pets") or {}).items():
            pet = pets.get(uid)
            if isinstance(pet, dict) and isinstance(rec, dict):
                for f in _PET_RECORD_FIELDS:
                    if isinstance(rec.get(f), list):
                        pet[f] = rec[f]
    users = data.get("users")
    if isinstance(users, dict):
        for uid, rec in (records.get("users") or {}).items():
            u = users.get(uid)
            if isinstance(u, dict) and isinstance(rec, dict):
                for f in _USER_RECORD_FIELDS:
                    if isinstance(rec.get(f), list):
                        u[f] = rec[f]


def check_and_migrate_data() -> None:
    """数据系统升级（每次启动调用，幂等）：
    1) data.json 缺失（全新安装）→ 补建空 records.json；
    2) data.json 损坏/非对象 → 备份后重置（下次读取按新标准重建）；
    3) data.json 仍内嵌记录字段（旧标准）→ 拆到 records.json 并写回剥离后的 data.json；
    4) 已符合新标准但缺 records.json → 补建。"""
    try:
        if not os.path.exists(DATA_FILE):
            if not os.path.exists(RECORDS_FILE):
                _write_json(RECORDS_FILE, {})
                logger.info("[插件] 数据系统检查：全新安装，已创建记录数据文件 records.json")
            return
        data = _read_json(DATA_FILE)
        if not isinstance(data, dict):
            try:
                os.replace(DATA_FILE, DATA_FILE + ".corrupt.bak")
            except Exception:
                pass
            _write_json(RECORDS_FILE, {})
            logger.warning("[插件] data.json 结构异常，已备份为 .corrupt.bak，下次读取将按新标准重建")
            return
        if has_embedded_records(data):
            user_data, records = split_records(data)
            _write_json(RECORDS_FILE, records)
            _write_json(DATA_FILE, user_data)
            logger.info(f"[插件] 数据系统升级：记录数据已拆分到 {os.path.basename(RECORDS_FILE)}"
                        f"（pets {len(records.get('pets') or {})} / users {len(records.get('users') or {})} / 价格记录 {len(records.get('shop_price_records') or [])}）")
        elif not os.path.exists(RECORDS_FILE):
            _write_json(RECORDS_FILE, {})
            logger.info("[插件] 数据系统检查：已补建记录数据文件 records.json")
    except Exception as e:
        logger.error(f"[插件] 数据系统升级失败: {e}")
