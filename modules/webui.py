# -*- coding: utf-8 -*-
# WebUI 后台 API。从原 main.py 的 SignInPlugin 拆出的 Mixin，由入口类组合继承。
from .base import *  # noqa: F401,F403  常量与共享工具
from .base import _register_runtime_module, _sync_runtime_global  # noqa: F401
import sys as _sys

_register_runtime_module(_sys.modules[__name__])


class WebUIMixin:
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
        """读取运行参数：返回参数 schema 列表（含当前值），前端据此渲染表单"""
        async with self._lock:
            data = self._load()
            saved = data.get("params") or {}
            items = []
            for spec in RUNTIME_PARAMS:
                key = spec["key"]
                cur = saved.get(key, globals().get(key, spec.get("default")))
                items.append({
                    "key": key,
                    "label": spec["label"],
                    "type": spec["type"],
                    "group": spec.get("group", "其他"),
                    "subgroup": spec.get("subgroup", "通用"),
                    "desc": spec.get("desc", ""),
                    "value": cur,
                    "min": spec.get("min"),
                    "max": spec.get("max"),
                })
            return json_response({"params": items})

    async def web_save_params(self):
        """保存运行参数：POST {params: {key: value}}，校验后立即生效并持久化"""
        async with self._lock:
            payload = await request.json(default={})
            incoming = payload.get("params")
            if not isinstance(incoming, dict):
                return error_response("params 必须是对象", status_code=400)
            data = self._load()
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
            flat["jobs"] = jobs
            flat["plays"] = plays
            ok, msg = self._write_items_json(flat)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"saved": True, "jobs": len(jobs), "plays": len(plays)})

    async def web_get_petshop(self):
        """读取宠物商店商品（结构化，供 WebUI 表格编辑）。
        保留旧字段 content/types（空字符串）仅为不破坏旧前端加载，新版前端使用 items。"""
        async with self._lock:
            flat = self._read_items_json()
            if flat is None:
                flat = self._items_normalized_to_flat(self._load_config())
            return json_response({"items": flat["shop"], "types": [], "content": ""})

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
            cur = dict(data.get("feature_switches", {}))
            for m in self.FEATURE_MODULES:
                if m["key"] in switches:
                    cur[m["key"]] = bool(switches[m["key"]])
            data["feature_switches"] = cur
            self._save(data)
            return json_response({"saved": True})

    async def web_export_data(self):
        """导出全部数据：data.json + game_items.json（商店/打工/玩耍/作物/肥料/贷款套餐数值）。
        打包为单个 JSON 文件（files: {文件名: 内容}），由前端下载。"""
        async with self._lock:
            files = {}
            for fn, path in self._exportable_files():
                files[fn] = self._read_file(path)
            return json_response({"files": files})

    def _exportable_files(self):
        """可导出的文件列表：存档 data.json + 数值配置 game_items.json"""
        return [
            ("data.json", DATA_FILE),
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
        返回 (ok, msg_or_written_list)"""
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
        return True, written

    async def web_import_data(self):
        """导入全部数据：JSON 请求体携带 files（{文件名: 内容}），覆盖写入对应文件。
        兼容旧格式：旧版 txt 备份（后台/宠物商店*.txt）还原后自动重新迁移为 game_items.json；
        更旧格式：仅 data.json 的 content 字段。"""
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
            ok, msg = self._write_data_text(content)
            if not ok:
                return error_response(msg, status_code=400)
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
            flat["loans"] = loans
            ok, msg = self._write_items_json(flat)
            if not ok:
                return error_response(msg, status_code=400)
            return json_response({"saved": True, "items": len(loans)})

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
