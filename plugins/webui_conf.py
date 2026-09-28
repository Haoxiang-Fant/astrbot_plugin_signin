# -*- coding: utf-8 -*-
"""WebUI 后台配置数据 Web API（3.0.0 从 2.3.0 modules/webui.py 的配置数据类端点移植）。

本模块是辅助模块（非挂载插件：无 NAME / register(core) 插件契约），由 webui 插件调用
register(core, lan_gate=None) 完成 Web API 注册（lan_gate 可选，用于统一包装处理器，
如局域网访问门；未提供时注册裸处理器）。端点（路径前缀 /{PLUGIN_NAME}/）：
    backend/config          GET   读取打工/玩耍数值（结构化，表格编辑）        ← web_get_backend_config
    backend/config          POST  保存打工/玩耍数值                            ← web_save_backend_config
    petshop                 GET   读取宠物商店商品                              ← web_get_petshop
    petshop                 POST  保存宠物商店商品                              ← web_save_petshop
    farm/crops              GET   读取作物配置                                  ← web_get_crops
    farm/crops              POST  保存作物配置                                  ← web_save_crops
    farm/ferts              GET   读取肥料配置                                  ← web_get_ferts
    farm/ferts              POST  保存肥料配置                                  ← web_save_ferts
    items/apply_benchmark   POST  一键恢复默认道具数据（Benchmark 模板）        ← web_apply_benchmark_items
    loan/packages           GET   读取贷款套餐                                  ← web_get_loan_pkgs
    loan/packages           POST  保存贷款套餐                                  ← web_save_loan_pkgs
    config/draft            GET   读取「待保存」容灾草稿                        ← web_get_config_draft
    config/draft            POST  暂存/清除容灾草稿                             ← web_save_config_draft
    history/list            GET   历史配置版本列表 + 保留开关状态               ← web_history_list
    history/toggle          POST  开关历史数据保留                              ← web_history_toggle
    history/captcha         POST  生成回溯验证码（5 分钟有效，会话单实例）      ← web_history_captcha
    history/verify          POST  回溯第一步校验（验证码 + 管理员密码）         ← web_history_verify
    history/rollback        POST  回溯（校验后覆盖配置数据，回溯前自动备份）    ← web_history_rollback
    history/delete          POST  删除某个历史版本                              ← web_history_delete

与 2.3.0 的差异（JSON 回复形状保持一致，pages/admin 前端无需改动）：
  - self._load() → core.data；self._save(d) → core.save()；self._lock → core.lock；
    self._read_items_json() → core.items()；self._write_items_json(flat) → core.save_items(flat)。
  - 3.0 数值配置常驻 core 缓存（core.items() 永不返回 None），「game_items.json 不存在回退
    解析 txt」的后台/商店分支消失；farm/crops、farm/ferts、loan/packages 的 GET 在对应段为空时
    回退解析旧版 txt（与 farm/loans 插件口径一致，同 webui_records._load_crops）。
  - 2.3.0 的 export/import、查看/保存后台配置（txt 文本）端点不在本模块：
    3.0 由 datamgr 插件拥有（core.service("datamgr") 的 export_files/import_files/
    backend_text/save_backend_text），本模块端点与其无重叠。
  - 历史保留开关 3.0 存于 data["lan"]["history_enabled"]（2.3.0 顶层 history_keep 兼容读取）。
  - 历史快照的设置类键 _HISTORY_CONFIG_KEYS：feature_switches → features（3.0 核心数据命名）。
  - 回溯后的 _apply_runtime_params → 逐键 core.set_param（同步 core 与已挂载插件模块全局）。
  - 回溯 verify/rollback 的管理员密码校验就地移植 _lan_verify_password（PBKDF2-SHA256），
    读 data["lan"]["password_hash"]（与 2.3.0 lan.py 同算法，不依赖 webui 插件内部实现）。
  - _history_capture/_backup/_verify_captcha/_restore 保持为模块级函数（操作 core.data/路径），
    验证码会话为模块级单实例 _HISTORY_CAPTCHA（2.3.0 挂在实例 self._history_captcha）。
"""
import hashlib
import hmac
import json
import os
import secrets
import string as _string
from datetime import datetime

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from ..core import (
    PLUGIN_NAME,
    PET_SHOP_TYPES,
    ITEMS_KEYS,
    LAN_DATA_KEY,
    ITEMS_JSON_FILE,
    BENCH_ITEMS_FILE,
    CROP_FILE,
    FERT_FILE,
    LOAN_FILE,
    DRAFT_FILE,
    HISTORY_DIR,
)

