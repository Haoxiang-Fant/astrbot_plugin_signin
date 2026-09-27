# -*- coding: utf-8 -*-
"""数据格式转化插件。

数据布局演化：
- 2.2.2 标准：data.json（用户数据）+ records.json（记录数据）分文件；
- 3.0 标准：runtime.json（群共享）+ settings.json + params.json + shop_prices.json
  + user_data/<uid>.json（每用户一个文件，内含 user/pet/farm/bank/loans/roulette/ledger）。

本插件负责：
1. 旧布局一次性转译为新布局（convert_legacy_to_new；旧文件保留不删，回滚即用）；
2. split_records / merge_records 兼容函数保留（数据管理插件导出旧格式包时使用）。
"""
import copy
import json
import os

from astrbot.api import logger

NAME = "format_convert"

from ..core import (DATA_FILE, RECORDS_FILE, SYSTEM_FILE, SETTINGS_FILE, PARAMS_FILE,  # noqa: E402
                    SHOP_PRICES_FILE, USER_DATA_DIR, _SETTINGS_NS, _RUNTIME_NS,
                    _USER_FILE_NS, _safe_uid, _read_json, _write_json)


def register(core):
    core.expose("format_convert", _Api())


class _Api:
    """数据格式转化服务（主要供核心与数据管理插件调用；函数式接口直接从模块导入亦可）"""

    @staticmethod
    def split(data):
        return split_records(data)

    @staticmethod
    def merge(data, records):
        merge_records(data, records)

    @staticmethod
    def check():
        check_and_convert()

    @staticmethod
    def convert():
        return convert_legacy_to_new()


def read_json(path):
    return _read_json(path)


def _write_json_local(path, obj):
    _write_json(path, obj)


def has_embedded_records(data) -> bool:
    """旧标准判断：记录字段是否内嵌在 data 里。"""
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("shop_price_records"), list):
        return True
    for pet in (data.get("pets") or {}).values():
        if isinstance(pet, dict) and "attr_log" in pet:
            return True
    for u in (data.get("users") or {}).values():
        if isinstance(u, dict) and any(k in u for k in ("auto_feed_logs", "auto_work_logs", "farm_logs", "signin_logs")):
            return True
    return False


def split_records(data: dict):
    """深拷贝并剥离记录字段 → (用户数据副本, 记录数据字典)。不修改入参。
    （2.2.2 标准的读写桥；3.0 用户记录随 user_data 文件走，仅旧包导出/导入仍使用）"""
    user_data = copy.deepcopy(data)
    records = {}
    if isinstance(user_data.get("shop_price_records"), list):
        records["shop_price_records"] = user_data.pop("shop_price_records")
    for uid, pet in (user_data.get("pets") or {}).items():
        if not isinstance(pet, dict):
            continue
        if "attr_log" in pet:
            records.setdefault("pets", {}).setdefault(uid, {})["attr_log"] = pet.pop("attr_log")
    for uid, u in (user_data.get("users") or {}).items():
        if not isinstance(u, dict):
            continue
        rec = {}
        for f in ("auto_feed_logs", "auto_work_logs", "farm_logs", "signin_logs"):
            if f in u:
                rec[f] = u.pop(f)
        if rec:
            records.setdefault("users", {})[uid] = rec
    return user_data, records


def merge_records(data: dict, records) -> None:
    """把 records 字典的记录字段回填进内存 data（原地修改）。"""
    if not isinstance(records, dict) or not isinstance(data, dict):
        return
    if isinstance(records.get("shop_price_records"), list):
        data["shop_price_records"] = records["shop_price_records"]
    pets = data.get("pets")
    if isinstance(pets, dict):
        for uid, rec in (records.get("pets") or {}).items():
            pet = pets.get(uid)
            if isinstance(pet, dict) and isinstance(rec, dict):
                if isinstance(rec.get("attr_log"), list):
                    pet["attr_log"] = rec["attr_log"]
    users = data.get("users")
    if isinstance(users, dict):
        for uid, rec in (records.get("users") or {}).items():
            u = users.get(uid)
            if isinstance(u, dict) and isinstance(rec, dict):
                for f in ("auto_feed_logs", "auto_work_logs", "farm_logs", "signin_logs"):
                    if isinstance(rec.get(f), list):
                        u[f] = rec[f]


def convert_legacy_to_new() -> bool:
    """旧布局（data.json + records.json）→ 3.0 布局。幂等：runtime.json 已存在则跳过。
    旧文件保留不删（与本插件数据同目录，删除即破坏 2.x 回滚能力）。
    返回 True 表示执行了转换。"""
    if os.path.exists(SYSTEM_FILE):
        return False
    legacy = _read_json(DATA_FILE)
    records = _read_json(RECORDS_FILE)
    if legacy is None and records is None:
        return False  # 全新安装
    data = legacy if isinstance(legacy, dict) else {}
    merge_records(data, records)  # records.json 记录回填后再拆装
    # 系统文件
    _write_json(SYSTEM_FILE, {k: data.get(k) for k in _RUNTIME_NS})
    _write_json(SETTINGS_FILE, {k: data.get(k) for k in _SETTINGS_NS})
    _write_json(PARAMS_FILE, data.get("params") or {})
    if isinstance(data.get("shop_price_records"), list) and data["shop_price_records"]:
        _write_json(SHOP_PRICES_FILE, data["shop_price_records"])
    # 用户文件（跨命名空间聚合到一人一文件）
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    seen = set()
    for ns, fkey in _USER_FILE_NS.items():
        for uid, v in (data.get(ns) or {}).items():
            if v in (None, {}, []):
                continue
            safe = _safe_uid(uid)
            path = os.path.join(USER_DATA_DIR, safe + ".json")
            obj = _read_json(path) or {}
            obj[fkey] = v
            _write_json(path, obj)
            seen.add(safe)
    logger.info(f"[格式转化] 旧布局已转译为 3.0 布局（用户文件 {len(seen)} 个）")
    return True


def check_and_convert() -> None:
    """数据格式检查（每次启动可调用，幂等）：新布局缺失且有旧数据 → 转译。"""
    try:
        convert_legacy_to_new()
    except Exception as e:
        logger.error(f"[格式转化] 数据格式检查失败: {e}")
