# -*- coding: utf-8 -*-
# WebUI 后台 API。从原 main.py 的 SignInPlugin 拆出的 Mixin，由入口类组合继承。
from .base import *  # noqa: F401,F403  常量与共享工具
from .base import _register_runtime_module, _sync_runtime_global  # noqa: F401
import string as _string
import sys as _sys

_register_runtime_module(_sys.modules[__name__])


class WebUIMixin:
    # 2.2.0：敏感运行参数 —— 不回传当前值；保存时留空 = 保持不变
    _SENSITIVE_PARAMS = ("DEBUG_PASSWORD",)

    def _apply_runtime_params(self, params: dict):
        """校验并应用运行参数：更新模块全局常量（立即生效）+ 同步实例属性。
        返回 (applied, errors)；errors 为 {key: 未生效原因}，供 WebUI 提示管理员"""
        applied = {}
        errors = {}
        for spec in RUNTIME_PARAMS:
            key = spec["key"]
            if key not in params:
                continue
            raw = params[key]
            label = spec["label"]
            try:
                # 2.2.0：密码类参数留空 = 保持不变（前端不回显当前值）
                if key in self._SENSITIVE_PARAMS and not str(raw).strip():
                    continue
                # 2.2.1：环境变量 SIGNIN_DEBUG_PASSWORD 优先级最高，运行参数不再覆盖（key 无害化）
                if key in self._SENSITIVE_PARAMS and os.environ.get("SIGNIN_DEBUG_PASSWORD"):
                    continue
                if key in self._SENSITIVE_PARAMS and len(str(raw).strip()) < 4:
                    errors[key] = f"「{label}」至少需要 4 位"
                    continue
                if spec["type"] == "int":
                    val = int(raw)
                elif spec["type"] == "float":
                    val = float(raw)
                elif spec["type"] == "bool":
                    if isinstance(raw, str):
                        val = raw.strip().lower() in ("1", "true", "yes", "on")
                    else:
                        val = bool(raw)
                elif spec["type"] == "list":
                    # 兼容字符串 "1000,1500,2000,3000" 与 JSON 数组 [1000, 1500, ...] 两种提交；
                    # 并容忍历史坏数据（数组 str() 后残留的方括号导致首尾项解析失败为 0）
                    if isinstance(raw, (list, tuple)):
                        items = [str(x) for x in raw]
                    else:
                        items = str(raw).replace("，", ",").split(",")
                    parts = []
                    for x in items:
                        s = str(x).strip().strip("[]")
                        if s:
                            parts.append(self._f(s))
                    val = tuple(parts)
                    # 土地升级费用应恰为 4 个正数（贫瘠→红→普通→肥沃→黑 共 4 次升级）；
                    # 历史坏值（如 (0.0,1500.0,2000.0,0.0)）回退默认
                    if key == "FARM_UPGRADE_COSTS" and len(val) == 4 and any(v <= 0 for v in val):
                        val = (1000.0, 1500.0, 2000.0, 3000.0)
                else:
                    val = str(raw)
            except (TypeError, ValueError):
                errors[key] = f"「{label}」需要输入{spec['type']}类型（收到：{raw!r}）"
                continue
            if spec.get("min") is not None and val < spec["min"]:
                errors[key] = f"「{label}」不能小于 {spec['min']}"
                continue
            if spec.get("max") is not None and val > spec["max"]:
                errors[key] = f"「{label}」不能大于 {spec['max']}"
                continue
            globals()[key] = val
            # 2.0.0（模块拆分后）：同步到 main.py 与各功能模块的全局常量（裸名与 globals().get 读取立即生效）
            _sync_runtime_global(key, val)
            attr = spec.get("attr")
            if attr and hasattr(self, attr):
                setattr(self, attr, val)
            applied[key] = val
        return applied, errors

    def _load_runtime_params(self) -> None:
        """从 data.json 加载已保存的运行参数并应用（WebUI 保存后立即生效，无需重启）"""
        try:
            data = self._load()
            params = data.get("params") or {}
            if params:
                self._apply_runtime_params(params)
        except Exception as e:
            logger.warning(f"[插件] 加载运行参数失败: {e}")

    def _load_activity_configs(self) -> None:
        """从 data.json 加载每个活动的参数覆盖（起始/结束时间、简介、要求、自定义参数）并应用到活动实例"""
        try:
            data = self._load()
            configs = data.get("activity_config") or {}
            for act in self._activities:
                cfg = configs.get(act.id)
                if not isinstance(cfg, dict):
                    continue
                for field, value in cfg.items():
                    try:
                        act.apply_override(field, value)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"[插件] 加载活动参数失败: {e}")

    async def web_get_params(self):
        """读取运行参数：返回参数 schema 列表（含当前值），前端据此渲染表单。
        2.2.0：敏感参数（调试口令等）不回传当前值，前端只显示「已设置」，修改时提交新值。"""
        async with self._lock:
            data = self._load()
            saved = data.get("params") or {}
            items = []
            for spec in RUNTIME_PARAMS:
                key = spec["key"]
                cur = saved.get(key, globals().get(key, spec.get("default")))
                item = {
                    "key": key,
                    "label": spec["label"],
                    "type": spec["type"],
                    "group": spec.get("group", "其他"),
                    "subgroup": spec.get("subgroup", "通用"),
                    "desc": spec.get("desc", ""),
                    "value": cur,
                    "min": spec.get("min"),
                    "max": spec.get("max"),
                }
                # 2.2.0：密码类参数不向访问终端回传任何值（哈希校验只在插件本地进行）
                if key in self._SENSITIVE_PARAMS:
                    item["value"] = ""
                    item["masked"] = True
                items.append(item)
            return json_response({"params": items})

    async def web_save_params(self):
        """保存运行参数：POST {params: {key: value}}，校验后立即生效并持久化"""
        async with self._lock:
            payload = await request.json(default={})
            incoming = payload.get("params")
            if not isinstance(incoming, dict):
                return error_response("params 必须是对象", status_code=400)
            data = self._load()
            self._history_backup(data, "保存前自动备份")
            saved = dict(data.get("params") or {})
            applied, errors = self._apply_runtime_params(incoming)
            for k, v in applied.items():
                saved[k] = v
            data["params"] = saved
            self._save(data)
            return json_response({"saved": True, "applied": applied, "errors": errors})

    async def web_get_aliases(self):
        """读取同义口令与全部标准指令（供下拉选择）"""
        async with self._lock:
            data = self._load()
            return json_response({
                "aliases": dict(data.get("alias_cmds") or {}),
                "heads": sorted(CMD_HEADS),
            })

    async def web_save_aliases(self):
        """保存同义口令：POST {aliases: {同义词: 标准指令}}，校验后立即生效并持久化"""
        async with self._lock:
            payload = await request.json(default={})
            incoming = payload.get("aliases")
            if not isinstance(incoming, dict):
                return error_response("aliases 必须是对象", status_code=400)
            cleaned, errors = {}, {}
            for k, v in incoming.items():
                alias = str(k).strip()
                target = str(v).strip()
                if not alias or not target:
                    errors[alias or "(空)"] = "同义词与目标指令不能为空"
                    continue
                if alias in CMD_HEADS:
                    errors[alias] = f"「{alias}」已是标准指令，不能作为同义词"
                    continue
                if target not in CMD_HEADS:
                    errors[alias] = f"「{target}」不是可用的标准指令"
                    continue
                if alias in cleaned:
                    errors[alias] = "同义词重复"
                    continue
                cleaned[alias] = target
            if errors:
                return error_response(f"保存失败：{errors}", status_code=400)
            data = self._load()
            self._history_backup(data, "保存前自动备份")
            data["alias_cmds"] = cleaned
            self._save(data)
            return json_response({"saved": True, "aliases": cleaned})

    async def web_debug_status(self):
        """调试模式状态：{unlocked: 是否已输入口令, enabled: 是否已开启}"""
        async with self._lock:
            return json_response({"unlocked": bool(self._debug_unlocked), "enabled": bool(self._debug)})

    async def web_debug_toggle(self):
        """开关调试模式（需先输入口令解锁）。开启后无限资源且不写盘；退出后回到开启前状态"""
        async with self._lock:
            if not self._debug_unlocked:
                return error_response("未解锁调试模式（请在对话框输入口令）", status_code=403)
            self._debug = not self._debug
            if self._debug:
                self._debug_data = None  # 开启：下次 _load 从磁盘载入内存缓存
            else:
                self._debug_data = None  # 退出：丢弃内存缓存，下次 _load 恢复磁盘原状态
            # 退出时不重置 unlocked（按钮保留，重启插件后才消失）
            state = "开启" if self._debug else "退出"
            return json_response({"enabled": bool(self._debug), "state": state})

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
    _ITEMS_JSON_VERSION = 4

    def _read_items_json(self):
        """读取 game_items.json（扁平结构 dict）；不存在/损坏返回 None"""
        try:
            if not os.path.exists(ITEMS_JSON_FILE):
                return None
            with open(ITEMS_JSON_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return None
            out = {}
            for k in ("jobs", "plays", "shop", "crops", "ferts", "loans"):
                out[k] = raw.get(k) if isinstance(raw.get(k), list) else []
            out["version"] = raw.get("version", 1)
            return out
        except Exception as e:
            logger.warning(f"[插件] 读取 game_items.json 失败（回退 txt）: {e}")
            return None

    def _write_items_json(self, flat: dict):
        """原子写入 game_items.json，返回 (ok, msg)"""
        try:
            payload = {"version": self._ITEMS_JSON_VERSION,
                       "jobs": flat.get("jobs", []),
                       "plays": flat.get("plays", []),
                       "shop": flat.get("shop", []),
                       "crops": flat.get("crops", []),
                       "ferts": flat.get("ferts", []),
                       "loans": flat.get("loans", [])}
            tmp = ITEMS_JSON_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, ITEMS_JSON_FILE)
            return True, "保存成功"
        except Exception as e:
            return False, f"保存失败: {e}"

    def _items_flat_to_normalized(self, flat: dict) -> dict:
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
                "min_level": self._f(j.get("min_level", 0)),
                "min_health": self._f(j.get("min_health", 0)),
                "min_mood": self._f(j.get("min_mood", 0)),
                "cost": {
                    "stamina": self._f(j.get("cost_stamina", 0)),
                    "satiety": self._f(j.get("cost_satiety", 0)),
                    "thirst": self._f(j.get("cost_thirst", 0)),
                    "health": self._f(j.get("cost_health", 0)),
                    "mood": self._f(j.get("cost_mood", 0)),
                },
                "time": self._f(j.get("time", 0)),
                "coins": self._f(j.get("coins", 0)),
                "exp": self._f(j.get("exp", 0)),
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
                "min_level": self._f(p.get("min_level", 0)),
                "min_health": self._f(p.get("min_health", 0)),
                "min_mood": self._f(p.get("min_mood", 0)),
                "cost": {
                    "stamina": self._f(p.get("cost_stamina", 0)),
                    "satiety": self._f(p.get("cost_satiety", 0)),
                    "thirst": self._f(p.get("cost_thirst", 0)),
                    "health": self._f(p.get("cost_health", 0)),
                    "mood": self._f(p.get("cost_mood", 0)),
                },
                "time": self._f(p.get("time", 0)),
                "exp": self._f(p.get("exp", 0)),
                "mood": self._f(p.get("mood", 0)),
                "stamina": self._f(p.get("stamina", 0)),
                "health": self._f(p.get("health", 0)),
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
                "price": self._f(s.get("price", 0)),
                "effects": {
                    "satiety": self._f(s.get("satiety", 0)),
                    "thirst": self._f(s.get("thirst", 0)),
                    "stamina": self._f(s.get("stamina", 0)),
                    "mood": self._f(s.get("mood", 0)),
                    "health": self._f(s.get("health", 0)),
                },
            })
        return {"jobs": jobs, "plays": plays, "shop": shop}

    @staticmethod
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

    def _validate_flat_items(self, entries, num_fields, kind_label, check_type=False, code_mode=False):
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

    def _crop_fert_from_txt(self):
        """从 作物.txt / 肥料.txt 解析作物与肥料（txt 不存在时返回空列表）"""
        crops = self._parse_crop_fert(CROP_FILE, "作物") if os.path.exists(CROP_FILE) else []
        ferts = self._parse_crop_fert(FERT_FILE, "肥料") if os.path.exists(FERT_FILE) else []
        return crops, ferts

    def _loans_from_txt(self):
        """从 贷款套餐.txt 解析贷款套餐（txt 不存在时返回空列表）"""
        if not os.path.exists(LOAN_FILE):
            return []
        out = []
        for it in _parse_kv_sections(LOAN_FILE, "贷款套餐"):
            d = it["data"]
            try:
                out.append({"code": int(it["name"]), "desc": d.get("描述", ""),
                            "max_amount": int(self._f(d.get("最大金额", 0))),
                            "fav_req": int(self._f(d.get("好感度等级要求", 0))),
                            "pet_req": int(self._f(d.get("宠物等级要求", 0))),
                            "farm_req": int(self._f(d.get("农场等级要求", 0))),
                            "rate": self._f(d.get("利息", 0))})
            except Exception:
                continue
        return out

    def _loans_from_benchmark(self):
        """从 Benchmark data/game_items.json 读取默认贷款套餐"""
        bench_flat = _load_benchmark_items()
        if bench_flat is not None:
            return [l for l in (self._norm_loan_entry(d) for d in bench_flat["loans"]) if l]
        return []

    def _crop_fert_from_benchmark(self):
        """从 Benchmark data 默认模板解析作物与肥料（优先 game_items.json，旧版 txt 兜底）"""
        bench_flat = _load_benchmark_items()
        if bench_flat is not None:
            crops = [c for c in (self._norm_crop_entry(d) for d in bench_flat["crops"]) if c]
            ferts = [f_ for f_ in (self._norm_fert_entry(d) for d in bench_flat["ferts"]) if f_]
            return crops, ferts
        bench = os.path.join(_PLUGIN_DIR, "Benchmark data")
        crops = ferts = []
        bp = os.path.join(bench, "作物.txt")
        if os.path.exists(bp):
            crops = self._parse_crop_fert(bp, "作物")
        bp = os.path.join(bench, "肥料.txt")
        if os.path.exists(bp):
            ferts = self._parse_crop_fert(bp, "肥料")
        return crops, ferts

    def _migrate_items_to_json(self):
        """1.7.7：商店/打工/玩耍/作物/肥料数值从旧版 txt 迁移为 game_items.json。
        - JSON 不存在 → 全量迁移（v2）；txt 全部缺失时从 Benchmark data 默认模板生成
        - JSON 已存在但为 v1（无 crops/ferts）→ 补迁作物/肥料（v1→v2）
        txt 文件保留作备份不再读取。只迁一次。"""
        try:
            if not os.path.exists(ITEMS_JSON_FILE):
                # ---- 全量迁移（v2）----
                cfg = {"jobs": [], "plays": [], "shop": []}
                self._parse_work_play(CONFIG_FILE, cfg)
                type_files = [p for p in PET_SHOP_TYPE_FILES.values() if os.path.exists(p)]
                if type_files:
                    for pth in type_files:
                        self._parse_shop(pth, cfg)
                elif os.path.exists(PET_SHOP_FILE):
                    self._parse_shop(PET_SHOP_FILE, cfg)
                else:
                    self._parse_shop(CONFIG_FILE, cfg)
                norm = self._normalize_config(cfg)
                crops, ferts = self._crop_fert_from_txt()
                loans = self._loans_from_txt()
                if not (norm["jobs"] or norm["plays"] or norm["shop"] or crops or ferts or loans):
                    # 全新部署：优先 Benchmark data/game_items.json（新格式默认模板）
                    bench_flat = _load_benchmark_items()
                    if bench_flat and any(bench_flat[k] for k in _ITEMS_KEYS):
                        flat = {k: bench_flat[k] for k in _ITEMS_KEYS}
                        ok, msg = self._write_items_json(flat)
                        if ok:
                            logger.info(f"[插件] 全新部署：已从 Benchmark data/game_items.json 生成默认数值"
                                        f"（打工 {len(flat['jobs'])} / 玩耍 {len(flat['plays'])} / 商品 {len(flat['shop'])}"
                                        f" / 作物 {len(flat['crops'])} / 肥料 {len(flat['ferts'])} 条）")
                        else:
                            logger.warning(f"[插件] game_items.json 写入失败: {msg}")
                        return
                    # 旧版 txt 模板兜底
                    bench = os.path.join(_PLUGIN_DIR, "Benchmark data")
                    cfg2 = {"jobs": [], "plays": [], "shop": []}
                    bp = os.path.join(bench, "后台.txt")
                    if os.path.exists(bp):
                        self._parse_work_play(bp, cfg2)
                    for typ in PET_SHOP_TYPES:
                        btp = os.path.join(bench, f"宠物商店-{typ}.txt")
                        if os.path.exists(btp):
                            self._parse_shop(btp, cfg2)
                    if cfg2["jobs"] or cfg2["plays"] or cfg2["shop"]:
                        norm = self._normalize_config(cfg2)
                    crops, ferts = self._crop_fert_from_benchmark()
                    loans = self._loans_from_benchmark()
                flat = self._items_normalized_to_flat(norm)
                flat["crops"] = crops
                flat["ferts"] = ferts
                flat["loans"] = loans
                ok, msg = self._write_items_json(flat)
                if ok:
                    logger.info(f"[插件] 已将 商店/打工/玩耍/作物/肥料/贷款套餐 数值迁移为 game_items.json"
                                f"（打工 {len(flat['jobs'])} / 玩耍 {len(flat['plays'])} / 商品 {len(flat['shop'])}"
                                f" / 作物 {len(crops)} / 肥料 {len(ferts)} / 贷款 {len(loans)} 条）")
                else:
                    logger.warning(f"[插件] game_items.json 写入失败: {msg}")
                return
            # ---- 版本升级补迁（v1→v2 作物/肥料；v2→v3 贷款套餐） ----
            try:
                with open(ITEMS_JSON_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception:
                return
            if not isinstance(raw, dict):
                return
            changed = False
            if raw.get("version", 1) < 2 or "crops" not in raw or "ferts" not in raw:
                crops, ferts = self._crop_fert_from_txt()
                if not (crops or ferts):
                    crops, ferts = self._crop_fert_from_benchmark()
                raw["crops"] = crops
                raw["ferts"] = ferts
                changed = True
                logger.info(f"[插件] game_items.json 升级：补迁 作物 {len(crops)} / 肥料 {len(ferts)} 条")
            if raw.get("version", 1) < 3 or "loans" not in raw:
                loans = self._loans_from_txt()
                if not loans:
                    loans = self._loans_from_benchmark()
                raw["loans"] = loans
                changed = True
                logger.info(f"[插件] game_items.json 升级：补迁 贷款套餐 {len(loans)} 条")
            # v3→v4（2.0.0）：肥料增加「可加速次数」max_accel（旧字段 max_uses 迁移）
            if raw.get("version", 1) < 4:
                ferts = raw.get("ferts") or []
                n = 0
                for f_ in ferts:
                    if isinstance(f_, dict) and "max_accel" not in f_:
                        f_["max_accel"] = int(f_.get("max_uses", -1))
                        n += 1
                if n:
                    raw["ferts"] = ferts
                    changed = True
                    logger.info(f"[插件] game_items.json 升级 v4：肥料补迁 可加速次数 {n} 条")
            if not changed:
                return
            raw["version"] = self._ITEMS_JSON_VERSION
            tmp = ITEMS_JSON_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            os.replace(tmp, ITEMS_JSON_FILE)
        except Exception as e:
            logger.warning(f"[插件] 数值配置迁移为 JSON 失败: {e}")

    def _cfg_to_backend_text(self) -> str:
        """把当前打工/玩耍数值渲染为旧版 后台.txt 格式文本（「查看后台配置」指令用）"""
        cfg = self._load_config()

        def _n(v):
            return self._fmt_price(v)

        out = []
        for j in cfg["jobs"]:
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
        for p in cfg["plays"]:
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

    def _load_config(self) -> dict:
        """1.7.7：优先读 game_items.json（WebUI 表格编辑的存储）；
        不存在/损坏时回退解析旧版 txt（后台.txt + 宠物商店-*.txt，兼容未拆分的 宠物商店.txt）。"""
        flat = self._read_items_json()
        if flat is not None:
            return self._items_flat_to_normalized(flat)
        # 旧版 txt 回退（未迁移时）
        cfg = {"jobs": [], "plays": [], "shop": []}
        self._parse_work_play(CONFIG_FILE, cfg)
        # 商店：优先 4 个类型文件；若类型文件不存在回退读 宠物商店.txt；再回退 后台.txt
        type_files = [p for p in PET_SHOP_TYPE_FILES.values() if os.path.exists(p)]
        if type_files:
            for p in type_files:
                self._parse_shop(p, cfg)
        elif os.path.exists(PET_SHOP_FILE):
            self._parse_shop(PET_SHOP_FILE, cfg)
        else:
            self._parse_shop(CONFIG_FILE, cfg)
        return self._normalize_config(cfg)

    def _parse_work_play(self, path, cfg: dict) -> None:
        """解析 后台.txt 的打工/玩耍段落"""
        for sec in _parse_kv_sections(path, "后台", types=("打工", "玩耍")):
            cfg["jobs" if sec["type"] == "打工" else "plays"].append(sec)

    def _parse_shop(self, path, cfg: dict) -> None:
        """解析商店段落（[商店:xxx]）到 cfg["shop"]"""
        cfg["shop"].extend(_parse_kv_sections(path, "商店", types=("商店",)))

    def _normalize_config(self, cfg: dict) -> dict:
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
                "min_level": self._f(d.get("最低等级", 0)),
                "min_health": self._f(d.get("最低健康度", 0)),
                "min_mood": self._f(d.get("最低心情值", 0)),
                "cost": {
                    "stamina": self._f(d.get("消耗体力", 0)),
                    "satiety": self._f(d.get("消耗饱食度", 0)),
                    "thirst": self._f(d.get("消耗口渴值", 0)),
                    "health": self._f(_c(d, "消耗健康度", "消耗健康值", "消耗健康")),
                    "mood": self._f(d.get("消耗心情值", 0)),
                },
                "time": self._f(d.get("需要时间", 0)),
                "coins": self._f(d.get("金币", 0)),
                "exp": self._f(d.get("经验", 0)),
            })

        plays = []
        for p in cfg["plays"]:
            d = p["data"]
            plays.append({
                "name": p["name"],
                "desc": d.get("描述", ""),
                "min_level": self._f(d.get("最低等级", 0)),
                "min_health": self._f(d.get("最低健康度", 0)),
                "min_mood": self._f(d.get("最低心情值", 0)),
                "cost": {
                    "stamina": self._f(d.get("消耗体力", 0)),
                    "satiety": self._f(d.get("消耗饱食度", 0)),
                    "thirst": self._f(d.get("消耗口渴值", 0)),
                    "health": self._f(_c(d, "消耗健康度", "消耗健康值", "消耗健康")),
                },
                "time": self._f(d.get("需要时间", 0)),
                "exp": self._f(d.get("经验", 0)),
                "mood": self._f(d.get("心情值", 0)),
                "stamina": self._f(d.get("体力", 0)),      # 玩耍收益体力（菜单红字显示）
                "health": self._f(d.get("健康度", 0)),     # 玩耍收益健康（可为负；红字显示净变化）
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
                "price": self._f(d.get("价格", 0)),
                "effects": {
                    "satiety": self._f(d.get("饱食度", 0)),
                    "thirst": self._f(d.get("口渴值", 0)),
                    "stamina": self._f(d.get("体力", 0)),
                    "mood": self._f(d.get("心情值", 0)),
                    "health": self._f(d.get("健康度", 0)),
                },
            })

        return {"jobs": jobs, "plays": plays, "shop": shop}

    # ================= WebUI 后端数据接口 =================
    def _read_config_text(self) -> str:
        """读取 后台.txt 原文，供 WebUI 编辑器使用"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            logger.error(f"[插件] 读取后台配置失败: {e}")
        return ""

    def _write_config_text(self, text: str):
        """保存 后台.txt 原文，返回 (ok, msg)"""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                f.write(text)
            return True, "保存成功"
        except Exception as e:
            return False, f"保存失败: {e}"

    def _read_data_text(self) -> str:
        """读取 data.json 原文，供 WebUI 导入导出使用"""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            logger.error(f"[插件] 读取数据失败: {e}")
        return "{}"

    def _write_data_text(self, text: str):
        """导入 data.json（覆盖），先做 JSON 校验，返回 (ok, msg)"""
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                return False, "数据必须是 JSON 对象（{...}）"
        except Exception as e:
            return False, f"JSON 格式错误: {e}"
        try:
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, DATA_FILE)
            return True, "导入成功"
        except Exception as e:
            return False, f"写入失败: {e}"

    # ================= WebUI Pages 后端 API =================
    async def web_get_backend_config(self):
        """读取打工/玩耍数值（结构化，供 WebUI 表格编辑）"""
        async with self._lock:
            flat = self._read_items_json()
            if flat is None:
                flat = self._items_normalized_to_flat(self._load_config())
            return json_response({"jobs": flat["jobs"], "plays": flat["plays"]})

    async def web_save_backend_config(self):
        """保存打工/玩耍数值：{jobs: [...], plays: [...]}，校验后写入 game_items.json（立即生效）"""
        async with self._lock:
            payload = await request.json(default={})
            jobs_in = payload.get("jobs")
            plays_in = payload.get("plays")
            if not isinstance(jobs_in, list) or not isinstance(plays_in, list):
                return error_response("jobs 和 plays 必须是数组", status_code=400)
            jobs, errs1 = self._validate_flat_items(jobs_in, self._JOB_NUM_FIELDS, "打工")
            plays, errs2 = self._validate_flat_items(plays_in, self._PLAY_NUM_FIELDS, "玩耍")
            errors = {**{f"打工·{k}": v for k, v in errs1.items()},
                      **{f"玩耍·{k}": v for k, v in errs2.items()}}
            if errors:
                return json_response({"saved": False, "errors": errors})
            flat = self._read_items_json() or {"jobs": [], "plays": [], "shop": [], "crops": [], "ferts": [], "loans": []}
            self._history_backup(self._load(), "保存前自动备份")
            flat["jobs"] = jobs
            flat["plays"] = plays
            ok, msg = self._write_items_json(flat)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"saved": True, "jobs": len(jobs), "plays": len(plays)})

    async def web_get_petshop(self):
        """读取宠物商店商品（结构化，供 WebUI 表格编辑）。2.2.0：不再返回冗余空字段 types/content。"""
        async with self._lock:
            flat = self._read_items_json()
            if flat is None:
                flat = self._items_normalized_to_flat(self._load_config())
            return json_response({"items": flat["shop"]})

    async def web_save_petshop(self):
        """保存宠物商店商品：{items: [...]}，校验后写入 game_items.json（立即生效）"""
        async with self._lock:
            payload = await request.json(default={})
            items_in = payload.get("items")
            if not isinstance(items_in, list):
                return error_response("items 必须是数组", status_code=400)
            shop, errors = self._validate_flat_items(items_in, self._SHOP_NUM_FIELDS, "商店", check_type=True)
            if errors:
                return json_response({"saved": False, "errors": errors})
            flat = self._read_items_json() or {"jobs": [], "plays": [], "shop": [], "crops": [], "ferts": [], "loans": []}
            self._history_backup(self._load(), "保存前自动备份")
            flat["shop"] = shop
            ok, msg = self._write_items_json(flat)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"saved": True, "items": len(shop)})

    # ================= 功能开关 =================
    FEATURE_MODULES = [
        {"key": "farm", "label": "农场系统"},
        {"key": "signin", "label": "签到系统"},
        {"key": "activity", "label": "活动系统"},
        {"key": "pet", "label": "宠物系统"},
        {"key": "redpacket", "label": "金币红包"},
        {"key": "bank_loan", "label": "银行-贷款"},
        {"key": "bank_saving", "label": "银行-储蓄"},
        {"key": "steal", "label": "偷菜系统"},
    ]
    # 指令 → 所属功能模块（关闭时该指令返回「功能已关闭」）
    FEATURE_CMD_MAP = {
        "签到": "signin", "我的签到": "signin", "签到帮助": "signin",
        "宠物": "pet", "解锁宠物": "pet", "更改宠物名字": "pet",
        "打工": "pet", "玩耍": "pet", "商店": "pet", "购买": "pet",
        "使用": "pet", "背包": "pet", "宠物帮助": "pet",
        "活动": "activity", "活动中心": "activity",
        "金币红包": "redpacket", "开": "redpacket", "开红包": "redpacket", "抢红包": "redpacket",
        "借款": "bank_loan", "还款": "bank_loan", "我的贷款": "bank_loan", "我的征信": "bank_loan",
        "存款": "bank_saving", "取款": "bank_saving", "银行统计": "bank_saving",
        # 农场系统
        "解锁农场": "farm", "购买土地": "farm", "土地升级": "farm",
        "农场商店": "farm", "种子商店": "farm", "肥料商店": "farm",
        "购买种子": "farm", "购买肥料": "farm",
        "种植": "farm", "种地": "farm", "施肥": "farm", "收割": "farm", "收获": "farm", "取消种植": "farm",
        "土地状态": "farm", "我的农场": "farm", "农场仓库": "farm",
        "售卖": "farm", "售卖种子": "farm", "农场帮助": "farm",
        # 偷菜系统
        "偷菜": "steal", "自动偷菜": "steal", "看家": "steal",
    }

    def _feature_enabled(self, data: dict, key: str) -> bool:
        """功能开关是否开启（默认开启）"""
        return bool(data.get("feature_switches", {}).get(key, True))

    async def web_get_feature_status(self):
        """读取功能开关：返回所有模块 + 当前开关状态"""
        async with self._lock:
            data = self._load()
            switches = data.get("feature_switches", {})
            return json_response({
                "modules": [
                    {"key": m["key"], "label": m["label"],
                     "enabled": bool(switches.get(m["key"], True))}
                    for m in self.FEATURE_MODULES
                ]
            })

    async def web_save_feature_status(self):
        """保存功能开关：{switches: {key: bool}}"""
        async with self._lock:
            payload = await request.json(default={})
            switches = payload.get("switches")
            if not isinstance(switches, dict):
                return error_response("switches 必须是对象", status_code=400)
            data = self._load()
            self._history_backup(data, "保存前自动备份")
            cur = dict(data.get("feature_switches", {}))
            for m in self.FEATURE_MODULES:
                if m["key"] in switches:
                    cur[m["key"]] = bool(switches[m["key"]])
            data["feature_switches"] = cur
            self._save(data)
            return json_response({"saved": True})

    def _redact_export_text(self, text: str) -> str:
        """导出前脱敏 data.json：移除局域网密码哈希/会话密钥/调试口令等敏感数据，
        确保任何访问终端都收不到密码类数据。解析失败时原样返回。"""
        try:
            obj = json.loads(text)
        except Exception:
            return text
        if not isinstance(obj, dict):
            return text
        lan = obj.get(LAN_DATA_KEY)
        if isinstance(lan, dict):
            lan.pop("password_hash", None)
            lan.pop("secret", None)
        params = obj.get("params")
        if isinstance(params, dict):
            for k in self._SENSITIVE_PARAMS:
                params.pop(k, None)
        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _snapshot_sensitive(self) -> dict:
        """快照当前数据中的敏感字段（导入/恢复用）：局域网密码哈希、会话密钥、调试口令。"""
        out = {}
        try:
            data = self._load()
            lan = data.get(LAN_DATA_KEY) or {}
            out["password_hash"] = lan.get("password_hash")
            out["secret"] = lan.get("secret")
            out["debug_password"] = (data.get("params") or {}).get("DEBUG_PASSWORD")
        except Exception:
            pass
        return out

    def _restore_sensitive(self, snapshot: dict) -> None:
        """导入后恢复敏感字段：备份/导入内容缺失或为空时，保留导入前的值，
        防止导入旧备份导致局域网访问失配或调试口令被清空。"""
        if not snapshot:
            return
        try:
            data = self._load()
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
            self._save(data)

    async def web_export_data(self):
        """导出全部数据：data.json + game_items.json（商店/打工/玩耍/作物/肥料/贷款套餐数值）。
        打包为单个 JSON 文件（files: {文件名: 内容}），由前端下载。
        2.2.0：data.json 导出前脱敏（局域网密码哈希/会话密钥/调试口令不随备份下发）。"""
        async with self._lock:
            files = {}
            for fn, path in self._exportable_files():
                text = self._read_file(path)
                if fn == "data.json":
                    text = self._redact_export_text(text)
                files[fn] = text
            return json_response({"files": files})

    def _exportable_files(self):
        """可导出的文件列表：存档 data.json + 记录数据 records.json + 数值配置 game_items.json"""
        from .data_migrate import RECORDS_FILE
        return [
            ("data.json", DATA_FILE),
            ("records.json", RECORDS_FILE),
            ("game_items.json", ITEMS_JSON_FILE),
        ]

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

    def _import_files(self, files: dict):
        """导入文件包：写入新格式文件；兼容旧版 txt 备份（还原后重新迁移为 game_items.json）。
        返回 (ok, msg_or_written_list)。
        2.2.0：导入前后保护敏感字段（备份文件不含密码哈希/会话密钥/调试口令时保留现有值）。"""
        sensitive = self._snapshot_sensitive()
        written = []
        for fn, path in self._exportable_files():
            if fn in files and isinstance(files[fn], str):
                if fn == "data.json":
                    ok, msg = self._write_data_text(files[fn])
                else:
                    ok, msg = self._write_file(path, files[fn])
                if ok:
                    written.append(fn)
                else:
                    return False, f"{fn} 导入失败: {msg}"
        legacy_hit = False
        for fn, path in self._LEGACY_TXT_FILES.items():
            if fn in files and isinstance(files[fn], str):
                ok, msg = self._write_file(path, files[fn])
                if ok:
                    written.append(fn)
                    legacy_hit = True
                else:
                    return False, f"{fn} 导入失败: {msg}"
        if legacy_hit and "game_items.json" not in files:
            # 旧版备份（txt）：删除现有 JSON 并按 txt 重新迁移
            try:
                if os.path.exists(ITEMS_JSON_FILE):
                    os.remove(ITEMS_JSON_FILE)
                _migrate_split_shop_config()
                self._migrate_items_to_json()
            except Exception as e:
                logger.warning(f"[插件] 导入旧版 txt 备份后重迁移失败: {e}")
        self._restore_sensitive(sensitive)
        # 2.2.2：导入旧格式备份（记录字段内嵌在 data.json）后立即按新标准拆分
        try:
            from .data_migrate import check_and_migrate_data
            check_and_migrate_data()
        except Exception as e:
            logger.warning(f"[插件] 导入后数据标准升级失败: {e}")
        return True, written

    async def web_import_data(self):
        """导入全部数据：JSON 请求体携带 files（{文件名: 内容}），覆盖写入对应文件。
        兼容旧格式：旧版 txt 备份（后台/宠物商店*.txt）还原后自动重新迁移为 game_items.json；
        更旧格式：仅 data.json 的 content 字段。2.2.0：导入后保留现有敏感字段。"""
        async with self._lock:
            payload = await request.json(default={})
            files = payload.get("files")
            if isinstance(files, dict):
                ok, result = self._import_files(files)
                if not ok:
                    return error_response(result, status_code=400)
                return json_response({"imported": True, "files": result})
            # 旧版格式：仅 data.json
            content = payload.get("content")
            if not isinstance(content, str):
                return error_response("files 或 content 必须提供", status_code=400)
            sensitive = self._snapshot_sensitive()
            ok, msg = self._write_data_text(content)
            if not ok:
                return error_response(msg, status_code=400)
            self._restore_sensitive(sensitive)
            try:
                from .data_migrate import check_and_migrate_data
                check_and_migrate_data()
            except Exception as e:
                logger.warning(f"[插件] 导入后数据标准升级失败: {e}")
            return json_response({"imported": True})

    def _read_file(self, path):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            logger.error(f"[插件] 读取文件失败: {e}")
        return ""

    def _write_file(self, path, text):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return True, "保存成功"
        except Exception as e:
            return False, f"保存失败: {e}"

    async def web_get_crops(self):
        """读取作物配置（结构化，供 WebUI 表格编辑）"""
        async with self._lock:
            flat = self._read_items_json()
            if flat is not None:
                items = [c for c in (self._norm_crop_entry(d) for d in flat["crops"]) if c]
            else:
                items = self._parse_crop_fert(CROP_FILE, "作物")
            return json_response({"items": items})

    async def web_save_crops(self):
        """保存作物配置：{items: [...]}，校验后写入 game_items.json（立即生效）"""
        async with self._lock:
            payload = await request.json(default={})
            items_in = payload.get("items")
            if not isinstance(items_in, list):
                return error_response("items 必须是数组", status_code=400)
            crops, errors = self._validate_flat_items(items_in, self._CROP_NUM_FIELDS, "作物")
            if errors:
                return json_response({"saved": False, "errors": errors})
            flat = self._read_items_json() or {"jobs": [], "plays": [], "shop": [], "crops": [], "ferts": [], "loans": []}
            self._history_backup(self._load(), "保存前自动备份")
            flat["crops"] = [self._norm_crop_entry(c) for c in crops]
            ok, msg = self._write_items_json(flat)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"saved": True, "items": len(crops)})

    async def web_get_ferts(self):
        """读取肥料配置（结构化，供 WebUI 表格编辑）"""
        async with self._lock:
            flat = self._read_items_json()
            if flat is not None:
                items = [f_ for f_ in (self._norm_fert_entry(d) for d in flat["ferts"]) if f_]
            else:
                items = self._parse_crop_fert(FERT_FILE, "肥料")
            return json_response({"items": items})

    async def web_save_ferts(self):
        """保存肥料配置：{items: [...]}，校验后写入 game_items.json（立即生效）"""
        async with self._lock:
            payload = await request.json(default={})
            items_in = payload.get("items")
            if not isinstance(items_in, list):
                return error_response("items 必须是数组", status_code=400)
            ferts, errors = self._validate_flat_items(items_in, self._FERT_NUM_FIELDS, "肥料")
            if errors:
                return json_response({"saved": False, "errors": errors})
            flat = self._read_items_json() or {"jobs": [], "plays": [], "shop": [], "crops": [], "ferts": [], "loans": []}
            self._history_backup(self._load(), "保存前自动备份")
            flat["ferts"] = [self._norm_fert_entry(f_) for f_ in ferts]
            ok, msg = self._write_items_json(flat)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"saved": True, "items": len(ferts)})

    async def web_apply_benchmark_items(self):
        """一键恢复默认道具数据：农场（作物/肥料）与宠物（商店商品）使用
        Benchmark data/game_items.json 的数值；打工/玩耍不受影响。"""
        async with self._lock:
            bench = _load_benchmark_items()
            if bench is None or not (bench["shop"] or bench["crops"] or bench["ferts"]):
                return error_response("Benchmark data/game_items.json 不存在或为空", status_code=400)
            shop, errors = self._validate_flat_items(bench["shop"], self._SHOP_NUM_FIELDS, "商店", check_type=True)
            if errors:
                return json_response({"saved": False, "errors": errors})
            crops = [c for c in (self._norm_crop_entry(d) for d in bench["crops"]) if c]
            ferts = [f_ for f_ in (self._norm_fert_entry(d) for d in bench["ferts"]) if f_]
            flat = self._read_items_json() or {"jobs": [], "plays": [], "shop": [], "crops": [], "ferts": [], "loans": []}
            self._history_backup(self._load(), "保存前自动备份")
            flat["shop"] = shop
            flat["crops"] = crops
            flat["ferts"] = ferts
            ok, msg = self._write_items_json(flat)
            if not ok:
                return error_response(msg, status_code=400)
            logger.info(f"[插件] 已应用 Benchmark 默认道具数据：商店 {len(shop)} / 作物 {len(crops)} / 肥料 {len(ferts)} 条")
            return json_response({"saved": True, "shop": len(shop), "crops": len(crops), "ferts": len(ferts)})

    async def web_get_loan_pkgs(self):
        """读取贷款套餐（结构化，供 WebUI 表格编辑）"""
        async with self._lock:
            flat = self._read_items_json()
            if flat is not None:
                items = [l for l in (self._norm_loan_entry(d) for d in flat["loans"]) if l]
            else:
                items = self._loans_from_txt()
            return json_response({"items": items})

    async def web_get_activities(self):
        """返回所有已注册活动：启用状态 + 参数表单 schema + 当前值（含覆盖）"""
        async with self._lock:
            data = self._load()
            enabled = data.get("activities", {})
            items = []
            for act in self._activities:
                schema = act.param_schema()
                values = {}
                for s in schema:
                    values[s["field"]] = getattr(act, s["field"], s.get("default", ""))
                items.append({
                    "id": act.id,
                    "name": act.name,
                    "time_str": act.time_str(),
                    "req_text": act.requirement_text(),
                    "commands": list(act.commands.keys()),
                    "enabled": bool(enabled.get(act.id, False)),
                    "expired": act.is_expired_now(),
                    "schema": schema,
                    "values": values,
                })
            return json_response({"activities": items})

    async def web_save_activities(self):
        """保存活动配置：{enabled: {id: bool}, configs?: {id: {字段: 值}}}"""
        async with self._lock:
            payload = await request.json(default={})
            enabled = payload.get("enabled")
            if not isinstance(enabled, dict):
                return error_response("enabled 必须是对象", status_code=400)
            data = self._load()
            self._history_backup(data, "保存前自动备份")
            cur = dict(data.get("activities", {}))
            for aid, flag in enabled.items():
                cur[aid] = bool(flag)
            data["activities"] = cur
            # 活动参数覆盖（可选）
            errors = {}
            configs = payload.get("configs")
            if isinstance(configs, dict):
                saved_cfg = dict(data.get("activity_config") or {})
                for aid, fields in configs.items():
                    if not isinstance(fields, dict):
                        continue
                    act = next((a for a in self._activities if a.id == aid), None)
                    if act is None:
                        continue
                    ok_fields = {}
                    field_errors = {}
                    for field, value in fields.items():
                        ok, err = act.validate_override(field, value)
                        if ok:
                            act.apply_override(field, value)
                            ok_fields[field] = value
                        else:
                            field_errors[field] = err
                    if field_errors:
                        errors[aid] = field_errors
                    if ok_fields:
                        saved_cfg[aid] = ok_fields
                data["activity_config"] = saved_cfg
            self._save(data)
            return json_response({"saved": True, "errors": errors})

    async def web_save_loan_pkgs(self):
        """保存贷款套餐：{items: [{code, max_amount, fav_req, pet_req, farm_req, rate}]}，
        校验（代码 3~10 整数且不重复）后写入 game_items.json（立即生效）"""
        async with self._lock:
            payload = await request.json(default={})
            items_in = payload.get("items")
            if not isinstance(items_in, list):
                return error_response("items 必须是数组", status_code=400)
            loans, errors = self._validate_flat_items(items_in, self._LOAN_NUM_FIELDS, "贷款套餐", code_mode=True)
            if errors:
                return json_response({"saved": False, "errors": errors})
            flat = self._read_items_json() or {"jobs": [], "plays": [], "shop": [], "crops": [], "ferts": [], "loans": []}
            self._history_backup(self._load(), "保存前自动备份")
            flat["loans"] = loans
            ok, msg = self._write_items_json(flat)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"saved": True, "items": len(loans)})

    # ================= 后台数据「待保存」容灾草稿（2.2.2） =================
    # 管理员在待保存状态下离开 WebUI 时，前端把未保存修改暂存到 DRAFT_FILE；
    # 下次访问时前端读取草稿并弹窗询问是否保存。
    _DRAFT_ENDPOINTS = ("backend/config", "petshop", "farm/crops", "farm/ferts", "loan/packages",
                        "feature/status", "params", "activities", "alias/save")

    def _read_draft(self):
        try:
            if not os.path.exists(DRAFT_FILE):
                return None
            with open(DRAFT_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) and d.get("payloads") else None
        except Exception:
            return None

    def _write_draft(self, obj) -> None:
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

    async def web_get_config_draft(self):
        """读取容灾草稿（上次未保存的修改，无则 null）"""
        async with self._lock:
            return json_response({"draft": self._read_draft()})

    async def web_save_config_draft(self):
        """暂存/清除容灾草稿：POST {payloads: {面板: {endpoint, payload}}} 或 {clear: true}。
        只接受已知配置端点，避免任意内容写入临时文档。"""
        async with self._lock:
            payload = await request.json(default={})
            if payload.get("clear"):
                self._write_draft(None)
                return json_response({"cleared": True})
            payloads = payload.get("payloads")
            if not isinstance(payloads, dict) or not payloads:
                return error_response("payloads 必须是非空对象", status_code=400)
            cleaned = {}
            for pid, item in payloads.items():
                if not isinstance(item, dict):
                    continue
                ep = str(item.get("endpoint", ""))
                if ep in self._DRAFT_ENDPOINTS:
                    cleaned[str(pid)[:40]] = {"endpoint": ep, "payload": item.get("payload")}
            if not cleaned:
                return error_response("没有可暂存的数据", status_code=400)
            self._write_draft({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "payloads": cleaned})
            return json_response({"saved": True})

    # ================= 历史配置数据（2.2.2：historydata/setting） =================
    # 配置数据 = data.json 的设置类键 + game_items.json（商店/打工/玩耍/作物/肥料/贷款套餐）；
    # 用户/游戏存档数据（users/pets/bank/farms/loans/roulette/ledger 等）不进历史、不被回溯覆盖。
    _HISTORY_CONFIG_KEYS = ("params", "alias_cmds", "activities", "activity_config", "feature_switches")
    # ponytail: 固定保留最近 30 个版本，超出丢弃最旧；需要可配置再加设置项
    _HISTORY_KEEP = 30

    def _history_capture(self, data: dict) -> dict:
        """抓取当前配置数据快照（敏感参数脱敏，不进历史）"""
        cfg = {}
        for k in self._HISTORY_CONFIG_KEYS:
            if k in data:
                cfg[k] = data[k]
        if isinstance(cfg.get("params"), dict):
            cfg["params"] = {k: v for k, v in cfg["params"].items() if k not in self._SENSITIVE_PARAMS}
        items = None
        try:
            if os.path.exists(ITEMS_JSON_FILE):
                with open(ITEMS_JSON_FILE, "r", encoding="utf-8") as f:
                    items = json.load(f)
        except Exception:
            items = None
        return {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "items": items, "config": cfg}

    def _history_backup(self, data: dict, reason: str) -> None:
        """把当前配置数据存为一个历史版本（开关 data.history_keep 开启时记录；
        回溯前的自动备份不受开关限制，始终记录）"""
        try:
            if not data.get("history_keep") and reason != "回溯前自动备份":
                return
            os.makedirs(HISTORY_DIR, exist_ok=True)
            snap = self._history_capture(data)
            snap["reason"] = reason
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(3)
            tmp = os.path.join(HISTORY_DIR, ts + ".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=2)
            os.replace(tmp, os.path.join(HISTORY_DIR, ts + ".json"))
            files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".json"))
            for old in files[: max(0, len(files) - self._HISTORY_KEEP)]:
                try:
                    os.remove(os.path.join(HISTORY_DIR, old))
                except OSError:
                    pass
        except Exception as e:
            logger.error(f"[插件] 保存历史配置失败: {e}")

    def _history_verify_captcha(self, file: str, code) -> tuple:
        """校验回溯验证码（与发起时生成的版本一一对应，5 分钟有效）"""
        st = getattr(self, "_history_captcha", None)
        if not isinstance(st, dict) or st.get("code") != str(code or ""):
            return False, "验证码不正确，请重新获取"
        if st.get("file") != file:
            return False, "验证码与所选版本不匹配"
        if datetime.now().timestamp() > float(st.get("exp", 0) or 0):
            return False, "验证码已过期，请重新获取"
        return True, ""

    def _history_restore(self, file: str) -> tuple:
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
        self._history_backup(self._load(), "回溯前自动备份")
        items = snap.get("items")
        if isinstance(items, dict):
            ok, msg = self._write_items_json(items)
            if not ok:
                return False, msg
        data = self._load()
        cur_params = dict(data.get("params") or {})
        cfg = snap.get("config") if isinstance(snap.get("config"), dict) else {}
        for k in self._HISTORY_CONFIG_KEYS:
            if k in cfg:
                data[k] = cfg[k]
        # 快照里被脱敏的敏感参数（调试口令等）保留当前值，不被清空
        if isinstance(data.get("params"), dict):
            for k in self._SENSITIVE_PARAMS:
                if k in cur_params and k not in data["params"]:
                    data["params"][k] = cur_params[k]
        self._save(data)
        try:
            self._apply_runtime_params(data.get("params") or {})
        except Exception:
            pass
        return True, "已回溯到 " + str(snap.get("time") or os.path.basename(file))

    async def web_history_list(self):
        """读取历史配置版本列表 + 保留开关状态"""
        async with self._lock:
            data = self._load()
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
            return json_response({"enabled": bool(data.get("history_keep")), "versions": versions})

    async def web_history_toggle(self):
        """开关历史数据保留功能：POST {enabled: bool}（设置 → 数据导入导出）"""
        async with self._lock:
            payload = await request.json(default={})
            data = self._load()
            data["history_keep"] = bool(payload.get("enabled"))
            self._save(data)
            return json_response({"saved": True, "enabled": bool(data["history_keep"])})

    async def web_history_captcha(self):
        """生成回溯验证码（6 位数字 + 大小写字母），5 分钟内有效，仅对指定版本可用"""
        async with self._lock:
            payload = await request.json(default={})
            file = str(payload.get("file") or "")
            if not file.endswith(".json") or "/" in file or "\\" in file \
                    or not os.path.isfile(os.path.join(HISTORY_DIR, file)):
                return error_response("历史版本不存在", status_code=400)
            code = "".join(secrets.choice(_string.ascii_letters + _string.digits) for _ in range(6))
            self._history_captcha = {"file": file, "code": code, "exp": datetime.now().timestamp() + 300}
            return json_response({"captcha": code})

    async def web_history_verify(self):
        """回溯第一步校验：只校验验证码与管理员密码，不执行回溯（第二步再发 rollback）"""
        async with self._lock:
            payload = await request.json(default={})
            file = str(payload.get("file") or "")
            if not file.endswith(".json") or "/" in file or "\\" in file:
                return error_response("参数不合法", status_code=400)
            ok, msg = self._history_verify_captcha(file, payload.get("captcha"))
            if not ok:
                return error_response(msg, status_code=400)
            data = self._load()
            lan = self._lan_conf(data)
            if lan.get("password_hash") and not _lan_verify_password(lan["password_hash"], str(payload.get("password") or "")):
                return error_response("管理员密码不正确", status_code=400)
            return json_response({"verified": True})

    async def web_history_rollback(self):
        """回溯：POST {file, password, captcha}。前端需先点第一个确认按钮（校验），
        再点第二个确认按钮发起本请求；校验验证码 + 管理员密码
        （未设置局域网访问密码时仅校验验证码）。"""
        async with self._lock:
            payload = await request.json(default={})
            file = str(payload.get("file") or "")
            if not file.endswith(".json") or "/" in file or "\\" in file:
                return error_response("参数不合法", status_code=400)
            ok, msg = self._history_verify_captcha(file, payload.get("captcha"))
            if not ok:
                return error_response(msg, status_code=400)
            data = self._load()
            lan = self._lan_conf(data)
            if lan.get("password_hash") and not _lan_verify_password(lan["password_hash"], str(payload.get("password") or "")):
                return error_response("管理员密码不正确", status_code=400)
            ok, msg = self._history_restore(file)
            if not ok:
                return error_response(msg, status_code=400)
            self._history_captcha = None
            return json_response({"restored": True, "msg": msg})

    async def web_history_delete(self):
        """删除某个历史版本：POST {file}"""
        async with self._lock:
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

    # ================= 宠物：指令 =================
    async def web_sync_group_names(self):
        """WebUI 按钮：拉取所有群聊的成员昵称（get_group_list → get_group_member_list），
        存入 data.group_names 作为排行榜用户默认昵称；返回统计结果。"""
        async with self._lock:
            bots = self._collect_bots()
            if not bots:
                return error_response(
                    "未找到支持群成员接口的平台机器人（需要 aiocqhttp / OneBot 适配器，且机器人已连接）",
                    status_code=400,
                )
            data = self._load()
            names = {}
            groups, members = 0, 0

            async def _sync_bot(bot):
                nonlocal groups, members
                call_action = getattr(bot, "call_action", None)
                if call_action is None:
                    call_action = getattr(getattr(bot, "api", None), "call_action", None)
                if call_action is None:
                    return
                try:
                    ret = await call_action("get_group_list")
                except Exception as e:
                    logger.error(f"[插件] 同步群昵称 get_group_list 失败: {e}")
                    return
                gl = ret if isinstance(ret, list) else (ret.get("data") if isinstance(ret, dict) else [])
                if not isinstance(gl, list):
                    gl = []
                for g in gl:
                    if not isinstance(g, dict):
                        continue
                    gid = g.get("group_id")
                    if not gid:
                        continue
                    try:
                        mret = await call_action("get_group_member_list", group_id=gid)
                        ml = mret if isinstance(mret, list) else (mret.get("data") if isinstance(mret, dict) else [])
                        if not isinstance(ml, list):
                            ml = []
                        g_map = names.setdefault(str(gid), {})
                        for m in ml:
                            if not isinstance(m, dict):
                                continue
                            uid = m.get("user_id")
                            if not uid:
                                continue
                            nick = str(m.get("card") or m.get("nickname") or "").strip()
                            if nick:
                                g_map[str(uid)] = nick
                                members += 1
                        groups += 1
                    except Exception as e:
                        logger.debug(f"[插件] 同步群昵称失败 gid={gid}: {e}")

            await asyncio.gather(*[_sync_bot(b) for b in bots])
            if not groups:
                return error_response("同步完成但未获取到任何群聊（机器人可能未加入群聊）", status_code=400)
            if names:
                data["group_names"] = names
                self._save(data)
            msg = f"已同步 {groups} 个群、共 {members} 名成员昵称（作为排行榜默认昵称）"
            logger.info(f"[插件] WebUI 同步群昵称：{msg}")
            return json_response({"ok": True, "groups": groups, "members": members, "msg": msg})

    # ================= 运行记录（2.0.4）：宠物记录 / 商店价格 =================
    def _record_account_name(self, data: dict, uid) -> str:
        """账户昵称（2.1.0）：取用户任意群聊里记录的昵称（WebUI 同步的 group_names 优先，
        实时标记的 group_members 次之）；与自定义昵称 `_custom_name_of` 组成回退链：
        自定义昵称 → 账户昵称 → uid。"""
        uid = str(uid)
        for src in ("group_names", "group_members"):
            groups = data.get(src) or {}
            if not isinstance(groups, dict):
                continue
            for g in groups.values():
                if not isinstance(g, dict):
                    continue
                info = g.get(uid)
                if isinstance(info, dict):
                    nm = str(info.get("name", "") or "").strip()
                elif isinstance(info, str):
                    nm = str(info).strip()
                else:
                    nm = ""
                if nm:
                    return nm
        return ""

    async def web_get_record_pets(self):
        """运行记录·宠物记录：全部宠物卡片（当前状态 / 正在进行的活动 / 自动购买·自动打工信息）。
        2.2.0：宠物每日结算由固定结算循环统一执行，此处只读取已结算结果。"""
        async with self._lock:
            data = self._load()
            now_ts = datetime.now().timestamp()
            pets = []
            for uid, pet in (data.get("pets") or {}).items():
                if not isinstance(pet, dict):
                    continue
                u = data.get("users", {}).get(uid) or {}
                custom = self._custom_name_of(data, uid)
                sat_max, thr_max, sta_max, mood_max = self._attr_max(pet["health"])
                now_ts = datetime.now().timestamp()
                busy_until = self._pet_busy_until(pet)
                busy = None
                if busy_until > now_ts:
                    busy = {
                        "activity": pet.get("busy_activity"),
                        "item": pet.get("busy_item"),
                        "until": busy_until,
                        "remaining_min": int((busy_until - now_ts) // 60),
                    }
                tickets = {
                    "sat": pet.get("satiety", 0), "thr": pet.get("thirst", 0),
                    "sta": pet.get("stamina", 0), "mood": pet.get("mood", 0),
                    "health": pet.get("health", 0),
                    "sat_max": sat_max, "thr_max": thr_max, "sta_max": sta_max,
                    "mood_max": mood_max, "health_max": PET_MAX_HEALTH,
                    "sat_red": self._attr_is_red("饱食", pet.get("satiety", 0)),
                    "thr_red": self._attr_is_red("口渴", pet.get("thirst", 0)),
                    "mood_red": self._attr_is_red("心情", pet.get("mood", 0)),
                    "health_red": self._attr_is_red("健康", pet.get("health", 0)),
                }
                pets.append({
                    "uid": uid,
                    # 2.2.2：主人ID显示平台昵称（自定义昵称 → 账户昵称 → uid）
                    "nick": custom or self._record_account_name(data, uid) or uid,
                    "name": pet.get("name", "宠物"),
                    "level": pet.get("level", 0),
                    "exp": round(float(pet.get("exp", 0) or 0), 1),
                    "tier": self._worst_tier(pet.get("satiety", 0), pet.get("thirst", 0), pet.get("mood", 0)),
                    "weak": bool(pet.get("weak")),
                    "attrs": tickets,
                    "busy": busy,
                    "auto": {
                        "purchase_on": bool(u.get("auto_feed_enabled")),
                        "purchase_global": bool(globals().get("AUTO_FEED_ENABLED", False)),
                        "work_on": bool(u.get("auto_work_enabled")),
                        "work_global": bool(globals().get("AUTO_WORK_ENABLED", True)),
                        "work_base": int(u.get("work_base", 0) or 0),
                        "work_next": float(u.get("auto_work_next", 0) or 0),
                        # 2.2.1：自动化贷款当前未还清欠款总额（0 = 无欠款）
                        "auto_loan_owed": self._auto_loan_owed_of(data, uid),
                        "feed_logs": (u.get("auto_feed_logs") or [])[-5:],
                        "work_logs": (u.get("auto_work_logs") or [])[-5:],
                    },
                })
            return json_response({"pets": pets})

    async def web_toggle_record_auto(self):
        """运行记录·宠物记录：管理员在 WebUI 直接切换某个用户的 自动购买/自动打工 开关。
        入参 {uid, key: purchase|work, on: bool}；同一用户维度，跨群共享。
        规则与群聊指令一致：开启自动购买 → 自动开启自动打工；不开启自动购买则不允许开启自动打工。
        2.1.0：管理员开启自动购买时立即触发一次自动购买（清空失败冷却，宠物处于第 3/4 档则立即补满，
        触发来源 = 管理员开启）。"""
        try:
            payload = await request.json(default={})
        except Exception:
            payload = {}
        uid = str(payload.get("uid") or "").strip()
        key = str(payload.get("key") or "").strip()
        on = bool(payload.get("on"))
        if not uid:
            return error_response("uid 不能为空", status_code=400)
        if key not in ("purchase", "work"):
            return error_response("key 只能是 purchase 或 work", status_code=400)
        async with self._lock:
            data = self._load()
            if uid not in (data.get("pets") or {}):
                return error_response("该用户还没有宠物", status_code=400)
            u = self._ensure_user(data, uid)
            if key == "work":
                if on and not u.get("auto_feed_enabled"):
                    return error_response("该用户未开启自动购买，无法开启自动打工（请先开启自动购买）", status_code=400)
                u["auto_work_enabled"] = on
                if on:
                    u.setdefault("work_base", int(u.get("work_base", 0) or 0))
                    u["auto_work_next"] = 0  # 立即可调度（基准金币 > 100 才真正执行）
                    self._ensure_auto_work_loop()
                    self._ensure_daily_settle_loop()
                msg = "已开启自动打工" if on else "已关闭自动打工"
            else:  # purchase
                u["auto_feed_enabled"] = on
                if on:
                    u["auto_work_enabled"] = True  # 用户开启自动购买 → 自动开启自动打工
                    u.setdefault("work_base", int(u.get("work_base", 0) or 0))
                    u["auto_work_next"] = 0
                    self._ensure_auto_work_loop()
                    self._ensure_daily_settle_loop()
                    msg = "已开启自动购买（自动打工同步开启）"
                    # 2.1.0：管理员开启自动购买 → 立即触发一次自动购买
                    u["auto_purchase_cool"] = 0
                    if self._auto_purchase_due(data, uid):
                        entry = self._auto_purchase_settle(data, uid, trigger="管理员开启")
                        if entry and entry.get("items"):
                            items = "、".join(
                                f"{'🛒' if it.get('src') == '购买' else '📦'}{it['name']}×{it['qty']}"
                                for it in entry["items"])
                            msg += f"；已立即触发自动购买：{items}（花费 {entry.get('total', 0)} 金币）"
                        else:
                            msg += "；已立即触发自动购买，但金币不足/无可用道具，未能补满（稍后自动重试）"
                    else:
                        msg += "；已立即检查：宠物状态良好，无需购买"
                else:
                    u["auto_work_enabled"] = False
                    msg = "已关闭自动购买（自动打工同步关闭）"
            self._save(data)
            return json_response({
                "ok": True, "msg": msg, "uid": uid, "key": key, "on": bool(u["auto_work_enabled"] if key == "work" else u["auto_feed_enabled"]),
                "purchase_on": bool(u.get("auto_feed_enabled")),
                "work_on": bool(u.get("auto_work_enabled")),
                "work_base": int(u.get("work_base", 0) or 0),
            })

    async def web_get_record_pet_detail(self):
        """运行记录·宠物记录·详情（2.2.1）：POST {uid} → 单只宠物的完整详情
        （当前状态/基本档案/每日结算/自动化/特殊记录 + 每种行为的属性变化记录 attr_log），
        宠物记录页点击宠物卡片进入详情页时按需拉取。"""
        try:
            payload = await request.json(default={})
        except Exception:
            payload = {}
        uid = str(payload.get("uid") or "").strip()
        if not uid:
            return error_response("uid 不能为空", status_code=400)
        try:
            async with self._lock:
                data = self._load()
                pet = data.get("pets", {}).get(uid)
                if not isinstance(pet, dict):
                    return json_response({"pet": None})
                return json_response({"pet": self._record_pet_detail_payload(data, uid, pet)})
        except Exception as e:
            logger.error(f"[插件] 运行记录·宠物详情 读取失败: {e}")
            return json_response({"pet": None, "error": str(e)})

    def _record_pet_detail_payload(self, data: dict, uid: str, pet: dict) -> dict:
        """（同步，锁内调用）单只宠物的完整详情数据（2.2.1）：
        分类展示宠物各项信息（基本档案/当前状态/每日结算/自动化/特殊记录/仓库道具），
        以及每种行为（每日结算/打工/玩耍/使用道具/治疗/自动购买/自动打工）造成的属性变化记录。"""
        u = data.get("users", {}).get(uid) or {}
        custom = self._custom_name_of(data, uid)
        now_ts = datetime.now().timestamp()
        sat_max, thr_max, sta_max, mood_max = self._attr_max(pet["health"])
        busy_until = self._pet_busy_until(pet)
        busy = None
        if busy_until > now_ts:
            busy = {
                "activity": pet.get("busy_activity"),
                "item": pet.get("busy_item"),
                "until": busy_until,
                "remaining_min": int((busy_until - now_ts) // 60),
            }
        tickets = {
            "sat": pet.get("satiety", 0), "thr": pet.get("thirst", 0),
            "sta": pet.get("stamina", 0), "mood": pet.get("mood", 0),
            "health": pet.get("health", 0),
            "sat_max": sat_max, "thr_max": thr_max, "sta_max": sta_max,
            "mood_max": mood_max, "health_max": PET_MAX_HEALTH,
            "sat_red": self._attr_is_red("饱食", pet.get("satiety", 0)),
            "thr_red": self._attr_is_red("口渴", pet.get("thirst", 0)),
            "mood_red": self._attr_is_red("心情", pet.get("mood", 0)),
            "health_red": self._attr_is_red("健康", pet.get("health", 0)),
        }
        level, exp_got, exp_need = self._pet_exp_progress(float(pet.get("exp", 0) or 0))
        # 属性变化记录（attr_log，最新在前）
        logs = []
        for it in (pet.get("attr_log") or []):
            if not isinstance(it, dict):
                continue
            logs.append({
                "time": str(it.get("time", "") or ""),
                "ts": float(it.get("ts", 0) or 0),
                "cat": str(it.get("cat", "其他") or "其他"),
                "behavior": str(it.get("behavior", "") or ""),
                "changes": it.get("changes", {}) or {},
                "extra": str(it.get("extra", "") or ""),
                # 2.2.2：变动后属性快照（属性条可视化用；旧记录可能没有）
                "after": it.get("after", {}) or {},
                # 2.2.2：变动发生时的属性上限快照（旧记录可能没有）
                "max": it.get("max", {}) or {},
            })
        logs.reverse()
        ls = pet.get("last_settle") or {}
        return {
            "uid": uid,
            # 2.2.2：主人ID显示平台昵称（自定义昵称 → 账户昵称 → uid）
            "nick": custom or self._record_account_name(data, uid) or uid,
            "name": pet.get("name", "宠物"),
            "level": level,
            "exp": round(float(pet.get("exp", 0) or 0), 1),
            "exp_got": round(exp_got, 1),
            "exp_need": round(exp_need, 1),
            "tier": self._worst_tier(pet.get("satiety", 0), pet.get("thirst", 0), pet.get("mood", 0)),
            "weak": bool(pet.get("weak")),
            "guard": bool(pet.get("guard")),
            "attrs": tickets,
            "busy": busy,
            "last_settle_date": str(pet.get("last_settle_date", "") or ""),
            "last_settle": {
                "date": str(ls.get("date", "") or ""),
                "satiety_d": ls.get("satiety_d", 0),
                "thirst_d": ls.get("thirst_d", 0),
                "stamina_d": ls.get("stamina_d", 0),
                "mood_d": ls.get("mood_d", 0),
                "health_d": ls.get("health_d", 0),
                "tier": ls.get("tier", 0),
                "rested_well": bool(ls.get("rested_well")),
                "sick": bool(ls.get("sick")),
            } if ls else None,
            "pill": {
                "used": int(pet.get("pill_used_count", 0) or 0),
                "limit": int(getattr(self, "pill_daily_limit", 3) or 3),
                "today": str(pet.get("pill_used_date", "") or ""),
            },
            "money_event": {
                "count": int(pet.get("money_event_count", 0) or 0),
                "max": int(getattr(self, "money_event_max_per_day", 5) or 5),
                "today": str(pet.get("money_event_date", "") or ""),
            },
            "bag": [{"name": str(k), "qty": int(v)} for k, v in (pet.get("inventory") or {}).items()],
            "auto": {
                "purchase_on": bool(u.get("auto_feed_enabled")),
                "purchase_global": bool(globals().get("AUTO_FEED_ENABLED", False)),
                "work_on": bool(u.get("auto_work_enabled")),
                "work_global": bool(globals().get("AUTO_WORK_ENABLED", True)),
                "work_base": int(u.get("work_base", 0) or 0),
                "work_next": float(u.get("auto_work_next", 0) or 0),
                "auto_loan_owed": self._auto_loan_owed_of(data, uid),
                "feed_logs": (u.get("auto_feed_logs") or [])[-5:],
                "work_logs": (u.get("auto_work_logs") or [])[-5:],
            },
            "attr_log": logs,
            "attr_labels": dict(ATTR_SHORT),
        }

    async def web_get_record_users(self):
        """运行记录·用户信息（2.1.0）：全部用户的信息卡片（基础信息）。
        2.2.0：列表只返回卡片级基础字段（昵称/金币/好感等级/宠物·农场等级/银行汇总/活跃时间/排序键），
        仓库/农场地块/存单明细等完整详情由「records/users/detail」按需拉取，避免全量下发给终端。
        排序键（昵称首拼/首字笔画）由后端计算随用户数据返回，前端本地排序（不依赖 query）。"""
        try:
            async with self._lock:
                return self._record_users_payload()
        except Exception as e:
            logger.error(f"[插件] 运行记录·用户信息 读取失败: {e}")
            return json_response({"users": [], "error": str(e)})

    async def web_get_record_user_detail(self):
        """运行记录·用户信息·详情（2.2.0）：POST {uid} → 单个用户的完整详情
        （仓库/农场地块/宠物/银行存单/自动化），页面展开卡片时才拉取。"""
        try:
            payload = await request.json(default={})
        except Exception:
            payload = {}
        uid = str(payload.get("uid") or "").strip()
        if not uid:
            return error_response("uid 不能为空", status_code=400)
        try:
            async with self._lock:
                data = self._load()
                u = data.get("users", {}).get(uid)
                if not isinstance(u, dict):
                    return json_response({"user": None})
                return json_response({"user": self._record_user_detail_payload(data, uid, u)})
        except Exception as e:
            logger.error(f"[插件] 运行记录·用户详情 读取失败: {e}")
            return json_response({"user": None, "error": str(e)})

    def _record_user_detail_payload(self, data: dict, uid: str, u: dict) -> dict:
        """（同步，锁内调用）单个用户的完整详情数据（2.2.0：按需拉取，替代全量下发的详情字段）。"""
        pet = data.get("pets", {}).get(uid)
        farm = data.get("farms", {}).get(uid)
        bank = data.get("bank", {}).get(uid)
        # ---- 宠物状态（详情独立附属卡片用） ----
        pet_info = None
        if isinstance(pet, dict):
            pet_info = {
                "name": pet.get("name", "宠物"),
                "level": pet.get("level", 0),
                "weak": bool(pet.get("weak")),
                "exp": round(float(pet.get("exp", 0) or 0), 1),
                "busy_activity": pet.get("busy_activity"),
                "busy_item": pet.get("busy_item"),
                "busy_until": float(pet.get("busy_until", 0) or 0),
            }
        # ---- 农场实时状态（详情独立附属卡片用：每块土地） ----
        farm_info = None
        if isinstance(farm, dict):
            now_ts = datetime.now().timestamp()
            crops_all = self._load_crops()
            plots = []
            for i, p in enumerate((farm.get("plots") or []), start=1):
                if not isinstance(p, dict):
                    continue
                crop = p.get("crop")
                grade_name = self._plot_grade(int(p.get("grade", 0) or 0))[0]
                if crop is None:
                    plots.append({"no": i, "grade": int(p.get("grade", 0) or 0), "grade_name": grade_name,
                                  "crop": None, "mature": False, "remain_min": 0,
                                  "yield": 0, "income": 0, "advance_min": 0})
                else:
                    c = self._find_item(crops_all, crop)
                    g = self._plot_growth(p, c, now_ts) if c else None
                    mature = now_ts >= float(p.get("mature_ts", 0) or 0)
                    remain = max(0, float(p.get("mature_ts", 0) or 0) - now_ts)
                    yield_n = int(p.get("yield", 0) or 0)
                    plots.append({"no": i, "grade": int(p.get("grade", 0) or 0), "grade_name": grade_name,
                                  "crop": str(crop), "mature": mature,
                                  "remain_min": int(remain // 60),
                                  "yield": yield_n,
                                  "income": int(round(yield_n * float(c["crop_price"]))) if c else 0,
                                  "advance_min": (int(g["advance_sec"] // 60) if g and g["advance_sec"] > 60 else 0)})
            # 2.2.3：农场仓库（作物/种子/肥料，按指令响应「农场仓库」的版式：名称 ×N 可售 X金币 / 小时不可售）
            wh = farm.get("warehouse", {}) if isinstance(farm.get("warehouse"), dict) else {}
            warehouse = {}
            for gkey, label in [("crops", "作物"), ("seeds", "种子"), ("fertilizers", "肥料")]:
                items = []
                for nm, cnt in ((wh.get(gkey) or {}).items()):
                    if gkey == "fertilizers":
                        items.append({"name": str(nm), "qty": round(float(cnt or 0), 2),
                                      "hours": True, "price": None})
                        continue
                    c = self._find_item(crops_all, nm)
                    if c:
                        price = float(c["crop_price"] if gkey == "crops" else c["seed_sell_price"])
                    else:
                        price = 0.0
                    items.append({"name": str(nm), "qty": int(cnt or 0), "hours": False, "price": price})
                warehouse[label] = items
            # 2.2.3：被偷记录（未收割 或 被偷批次收割后 24h 内有效，最新在前）
            steals = []
            for it in (farm.get("steal_infos") or []):
                if not isinstance(it, dict):
                    continue
                ht = it.get("harvest_ts")
                if ht is not None and now_ts - float(ht or 0) > 86400:
                    continue
                steals.append({
                    "time": datetime.fromtimestamp(float(it.get("ts", 0) or 0)).strftime("%Y-%m-%d %H:%M"),
                    "thief_name": str(it.get("thief_name", "") or ""),
                    "items": it.get("items", []) or [],
                    "harvested": ht is not None,
                })
            steals.reverse()
            farm_info = {
                "level": int(farm.get("level", 0) or 0),
                "exp": round(float(farm.get("exp", 0) or 0), 1),
                "plots": plots,
                "total_profit": int(farm.get("total_profit", 0) or 0),
                "warehouse": warehouse,
                "steal_infos": steals[:30],
            }
        # ---- 仓库（宠物背包，详情独立附属卡片用） ----
        bag = []
        if isinstance(pet, dict):
            inv = pet.get("inventory") or {}
            if isinstance(inv, dict):
                for nm, cnt in inv.items():
                    n = int(cnt or 0)
                    if n > 0:
                        bag.append({"name": str(nm), "qty": n})
                bag.sort(key=lambda x: (-x["qty"], x["name"]))
        # ---- 银行（详情独立附属卡片用：存单列表；统计数值取自银行模块 _bank_summary_of，WebUI 不另行统计） ----
        bank_info = None
        if isinstance(bank, dict) and bank.get("deposits"):
            deposits = []
            for d in (bank["deposits"] or []):
                if not isinstance(d, dict):
                    continue
                deposits.append({
                    "amount": int(d.get("amount", 0) or 0),
                    "interest": int(d.get("interest", 0) or 0),
                    "status": d.get("status", "locked"),
                    "hours": int(d.get("hours", 0) or 0),
                    "deposit_time": d.get("deposit_time", ""),
                    "base_rate": float(d.get("base_rate", 0) or 0),
                    "bonus_rate": float(d.get("bonus_rate", 0) or 0),
                })
            bank_info = self._bank_summary_of(data, uid)
            bank_info["deposits"] = deposits[:8]  # 最近 8 笔，前端折叠展示
        # ---- 2.2.3：详情页扩展（宠物属性/最近变动、农场记录与统计、签到日历、金币流水、欠款、最后活跃） ----
        la = float(u.get("last_active", 0) or 0)
        la_text = "-"
        if la > 0:
            diff = datetime.now().timestamp() - la
            if diff < 60:
                la_text = "刚刚"
            elif diff < 3600:
                la_text = f"{int(diff // 60)} 分钟前"
            elif diff < 86400:
                la_text = f"{int(diff // 3600)} 小时前"
            else:
                la_text = f"{int(diff // 86400)} 天前"
        # 宠物属性 + 最近一次属性变动（attr_log 最新一条）
        if pet_info is not None:
            sat_max, thr_max, sta_max, mood_max = self._attr_max(pet["health"])
            pet_info["attrs"] = {
                "satiety": round(float(pet.get("satiety", 0) or 0), 1),
                "thirst": round(float(pet.get("thirst", 0) or 0), 1),
                "stamina": round(float(pet.get("stamina", 0) or 0), 1),
                "mood": round(float(pet.get("mood", 0) or 0), 1),
                "health": round(float(pet.get("health", 0) or 0), 1),
                "satiety_max": sat_max, "thirst_max": thr_max,
                "stamina_max": sta_max, "mood_max": mood_max, "health_max": PET_MAX_HEALTH,
                "satiety_red": self._attr_is_red("饱食", pet.get("satiety", 0)),
                "thirst_red": self._attr_is_red("口渴", pet.get("thirst", 0)),
                "mood_red": self._attr_is_red("心情", pet.get("mood", 0)),
                "health_red": self._attr_is_red("健康", pet.get("health", 0)),
            }
            pet_info["last_change"] = None
            for it in reversed(pet.get("attr_log") or []):
                if isinstance(it, dict):
                    pet_info["last_change"] = {
                        "time": str(it.get("time", "") or ""),
                        "cat": str(it.get("cat", "") or ""),
                        "behavior": str(it.get("behavior", "") or ""),
                        "changes": it.get("changes", {}) or {},
                        "extra": str(it.get("extra", "") or ""),
                    }
                    break
        # 农场变化记录（farm_logs，最新在前，最近 60 条）+ 累计统计（farm_stats）
        farm_log_list = [x for x in (u.get("farm_logs") or []) if isinstance(x, dict)][-60:]
        farm_log_list.reverse()
        farm_stat = u.get("farm_stats") if isinstance(u.get("farm_stats"), dict) else {}
        # 签到日历（signin_logs，每天一条）+ 累计签到天数 / 累计签到金币
        sign_logs = [x for x in (u.get("signin_logs") or []) if isinstance(x, dict)][-120:]
        # 金币流水（ledger，最新在前，最近 60 条）
        ledger = [x for x in reversed(data.get("ledger", {}).get(uid) or []) if isinstance(x, dict)][:60]
        # 欠款账单统计取自贷款模块 _debt_summary_of（生效账单数 + 含息总额），WebUI 不另行统计
        debt = self._debt_summary_of(data, uid)
        return {
            "uid": uid,
            "nick": self._record_user_nick(data, uid),
            "fav": round(float(u.get("favorability", 0) or 0), 1),
            "fav_level": self._level_of(float(u.get("favorability", 0) or 0)),
            "coins": int(u.get("coins", 0) or 0),
            "pet": pet_info,
            "farm": farm_info,
            "bag": bag,
            "bank": bank_info,
            "auto": {
                "purchase_on": bool(u.get("auto_feed_enabled")),
                "work_on": bool(u.get("auto_work_enabled")),
                "work_base": int(u.get("work_base", 0) or 0),
                # 2.2.1：自动化贷款当前未还清欠款总额（0 = 无欠款）
                "auto_loan_owed": self._auto_loan_owed_of(data, uid),
            },
            # 2.2.3 扩展字段
            "last_active_text": la_text,
            "farm_logs": farm_log_list,
            "farm_stat": {
                "plant": int(farm_stat.get("plant", 0) or 0),
                "fertilize": int(farm_stat.get("fertilize", 0) or 0),
                "harvest": int(farm_stat.get("harvest", 0) or 0),
                "harvest_yield": int(farm_stat.get("harvest_yield", 0) or 0),
                "steal": int(farm_stat.get("steal", 0) or 0),
                "steal_gain": int(farm_stat.get("steal_gain", 0) or 0),
                "cost": int(farm_stat.get("cost", 0) or 0),
            },
            "sign_logs": sign_logs,
            "sign_total": int(u.get("signin_total", 0) or 0),
            "sign_coins_all": int(u.get("signin_coins_all", 0) or 0),
            "ledger": ledger,
            "debt": debt,
        }

    def _record_user_nick(self, data: dict, uid: str) -> str:
        """用户昵称回退链：自定义昵称 → 账户昵称 → uid。"""
        custom = self._custom_name_of(data, uid)
        if custom:
            return custom
        acc = self._record_account_name(data, uid)
        return acc or str(uid)

    def _record_users_payload(self):
        """（同步，锁内调用）组装全部用户信息卡片的基础数据（2.2.0：详情字段按需拉取）。"""
        data = self._load()
        now_ts = datetime.now().timestamp()
        users = []
        for uid, u in (data.get("users") or {}).items():
            if not isinstance(u, dict):
                continue
            pet = data.get("pets", {}).get(uid)
            farm = data.get("farms", {}).get(uid)
            bank = data.get("bank", {}).get(uid)
            custom = self._custom_name_of(data, uid)
            acc_name = self._record_account_name(data, uid)
            nick = custom or acc_name or uid
            la = float(u.get("last_active", 0) or 0)
            # 登录活跃时间：显示相对时间（如 3 分钟前 / 昨天 14:30）
            la_text = "-"
            if la > 0:
                diff = now_ts - la
                if diff < 60:
                    la_text = "刚刚"
                elif diff < 3600:
                    la_text = f"{int(diff // 60)} 分钟前"
                elif diff < 86400:
                    la_text = f"{int(diff // 3600)} 小时前"
                else:
                    la_text = f"{int(diff // 86400)} 天前"
            # ---- 卡片级汇总字段（详情字段由 records/users/detail 按需拉取） ----
            pet_summary = None
            if isinstance(pet, dict):
                pet_summary = {
                    "level": int(pet.get("level", 0) or 0),
                    "weak": bool(pet.get("weak")),
                }
            farm_summary = None
            if isinstance(farm, dict):
                farm_summary = {"level": int(farm.get("level", 0) or 0)}
            bank_summary = None
            if isinstance(bank, dict):
                # 2.2.3：银行汇总统计取自银行模块 _bank_summary_of（WebUI 不另行统计）
                bank_summary = self._bank_summary_of(data, uid)
            users.append({
                "uid": uid,
                "nick": nick,
                "nick_source": "custom" if custom else ("account" if acc_name else "uid"),
                "coins": int(u.get("coins", 0) or 0),
                "fav": round(float(u.get("favorability", 0) or 0), 1),
                "fav_level": self._level_of(float(u.get("favorability", 0) or 0)),
                "pet": pet_summary,
                "farm": farm_summary,
                "bank": bank_summary,
                "last_active": la,
                "last_active_text": la_text,
                "auto": {
                    "purchase_on": bool(u.get("auto_feed_enabled")),
                    "work_on": bool(u.get("auto_work_enabled")),
                    "work_base": int(u.get("work_base", 0) or 0),
                },
                # 2.1.0：前端本地排序键（昵称首拼 / 昵称首字笔画 由后端计算好，
                # 前端切换排序方式/升降序时不再重新请求，规避带 query 的 API 兼容性问题）
                "sort_pinyin": list(_record_nick_sort_key(nick, "pinyin")),
                "sort_stroke": list(_record_nick_sort_key(nick, "stroke")),
            })
        # 后端默认按最后活跃时间降序（排序交给前端，这里仅做基础稳定序）
        users.sort(key=lambda x: x["last_active"], reverse=True)
        return json_response({"users": users})

    async def web_get_record_prices(self):
        """运行记录·商店价格：最近的价格变动记录 + 当前窗口折扣信息。"""
        async with self._lock:
            data = self._load()
            added = self._record_shop_price_window(data)
            if added:
                self._save(data)
            enabled = bool(globals().get("SHOP_PRICE_FLOAT_ENABLED", False))
            now = datetime.now()
            _start, wid = self._shop_price_window(now)
            disc = self._shop_discount_map_cached(wid) if enabled else {}
            current = [
                {"name": it.get("name"), "base": int(it.get("price", 0)),
                 "price": max(1, int(round(int(it.get("price", 0)) * disc.get(it.get("name"), 1.0)))),
                 "mult": round(disc.get(it.get("name"), 1.0), 2)}
                for it in self._load_config().get("shop", []) if enabled
            ]
            special = _start in tuple(globals().get("SHOP_PRICE_SPECIAL_HOURS", (10, 12, 18, 0)))
            return json_response({
                "enabled": enabled,
                "now": now.strftime("%Y-%m-%d %H:%M"),
                "window": wid,
                "special": special,
                "current": current,
                "records": (data.get("shop_price_records") or [])[-int(globals().get("SHOP_PRICE_RECORD_MAX", 60) or 60):],
            })