__all__ = ["ENDPOINTS", "register"]

_CORE = None            # register(core) 时注入（处理器经它访问 core.lock / core.data / core.items 等）
_HISTORY_CAPTCHA = None  # 回溯验证码会话：{"file": 文件名, "code": 6 位, "exp": 过期时间戳}


def _core():
    return _CORE


def _f(v):
    """宽松转 float（失败回 0，与 2.3.0 self._f 一致）"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ================= 数值配置校验/规范化（2.3.0 webui.py / farm.py / loans.py 移植） =================
# 数值字段清单（2.3.0 webui.py 214-224 同款）
_JOB_NUM_FIELDS = ("min_level", "min_health", "min_mood",
                   "cost_stamina", "cost_satiety", "cost_thirst", "cost_health", "cost_mood",
                   "time", "coins", "exp")
_PLAY_NUM_FIELDS = ("min_level", "min_health", "min_mood",
                    "cost_stamina", "cost_satiety", "cost_thirst", "cost_health", "cost_mood",
                    "time", "exp", "mood", "stamina", "health")
_SHOP_NUM_FIELDS = ("price", "satiety", "thirst", "stamina", "mood", "health")
_CROP_NUM_FIELDS = ("seed_price", "seed_sell_price", "yield", "crop_price",
                    "exp", "min_level", "grow_minutes")
_FERT_NUM_FIELDS = ("price", "yield_add", "max_accel")
_LOAN_NUM_FIELDS = ("max_amount", "fav_req", "pet_req", "farm_req", "rate")


def _read_items_flat(core):
    """game_items.json 全量扁平结构（3.0：core.items() 为常驻缓存，这里浅拷贝六个键，
    避免处理器直接改动缓存对象；保存仍走 core.save_items 覆盖缓存与磁盘）。"""
    src = core.items() or {}
    return {k: list(src.get(k) or []) for k in ITEMS_KEYS}


def _validate_flat_items(entries, num_fields, kind_label, check_type=False, code_mode=False):
    """校验并规范化扁平条目列表。code_mode=True 时以 code（3~10 整数）代替名称（贷款套餐）。
    返回 (clean_list, errors_dict)"""
    clean = []
    errors = {}
    seen = set()
    for i, e in enumerate(entries or []):
        row = f"第{i + 1}行"
        if not isinstance(e, dict):
            errors[row] = "条目格式错误"
            continue
        if code_mode:
            try:
                code = int(str(e.get("code", "")).strip())
            except (TypeError, ValueError):
                errors[row] = "套餐代码必须是 3~10 的整数"
                continue
            if not 3 <= code <= 10:
                errors[row] = f"套餐代码 {code} 超出范围（3~10）"
                continue
            if code in seen:
                errors[f"套餐{code}（{row}）"] = "代码重复"
                continue
            seen.add(code)
            item = {"code": code, "desc": str(e.get("desc", "") or "")}
            row = f"套餐{code}（{row}）"
        else:
            name = str(e.get("name", "")).strip()
            if not name:
                errors[row] = "名称不能为空"
                continue
            if name in seen:
                errors[f"{name}（{row}）"] = "名称重复"
                continue
            seen.add(name)
            item = {"name": name, "desc": str(e.get("desc", "") or "")}
            row = f"{name}（{row}）"
        bad = False
        for f_ in num_fields:
            v = e.get(f_, 0)
            if isinstance(v, str):
                v = v.strip()
            try:
                fv = float(v) if str(v) != "" else 0.0
            except (TypeError, ValueError):
                errors[row] = f"数值字段 {f_} 不是数字"
                bad = True
                break
            item[f_] = int(fv) if fv == int(fv) else fv
        if bad:
            continue
        if check_type:
            typ = str(e.get("type", "") or "").strip()
            if typ == "食品":
                typ = "食物"
            if typ not in PET_SHOP_TYPES:
                errors[row] = f"类型必须是 {'/'.join(PET_SHOP_TYPES)}"
                continue
            item["type"] = typ
        clean.append(item)
    return clean, errors


def _norm_crop_entry(d):
    """规范化一条作物配置（扁平 dict，键与 game_items.json 一致；2.3.0 farm.py 同款）"""
    if not isinstance(d, dict):
        return None
    name = str(d.get("name", "")).strip()
    if not name:
        return None
    return {
        "name": name,
        "desc": str(d.get("desc", "") or ""),
        "seed_price": _f(d.get("seed_price", 0)),
        "seed_sell_price": _f(d.get("seed_sell_price", 0)),
        "yield": int(_f(d.get("yield", 0))),
        "crop_price": _f(d.get("crop_price", 0)),
        "exp": int(_f(d.get("exp", 0))),
        "min_level": int(_f(d.get("min_level", 0))),
        "grow_minutes": int(_f(d.get("grow_minutes", 0))),
    }


def _norm_fert_entry(d):
    """规范化一条肥料配置（2.0.0：max_accel 为可加速次数，替代 max_uses；2.3.0 farm.py 同款）"""
    if not isinstance(d, dict):
        return None
    name = str(d.get("name", "")).strip()
    if not name:
        return None
    max_accel = d.get("max_accel", d.get("max_uses", -1))
    try:
        max_accel = int(float(max_accel))
    except (TypeError, ValueError):
        max_accel = -1
    return {
        "name": name,
        "desc": str(d.get("desc", "") or ""),
        "price": int(_f(d.get("price", 0))),
        "time_reduce": _f(d.get("time_reduce", 0)),
        "yield_add": _f(d.get("yield_add", 0)),
        "max_uses": int(_f(d.get("max_uses", -1))),
        "max_accel": max_accel,
    }


def _norm_loan_entry(d):
    """规范化一条贷款套餐（扁平 dict：code + 5 个数值字段；2.3.0 loans.py 同款）"""
    if not isinstance(d, dict):
        return None
    try:
        code = int(str(d.get("code", "")).strip())
    except (TypeError, ValueError):
        return None
    if not 3 <= code <= 10:
        return None
    return {
        "code": code,
        "desc": str(d.get("desc", "") or ""),
        "max_amount": int(_f(d.get("max_amount", 0))),
        "fav_req": int(_f(d.get("fav_req", 0))),
        "pet_req": int(_f(d.get("pet_req", 0))),
        "farm_req": int(_f(d.get("farm_req", 0))),
        "rate": _f(d.get("rate", 0)),
    }


def _parse_crop_fert(core, path: str, kind: str):
    """从 作物.txt / 肥料.txt 解析作物与肥料（txt 不存在时返回空列表；2.3.0 farm.py 同款）"""
    items = core.parse_kv_sections(path, kind)
    result = []
    for it in items:
        d = it["data"]
        if kind == "作物":
            result.append({
                "name": it["name"],
                "desc": d.get("描述", ""),
                "seed_price": _f(d.get("种子价格", 0)),
                "seed_sell_price": _f(d.get("种子卖出价格", 0)),
                "yield": int(_f(d.get("产量", 0))),
                "crop_price": _f(d.get("成熟作物价格", 0)),
                "exp": int(_f(d.get("收获经验值", 0))),
                "min_level": int(_f(d.get("最低农场等级要求", 0))),
                "grow_minutes": int(_f(d.get("成熟时间", 0))),
            })
        else:
            result.append({
                "name": it["name"],
                "desc": d.get("描述", ""),
                "price": int(_f(d.get("肥料价格", 0))),
                "time_reduce": _f(d.get("减少时间", 0)),
                "yield_add": _f(d.get("增加产量", 0)),
                "max_uses": int(_f(d.get("最大使用次数", -1))),
                "max_accel": int(_f(d.get("可加速次数", d.get("最大使用次数", -1)))),
            })
    return result


def _loans_from_txt(core):
    """从 贷款套餐.txt 解析贷款套餐（txt 不存在时返回空列表；2.3.0 webui.py 同款）"""
    if not os.path.exists(LOAN_FILE):
        return []
    out = []
    for it in core.parse_kv_sections(LOAN_FILE, "贷款套餐"):
        d = it["data"]
        try:
            out.append({"code": int(it["name"]), "desc": d.get("描述", ""),
                        "max_amount": int(_f(d.get("最大金额", 0))),
                        "fav_req": int(_f(d.get("好感度等级要求", 0))),
                        "pet_req": int(_f(d.get("宠物等级要求", 0))),
                        "farm_req": int(_f(d.get("农场等级要求", 0))),
                        "rate": _f(d.get("利息", 0))})
        except Exception:
            continue
    return out


def _load_benchmark_items():
    """读取 Benchmark data/game_items.json（默认数值模板）；不存在/损坏返回 None"""
    try:
        if not os.path.exists(BENCH_ITEMS_FILE):
            return None
        with open(BENCH_ITEMS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return None
        return {k: raw.get(k) if isinstance(raw.get(k), list) else [] for k in ITEMS_KEYS}
    except Exception as e:
        logger.warning(f"[插件] 读取 Benchmark data/game_items.json 失败: {e}")
        return None


# ================= Web API：backend/config + petshop（2.3.0 webui.py 791-846 移植） =================
async def web_get_backend_config():
    """读取打工/玩耍数值（结构化，供 WebUI 表格编辑）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        flat = core.items()
        return json_response({"jobs": flat.get("jobs") or [], "plays": flat.get("plays") or []})


