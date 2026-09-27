# -*- coding: utf-8 -*-
"""数据管理插件（3.0.0）：后台配置查看/保存 + 数据导出导入（管理员指令）与 WebUI 导出导入后端。

自 2.3.0 main.py 的 _handle_view_config / _handle_save_backend_config / _handle_export_data /
_handle_import_data 与 modules/webui.py 的 _exportable_files / _redact_export_text /
_import_files / _snapshot_sensitive / _restore_sensitive / _read_file / _write_file /
_read_data_text / _write_data_text / _cfg_to_backend_text / _normalize_config /
_items_normalized_to_flat / _items_flat_to_normalized 原样迁移。

3.0 适配：数据经 core.data / core.save（记录数据剥离由 format_convert 插件完成）、
数值配置经 core.items() / core.save_items、txt 段落解析经 core.parse_kv_sections_text；
导入 data.json 后同步替换核心内存数据（2.3.0 每次从磁盘重读，3.0 data 常驻内存）。
"""
import json
import os
from datetime import datetime

from astrbot.api import logger

NAME = "datamgr"

from ..core import (SYSTEM_FILE, SETTINGS_FILE, PARAMS_FILE, SHOP_PRICES_FILE,  # noqa: E402
                    USER_DATA_DIR, BACKUP_DATA_DIR, ITEMS_JSON_FILE, CONFIG_FILE, PET_SHOP_FILE,
                    PET_SHOP_TYPE_FILES, CROP_FILE, FERT_FILE, LOAN_FILE, BENCH_ITEMS_FILE,
                    ITEMS_KEYS, DEFAULT_ALIAS_CMDS, LAN_DATA_KEY)

# 2.2.0：敏感运行参数 —— 导出脱敏 / 导入恢复（与 WebUI 端一致）
_SENSITIVE_PARAMS = ("DEBUG_PASSWORD",)

# 旧版备份里的 txt 数值配置文件名 → 对应路径（导入旧备份时还原并重新迁移）
_LEGACY_TXT_FILES = {
    "后台.txt": CONFIG_FILE,
    "宠物商店.txt": PET_SHOP_FILE,
    "宠物商店-食物.txt": PET_SHOP_TYPE_FILES["食物"],
    "宠物商店-饮料.txt": PET_SHOP_TYPE_FILES["饮料"],
    "宠物商店-药物.txt": PET_SHOP_TYPE_FILES["药物"],
    "宠物商店-玩具.txt": PET_SHOP_TYPE_FILES["玩具"],
    "作物.txt": CROP_FILE,
    "肥料.txt": FERT_FILE,
    "贷款套餐.txt": LOAN_FILE,
}