async def web_save_backend_config():
    """保存打工/玩耍数值：{jobs: [...], plays: [...]}，校验后写入 game_items.json（立即生效）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        jobs_in = payload.get("jobs")
        plays_in = payload.get("plays")
        if not isinstance(jobs_in, list) or not isinstance(plays_in, list):
            return error_response("jobs 和 plays 必须是数组", status_code=400)
        jobs, errs1 = _validate_flat_items(jobs_in, _JOB_NUM_FIELDS, "打工")
        plays, errs2 = _validate_flat_items(plays_in, _PLAY_NUM_FIELDS, "玩耍")
        errors = {**{f"打工·{k}": v for k, v in errs1.items()},
                  **{f"玩耍·{k}": v for k, v in errs2.items()}}
        if errors:
            return json_response({"saved": False, "errors": errors})
        flat = _read_items_flat(core)
        _history_backup(core, core.data, "保存前自动备份")
        flat["jobs"] = jobs
        flat["plays"] = plays
        ok, msg = core.save_items(flat)
        if not ok:
            return error_response(msg, status_code=400)
        return json_response({"saved": True, "jobs": len(jobs), "plays": len(plays)})


async def web_get_petshop():
    """读取宠物商店商品（结构化，供 WebUI 表格编辑）。2.2.0：不再返回冗余空字段 types/content。"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        flat = core.items()
        return json_response({"items": flat.get("shop") or []})


async def web_save_petshop():
    """保存宠物商店商品：{items: [...]}，校验后写入 game_items.json（立即生效）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        items_in = payload.get("items")
        if not isinstance(items_in, list):
            return error_response("items 必须是数组", status_code=400)
        shop, errors = _validate_flat_items(items_in, _SHOP_NUM_FIELDS, "商店", check_type=True)
        if errors:
            return json_response({"saved": False, "errors": errors})
        flat = _read_items_flat(core)
        _history_backup(core, core.data, "保存前自动备份")
        flat["shop"] = shop
        ok, msg = core.save_items(flat)
        if not ok:
            return error_response(msg, status_code=400)
        return json_response({"saved": True, "items": len(shop)})


# ================= 端点清单与注册 =================
# 与 2.3.0 main.py 的 _web_apis 中配置数据类条目对应（顺序一致）。
# 注意：清单登记的是裸处理器；register(core, lan_gate) 按清单注册并逐项套 lan_gate（可选）。
ENDPOINTS = [
    ("backend/config", "GET", web_get_backend_config, "读取打工/玩耍数值"),
    ("backend/config", "POST", web_save_backend_config, "保存打工/玩耍数值"),
    ("petshop", "GET", web_get_petshop, "读取宠物商店商品"),
    ("petshop", "POST", web_save_petshop, "保存宠物商店商品"),
]


def register(core, lan_gate=None):
    """注册后台配置数据类 Web API（由 webui 插件调用；lan_gate 可选，用于包装处理器）"""
    global _CORE
    _CORE = core
    wrap = (lambda h: h) if lan_gate is None else lan_gate
    for path, method, handler, desc in ENDPOINTS:
        core.context.register_web_api(f"/{PLUGIN_NAME}/{path}", wrap(handler), [method], desc)
    return [(path, method) for path, method, _h, _d in ENDPOINTS]


# ================= Web API：farm/crops + farm/ferts + items/apply_benchmark（2.3.0 webui.py 1114-1191 移植） =================
async def web_get_crops():
    """读取作物配置（结构化，供 WebUI 表格编辑）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        flat = core.items()
        items = [c for c in (_norm_crop_entry(d) for d in (flat.get("crops") or [])) if c]
        if not items:  # 3.0：数值段为空时回退解析 作物.txt（同 farm 插件口径）
            items = _parse_crop_fert(core, CROP_FILE, "作物")
        return json_response({"items": items})