# ================= 通用小工具 =================
def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fmt_price(v):
    """价格显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0"""
    v = float(v)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _read_file(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    except Exception as e:
        logger.error(f"[数据管理] 读取文件失败: {e}")
    return ""


def _write_file(path, text):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return True, "保存成功"
    except Exception as e:
        return False, f"保存失败: {e}"


# ================= 数值配置：扁平 JSON ↔ 内部规范化（2.3.0 同款） =================
def _items_flat_to_normalized(flat: dict) -> dict:
    """扁平 JSON 结构 → 内部规范化结构（与 _normalize_config 输出一致）"""
    jobs = []
    for j in flat.get("jobs") or []:
        if not isinstance(j, dict):
            continue
        name = str(j.get("name", "")).strip()
        if not name:
            continue
        jobs.append({
            "name": name, "desc": str(j.get("desc", "") or ""),
            "min_level": _f(j.get("min_level", 0)),
            "min_health": _f(j.get("min_health", 0)),
            "min_mood": _f(j.get("min_mood", 0)),
            "cost": {
                "stamina": _f(j.get("cost_stamina", 0)),
                "satiety": _f(j.get("cost_satiety", 0)),
                "thirst": _f(j.get("cost_thirst", 0)),
                "health": _f(j.get("cost_health", 0)),
                "mood": _f(j.get("cost_mood", 0)),
            },
            "time": _f(j.get("time", 0)),
            "coins": _f(j.get("coins", 0)),
            "exp": _f(j.get("exp", 0)),
        })
    plays = []
    for p in flat.get("plays") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        plays.append({
            "name": name, "desc": str(p.get("desc", "") or ""),
            "min_level": _f(p.get("min_level", 0)),
            "min_health": _f(p.get("min_health", 0)),
            "min_mood": _f(p.get("min_mood", 0)),
            "cost": {
                "stamina": _f(p.get("cost_stamina", 0)),
                "satiety": _f(p.get("cost_satiety", 0)),
                "thirst": _f(p.get("cost_thirst", 0)),
                "health": _f(p.get("cost_health", 0)),
                "mood": _f(p.get("cost_mood", 0)),
            },
            "time": _f(p.get("time", 0)),
            "exp": _f(p.get("exp", 0)),
            "mood": _f(p.get("mood", 0)),
            "stamina": _f(p.get("stamina", 0)),
            "health": _f(p.get("health", 0)),
        })
    shop = []
    for s in flat.get("shop") or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name", "")).strip()
        if not name:
            continue
        typ = str(s.get("type", "") or "").strip()
        if typ == "食品":  # 旧类型兼容
            typ = "食物"
        shop.append({
            "name": name, "type": typ, "desc": str(s.get("desc", "") or ""),
            "price": _f(s.get("price", 0)),
            "effects": {
                "satiety": _f(s.get("satiety", 0)),
                "thirst": _f(s.get("thirst", 0)),
                "stamina": _f(s.get("stamina", 0)),
                "mood": _f(s.get("mood", 0)),
                "health": _f(s.get("health", 0)),
            },
        })
    return {"jobs": jobs, "plays": plays, "shop": shop}


def _items_normalized_to_flat(norm: dict) -> dict:
    """内部规范化结构 → 扁平 JSON 结构（迁移用）"""
    jobs = []
    for j in norm.get("jobs") or []:
        c = j.get("cost", {})
        jobs.append({
            "name": j.get("name", ""), "desc": j.get("desc", ""),
            "min_level": j.get("min_level", 0), "min_health": j.get("min_health", 0),
            "min_mood": j.get("min_mood", 0),
            "cost_stamina": c.get("stamina", 0), "cost_satiety": c.get("satiety", 0),
            "cost_thirst": c.get("thirst", 0), "cost_health": c.get("health", 0),
            "cost_mood": c.get("mood", 0),
            "time": j.get("time", 0), "coins": j.get("coins", 0), "exp": j.get("exp", 0),
        })
    plays = []
    for p in norm.get("plays") or []:
        c = p.get("cost", {})
        plays.append({
            "name": p.get("name", ""), "desc": p.get("desc", ""),
            "min_level": p.get("min_level", 0), "min_health": p.get("min_health", 0),
            "min_mood": p.get("min_mood", 0),
            "cost_stamina": c.get("stamina", 0), "cost_satiety": c.get("satiety", 0),
            "cost_thirst": c.get("thirst", 0), "cost_health": c.get("health", 0),
            "cost_mood": c.get("mood", 0),
            "time": p.get("time", 0), "exp": p.get("exp", 0),
            "mood": p.get("mood", 0), "stamina": p.get("stamina", 0),
            "health": p.get("health", 0),
        })
    shop = []
    for s in norm.get("shop") or []:
        e = s.get("effects", {})
        shop.append({
            "name": s.get("name", ""), "type": s.get("type", ""), "desc": s.get("desc", ""),
            "price": s.get("price", 0),
            "satiety": e.get("satiety", 0), "thirst": e.get("thirst", 0),
            "stamina": e.get("stamina", 0), "mood": e.get("mood", 0),
            "health": e.get("health", 0),
        })
    return {"jobs": jobs, "plays": plays, "shop": shop}


def _normalize_config(cfg: dict) -> dict:
    """txt 段落结构 → 内部规范化结构（2.3.0 同款字段名兼容）"""
    def _c(d, *keys):
        """兼容多个字段名取值（如 消耗健康度/消耗健康值/消耗健康）"""
        for k in keys:
            if k in d and str(d[k]).strip() != "":
                return d[k]
        return 0

    jobs = []
    for j in cfg["jobs"]:
        d = j["data"]
        jobs.append({
            "name": j["name"],
            "desc": d.get("描述", ""),
            "min_level": _f(d.get("最低等级", 0)),
            "min_health": _f(d.get("最低健康度", 0)),
            "min_mood": _f(d.get("最低心情值", 0)),
            "cost": {
                "stamina": _f(d.get("消耗体力", 0)),
                "satiety": _f(d.get("消耗饱食度", 0)),
                "thirst": _f(d.get("消耗口渴值", 0)),
                "health": _f(_c(d, "消耗健康度", "消耗健康值", "消耗健康")),
                "mood": _f(d.get("消耗心情值", 0)),
            },
            "time": _f(d.get("需要时间", 0)),
            "coins": _f(d.get("金币", 0)),
            "exp": _f(d.get("经验", 0)),
        })

    plays = []
    for p in cfg["plays"]:
        d = p["data"]
        plays.append({
            "name": p["name"],
            "desc": d.get("描述", ""),
            "min_level": _f(d.get("最低等级", 0)),
            "min_health": _f(d.get("最低健康度", 0)),
            "min_mood": _f(d.get("最低心情值", 0)),
            "cost": {
                "stamina": _f(d.get("消耗体力", 0)),
                "satiety": _f(d.get("消耗饱食度", 0)),
                "thirst": _f(d.get("消耗口渴值", 0)),
                "health": _f(_c(d, "消耗健康度", "消耗健康值", "消耗健康")),
            },
            "time": _f(d.get("需要时间", 0)),
            "exp": _f(d.get("经验", 0)),
            "mood": _f(d.get("心情值", 0)),
            "stamina": _f(d.get("体力", 0)),      # 玩耍收益体力（菜单红字显示）
            "health": _f(d.get("健康度", 0)),     # 玩耍收益健康（可为负；红字显示净变化）
        })

    shop = []
    for s in cfg["shop"]:
        d = s["data"]
        _typ = d.get("类型", "")
        if _typ == "食品":  # 旧类型统一归一为「食物」
            _typ = "食物"
        shop.append({
            "name": s["name"],
            "type": _typ,
            "desc": d.get("描述", ""),
            "price": _f(d.get("价格", 0)),
            "effects": {
                "satiety": _f(d.get("饱食度", 0)),
                "thirst": _f(d.get("口渴值", 0)),
                "stamina": _f(d.get("体力", 0)),
                "mood": _f(d.get("心情值", 0)),
                "health": _f(d.get("健康度", 0)),
            },
        })

    return {"jobs": jobs, "plays": plays, "shop": shop}


def _cfg_to_backend_text(core) -> str:
    """把当前打工/玩耍数值渲染为旧版 后台.txt 格式文本（「查看后台配置」指令用）"""
    norm = _items_flat_to_normalized(core.items())

    def _n(v):
        return _fmt_price(v)

    out = []
    for j in norm["jobs"]:
        c = j["cost"]
        out += [
            f"[打工:{j['name']}]",
            f"描述={j.get('desc', '')}",
            f"最低等级={_n(j['min_level'])}",
            f"最低健康度={_n(j['min_health'])}",
            f"最低心情值={_n(j['min_mood'])}",
            f"消耗体力={_n(c['stamina'])}",
            f"消耗饱食度={_n(c['satiety'])}",
            f"消耗口渴值={_n(c['thirst'])}",
            f"消耗健康值={_n(c['health'])}",
            f"消耗心情值={_n(c['mood'])}",
            f"需要时间={_n(j['time'])}",
            f"金币={_n(j['coins'])}",
            f"经验={_n(j['exp'])}",
            "",
        ]
    for p in norm["plays"]:
        c = p["cost"]
        out += [
            f"[玩耍:{p['name']}]",
            f"描述={p.get('desc', '')}",
            f"最低等级={_n(p['min_level'])}",
            f"最低健康度={_n(p['min_health'])}",
            f"最低心情值={_n(p['min_mood'])}",
            f"消耗体力={_n(c['stamina'])}",
            f"消耗饱食度={_n(c['satiety'])}",
            f"消耗口渴值={_n(c['thirst'])}",
            f"消耗健康值={_n(c['health'])}",
            f"消耗心情值={_n(c['mood'])}",
            f"需要时间={_n(p['time'])}",
            f"经验={_n(p['exp'])}",
            f"心情值={_n(p['mood'])}",
            f"体力={_n(p['stamina'])}",
            f"健康度={_n(p['health'])}",
            "",
        ]
    return "\n".join(out)


def _save_backend_text(core, text: str):
    """保存后台配置（兼容旧版 txt 文本：解析后写入 game_items.json，商店部分不受影响）。
    返回 (ok, msg)。"""
    sections = core.parse_kv_sections_text(text, "后台", types=("打工", "玩耍"))
    if not sections:
        return False, "没有解析到 [打工:xxx] / [玩耍:xxx] 段落，格式未变化。"
    cfg = {"jobs": [], "plays": [], "shop": []}
    for sec in sections:
        cfg["jobs" if sec["type"] == "打工" else "plays"].append(sec)
    norm = _normalize_config(cfg)
    flat_new = _items_normalized_to_flat(norm)
    flat = dict(core.items())
    flat["jobs"] = flat_new["jobs"]
    flat["plays"] = flat_new["plays"]
    ok, msg = core.save_items(flat)
    return ok, f"{msg}（打工 {len(flat['jobs'])} / 玩耍 {len(flat['plays'])} 条）"


# ================= 导出导入（2.2.0 脱敏 / 敏感字段保护；3.0 新布局） =================
def _exportable_files():
    """3.0 数据文件清单：运行/设置/参数/商店价格/数值配置（用户数据经 user_data.json 单独打包导出）"""
    return [
        ("runtime.json", SYSTEM_FILE),
        ("settings.json", SETTINGS_FILE),
        ("params.json", PARAMS_FILE),
        ("shop_prices.json", SHOP_PRICES_FILE),
        ("game_items.json", ITEMS_JSON_FILE),
    ]


def _redact_system_text(text: str) -> str:
    """脱敏 runtime.json 文本：移除局域网密码哈希/会话密钥。解析失败原样返回。"""
    try:
        obj = json.loads(text)
    except Exception:
        return text
    if isinstance(obj, dict) and isinstance(obj.get(LAN_DATA_KEY), dict):
        obj[LAN_DATA_KEY].pop("password_hash", None)
        obj[LAN_DATA_KEY].pop("secret", None)
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _redact_params_text(text: str) -> str:
    """脱敏 params.json 文本：移除敏感运行参数（DEBUG_PASSWORD）。"""
    try:
        obj = json.loads(text)
    except Exception:
        return text
    if isinstance(obj, dict):
        for k in _SENSITIVE_PARAMS:
            obj.pop(k, None)
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _snapshot_sensitive(core) -> dict:
    """快照当前数据中的敏感字段（导入/恢复用）：局域网密码哈希、会话密钥、调试口令。"""
    out = {}
    try:
        data = core.data
        lan = data.get(LAN_DATA_KEY) or {}
        out["password_hash"] = lan.get("password_hash")
        out["secret"] = lan.get("secret")
        out["debug_password"] = (data.get("params") or {}).get("DEBUG_PASSWORD")
    except Exception:
        pass
    return out


def _restore_sensitive(core, snapshot: dict) -> None:
    """导入后恢复敏感字段：备份/导入内容缺失或为空时，保留导入前的值，
    防止导入旧备份导致局域网访问失配或调试口令被清空。"""
    if not snapshot:
        return
    try:
        data = core.data
    except Exception:
        return
    changed = False
    lan = data.setdefault(LAN_DATA_KEY, {})
    if not isinstance(lan, dict):
        lan = {}
        data[LAN_DATA_KEY] = lan
    if not lan.get("password_hash") and snapshot.get("password_hash"):
        lan["password_hash"] = snapshot["password_hash"]
        changed = True
    if not lan.get("secret") and snapshot.get("secret"):
        lan["secret"] = snapshot["secret"]
        changed = True
    params = data.setdefault("params", {})
    if not isinstance(params, dict):
        params = {}
        data["params"] = params
    if not params.get("DEBUG_PASSWORD") and snapshot.get("debug_password"):
        params["DEBUG_PASSWORD"] = snapshot["debug_password"]
        changed = True
    if changed:
        core.save()


def _export_files(core) -> dict:
    """导出全部数据（3.0 布局，内存数据为权威）→ {文件名: 内容}：
    runtime/settings/params/shop_prices/user_data（{uid: 用户聚合}）/ game_items。
    runtime 与 params 导出前脱敏（局域网密码哈希/会话密钥/调试口令不随备份下发）。"""
    data = core.data
    user_data = {}
    for ns, fkey in _USER_FILE_NS.items():
        for uid, v in (data.get(ns) or {}).items():
            if v in (None, {}, []):
                continue
            user_data.setdefault(uid, {})[fkey] = v
    return {
        "runtime.json": _redact_system_text(json.dumps(
            {k: data.get(k) for k in _RUNTIME_NS}, ensure_ascii=False, indent=2)),
        "settings.json": json.dumps(
            {k: data.get(k) for k in _SETTINGS_NS}, ensure_ascii=False, indent=2),
        "params.json": _redact_params_text(json.dumps(
            data.get("params") or {}, ensure_ascii=False, indent=2)),
        "shop_prices.json": json.dumps(
            data.get("shop_price_records") or [], ensure_ascii=False, indent=2),
        "user_data.json": json.dumps(user_data, ensure_ascii=False, indent=2),
        "game_items.json": json.dumps(core.items(), ensure_ascii=False, indent=2),
    }


def _replace_core_data(core, new_data: dict) -> None:
    """导入 data.json 后同步核心内存数据（3.0 data 常驻内存，仅写盘不换内存会读旧值）：
    原地替换 → 补默认命名空间 → 回填 records.json 记录数据（与 core._load 同流程）→ 写盘。"""
    core.data.clear()
    core.data.update(new_data)
    for k in getattr(core, "_DATA_DICT_KEYS", ()):
        core.data.setdefault(k, {})
    core.data.setdefault("redpackets", [])
    if not isinstance(core.data.get("alias_cmds"), dict):
        core.data["alias_cmds"] = {**DEFAULT_ALIAS_CMDS}
    try:
        from .format_convert import merge_records
        records = None
        if os.path.exists(RECORDS_FILE):
            with open(RECORDS_FILE, "r", encoding="utf-8") as f:
                obj = json.load(f)
            records = obj if isinstance(obj, dict) else None
        merge_records(core.data, records)
    except Exception as e:
        logger.warning(f"[数据管理] 导入后回填记录数据失败: {e}")
    core.save()


def _write_data_text(core, text: str):
    """导入 data.json 文本（2.2.2 旧包兼容）：JSON 校验 → 替换核心内存数据 →
    core.save() 落为新布局。不再写旧 data.json 文件。返回 (ok, msg)"""
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            return False, "数据必须是 JSON 对象（{...}）"
    except Exception as e:
        return False, f"JSON 格式错误: {e}"
    _replace_core_data(core, data)
    core.save()
    return True, "导入成功"


def _check_convert(core):
    """导入后按新标准检查记录数据分文件（幂等；委托 format_convert 插件服务）"""
    try:
        svc = core.service("format_convert")
        fn = getattr(svc, "check", None)
        if callable(fn):
            fn()
            return
        from .format_convert import check_and_convert
        check_and_convert()
    except Exception as e:
        logger.warning(f"[数据管理] 导入后数据标准检查失败: {e}")


# ---- 旧版 txt 数值配置解析（导入旧备份后重新迁移为 game_items.json 用） ----
def _parse_crop_fert(core, path: str, kind: str):
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
    """从 贷款套餐.txt 解析贷款套餐（txt 不存在时返回空列表）"""
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
        logger.warning(f"[数据管理] 读取 Benchmark data/game_items.json 失败: {e}")
        return None


def _remigrate_items_from_txt(core):
    """导入旧版 txt 备份（无 game_items.json）后：按 txt 重新迁移生成数值配置
    （2.3.0 _migrate_items_to_json 全量迁移段；全部为空时回退 Benchmark 默认模板）。"""
    try:
        if os.path.exists(ITEMS_JSON_FILE):
            os.remove(ITEMS_JSON_FILE)
        cfg = {"jobs": [], "plays": [], "shop": []}
        for sec in core.parse_kv_sections(CONFIG_FILE, "后台", types=("打工", "玩耍")):
            cfg["jobs" if sec["type"] == "打工" else "plays"].append(sec)
        type_files = [p for p in PET_SHOP_TYPE_FILES.values() if os.path.exists(p)]
        if type_files:
            for pth in type_files:
                cfg["shop"].extend(core.parse_kv_sections(pth, "商店", types=("商店",)))
        elif os.path.exists(PET_SHOP_FILE):
            cfg["shop"].extend(core.parse_kv_sections(PET_SHOP_FILE, "商店", types=("商店",)))
        else:
            cfg["shop"].extend(core.parse_kv_sections(CONFIG_FILE, "商店", types=("商店",)))
        norm = _normalize_config(cfg)
        flat = _items_normalized_to_flat(norm)
        flat["crops"] = _parse_crop_fert(core, CROP_FILE, "作物") if os.path.exists(CROP_FILE) else []
        flat["ferts"] = _parse_crop_fert(core, FERT_FILE, "肥料") if os.path.exists(FERT_FILE) else []
        flat["loans"] = _loans_from_txt(core)
        if not (flat["jobs"] or flat["plays"] or flat["shop"] or flat["crops"] or flat["ferts"] or flat["loans"]):
            bench = _load_benchmark_items()
            if bench and any(bench[k] for k in ITEMS_KEYS):
                flat = {k: bench[k] for k in ITEMS_KEYS}
        ok, msg = core.save_items(flat)
        if ok:
            logger.info(f"[数据管理] 已按旧版 txt 备份重新迁移数值配置"
                        f"（打工 {len(flat['jobs'])} / 玩耍 {len(flat['plays'])} / 商品 {len(flat['shop'])}"
                        f" / 作物 {len(flat['crops'])} / 肥料 {len(flat['ferts'])} / 贷款 {len(flat['loans'])} 条）")
        else:
            logger.warning(f"[数据管理] 导入旧版 txt 备份后重迁移失败: {msg}")
    except Exception as e:
        logger.warning(f"[数据管理] 导入旧版 txt 备份后重迁移失败: {e}")


def datamgr_write_user(user_data_dir, uid, obj):
    """写单个用户数据文件（导入用）"""
    from ..core import _safe_uid, _write_json
    _write_json(os.path.join(user_data_dir, _safe_uid(uid) + ".json"), obj)


def _reload_user_data(core):
    """从 user_data/ 目录重建内存用户命名空间（导入用户数据后调用）"""
    for ns in _USER_FILE_NS:
        core.data[ns] = {}
    if not os.path.isdir(USER_DATA_DIR):
        return
    for fn in os.listdir(USER_DATA_DIR):
        if not fn.endswith(".json"):
            continue
        uid = fn[:-5]
        try:
            with open(os.path.join(USER_DATA_DIR, fn), encoding="utf-8") as f:
                obj = json.load(f)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        for ns, fkey in _USER_FILE_NS.items():
            v = obj.get(fkey)
            if v not in (None, {}, []):
                core.data.setdefault(ns, {})[uid] = v


def _import_files(core, files: dict):
    """导入文件包（3.0 布局：runtime/settings/params/shop_prices/user_data/game_items；
    兼容 2.2.2 旧包 data.json/records.json 与更早的 txt 备份）。
    返回 (ok, msg_or_written_list)。导入前后保护敏感字段。"""
    sensitive = _snapshot_sensitive(core)
    written = []
    # ---- 3.0 布局 ----
    if "user_data.json" in files and isinstance(files["user_data.json"], str):
        try:
            users = json.loads(files["user_data.json"])
            if not isinstance(users, dict):
                raise ValueError("user_data.json 必须是 {uid: 用户聚合}")
        except Exception as e:
            return False, f"user_data.json 导入失败: {e}"
        # 清空现有用户文件后按导入重建
        if os.path.isdir(USER_DATA_DIR):
            for fn in os.listdir(USER_DATA_DIR):
                if fn.endswith(".json"):
                    try:
                        os.remove(os.path.join(USER_DATA_DIR, fn))
                    except OSError:
                        pass
        else:
            os.makedirs(USER_DATA_DIR, exist_ok=True)
        for uid, obj in users.items():
            if isinstance(obj, dict):
                datamgr_write_user(USER_DATA_DIR, uid, obj)
        _reload_user_data(core)
        written.append("user_data.json")
    _ns_files = {"runtime.json": None, "settings.json": None, "params.json": None,
                 "shop_prices.json": None}
    changed_sys = False
    for fn in _ns_files:
        if fn in files and isinstance(files[fn], str):
            try:
                obj = json.loads(files[fn])
            except Exception as e:
                return False, f"{fn} 导入失败: JSON 格式错误: {e}"
            if fn == "runtime.json":
                for k in _RUNTIME_NS:
                    if k in obj:
                        core.data[k] = obj[k]
            elif fn == "settings.json":
                for k in _SETTINGS_NS:
                    if k in obj:
                        core.data[k] = obj[k]
            elif fn == "params.json":
                core.data["params"] = obj if isinstance(obj, dict) else {}
            else:
                core.data["shop_price_records"] = obj if isinstance(obj, list) else []
            written.append(fn)
            changed_sys = True
    if changed_sys:
        core.save()
    if "game_items.json" in files and isinstance(files["game_items.json"], str):
        try:
            obj = json.loads(files["game_items.json"])
            if isinstance(obj, dict):
                core.save_items(obj)
                written.append("game_items.json")
        except Exception as e:
            return False, f"game_items.json 导入失败: {e}"
    # ---- 2.2.2 旧包（data.json / records.json）----
    if "data.json" in files and isinstance(files["data.json"], str):
        ok, msg = _write_data_text(core, files["data.json"])
        if not ok:
            return False, f"data.json 导入失败: {msg}"
        written.append("data.json")
        if "records.json" in files and isinstance(files["records.json"], str):
            try:
                records = json.loads(files["records.json"])
                if isinstance(records, dict):
                    from .format_convert import merge_records
                    merge_records(core.data, records)
                    core.save()
            except Exception as e:
                logger.warning(f"[数据管理] records.json 回填失败: {e}")
    legacy_hit = False
    for fn, path in _LEGACY_TXT_FILES.items():
        if fn in files and isinstance(files[fn], str):
            ok, msg = _write_file(path, files[fn])
            if ok:
                written.append(fn)
                legacy_hit = True
            else:
                return False, f"{fn} 导入失败: {msg}"
    if legacy_hit and "game_items.json" not in files:
        # 旧版备份（txt）：删除现有 JSON 并按 txt 重新迁移
        _remigrate_items_from_txt(core)
    _restore_sensitive(core, sensitive)
    return True, written


# ================= 指令处理（2.2.1：仅管理员；main.py _handle_* 原样迁移） =================
def _handle_view_config(core) -> str:
    """查看后台配置（1.7.7：数值存于 game_items.json，这里渲染为旧版文本格式供查看）"""
    text = _cfg_to_backend_text(core)
    if not text.strip():
        return "当前没有打工/玩耍配置（可在 WebUI 后台管理页编辑）。"
    return f"当前打工/玩耍配置（WebUI 表格编辑，存储于 game_items.json）：\n{text}"


def _handle_export_data(core) -> str:
    """导出全部数据（存档 + 自定义配置）到 plugin_data 备份文件，小数据直接返回内容。
    2.2.0：导出内容经过脱敏（局域网密码哈希/会话密钥/调试口令等不随备份下发）。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(BACKUP_DATA_DIR, exist_ok=True)
    bak = os.path.join(BACKUP_DATA_DIR, f"signin_export_{ts}.json")
    files = _export_files(core)
    try:
        with open(bak, "w", encoding="utf-8") as f:
            json.dump({"files": files}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"❌ 导出失败: {e}"
    s = json.dumps({"files": files}, ensure_ascii=False)
    if len(s) <= 3500:
        return f"✅ 数据已导出到：{bak}\n内容（已脱敏）：\n{s}"
    return f"✅ 数据已导出到：{bak}\n数据较大（{len(s)} 字符），请直接到上述路径取文件。"


def _handle_import_data(core, event) -> str:
    """导入全部数据（兼容新版 files 打包与旧版仅 data.json 的 content）"""
    parts = event.message_str.split(maxsplit=1)
    if len(parts) >= 2 and parts[1].strip():
        raw = parts[1].strip()
    else:
        imp = os.path.join(BACKUP_DATA_DIR, "data_import.json")
        if not os.path.exists(imp):
            return "请把要导入的数据保存为 backup_data 文件夹下的 data_import.json，或直接发送「导入数据 <JSON内容>」。"
        try:
            with open(imp, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as e:
            return f"❌ 读取 data_import.json 失败: {e}"
    try:
        parsed = json.loads(raw)
    except Exception as e:
        return f"❌ JSON 格式错误: {e}"
    files = parsed.get("files") if isinstance(parsed, dict) else None
    if isinstance(files, dict):
        ok, result = _import_files(core, files)
        if not ok:
            return f"❌ {result}"
        return f"✅ 导入成功（{len(result)} 个文件已还原）！"
    # 2.2.0：旧格式（仅 data.json 内容）导入时保留当前敏感值（密码哈希/会话密钥/调试口令）
    sensitive = _snapshot_sensitive(core)
    ok, msg = _write_data_text(core, raw)
    if ok:
        _restore_sensitive(core, sensitive)
        _check_convert(core)
    return "✅ 导入成功！" if ok else f"❌ {msg}"


# ================= 服务接口（WebUI 导出导入面板复用） =================
class _Api:
    """core.service("datamgr")：数据导出/导入 + 后台配置文本读写（WebUI 调用）"""

    def __init__(self, core):
        self._core = core

    def exportable_files(self):
        return list(_exportable_files())

    def export_files(self) -> dict:
        """导出全部数据（data.json 已脱敏）→ {文件名: 内容}"""
        return _export_files(self._core)

    def import_files(self, files: dict):
        """导入文件包（兼容旧版 txt 备份）→ (ok, msg_or_written_list)"""
        return _import_files(self._core, files)

    def snapshot_sensitive(self) -> dict:
        return _snapshot_sensitive(self._core)

    def restore_sensitive(self, snapshot: dict) -> None:
        _restore_sensitive(self._core, snapshot)

    def write_data_text(self, text: str):
        """导入 data.json（校验 + 写盘 + 同步核心内存）→ (ok, msg)"""
        return _write_data_text(self._core, text)

    def backend_text(self) -> str:
        """当前打工/玩耍数值 → 旧版 后台.txt 格式文本"""
        return _cfg_to_backend_text(self._core)

    def save_backend_text(self, text: str):
        """旧版 txt 文本 → 解析保存到 game_items.json → (ok, msg)"""
        return _save_backend_text(self._core, text)


def register(core):
    """挂载：管理员指令（数据管理）+ 服务暴露 + 帮助段。"""
    @core.command("查看后台配置", admin=True)
    def handle_view_config(event):
        return _handle_view_config(core)

    @core.command("保存后台配置", admin=True)
    def handle_save_backend_config(event):
        """保存后台配置 <内容>"""
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return "格式：保存后台配置 <内容>（先「查看后台配置」复制全文，改好后粘贴到指令后）"
        ok, msg = _save_backend_text(core, parts[1].strip())
        return f"✅ {msg}" if ok else f"❌ {msg}"

    @core.command("导出数据", admin=True)
    def handle_export_data(event):
        return _handle_export_data(core)

    @core.command("导入数据", admin=True)
    def handle_import_data(event):
        return _handle_import_data(core, event)

    core.add_help("数据管理（仅管理员可用）", [
        ("查看后台配置 / 保存后台配置", "管理后台配置（管理员专属）"),
        ("导出数据 / 导入数据", "数据导入导出（管理员专属）"),
    ])

    core.expose("datamgr", _Api(core))