async def web_save_crops():
    """保存作物配置：{items: [...]}，校验后写入 game_items.json（立即生效）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        items_in = payload.get("items")
        if not isinstance(items_in, list):
            return error_response("items 必须是数组", status_code=400)
        crops, errors = _validate_flat_items(items_in, _CROP_NUM_FIELDS, "作物")
        if errors:
            return json_response({"saved": False, "errors": errors})
        flat = _read_items_flat(core)
        _history_backup(core, core.data, "保存前自动备份")
        flat["crops"] = [_norm_crop_entry(c) for c in crops]
        ok, msg = core.save_items(flat)
        if not ok:
            return error_response(msg, status_code=400)
        return json_response({"saved": True, "items": len(crops)})


async def web_get_ferts():
    """读取肥料配置（结构化，供 WebUI 表格编辑）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        flat = core.items()
        items = [f_ for f_ in (_norm_fert_entry(d) for d in (flat.get("ferts") or [])) if f_]
        if not items:  # 3.0：数值段为空时回退解析 肥料.txt（同 farm 插件口径）
            items = _parse_crop_fert(core, FERT_FILE, "肥料")
        return json_response({"items": items})


async def web_save_ferts():
    """保存肥料配置：{items: [...]}，校验后写入 game_items.json（立即生效）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        items_in = payload.get("items")
        if not isinstance(items_in, list):
            return error_response("items 必须是数组", status_code=400)
        ferts, errors = _validate_flat_items(items_in, _FERT_NUM_FIELDS, "肥料")
        if errors:
            return json_response({"saved": False, "errors": errors})
        flat = _read_items_flat(core)
        _history_backup(core, core.data, "保存前自动备份")
        flat["ferts"] = [_norm_fert_entry(f_) for f_ in ferts]
        ok, msg = core.save_items(flat)
        if not ok:
            return error_response(msg, status_code=400)
        return json_response({"saved": True, "items": len(ferts)})


async def web_apply_benchmark_items():
    """一键恢复默认道具数据：农场（作物/肥料）与宠物（商店商品）使用
    Benchmark data/game_items.json 的数值；打工/玩耍不受影响。"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        bench = _load_benchmark_items()
        if bench is None or not (bench["shop"] or bench["crops"] or bench["ferts"]):
            return error_response("Benchmark data/game_items.json 不存在或为空", status_code=400)
        shop, errors = _validate_flat_items(bench["shop"], _SHOP_NUM_FIELDS, "商店", check_type=True)
        if errors:
            return json_response({"saved": False, "errors": errors})
        crops = [c for c in (_norm_crop_entry(d) for d in bench["crops"]) if c]
        ferts = [f_ for f_ in (_norm_fert_entry(d) for d in bench["ferts"]) if f_]
        flat = _read_items_flat(core)
        _history_backup(core, core.data, "保存前自动备份")
        flat["shop"] = shop
        flat["crops"] = crops
        flat["ferts"] = ferts
        ok, msg = core.save_items(flat)
        if not ok:
            return error_response(msg, status_code=400)
        logger.info(f"[插件] 已应用 Benchmark 默认道具数据：商店 {len(shop)} / 作物 {len(crops)} / 肥料 {len(ferts)} 条")
        return json_response({"saved": True, "shop": len(shop), "crops": len(crops), "ferts": len(ferts)})


ENDPOINTS.extend([
    ("farm/crops", "GET", web_get_crops, "读取作物配置"),
    ("farm/crops", "POST", web_save_crops, "保存作物配置"),
    ("farm/ferts", "GET", web_get_ferts, "读取肥料配置"),
    ("farm/ferts", "POST", web_save_ferts, "保存肥料配置"),
    ("items/apply_benchmark", "POST", web_apply_benchmark_items, "一键恢复默认道具数据（Benchmark 模板）"),
])

# ================= Web API：loan/packages（2.3.0 webui.py 1193-1201 + 1268-1285 移植） =================
async def web_get_loan_pkgs():
    """读取贷款套餐（结构化，供 WebUI 表格编辑）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        flat = core.items()
        items = [l for l in (_norm_loan_entry(d) for d in (flat.get("loans") or [])) if l]
        if not items:  # 3.0：数值段为空时回退解析 贷款套餐.txt（同 loans 插件口径）
            items = _loans_from_txt(core)
        return json_response({"items": items})


async def web_save_loan_pkgs():
    """保存贷款套餐：{items: [{code, max_amount, fav_req, pet_req, farm_req, rate}]}，
    校验（代码 3~10 整数且不重复）后写入 game_items.json（立即生效）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        items_in = payload.get("items")
        if not isinstance(items_in, list):
            return error_response("items 必须是数组", status_code=400)
        loans, errors = _validate_flat_items(items_in, _LOAN_NUM_FIELDS, "贷款套餐", code_mode=True)
        if errors:
            return json_response({"saved": False, "errors": errors})
        flat = _read_items_flat(core)
        _history_backup(core, core.data, "保存前自动备份")
        flat["loans"] = loans
        ok, msg = core.save_items(flat)
        if not ok:
            return error_response(msg, status_code=400)
        return json_response({"saved": True, "items": len(loans)})


# ================= 后台数据「待保存」容灾草稿（2.2.2；2.3.0 webui.py 1287-1342 移植） =================
# 管理员在待保存状态下离开 WebUI 时，前端把未保存修改暂存到 DRAFT_FILE；
# 下次访问时前端读取草稿并弹窗询问是否保存。
_DRAFT_ENDPOINTS = ("backend/config", "petshop", "farm/crops", "farm/ferts", "loan/packages",
                    "feature/status", "params", "activities", "alias/save")


def _read_draft():
    try:
        if not os.path.exists(DRAFT_FILE):
            return None
        with open(DRAFT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("payloads") else None
    except Exception:
        return None


def _write_draft(obj) -> None:
    try:
        if obj is None:
            if os.path.exists(DRAFT_FILE):
                os.remove(DRAFT_FILE)
            return
        tmp = DRAFT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DRAFT_FILE)
    except Exception as e:
        logger.error(f"[插件] 写入容灾草稿失败: {e}")


async def web_get_config_draft():
    """读取容灾草稿（上次未保存的修改，无则 null）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        return json_response({"draft": _read_draft()})


async def web_save_config_draft():
    """暂存/清除容灾草稿：POST {payloads: {面板: {endpoint, payload}}} 或 {clear: true}。
    只接受已知配置端点，避免任意内容写入临时文档。"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        if payload.get("clear"):
            _write_draft(None)
            return json_response({"cleared": True})
        payloads = payload.get("payloads")
        if not isinstance(payloads, dict) or not payloads:
            return error_response("payloads 必须是非空对象", status_code=400)
        cleaned = {}
        for pid, item in payloads.items():
            if not isinstance(item, dict):
                continue
            ep = str(item.get("endpoint", ""))
            if ep in _DRAFT_ENDPOINTS:
                cleaned[str(pid)[:40]] = {"endpoint": ep, "payload": item.get("payload")}
        if not cleaned:
            return error_response("没有可暂存的数据", status_code=400)
        _write_draft({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "payloads": cleaned})
        return json_response({"saved": True})


ENDPOINTS.extend([
    ("loan/packages", "GET", web_get_loan_pkgs, "读取贷款套餐"),
    ("loan/packages", "POST", web_save_loan_pkgs, "保存贷款套餐"),
    ("config/draft", "GET", web_get_config_draft, "读取「待保存」容灾草稿"),
    ("config/draft", "POST", web_save_config_draft, "暂存/清除容灾草稿"),
])

# ================= 历史配置数据（2.2.2：historydata/setting；2.3.0 webui.py 1344-1534 移植） =================
# 配置数据 = data.json 的设置类键 + game_items.json（商店/打工/玩耍/作物/肥料/贷款套餐）；
# 用户/游戏存档数据（users/pets/bank/farms/loans/roulette/ledger 等）不进历史、不被回溯覆盖。
_HISTORY_CONFIG_KEYS = ("params", "alias_cmds", "activities", "activity_config", "features")
# 2.3.0 敏感运行参数：不进历史快照、回溯时保留当前值
_SENSITIVE_PARAMS = ("DEBUG_PASSWORD",)
# ponytail: 固定保留最近 30 个版本，超出丢弃最旧；需要可配置再加设置项
_HISTORY_KEEP = 30


def _history_enabled(data: dict) -> bool:
    """历史数据保留开关（3.0 存于 data["lan"]["history_enabled"]；兼容 2.3.0 顶层 history_keep）"""
    lan = data.get(LAN_DATA_KEY)
    v = lan.get("history_enabled") if isinstance(lan, dict) else None
    if v is None:
        v = data.get("history_keep")
    return bool(v)


def _lan_verify_password(stored, password: str) -> bool:
    """校验密码：stored 形如 'salt$hash'（PBKDF2-SHA256，2.3.0 base.py 同算法），否则直接失败。"""
    if not isinstance(stored, str) or not isinstance(password, str):
        return False
    if "$" not in stored:
        return False
    salt, want = stored.split("$", 1)
    try:
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  bytes.fromhex(salt), 120_000).hex()
    except Exception:
        return False
    return hmac.compare_digest(got, want)


def _history_capture(core, data: dict) -> dict:
    """抓取当前配置数据快照（敏感参数脱敏，不进历史）"""
    cfg = {}
    for k in _HISTORY_CONFIG_KEYS:
        if k in data:
            cfg[k] = data[k]
    if isinstance(cfg.get("params"), dict):
        cfg["params"] = {k: v for k, v in cfg["params"].items() if k not in _SENSITIVE_PARAMS}
    items = None
    try:
        if os.path.exists(ITEMS_JSON_FILE):
            with open(ITEMS_JSON_FILE, "r", encoding="utf-8") as f:
                items = json.load(f)
    except Exception:
        items = None
    return {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "items": items, "config": cfg}


def _history_backup(core, data: dict, reason: str) -> None:
    """把当前配置数据存为一个历史版本（开关 data["lan"]["history_enabled"] 开启时记录；
    回溯前的自动备份不受开关限制，始终记录）"""
    try:
        if not _history_enabled(data) and reason != "回溯前自动备份":
            return
        os.makedirs(HISTORY_DIR, exist_ok=True)
        snap = _history_capture(core, data)
        snap["reason"] = reason
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(3)
        tmp = os.path.join(HISTORY_DIR, ts + ".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(HISTORY_DIR, ts + ".json"))
        files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".json"))
        for old in files[: max(0, len(files) - _HISTORY_KEEP)]:
            try:
                os.remove(os.path.join(HISTORY_DIR, old))
            except OSError:
                pass
    except Exception as e:
        logger.error(f"[插件] 保存历史配置失败: {e}")


def _history_verify_captcha(file: str, code) -> tuple:
    """校验回溯验证码（与发起时生成的版本一一对应，5 分钟有效；会话为模块级单实例）"""
    st = _HISTORY_CAPTCHA
    if not isinstance(st, dict) or st.get("code") != str(code or ""):
        return False, "验证码不正确，请重新获取"
    if st.get("file") != file:
        return False, "验证码与所选版本不匹配"
    if datetime.now().timestamp() > float(st.get("exp", 0) or 0):
        return False, "验证码已过期，请重新获取"
    return True, ""


def _history_restore(core, file: str) -> tuple:
    """回溯到指定版本：先把当前配置自动备份为独立版本，再用快照覆盖配置数据（用户存档保留）"""
    path = os.path.join(HISTORY_DIR, os.path.basename(file))
    if not os.path.isfile(path):
        return False, "历史版本不存在"
    try:
        with open(path, "r", encoding="utf-8") as f:
            snap = json.load(f)
    except Exception as e:
        return False, f"读取历史版本失败: {e}"
    if not isinstance(snap, dict):
        return False, "历史版本数据损坏"
    # 回溯正式开始前：当前配置自动备份为单独版本（无论开关是否开启）
    _history_backup(core, core.data, "回溯前自动备份")
    items = snap.get("items")
    if isinstance(items, dict):
        ok, msg = core.save_items(items)
        if not ok:
            return False, msg
    data = core.data
    cur_params = dict(data.get("params") or {})
    cfg = snap.get("config") if isinstance(snap.get("config"), dict) else {}
    for k in _HISTORY_CONFIG_KEYS:
        if k in cfg:
            data[k] = cfg[k]
    # 快照里被脱敏的敏感参数（调试口令等）保留当前值，不被清空
    if isinstance(data.get("params"), dict):
        for k in _SENSITIVE_PARAMS:
            if k in cur_params and k not in data["params"]:
                data["params"][k] = cur_params[k]
    core.save()
    # 3.0：回溯后的运行参数立即生效（2.3.0 _apply_runtime_params 等价：同步 core 与已挂载插件模块全局）
    try:
        for k, v in (data.get("params") or {}).items():
            core.set_param(k, v)
    except Exception:
        pass
    return True, "已回溯到 " + str(snap.get("time") or os.path.basename(file))


async def web_history_list():
    """读取历史配置版本列表 + 保留开关状态"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        data = core.data
        versions = []
        try:
            if os.path.isdir(HISTORY_DIR):
                for f in sorted((x for x in os.listdir(HISTORY_DIR) if x.endswith(".json")), reverse=True):
                    info = {"file": f, "time": "", "reason": ""}
                    try:
                        with open(os.path.join(HISTORY_DIR, f), "r", encoding="utf-8") as fh:
                            d = json.load(fh)
                        if isinstance(d, dict):
                            info["time"] = str(d.get("time") or "")
                            info["reason"] = str(d.get("reason") or "")
                    except Exception:
                        pass
                    versions.append(info)
        except Exception as e:
            logger.error(f"[插件] 读取历史配置列表失败: {e}")
        return json_response({"enabled": _history_enabled(data), "versions": versions})


async def web_history_toggle():
    """开关历史数据保留功能：POST {enabled: bool}（设置 → 数据导入导出）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        data = core.data
        enabled = bool(payload.get("enabled"))
        lan = data.get(LAN_DATA_KEY)
        if not isinstance(lan, dict):
            lan = {}
            data[LAN_DATA_KEY] = lan
        lan["history_enabled"] = enabled
        core.save()
        return json_response({"saved": True, "enabled": enabled})


async def web_history_captcha():
    """生成回溯验证码（6 位数字 + 大小写字母），5 分钟内有效，仅对指定版本可用"""
    global _HISTORY_CAPTCHA
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        file = str(payload.get("file") or "")
        if not file.endswith(".json") or "/" in file or "\\" in file \
                or not os.path.isfile(os.path.join(HISTORY_DIR, file)):
            return error_response("历史版本不存在", status_code=400)
        code = "".join(secrets.choice(_string.ascii_letters + _string.digits) for _ in range(6))
        _HISTORY_CAPTCHA = {"file": file, "code": code, "exp": datetime.now().timestamp() + 300}
        return json_response({"captcha": code})


async def web_history_verify():
    """回溯第一步校验：只校验验证码与管理员密码，不执行回溯（第二步再发 rollback）"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        file = str(payload.get("file") or "")
        if not file.endswith(".json") or "/" in file or "\\" in file:
            return error_response("参数不合法", status_code=400)
        ok, msg = _history_verify_captcha(file, payload.get("captcha"))
        if not ok:
            return error_response(msg, status_code=400)
        data = core.data
        lan = data.get(LAN_DATA_KEY) or {}
        if lan.get("password_hash") and not _lan_verify_password(lan["password_hash"], str(payload.get("password") or "")):
            return error_response("管理员密码不正确", status_code=400)
        return json_response({"verified": True})


async def web_history_rollback():
    """回溯：POST {file, password, captcha}。前端需先点第一个确认按钮（校验），
    再点第二个确认按钮发起本请求；校验验证码 + 管理员密码
    （未设置局域网访问密码时仅校验验证码）。"""
    global _HISTORY_CAPTCHA
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        file = str(payload.get("file") or "")
        if not file.endswith(".json") or "/" in file or "\\" in file:
            return error_response("参数不合法", status_code=400)
        ok, msg = _history_verify_captcha(file, payload.get("captcha"))
        if not ok:
            return error_response(msg, status_code=400)
        data = core.data
        lan = data.get(LAN_DATA_KEY) or {}
        if lan.get("password_hash") and not _lan_verify_password(lan["password_hash"], str(payload.get("password") or "")):
            return error_response("管理员密码不正确", status_code=400)
        ok, msg = _history_restore(core, file)
        if not ok:
            return error_response(msg, status_code=400)
        _HISTORY_CAPTCHA = None
        return json_response({"restored": True, "msg": msg})


async def web_history_delete():
    """删除某个历史版本：POST {file}"""
    core = _core()
    if core is None:
        return error_response("后台配置模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        file = os.path.basename(str(payload.get("file") or ""))
        if not file.endswith(".json"):
            return error_response("参数不合法", status_code=400)
        path = os.path.join(HISTORY_DIR, file)
        if not os.path.isfile(path):
            return error_response("历史版本不存在", status_code=400)
        try:
            os.remove(path)
        except OSError as e:
            return error_response(f"删除失败: {e}", status_code=400)
        return json_response({"deleted": True})


ENDPOINTS.extend([
    ("history/list", "GET", web_history_list, "历史配置版本列表 + 保留开关状态"),
    ("history/toggle", "POST", web_history_toggle, "开关历史数据保留"),
    ("history/captcha", "POST", web_history_captcha, "生成回溯验证码"),
    ("history/verify", "POST", web_history_verify, "回溯第一步校验（验证码 + 管理员密码）"),
    ("history/rollback", "POST", web_history_rollback, "回溯到指定历史版本（回溯前自动备份）"),
    ("history/delete", "POST", web_history_delete, "删除某个历史版本"),
])
