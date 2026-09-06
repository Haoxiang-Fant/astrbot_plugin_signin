# -*- coding: utf-8 -*-
# 农场系统（土地/种植/施肥/收割/售卖/商店/偷菜）。从原 main.py 的 SignInPlugin 拆出的 Mixin，由入口类组合继承。
import re

from .base import *  # noqa: F401,F403  常量与共享工具
from .base import _register_runtime_module  # noqa: F401
import sys as _sys

_register_runtime_module(_sys.modules[__name__])


class FarmMixin:
    # ================= 农场 =================
    def _parse_crop_fert(self, path: str, kind: str):
        items = _parse_kv_sections(path, kind)
        result = []
        for it in items:
            d = it["data"]
            if kind == "作物":
                result.append({
                    "name": it["name"],
                    "desc": d.get("描述", ""),
                    "seed_price": self._f(d.get("种子价格", 0)),
                    "seed_sell_price": self._f(d.get("种子卖出价格", 0)),
                    "yield": int(self._f(d.get("产量", 0))),
                    "crop_price": self._f(d.get("成熟作物价格", 0)),
                    "exp": int(self._f(d.get("收获经验值", 0))),
                    "min_level": int(self._f(d.get("最低农场等级要求", 0))),
                    "grow_minutes": int(self._f(d.get("成熟时间", 0))),
                })
            else:
                result.append({
                    "name": it["name"],
                    "desc": d.get("描述", ""),
                    "price": int(self._f(d.get("肥料价格", 0))),
                    "time_reduce": self._f(d.get("减少时间", 0)),
                    "yield_add": self._f(d.get("增加产量", 0)),
                    "max_uses": int(self._f(d.get("最大使用次数", -1))),
                    "max_accel": int(self._f(d.get("可加速次数", d.get("最大使用次数", -1)))),
                })
        return result

    def _norm_crop_entry(self, d: dict):
        """规范化一条作物配置（扁平 dict，键与 _parse_crop_fert 输出一致）"""
        if not isinstance(d, dict):
            return None
        name = str(d.get("name", "")).strip()
        if not name:
            return None
        return {
            "name": name,
            "desc": str(d.get("desc", "") or ""),
            "seed_price": self._f(d.get("seed_price", 0)),
            "seed_sell_price": self._f(d.get("seed_sell_price", 0)),
            "yield": int(self._f(d.get("yield", 0))),
            "crop_price": self._f(d.get("crop_price", 0)),
            "exp": int(self._f(d.get("exp", 0))),
            "min_level": int(self._f(d.get("min_level", 0))),
            "grow_minutes": int(self._f(d.get("grow_minutes", 0))),
        }

    def _norm_fert_entry(self, d: dict):
        """规范化一条肥料配置（2.0.0：max_accel 为可加速次数，替代 max_uses）"""
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
            "price": int(self._f(d.get("price", 0))),
            "time_reduce": self._f(d.get("time_reduce", 0)),
            "yield_add": self._f(d.get("yield_add", 0)),
            "max_uses": int(self._f(d.get("max_uses", -1))),
            "max_accel": max_accel,
        }

    def _load_crops(self):
        """1.7.7：优先读 game_items.json；不存在/损坏回退解析 作物.txt"""
        flat = self._read_items_json()
        if flat is not None:
            return [c for c in (self._norm_crop_entry(d) for d in flat["crops"]) if c]
        return self._parse_crop_fert(CROP_FILE, "作物")

    def _load_fertilizers(self):
        """1.7.7：优先读 game_items.json；不存在/损坏回退解析 肥料.txt"""
        flat = self._read_items_json()
        if flat is not None:
            return [f_ for f_ in (self._norm_fert_entry(d) for d in flat["ferts"]) if f_]
        return self._parse_crop_fert(FERT_FILE, "肥料")

    def _farm_of(self, data, key):
        return data.get("farms", {}).get(key)

    def _ensure_farm(self, data, key):
        return data.setdefault("farms", {}).setdefault(key, {
            "level": 0, "exp": 0.0, "plots": [],
            "warehouse": {"crops": {}, "seeds": {}, "fertilizers": {}},
            "tools": {},          # 农场特殊道具（如 农场经验球 2.0.1 改属农场）
            "total_profit": 0,
            "steal_infos": [], "steal_log": {}, "scent_memory": {},
        })

    @staticmethod
    def _new_plot():
        return {"grade": 0, "crop": None, "seed": None, "plant_ts": 0, "mature_ts": 0,
                "base_time": 0, "yield": 0, "fert_time": 0.0, "fert_yield": 0.0, "fert": {}}

    @staticmethod
    def _plot_grade(grade):
        grades = _farm_grades(globals().get("FARM_GRADE_BONUSES", ""))  # 2.0.2：WebUI「设置 → 农场 → 土地」表格可编辑
        return grades[grade] if 0 <= grade < len(grades) else grades[0]

    @staticmethod
    def _plot_free(plot):
        return plot is None or plot.get("crop") is None

    def _farm_seed_mult(self, farm):
        lv = int(farm.get("level", 0))
        if lv <= 19:
            return 1.5
        if lv <= 49:
            return 1.0
        if lv <= 99:
            return 0.8
        return 0.7

    def _farm_gain_exp(self, farm, amount) -> str:
        if int(farm.get("level", 0)) >= FARM_MAX_LEVEL:
            return ""
        farm["exp"] = float(farm.get("exp", 0.0)) + amount
        level = int(farm.get("level", 0))
        old = level
        while level < FARM_MAX_LEVEL:
            need = FARM_EXP_BASE * (level + 1)
            if farm["exp"] >= need:
                farm["exp"] = round(farm["exp"] - need, 2)
                level += 1
            else:
                break
        farm["level"] = level
        if level > old:
            return f"\n🎉 农场升级！Lv.{old} → Lv.{level}"
        return ""

    @staticmethod
    def _fmt_duration(sec):
        sec = max(0, int(sec))
        if sec < 60:
            return f"{sec}秒"
        minutes = sec // 60
        if minutes < 60:
            return f"{minutes}分钟"
        hours, rem_min = divmod(minutes, 60)
        if hours < 24:
            return f"{hours}小时{rem_min}分"
        days, rem_h = divmod(hours, 24)
        return f"{days}天{rem_h}小时"

    @staticmethod
    def _fmt_price(v):
        """价格显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0"""
        v = float(v)
        if v == int(v):
            return str(int(v))
        return f"{v:.2f}".rstrip("0").rstrip(".")

    @staticmethod
    def _find_item(items, name):
        return next((x for x in items if x["name"] == name), None)

    # ---- 2.0.0：作物成长阶段 & 化肥加速机制 ----
    @staticmethod
    def _fert_max_accel(fert):
        """化肥可加速次数（-1 = 不限）。兼容旧字段 max_uses"""
        v = fert.get("max_accel", fert.get("max_uses", -1))
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return -1

    def _crop_level_ranges(self):
        """作物等级划分上限（分钟）：如 (0,240,480,720,1440) → 0~240 一级 / 241~480 二级 / …"""
        raw = globals().get("CROP_LEVEL_RANGES", (0, 240, 480, 720, 1440))
        try:
            if isinstance(raw, str):
                vals = [float(x) for x in raw.replace("，", ",").split(",") if str(x).strip() != ""]
            else:
                vals = [float(x) for x in raw]
        except (TypeError, ValueError):
            vals = [0.0, 240.0, 480.0, 720.0, 1440.0]
        if len(vals) < 2:
            vals = [0.0, 240.0, 480.0, 720.0, 1440.0]
        return vals

    def _crop_stage_counts(self):
        """各级作物成长阶段数（默认 一~四级 = 4/5/5/6）"""
        raw = globals().get("CROP_LEVEL_STAGES", (4, 5, 5, 6))
        try:
            if isinstance(raw, str):
                vals = [int(float(x)) for x in raw.replace("，", ",").split(",") if str(x).strip() != ""]
            else:
                vals = [int(float(x)) for x in raw]
        except (TypeError, ValueError):
            vals = [4, 5, 5, 6]
        if not vals:
            vals = [4, 5, 5, 6]
        return vals

    def _crop_level_of(self, crop):
        """按贫瘠土地上的成熟分钟数划分作物等级（1~N 级）。
        CROP_LEVEL_RANGES 形如 (0,240,480,720,1440)：0 为一级下限，240/480/720/1440 为各等级上限。"""
        grow = int(self._f(crop.get("grow_minutes", 0)))
        ranges = self._crop_level_ranges()
        if len(ranges) <= 1:
            return 1
        for i in range(1, len(ranges)):
            if grow <= ranges[i]:
                return i
        return len(ranges)

    def _crop_stage_count(self, crop):
        """该作物在总生长周期内划分的成长阶段数"""
        lv = self._crop_level_of(crop)
        stages = self._crop_stage_counts()
        idx = min(lv - 1, len(stages) - 1)
        return max(1, int(stages[idx]))

    def _plot_growth(self, plot, crop, now):
        """计算一块种植地的生长状态（2.0.0 阶段模型）。返回 dict：
        base_sec 贫瘠总时间(秒) / total_sec 当前土地最大生长时间(秒, 含等级减时) /
        stage_count 阶段数 / stage_sec 每阶段时长(秒, 按贫瘠总时间均分) /
        elapsed_sec 已真实经过时间 / advance_sec 化肥已推进时间 /
        progress_sec 有效生长进度 = elapsed + advance /
        stage_idx 当前阶段(0 起) / stage_left_sec 当前阶段剩余时间 /
        remain_sec 距成熟剩余时间 / mature 是否已成熟"""
        base_sec = int(plot.get("base_time", 0)) or (crop["grow_minutes"] * 60 if crop else 0)
        _, _, gt = self._plot_grade(int(plot.get("grade", 0)))
        total_sec = base_sec * (1 - gt)
        elapsed_sec = max(0.0, float(now) - float(plot.get("plant_ts", 0)))
        advance_sec = float(plot.get("fert_advance", 0.0))
        if not plot.get("fert_advance") and plot.get("fert_time"):
            # 旧数据（1.7.9 及以前：减时比例）→ 折算为推进秒数
            advance_sec = float(plot.get("fert_time", 0.0)) * base_sec
        progress_sec = elapsed_sec + advance_sec
        stage_count = self._crop_stage_count(crop)
        stage_sec = base_sec / max(1, stage_count)
        stage_idx = min(stage_count - 1, int(progress_sec // stage_sec)) if stage_sec > 0 else 0
        stage_left_sec = stage_sec - (progress_sec % stage_sec)
        remain_sec = total_sec - progress_sec
        return {
            "base_sec": base_sec, "total_sec": total_sec,
            "stage_count": stage_count, "stage_sec": stage_sec,
            "elapsed_sec": elapsed_sec, "advance_sec": advance_sec,
            "progress_sec": progress_sec, "stage_idx": stage_idx,
            "stage_left_sec": stage_left_sec, "remain_sec": remain_sec,
            "mature": remain_sec <= 0,
        }

    def _fert_remaining_accel(self, plot, fert):
        """该化肥在这块地上的剩余可加速次数"""
        max_accel = self._fert_max_accel(fert)
        if max_accel < 0:
            return 10 ** 9
        used = int(plot.get("fert_accel", {}).get(fert["name"], 0))
        return max(0, max_accel - used)

    def _fert_actual_available_min(self, plot, crop, fert, now):
        """化肥实际可用时间（分钟）：
        理论最大使用时间 = 可加速次数 × 每阶段时间；
        实际可用时间 = 剩余可加速完整阶段次数 × 阶段时间 + 残余阶段总时间；
        且满足「当前所用时间 + 实际可用时间 ≤ 当前土地上的最大生长时间」。"""
        g = self._plot_growth(plot, crop, now)
        if g["mature"]:
            return 0.0
        stage_min = g["stage_sec"] / 60.0
        remain = self._fert_remaining_accel(plot, fert)
        if remain <= 0:
            return 0.0
        residual_min = g["stage_left_sec"] / 60.0
        if residual_min >= stage_min - 1e-9:
            # 恰在阶段起点：剩余加速都作用于完整阶段
            avail = remain * stage_min
        else:
            # 残余阶段先消耗一次加速（残余阶段总时间），其余为完整阶段
            avail = (remain - 1) * stage_min + residual_min
        cap = g["total_sec"] / 60.0 - g["progress_sec"] / 60.0
        if cap <= 0:
            return 0.0
        return max(0.0, min(avail, cap))

    def _fert_to_next_stage_min(self, plot, crop, now):
        """使用到下一个成长阶段所需的时间（分钟）= 当前阶段剩余时间"""
        g = self._plot_growth(plot, crop, now)
        if g["mature"]:
            return 0
        return max(0, int(g["stage_left_sec"] / 60.0 + 0.999))

    @staticmethod
    def _fmt_hours(h):
        """化肥库存显示：小时数（保留最多 2 位小数去尾 0）"""
        try:
            h = float(h)
        except (TypeError, ValueError):
            h = 0.0
        if h == int(h):
            return str(int(h))
        return f"{h:.2f}".rstrip("0").rstrip(".")

    # ---- 2.0.0：施肥指令解析（土地编号 / 时间） ----
    def _parse_fert_targets(self, raw, max_plots):
        """解析「施肥」指令的土地编号与使用分钟数。
        规则（不分全半角）：
        - 括号包裹的编号：（20）→ 20 号地；（1，7）→ 1~7 号地（区间）
        - 逗号/顿号分隔的编号：1，6 → 1 号、6 号地（逗号组内数字都视为土地编号）
        - 括号区间：如 （1,7）→ 第 1 到第 7 号地
        - 裸数字：≤ 最大土地数 → 土地编号；> 最大土地数 → 使用时间（分钟）
        返回 (土地编号列表, 时间分钟 or None)。"""
        if raw is None:
            return [], None
        s = str(raw).strip().replace("，", ",").replace("、", ",").replace("（", "(").replace("）", ")")
        if not s:
            return [], None
        plots = []
        time_min = None
        # 1) 括号组：区间或单号
        rest = s
        while "(" in rest:
            a = rest.find("(")
            b = rest.find(")", a)
            if b < 0:
                break
            grp = rest[a + 1:b].strip()
            rest = rest[:a] + " " + rest[b + 1:]
            if not grp:
                continue
            if "," in grp:
                nums = []
                for p in grp.split(","):
                    try:
                        nums.append(int(float(p.strip())))
                    except (TypeError, ValueError):
                        nums = []
                        break
                if len(nums) >= 2 and nums[0] <= nums[-1]:
                    plots.extend(range(nums[0], nums[-1] + 1))
            else:
                try:
                    plots.append(int(float(grp)))
                except (TypeError, ValueError):
                    continue
        # 2) 剩余文本：按空格切分（逗号组内数字均为土地编号）
        for chunk in re.split(r"\s+", rest):
            chunk = chunk.strip().strip(",")
            if not chunk:
                continue
            if "," in chunk:
                # 逗号分隔 → 全部视为土地编号
                for tok in chunk.split(","):
                    try:
                        plots.append(int(float(tok)))
                    except (TypeError, ValueError):
                        continue
            else:
                try:
                    n = int(float(chunk))
                except (TypeError, ValueError):
                    continue
                if n <= max_plots:
                    plots.append(n)
                else:
                    time_min = n
        # 3) 去重、排序
        seen = set()
        out = []
        for n in plots:
            if n >= 1 and n not in seen:
                seen.add(n)
                out.append(n)
        out.sort()
        return out, time_min

    def _farm_rank_text(self, uid, data):
        """农场排行榜的排行积分文本（标题右侧）"""
        try:
            entries = self._rank_entries("farm", data)
        except Exception:
            entries = []
        for i, (score, euid, _) in enumerate(entries, 1):
            if str(euid) == str(uid):
                return f"🌾 农场榜 第{i}名 · {self._fmt_score(score)}分"
        return "🌾 农场榜未上榜"

    # ---- 农场富文本图片 ----
    def _render_seed_shop(self, name, farm, crops):
        rows = []
        rows.append([(name, DS_TEXT, False)])
        lv = int(farm.get("level", 0))
        mult = self._farm_seed_mult(farm)
        if mult > 1:
            bonus = f"农场等级 Lv.{lv}：种子价格 +{int(round((mult - 1) * 100))}%"
        elif mult < 1:
            bonus = f"农场等级 Lv.{lv}：种子价格 -{int(round((1 - mult) * 100))}%"
        else:
            bonus = f"农场等级 Lv.{lv}：种子价格无加成"
        rows.append([(bonus, DS_MUTED, False)])
        rows.append([("", (0, 0, 0), False)])
        for c in crops:
            base = float(c["seed_price"])
            p = round(base * mult, 2)
            lv_req = f"（需 Lv.{c['min_level']}）" if c["min_level"] > 0 else ""
            if p > base:
                rows.append([(f"{c['name']} {self._fmt_price(p)} 金币{lv_req}", DS_TEXT, False)])
            elif p < base:
                rows.append([(f"{c['name']} ", DS_TEXT, False),
                             (f"{self._fmt_price(base)}", DS_TEXT, True),
                             (" ", (0, 0, 0), False),
                             (f"{self._fmt_price(p)} 金币{lv_req}", DS_DANGER, False)])
            else:
                rows.append([(f"{c['name']} {self._fmt_price(p)} 金币{lv_req}", DS_TEXT, False)])
        return self._render_rich_image("种子商店", rows)

    def _farm_shop_seed_list(self, farm, crops, expanded=False, page=1, all_items=False):
        """农场商店种子选择（2.0.2）：
        全部（「农场商店 全部」）= 所有种子（可购 + 不可购）；
        展开 = 全部可购种子按价格升序分页（每页 FARM_SHOP_SHOW_BUY 款）；
        默认 = 可购等级最高的 FARM_SHOP_SHOW_BUY 款 + 不可购等级最低的 FARM_SHOP_SHOW_LOCKED 款灰卡。
        统一按价格升序返回。"""
        farm_lv = int(farm.get("level", 0))
        mult = self._farm_seed_mult(farm)
        price_of = lambda c: int(round(float(c["seed_price"]) * mult))
        buy_n = int(globals().get("FARM_SHOP_SHOW_BUY", 9))
        lock_n = int(globals().get("FARM_SHOP_SHOW_LOCKED", 3))
        if all_items:
            return sorted(crops, key=lambda c: (price_of(c), c["name"]))
        buyable = [c for c in crops if c["min_level"] <= farm_lv]
        if expanded:
            sorted_buy = sorted(buyable, key=lambda c: (price_of(c), c["name"]))
            per_page = max(1, buy_n)
            total_pages = max(1, (len(sorted_buy) + per_page - 1) // per_page)
            page = max(1, min(page, total_pages))
            return sorted_buy[(page - 1) * per_page: page * per_page]
        topN = sorted(buyable, key=lambda c: c["min_level"], reverse=True)[:max(1, buy_n)]
        lowM = sorted((c for c in crops if c["min_level"] > farm_lv), key=lambda c: c["min_level"])[:max(0, lock_n)]
        return sorted(topN + lowM, key=lambda c: (price_of(c), c["name"]))

    def _render_farm_shop(self, name, farm, crops, ferts, expanded=False, page=1, all_items=False):
        """农场商店：种子（上）+ 化肥（下）分类卡片展示，完全套用商店卡片模板。
        图片排版：用户名 / 商品种类（居中）+ 居中分割线 / 商品卡片（每行 FARM_SHOP_COLS 张）。
        商品卡片：名称(大三号)+持有数(居右) / 等级条件(如有) / 效果(成熟售价+收割经验，空格优先换行) /
        卡片内分割线 / 价格(红 #C00000、居右、大一号、贴底边 N)；不可购买为灰卡 #D9D9D9。
        卡片高度自适应（同行取最高，低卡拉伸忽略分割线侧 N）；图片高度按绘制流程计算，文字不溢出。
        默认：能买等级最大的 FARM_SHOP_SHOW_BUY 款种子 + 不能买等级最低的 FARM_SHOP_SHOW_LOCKED 款（灰卡）；化肥全部。
        展开：按等级从高到低分页显示全部能购买的种子（每页 FARM_SHOP_SHOW_BUY 款）。
        全部（2.0.2「农场商店 全部」）：展示所有商品（全部种子 + 全部化肥）。"""
        Image, ImageDraw = _ensure_pillow()
        if Image is None:
            return None
        # 字号语义：标题 36（衬线）/ 分类 26 / 名称 26（大三号）/ 正文 18 / 价格 20（大一号）/ 原价 14（小一号）
        fonts = _load_fonts(26, 26, 18, 20, 14)
        if fonts is None:
            return None
        cat_font, name_font, body_font, price_font, small_price_font = fonts
        title_font = _title_font(kind="farm")
        if title_font is None:
            return None

        pad = 20
        title_h = 76
        cat_h = 30
        rule_h = 18
        gap = 12
        inner = 10
        name_h = 34  # 名称行高（大三号）
        line_h = 26
        price_h = 26  # 价格文字行高
        cols = int(globals().get("FARM_SHOP_COLS", 4))
        card_w = 246
        content_w = card_w - inner * 2
        n_pad = int(globals().get("SHOP_PRICE_PAD", 4))  # N：价格距分割线/底边

        tw = _text_measurer()
        if tw is None:
            return None

        wrap = _make_wrapper(tw, content_w)

        farm_lv = int(farm.get("level", 0))
        mult = self._farm_seed_mult(farm)
        seeds_have = farm.get("warehouse", {}).get("seeds", {})
        ferts_have = farm.get("warehouse", {}).get("fertilizers", {})
        disc_map = self._seed_discount_map(crops)  # 2.0.3：种子每日折扣（默认关闭）

        # ---- 种子选择 ----
        seed_list = self._farm_shop_seed_list(farm, crops, expanded, page, all_items)

        # ---- 卡片行规划（plain 行已按宽度换行展开，保证高度自适应） ----
        # 行类型：
        #   ("pair", 左, 右)   名称(大三号,左) + 持有数(右)
        #   ("pair2", 左, 右)  等级条件(左) + 成熟时间(右) / 售价(左) + 经验(右)
        #   ("plain", 文本)    普通文本行（自动换行）
        #   ("rule", "", "")   卡片内分割线（位置固定在价格上方 N，见下）
        #   ("price", 文本)    价格（红、右、大一号、贴底边 N）
        def seed_rows(c):
            rows = []
            cnt_text = f"×{int(seeds_have.get(c['name'], 0))}"
            if tw(c["name"], name_font) + tw(cnt_text, body_font) + 8 <= content_w:
                rows.append(("pair", c["name"], cnt_text))
            else:
                for ln in wrap(c["name"], name_font):
                    rows.append(("plain", ln, ""))
                rows.append(("plain", cnt_text, ""))
            # 商品描述：名称下方、要求（等级条件）上方
            if c.get("desc"):
                for ln in wrap(c["desc"], body_font):
                    rows.append(("plain", ln, ""))
            # 等级条件（左）+ 成熟时间（右）
            lv_t = f"需要 Lv.{c['min_level']}" if c["min_level"] > 0 else ""
            tm_t = f"成熟 {c['grow_minutes']} 分钟"
            if lv_t:
                rows.append(("pair2", lv_t, tm_t))
            else:
                rows.append(("plain", tm_t, ""))
            # 成熟后售价（贫瘠土地 + 无肥料状态）+ 收割农场经验（仅种子）
            sell_v = int(round(float(c["yield"]) * float(c["crop_price"])))
            rows.append(("pair2", f"售价 {sell_v} 金币", f"经验 {c['exp']}"))
            rows.append(("rule", "", ""))
            # 2.0.3：种子折扣 → 原价（灰小字）+ 折后价（红正常字）
            disc = float(disc_map.get(c["name"], 1.0) or 1.0)
            base_price = int(round(float(c["seed_price"]) * mult))
            price = max(1, int(round(base_price * disc)))
            rows.append(("price", f"{price} 金币", (f"{base_price} 金币" if disc < 1.0 else "")))
            return rows

        def fert_rows(f):
            rows = []
            have_h = float(ferts_have.get(f["name"], 0) or 0)
            cnt_text = f"×{self._fmt_hours(have_h)}h"
            if tw(f["name"], name_font) + tw(cnt_text, body_font) + 8 <= content_w:
                rows.append(("pair", f["name"], cnt_text))
            else:
                for ln in wrap(f["name"], name_font):
                    rows.append(("plain", ln, ""))
                rows.append(("plain", cnt_text, ""))
            # 商品描述：名称下方、效果上方
            if f.get("desc"):
                for ln in wrap(f["desc"], body_font):
                    rows.append(("plain", ln, ""))
            # 2.0.0：化肥效果 = 加速成长阶段 + 每加速一次的增产
            parts = []
            if float(f.get("yield_add", 0) or 0) > 0:
                parts.append(f"增产{f['yield_add']:.0f}%/次")
            if parts:
                for ln in wrap(" ".join(parts), body_font):
                    rows.append(("plain", ln, ""))
            maxa = "不限" if self._fert_max_accel(f) < 0 else f"{self._fert_max_accel(f)}次"
            rows.append(("plain", f"每株可加速 {maxa}"))
            rows.append(("rule", "", ""))
            rows.append(("price", f"{int(f['price'])} 金币/时", ""))
            return rows

        seed_plans = [(c, seed_rows(c), c["min_level"] > farm_lv) for c in seed_list]
        fert_plans = [(f, fert_rows(f), False)
                      for f in sorted(ferts, key=lambda f: (int(f["price"]), f["name"]))]

        # 卡片高度：内容区（inner*2 + 各行）+ 分割线间隙 N + 分割线半行 + 价格区（价格高 + 底边 N）
        # 分割线固定在价格上方 N 距离（N = SHOP_PRICE_PAD）
        def card_height(rows):
            h = inner * 2
            for r in rows:
                if r[0] == "pair":
                    h += name_h
                elif r[0] == "pair2":
                    h += line_h
                elif r[0] == "rule":
                    h += n_pad + 1  # 分割线距价格上方固定 N + 线本身 1px
                elif r[0] == "price":
                    h += price_h + n_pad  # 价格区 = 价格高 + 底边 N
                else:
                    h += line_h
            return h

        width = pad * 2 + card_w * cols + gap * (cols - 1)

        # 总高度：标题 + 展开提示 + 每区（类别标题 + 居中分割线 + 卡片组高和）+ 底部边距。
        # 与绘制流程完全一致（区之间无额外间距），保证最后一行卡片不溢出图片底部。
        def section_height(plans):
            h = 0
            for g in range(0, len(plans), cols):
                group = plans[g:g + cols]
                h += max(card_height(p[1]) for p in group) + gap
            return max(0, h - gap)

        subtitle = ""
        if expanded:
            per_page = max(1, int(globals().get("FARM_SHOP_SHOW_BUY", 9)))
            n_buy = sum(1 for c in crops if c["min_level"] <= int(farm.get("level", 0)))
            total_pages = max(1, (n_buy + per_page - 1) // per_page)
            subtitle = f"（展开模式：全部可购种子 第 {page}/{total_pages} 页，发送「农场商店 展开 {page + 1}」翻页）"
        height = pad * 2 + title_h + (26 if subtitle else 0)
        for plans in (seed_plans, fert_plans):
            height += cat_h + rule_h + section_height(plans)

        img = Image.new("RGB", (width, height), DS_BG)
        d = ImageDraw.Draw(img)
        y = pad
        _dtext(d, (pad, y), f"{name} 的农场商店", font=title_font, fill=DS_ACCENT)
        y += title_h
        if subtitle:
            _dtext(d, (pad, y), subtitle, font=body_font, fill=DS_GOLD)
            y += 26

        for title, plans in (("🌱 种子", seed_plans), ("🧪 化肥", fert_plans)):
            # 类别名（居中）
            cx = int(pad + (width - 2 * pad - tw(title, cat_font)) / 2)
            _dtext(d, (cx, y), title, font=cat_font, fill=DS_TEXT_2)
            y += cat_h
            # 居中分隔线
            d.line([(pad + 20, y), (width - pad - 20, y)], fill=DS_BORDER, width=2)
            y += rule_h
            for g in range(0, len(plans), cols):
                group = plans[g:g + cols]
                gh = max(card_height(p[1]) for p in group)
                for j, (item, rows, grey) in enumerate(group):
                    x0 = pad + j * (card_w + gap)
                    # 灰卡（不可购买）
                    if grey:
                        d.rectangle([x0, y, x0 + card_w, y + gh], fill=DS_BORDER_2, outline=DS_BORDER_2, width=1)
                    else:
                        d.rectangle([x0, y, x0 + card_w, y + gh], outline=DS_BORDER, width=1)
                    yy = y + inner
                    rule_y = None  # 分割线 y 坐标（固定在价格上方 N 距离）
                    price_y = y + gh - n_pad - price_h  # 价格基线
                    for r in rows:
                        kind = r[0]
                        if kind == "pair":
                            _dtext(d, (int(x0 + inner), yy), r[1], font=name_font, fill=DS_TEXT)
                            _dtext(d, (int(x0 + card_w - inner - tw(r[2], body_font)), yy + 4),
                                   r[2], font=body_font, fill=DS_GOLD)
                            yy += name_h
                        elif kind == "pair2":
                            # 左 + 右 两列文字（等级条件+成熟时间 / 售价+经验）
                            if r[1]:
                                _dtext(d, (int(x0 + inner), yy), r[1], font=body_font, fill=DS_TEXT_2)
                            if r[2]:
                                _dtext(d, (int(x0 + card_w - inner - tw(r[2], body_font)), yy + 2),
                                       r[2], font=body_font, fill=DS_MUTED)
                            yy += line_h
                        elif kind == "plain":
                            for wl in wrap(r[1], body_font):
                                _dtext(d, (int(x0 + inner), yy), wl, font=body_font, fill=DS_TEXT_2)
                                yy += line_h
                        elif kind == "rule":
                            rule_y = price_y - n_pad  # 分割线固定在价格上方 N 距离
                        elif kind == "price":
                            # 价格贴底边 N（2.0.3：原价灰色小字 + 实时价红色正常字）
                            real_text = r[1]
                            orig_text = r[2] if len(r) > 2 else ""
                            right_x = int(x0 + card_w - inner - tw(real_text, price_font))
                            if orig_text:
                                _dtext(d, (int(right_x - 6 - tw(orig_text, small_price_font)), price_y + 4),
                                       orig_text, font=small_price_font, fill=DS_MUTED)
                            _dtext(d, (right_x, price_y), real_text, font=price_font, fill=DS_DANGER)
                            break
                    if rule_y is not None:
                        d.line([(x0 + 8, rule_y), (x0 + card_w - 8, rule_y)], fill=DS_BORDER, width=1)
                y += gh + gap
            y -= gap

        return _save_temp_image(img, "_farmshop_", "农场商店")

    def _render_warehouse(self, farm, crops, ferts):
        wh = farm.get("warehouse", {})
        rows = []
        for key, label in [("crops", "作物"), ("seeds", "种子"), ("fertilizers", "肥料")]:
            rows.append([(f"----{label}----", DS_TEXT_2, False)])
            items = wh.get(key, {})
            if not items:
                rows.append([("（空）", DS_MUTED, False)])
                continue
            for nm, cnt in items.items():
                if key == "crops":
                    c = self._find_item(crops, nm)
                    price = c["crop_price"] if c else 0.0
                elif key == "seeds":
                    c = self._find_item(crops, nm)
                    price = c["seed_sell_price"] if c else 0.0
                else:
                    # 化肥只能买和使用，不可卖（2.0.0 起按小时计量）
                    rows.append([(f"{nm} ×{self._fmt_hours(cnt)} 小时（不可售）", DS_MUTED, False)])
                    continue
                rows.append([(f"{nm} ×{cnt} 可售 {self._fmt_price(price)}金币", DS_TEXT, False)])
        return self._render_rich_image("农场仓库", rows)

    def _unusable_ferts(self, plot, ferts):
        used = plot.get("fert_accel", {}) or {}
        bad = [f["name"] for f in ferts
               if self._fert_max_accel(f) >= 0
               and int(used.get(f["name"], 0)) >= self._fert_max_accel(f)]
        return "、".join(bad) if bad else "无"

    def _render_plot_status(self, name, uid, data, farm, crops, ferts, steal_lines=None,
                            highlights=None, highlight_plots=None, new_plots=None,
                            profit_delta=0, actions=None, coins_delta=None):
        """土地状态 / 我的农场（2.0.0 新模板）：
        - 标题行：<用户名>的农场（右对齐）农场排行榜排行积分
        - 盈利行：总盈利 + 本次盈利变化（支出 -，收入 +）
        - 等级展示模块（Lv / 经验 / 升级进度条）→ 分割线 → 土地卡片区
        - 土地卡片高亮：黄 #FFE699=种植 / 红 #FFC5C5=收割 / 蓝 #B4C7E7=开垦·施肥·升级
          （高亮变化涉及的提示内容不再作文本提示）
        - 底部大卡片（宽度 = 同一行所有土地卡片宽度+间距总和）：自动化行为提示 + 金币变化
        highlights：{地块编号(1-based): 'plant'|'harvest'|'till'|'fert'|'upgrade'}；
        兼容旧参数 highlight_plots（蓝=施肥/开垦）与 new_plots（黄=种植）。"""
        Image, ImageDraw = _ensure_pillow()
        if Image is None:
            return None
        # 字号语义：标题 36（衬线）/ 等级 26 / 小字 18 / 经验 16（小两号）
        fonts = _load_fonts(26, 18, 16)
        if fonts is None:
            return None
        lv_font, small_font, exp_font = fonts
        title_font = _title_font(kind="farm")
        if title_font is None:
            return None
        now = datetime.now().timestamp()
        plots = farm.get("plots", [])
        level = int(farm.get("level", 0))
        exp = float(farm.get("exp", 0.0))
        need = FARM_EXP_BASE * (level + 1) if level < FARM_MAX_LEVEL else 0
        profit = int(farm.get("total_profit", 0))

        # 高亮：新参数优先，兼容旧参数
        hl_map = dict(highlights or {})
        for n in (highlight_plots or []):
            hl_map.setdefault(int(n), "fert")
        for n in (new_plots or []):
            hl_map.setdefault(int(n), "plant")
        HL_COLORS = {
            "plant": (255, 230, 153),    # 黄 #FFE699 种植
            "harvest": (255, 197, 197),  # 红 #FFC5C5 收割
            "till": (180, 199, 231),     # 蓝 #B4C7E7 开垦
            "fert": (180, 199, 231),     # 蓝 #B4C7E7 施肥
            "upgrade": (180, 199, 231),  # 蓝 #B4C7E7 升级
        }

        # ---------- 土地卡片内容 ----------
        cards = []  # (行列表, 高亮颜色 or None)
        for i, plot in enumerate(plots):
            num = i + 1
            gname = self._plot_grade(int(plot.get("grade", 0)))[0]
            grade = int(plot.get("grade", 0))
            if grade >= len(FARM_UPGRADE_COSTS):
                upgrade = "🏆 已满级"
            else:
                upgrade = f"⬆️ 升级 {int(FARM_UPGRADE_COSTS[grade])}金"
            color = HL_COLORS.get(hl_map.get(num))
            if plot.get("crop") is None:
                lines = [f"#{num} {gname}", "空闲中", upgrade]
            else:
                crop_name = plot.get("crop", "")
                c = self._find_item(crops, crop_name)
                price = c["crop_price"] if c else 0.0
                income = int(round(int(plot.get("yield", 0)) * float(price)))
                if now >= plot.get("mature_ts", 0):
                    state = "已成熟"
                    remain = "可收割"
                else:
                    state = "占用中"
                    remain = self._fmt_duration(plot.get("mature_ts", 0) - now)
                # 化肥已推进时间（分钟）
                adv_min = ""
                g = self._plot_growth(plot, c, now) if c else None
                if g is not None and g["advance_sec"] > 60:
                    adv_min = f"加速{int(g['advance_sec'] // 60)}分"
                # 2.0.1：加速信息独立一行，位于「剩余时间 / 预计收入」之下
                lines = [
                    f"#{num} {gname} {state}",
                    crop_name,
                    f"剩余 {remain} 预计 {income}金",
                ]
                if adv_min:
                    lines.append(adv_min)
                lines.append(upgrade)
            cards.append((lines, color))

        # ---------- 布局参数 ----------
        pad = 20
        title_h = 52
        gap = 10
        inner = 8
        line_h = 26
        cols = int(globals().get("FARM_PLOT_COLS", 4))
        card_w = int(globals().get("FARM_PLOT_CARD_WIDTH", 270))
        content_w = card_w - inner * 2

        tw = _text_measurer()
        if tw is None:
            return None

        wrap = _make_wrapper(tw, content_w)

        # 预计算每张卡片换行后的行数与高度
        card_rows = []  # (行列表, 高度, 高亮颜色 or None)
        for lines, color in cards:
            rows = []
            for ln in lines:
                for wl in wrap(ln, small_font):
                    rows.append(wl)
            card_rows.append((rows, inner * 2 + len(rows) * line_h, color))

        # 顶部属性区高度
        profit_h = 28
        lv_row_h = 38
        bar_h = 26
        rule_h = 22

        width = pad * 2 + card_w * cols + gap * (cols - 1)
        bar_w = int((width - pad * 2) * 0.5)  # 等级行 / 进度条宽度 = 内容宽 * 50%

        rows_n = (len(card_rows) + cols - 1) // cols if card_rows else 1
        cards_h = sum(max(card_rows[r * cols:(r + 1) * cols][j][1] for j in range(len(card_rows[r * cols:(r + 1) * cols])))
                      for r in range(rows_n)) + gap * max(0, rows_n - 1) if card_rows else 0

        # 偷菜信息区（底部）：表格行数（含标题行）
        steal_table = []
        steal_h = 0
        if steal_lines:
            steal_table, steal_h = self._layout_steal_table(steal_lines, width - pad * 2, small_font, tw)
            steal_h += 8 + line_h  # 分割线间距 + 标题行

        # 底部大卡片（自动化行为 + 金币变化）：宽度 = 图片宽度 = 同一行土地卡片宽度+间距总和
        big_lines = []
        if actions:
            big_lines.extend(actions)
        if coins_delta is not None:
            cur_coins = int(self._coins_of(data, uid)) if data else 0
            sign = "+" if coins_delta >= 0 else ""
            big_lines.append(f"💰 金币变化：{sign}{int(coins_delta)}（当前 {cur_coins}）")
        big_h = 0
        big_pad = 10
        big_wrapped = []
        if big_lines:
            # 超长行为提示（如 收割+升级 合并文本，含 \n 与超宽）按 \n 拆分并按大卡片宽度换行，
            # 防止文字溢出/重叠（Pillow text() 不识别 \n）
            big_w = width - pad * 2 - 24
            for ln in big_lines:
                for part in str(ln).split("\n"):
                    for wl in wrap(part, small_font, big_w):
                        big_wrapped.append(wl)
            big_h = big_pad * 2 + len(big_wrapped) * line_h + 6

        height = pad * 2 + title_h + profit_h + lv_row_h + bar_h + rule_h + cards_h + steal_h + big_h

        img = Image.new("RGB", (width, height), DS_BG)
        d = ImageDraw.Draw(img)
        y = pad

        # 标题：<用户名称> + 右侧 农场排行榜排行积分
        rank_text = self._farm_rank_text(uid, data) if data else ""
        title_line = f"{name} 的农场"
        if rank_text and tw(title_line, title_font) + 24 + tw(rank_text, small_font) <= width - pad * 2:
            _dtext(d, (pad, y), title_line, font=title_font, fill=DS_ACCENT)
            _dtext(d, (int(width - pad - tw(rank_text, small_font)), y + 16), rank_text,
                   font=small_font, fill=DS_MUTED)
        else:
            _dtext(d, (pad, y), title_line, font=title_font, fill=DS_ACCENT)
            if rank_text:
                _dtext(d, (pad, y + 28), rank_text, font=small_font, fill=DS_MUTED)
                title_h = max(title_h, 52 + 22)
        y += title_h

        # 盈利行：总盈利 + 本次盈利变化（支出 -，收入 +）
        profit_txt = f"📈 总盈利：{profit} 金币"
        if profit_delta:
            psign = "+" if profit_delta > 0 else ""
            profit_txt += f"　（本次 {psign}{int(profit_delta)}）"
        _dtext(d, (pad, y), profit_txt, font=small_font, fill=DS_MUTED)
        y += profit_h

        # 等级行：Lv.X（左）+ 经验 Y/Z（右，字号小两号）
        _dtext(d, (pad, y), f"Lv.{level}", font=lv_font, fill=DS_TEXT)
        exp_text = f"经验 {exp:.0f}/{need:.0f}" if need > 0 else "已满级"
        _dtext(d, (int(pad + bar_w - tw(exp_text, exp_font)), y + 6), exp_text, font=exp_font, fill=DS_TEXT_2)
        y += lv_row_h

        # 升级进度条（宽度与等级行相同，含百分比）
        bar_y = y
        ratio = min(1.0, exp / need) if need > 0 else 1.0
        d.rectangle([pad, bar_y, pad + bar_w, bar_y + 14], outline=DS_BORDER, width=1)
        if ratio > 0:
            d.rectangle([pad + 1, bar_y + 1, int(pad + 1 + (bar_w - 2) * ratio), bar_y + 13], fill=DS_SUCCESS)
        pct_text = f"{int(ratio * 100)}%"
        _dtext(d, (int(pad + bar_w - tw(pct_text, small_font) - 4), bar_y - 3), pct_text, font=small_font, fill=DS_TEXT)
        y += bar_h

        # 分割线
        d.line([(pad, y), (width - pad, y)], fill=DS_BORDER, width=2)
        y += rule_h

        # 土地卡片（4 列，自动换行，同行取最高）
        for r in range(rows_n):
            group = card_rows[r * cols:(r + 1) * cols]
            if not group:
                break
            gh = max(h for _, h, _ in group)
            for j, (rows, _, color) in enumerate(group):
                x0 = pad + j * (card_w + gap)
                d.rectangle([x0, y, x0 + card_w, y + gh], fill=color, outline=DS_BORDER, width=1)
                yy = y + inner
                for ln in rows:
                    _dtext(d, (int(x0 + inner), yy), ln, font=small_font, fill=DS_TEXT)
                    yy += line_h
            y += gh + gap

        # 偷菜信息区：分割线 + 标题行 + 表格
        if steal_lines:
            d.line([(pad, y), (width - pad, y)], fill=DS_BORDER, width=1)
            y += 8
            title_line = steal_lines[0] if steal_lines else ""
            _dtext(d, (pad, y), title_line, font=small_font, fill=DS_MUTED)
            y += line_h
            self._draw_steal_table(d, steal_table, pad, y, width - pad, small_font)
            y += steal_h - (8 + line_h)

        # 底部大卡片：自动化行为提示 + 金币变化（按换行后的行数绘制，避免重叠）
        if big_wrapped:
            big_y0 = y + 6
            big_h_real = big_pad * 2 + len(big_wrapped) * line_h
            d.rectangle([pad, big_y0, width - pad, big_y0 + big_h_real],
                        fill=DS_SURFACE_2, outline=DS_BORDER, width=1)
            yy = big_y0 + big_pad
            for ln in big_wrapped:
                _dtext(d, (int(pad + 12), yy), ln, font=small_font, fill=DS_TEXT_2)
                yy += line_h

        return _save_temp_image(img, "_farm_", "土地状态")

    def _layout_steal_table(self, lines, max_w, font, tw):
        """偷菜信息无框线表格：行 = 用户|作物 * 数量|损失金额|状态。
        返回 (单元格二维列表, 总高度)。列宽按内容自适应。"""
        rows_data = []
        for ln in lines:
            if "|" not in ln:
                continue
            cells = [c.strip() for c in ln.split("|")]
            if len(cells) < 4:
                cells += [""] * (4 - len(cells))
            rows_data.append(cells[:4])
        if not rows_data:
            return [], 0
        # 计算列宽（按最长内容，含换行：同一用户多作物换行显示在作物列）
        n_cols = 4
        col_w = [0] * n_cols
        for cells in rows_data:
            for i in range(n_cols):
                col_w[i] = max(col_w[i], tw(cells[i], font))
        # 多作物换行：作物列内容含换行 → 按行拆
        line_h = 26
        total = 0
        table = []
        for cells in rows_data:
            # 作物列可能含多行（换行）
            crop_lines = cells[1].split("\n") if "\n" in cells[1] else [cells[1]]
            table.append((cells, crop_lines))
            total += line_h * len(crop_lines)
        return table, total

    def _draw_steal_table(self, d, table, x0, y0, x1, font):
        """绘制偷菜表格：无框线，列宽自适应；失败 #7F7F7F / 成功 #C00000 / 宠物起作用 #BF9000；
        2.0.3：折叠次数 *N 用黄色 #FFC000 表示。"""
        line_h = 26
        y = y0
        YELLOW = DS_GOLD_2
        for cells, crop_lines in table:
            name = cells[0]
            loss = cells[2]
            status = cells[3]
            # 颜色由状态决定
            if "失败" in status:
                color = DS_MUTED
            elif "宠物" in status or "追回" in status:
                color = DS_GOLD
            else:
                color = DS_DANGER
            for k, crop_line in enumerate(crop_lines):
                _dtext(d, (int(x0), y), name if k == 0 else "", font=font, fill=color)
                # 折叠次数 *N（行尾的 *数字）用黄色绘制
                x_crop = int(x0 + 90)
                m = re.match(r"^(.*?)(\*\d+)$", crop_line)
                if m:
                    _dtext(d, (x_crop, y), m.group(1), font=font, fill=color)
                    star_w = d.textlength(m.group(2), font=font)
                    _dtext(d, (int(x_crop + d.textlength(m.group(1), font=font)), y),
                           m.group(2), font=font, fill=YELLOW)
                else:
                    _dtext(d, (x_crop, y), crop_line, font=font, fill=color)
                _dtext(d, (int(x0 + 90 + 160), y), loss if k == 0 else "", font=font, fill=color)
                _dtext(d, (int(x0 + 90 + 160 + 90), y), status if k == 0 else "", font=font, fill=color)
                y += line_h

    # ---- 农场指令 ----
    def _farm_need(self, data, key, name):
        if not self._farm_of(data, key):
            return f"{name} 还没有农场，发送「解锁农场」（需 {FARM_UNLOCK_COST} 金币）解锁。"
        return None

    def _handle_farm_unlock(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        if self._farm_of(data, key):
            return f"{name} 已经拥有农场啦。"
        if self._coins_of(data, key) < FARM_UNLOCK_COST:
            return f"解锁农场需要 {FARM_UNLOCK_COST} 金币（当前 {self._coins_of(data, key)}）。"
        self._add_coins(data, key, -FARM_UNLOCK_COST, "解锁农场")
        farm = self._ensure_farm(data, key)
        # 盈利公式：解锁农场计入成本
        farm["total_profit"] = int(farm.get("total_profit", 0)) - FARM_UNLOCK_COST
        for _ in range(FARM_FREE_PLOTS):
            farm["plots"].append(self._new_plot())
        self._save(data)
        # 2.0.0 纯图片回复：赠送土地蓝色高亮（开垦）+ 底部大卡片
        actions = [f"🎉 花费 {FARM_UNLOCK_COST} 金币解锁农场，赠送 {FARM_FREE_PLOTS} 块土地"]
        return self._safe_render_plot_status(
            name, key, data, farm, self._load_crops(), self._load_fertilizers(),
            highlights={i + 1: "till" for i in range(FARM_FREE_PLOTS)},
            profit_delta=-FARM_UNLOCK_COST,
            actions=actions,
            coins_delta=-FARM_UNLOCK_COST)

    def _handle_farm_buy_land(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if len(farm["plots"]) >= FARM_MAX_PLOTS:
            return "土地数量已达上限（24 块）。"
        if self._coins_of(data, key) < FARM_PLOT_COST:
            return f"购买土地需要 {FARM_PLOT_COST} 金币（当前 {self._coins_of(data, key)}）。"
        self._add_coins(data, key, -FARM_PLOT_COST, "购买土地")
        farm["plots"].append(self._new_plot())
        # 盈利公式：购买土地计入成本
        farm["total_profit"] = int(farm.get("total_profit", 0)) - FARM_PLOT_COST
        self._save(data)
        # 2.0.0 纯图片回复：新开垦土地蓝色高亮（开垦）+ 底部大卡片
        new_num = len(farm["plots"])
        actions = [f"🆕 花费 {FARM_PLOT_COST} 金币开垦了一块新土地（当前共 {new_num} 块）"]
        return self._safe_render_plot_status(
            name, key, data, farm, self._load_crops(), self._load_fertilizers(),
            highlights={new_num: "till"},
            profit_delta=-FARM_PLOT_COST,
            actions=actions,
            coins_delta=-FARM_PLOT_COST)

    def _handle_farm_upgrade(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：土地升级 <土地编号>"
        try:
            num = int(parts[1].strip())
        except ValueError:
            return "土地编号必须是整数。"
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if num < 1 or num > len(farm["plots"]):
            return f"没有编号为 {num} 的土地（当前共 {len(farm['plots'])} 块）。"
        plot = farm["plots"][num - 1]
        grade = int(plot.get("grade", 0))
        if grade >= len(FARM_UPGRADE_COSTS):
            return "这块地已经是最高等级（黑土地）了。"
        if plot.get("crop") is not None:
            return "这块土地正在种植中，收割后才能升级。"
        cost = int(FARM_UPGRADE_COSTS[grade])
        if self._coins_of(data, key) < cost:
            return f"升级需要 {cost} 金币（当前 {self._coins_of(data, key)}）。"
        self._add_coins(data, key, -cost, f"升级土地·{num}号")
        plot["grade"] = grade + 1
        # 盈利公式：升级土地计入成本
        farm["total_profit"] = int(farm.get("total_profit", 0)) - cost
        self._save(data)
        ng = self._plot_grade(grade + 1)
        # 2.0.0 纯图片回复：升级土地蓝色高亮（升级）+ 底部大卡片
        actions = [f"⬆️ {num} 号土地升级为 {ng[0]}（产量 +{int(ng[1] * 100)}%，时间 -{int(ng[2] * 100)}%）"]
        return self._safe_render_plot_status(
            name, key, data, farm, self._load_crops(), self._load_fertilizers(),
            highlights={num: "upgrade"},
            profit_delta=-cost,
            actions=actions,
            coins_delta=-cost)

    def _seed_discount_map(self, crops):
        """种子每日折扣（2.0.3，默认关闭）：每天有概率让 1~3 款种子打八折（肥料不受影响）。
        按「当天」固定随机（同一天折扣款一致）；返回 {作物名: 倍率}（无折扣为空）。"""
        out = {}
        if not bool(globals().get("SEED_DISCOUNT_ENABLED", False)):
            return out
        today = date.today().isoformat()
        rng = random.Random("seed_discount|" + today)
        if rng.random() >= float(globals().get("SEED_DISCOUNT_CHANCE", 0.5)):
            return out
        names = sorted({c["name"] for c in crops})
        if not names:
            return out
        n = rng.randint(int(globals().get("SEED_DISCOUNT_MIN", 1) or 1),
                        int(globals().get("SEED_DISCOUNT_MAX", 3) or 3))
        pct = float(globals().get("SEED_DISCOUNT_PCT", 0.8) or 0.8)
        for nm in rng.sample(names, min(n, len(names))):
            out[nm] = pct
        return out

    def _farm_buy_seed(self, data, key, name, crop_name, count):
        """购买种子核心逻辑（供「购买」「购买种子」使用），盈利即时扣减成本；
        2.0.3：种子每日折扣（打八折款按折后价结算）"""
        crops = self._load_crops()
        crop = self._find_item(crops, crop_name)
        if not crop:
            return f"没有「{crop_name}」这种作物，发送「农场商店」查看。"
        farm = self._farm_of(data, key)
        if not farm:
            return f"{name} 还没有农场，发送「解锁农场」（需 {FARM_UNLOCK_COST} 金币）解锁。"
        if int(farm.get("level", 0)) < crop["min_level"]:
            return f"农场等级不足（需要 Lv.{crop['min_level']}，当前 Lv.{farm['level']}）。"
        disc = self._seed_discount_map(crops).get(crop_name, 1.0)
        p = round(crop["seed_price"] * self._farm_seed_mult(farm) * disc, 2)
        total = int(round(p * count))
        if self._coins_of(data, key) < total:
            return f"金币不足（需要 {total}，当前 {self._coins_of(data, key)}）。"
        self._add_coins(data, key, -total, f"购买种子·{crop_name}")
        wh = farm["warehouse"].setdefault("seeds", {})
        wh[crop_name] = int(wh.get(crop_name, 0)) + count
        # 盈利即时扣减种子成本（允许为负）
        farm["total_profit"] = int(farm.get("total_profit", 0)) - total
        self._save(data)
        price_note = f"（折后价，原价 {self._fmt_price(crop['seed_price'] * self._farm_seed_mult(farm))}）" if disc < 1.0 else ""
        return (f"✅ 购买 {crop_name} 种子 ×{count}，花费 {total} 金币（单价 {self._fmt_price(p)}）{price_note}。\n"
                f"{self._coin_line(data, key)}\n"
                f"{self._farm_state_snippet(farm)}")

    def _farm_buy_fert(self, data, key, name, fert_name, hours):
        """购买化肥核心逻辑（2.0.0 起按「小时」购买，最小单位为 1 小时），盈利即时扣减成本"""
        fert = self._find_item(self._load_fertilizers(), fert_name)
        if not fert:
            return f"没有「{fert_name}」这种肥料，发送「农场商店」查看。"
        farm = self._farm_of(data, key)
        if not farm:
            return f"{name} 还没有农场，发送「解锁农场」（需 {FARM_UNLOCK_COST} 金币）解锁。"
        try:
            hours = max(1, int(float(hours)))
        except (TypeError, ValueError):
            return "小时数必须是整数。"
        total = int(fert["price"]) * hours
        if self._coins_of(data, key) < total:
            return f"金币不足（需要 {total}，当前 {self._coins_of(data, key)}）。"
        self._add_coins(data, key, -total, f"购买肥料·{fert_name}")
        wh = farm["warehouse"].setdefault("fertilizers", {})
        wh[fert_name] = float(wh.get(fert_name, 0) or 0) + hours
        # 盈利即时扣减肥料成本（允许为负）
        farm["total_profit"] = int(farm.get("total_profit", 0)) - total
        self._save(data)
        return (f"✅ 购买 {fert_name} {hours} 小时（单价 {int(fert['price'])} 金币/小时），花费 {total} 金币。\n"
                f"{self._coin_line(data, key)}\n"
                f"{self._farm_state_snippet(farm)}")

    def _handle_farm_buy_seed(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：购买种子 <作物名> <数量>（或直接「购买 <作物名>种子 <数量>」）"
        args = parts[1].split()
        crop_name = args[0]
        count = 1
        if len(args) >= 2:
            try:
                count = int(args[1])
            except ValueError:
                return "数量必须是整数。"
        if count <= 0:
            return "数量必须为正整数。"
        data = self._load()
        return self._farm_buy_seed(data, key, name, crop_name, count)

    def _handle_farm_buy_fert(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：购买肥料 <肥料名> <小时数>（最小购买单位为 1 小时，如「购买肥料 化肥 2」= 2 小时）"
        args = parts[1].split()
        fert_name = args[0]
        hours = 1
        if len(args) >= 2:
            try:
                hours = int(args[1])
            except ValueError:
                return "小时数必须是整数。"
        if hours <= 0:
            return "小时数必须为正整数（最小购买单位为 1 小时）。"
        data = self._load()
        return self._farm_buy_fert(data, key, name, fert_name, hours)

    def _handle_farm_plant(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            # 1.7.6 快捷种地：不指定作物 → 收割成熟 → 仓库随机种子自动种 → 缺则自动购买 → 种满
            return self._farm_plant_auto(event)
        args = parts[1].split()
        crop_name = args[0]
        crops = self._load_crops()
        crop = self._find_item(crops, crop_name)
        if not crop:
            return f"没有「{crop_name}」这种作物，发送「种子商店」查看。"
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if int(farm.get("level", 0)) < crop["min_level"]:
            return f"农场等级不足（需要 Lv.{crop['min_level']}，当前 Lv.{farm['level']}）。"
        plots = farm["plots"]
        if not plots:
            return "还没有土地，发送「购买土地」开垦。"

        wh = farm["warehouse"].setdefault("seeds", {})
        have = int(wh.get(crop_name, 0))

        if len(args) == 1:
            # 种下最大数量：种子足够则种满所有空闲耕地；种子不足则把持有的种子全部种完
            free = [i for i, p in enumerate(plots) if self._plot_free(p)]
            if not free:
                return "没有空闲的土地可以种植。"
            if have <= 0:
                return f"{crop_name} 种子不足，发送「购买种子」购买。"
            targets = free[:min(len(free), have)]
        elif len(args) == 2:
            try:
                count = int(args[1])
            except ValueError:
                return "数量必须是整数。"
            if count <= 0:
                return "数量必须为正整数。"
            free = [i for i, p in enumerate(plots) if self._plot_free(p)]
            if count > len(free):
                return f"空闲土地只有 {len(free)} 块，无法种植 {count} 块。"
            targets = free[:count]
            if have < len(targets):
                return f"{crop_name} 种子不足（需要 {len(targets)}，当前 {have}），发送「购买种子」购买。"
        else:
            try:
                start, end = int(args[1]), int(args[2])
            except ValueError:
                return "土地编号必须是整数。"
            if start < 1 or end < start or end > len(plots):
                return f"土地编号无效（当前共 {len(plots)} 块，范围 1~{len(plots)}）。"
            targets = list(range(start - 1, end))
            for i in targets:
                if not self._plot_free(plots[i]):
                    return f"{i + 1} 号土地不是空闲状态，无法种植。"
            if have < len(targets):
                return f"{crop_name} 种子不足（需要 {len(targets)}，当前 {have}），发送「购买种子」购买。"

        now = datetime.now().timestamp()
        for i in targets:
            self._plant_plot(plots[i], crop, now)
        wh[crop_name] = have - len(targets)
        if wh[crop_name] <= 0:
            wh.pop(crop_name, None)
        self._save(data)
        # 2.0.0：纯图片回复——新种地块黄色高亮 + 底部大卡片（不再附带文本提示）
        ferts = self._load_fertilizers()
        actions = [f"🌱 在 {len(targets)} 块土地上种下 {crop_name}"
                   f"（编号 {targets[0] + 1}~{targets[-1] + 1}）"]
        return self._safe_render_plot_status(
            name, key, data, farm, crops, ferts,
            highlights={i + 1: "plant" for i in targets},
            actions=actions)

    @staticmethod
    def _clear_plot(plot):
        """清空一块地块（收割/取消种植后重置所有种植字段）"""
        plot["crop"] = None
        plot["seed"] = None
        plot["plant_ts"] = 0
        plot["mature_ts"] = 0
        plot["base_time"] = 0
        plot["yield"] = 0
        plot["fert_time"] = 0.0
        plot["fert_yield"] = 0.0
        plot["fert"] = {}
        plot["fert_advance"] = 0.0
        plot["fert_accel"] = {}
        plot["orig_yield"] = 0

    @staticmethod
    def _plant_plot(plot, crop, now):
        """把作物种到一块空闲地块（字段赋值，不落盘）。
        2.0.0：记录 stage_count / stage_sec（按贫瘠总时间均分）与化肥推进字段。
        2.0.1：记录 orig_yield（原始产量，用于偷菜「保护地块」判定）。"""
        gname, gy, gt = FarmMixin._plot_grade(int(plot.get("grade", 0)))
        base_sec = crop["grow_minutes"] * 60
        plot["crop"] = crop["name"]
        plot["seed"] = crop["name"]
        plot["plant_ts"] = now
        plot["base_time"] = base_sec
        plot["yield"] = int(crop["yield"] * (1 + gy))
        plot["orig_yield"] = plot["yield"]
        plot["mature_ts"] = now + base_sec * (1 - gt)
        plot["fert_time"] = 0.0
        plot["fert_yield"] = 0.0
        plot["fert"] = {}
        plot["fert_advance"] = 0.0
        plot["fert_accel"] = {}

    def _harvest_mature(self, data, farm, crops, now, targets=None):
        """收割成熟地块作物进仓库（不落盘）。targets=None = 全部成熟地块。
        返回 (harvested 编号列表[1-based], {作物:数量}, 总经验)。"""
        plots = farm["plots"]
        if targets is None:
            idxs = [i for i, p in enumerate(plots)
                    if p.get("crop") is not None and now >= p.get("mature_ts", 0)]
        else:
            idxs = [i for i in targets
                    if plots[i].get("crop") is not None and now >= plots[i].get("mature_ts", 0)]
        if not idxs:
            return [], {}, 0
        wh = farm["warehouse"].setdefault("crops", {})
        amounts = {}
        total_exp = 0
        harvested = []
        for i in idxs:
            plot = plots[i]
            crop = self._find_item(crops, plot["crop"])
            amount = int(plot.get("yield", 0))
            wh[plot["crop"]] = int(wh.get(plot["crop"], 0)) + amount
            amounts[plot["crop"]] = amounts.get(plot["crop"], 0) + amount
            gy = self._plot_grade(int(plot.get("grade", 0)))[1]
            base_exp = int(crop["exp"]) if crop else 0
            total_exp += int(round(base_exp * (1 + gy))) if crop else 0
            harvested.append(i + 1)
            self._clear_plot(plot)
        # 被偷批次：本次收割的地块若有偷菜信息，标记 harvest_ts（24h 内可见）
        now_ts = datetime.now().timestamp()
        for it in farm.get("steal_infos", []):
            if it.get("harvest_ts") is None:
                it["harvest_ts"] = now_ts
        return harvested, amounts, total_exp

    def _farm_plant_auto(self, event):
        """种地/种植（无参数）快捷流程：
        1) 先收割成熟作物；2) 用仓库随机种子自动种；3) 仓库不足 → 自动购买能购买的种子；
        4) 金币不足则尽可能种满。2.0.0 回复 = 纯图片（收割红高亮 + 种植黄高亮 + 底部大卡片）。"""
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if not farm or not farm.get("plots"):
            return "还没有土地，发送「购买土地」开垦。"
        crops = self._load_crops()
        now = datetime.now().timestamp()
        actions = []
        highlights = {}
        planted_ids = []
        spent = 0

        # 1) 先收割成熟作物（进仓库）→ 红色高亮
        harvested, _, total_exp = self._harvest_mature(data, farm, crops, now)
        if harvested:
            lvl_msg = self._farm_gain_exp(farm, total_exp)
            actions.append(f"🌾 先收割了 {len(harvested)} 块成熟作物（编号 {'、'.join(str(n) for n in harvested)}），农场经验 +{total_exp}{lvl_msg}")
            for n in harvested:
                highlights[n] = "harvest"

        plots = farm["plots"]
        free = [i for i, p in enumerate(plots) if self._plot_free(p)]
        if not free and not actions:
            return "没有空闲的土地可以种植。"

        # 2) 使用仓库种子（随机名称逐个种）
        wh_seeds = farm["warehouse"].setdefault("seeds", {})
        usable = [n for n, c in wh_seeds.items() if int(c or 0) > 0 and self._find_item(crops, n)]
        free_left = list(free)
        used_desc = {}
        while free_left and usable:
            nm = random.choice(usable)
            crop = self._find_item(crops, nm)
            if int(farm.get("level", 0)) < crop["min_level"]:
                usable.remove(nm)  # 等级不够的种子跳过（不种）
                continue
            i = free_left.pop(0)
            self._plant_plot(plots[i], crop, now)
            planted_ids.append(i)
            highlights[i + 1] = "plant"
            wh_seeds[nm] = int(wh_seeds.get(nm, 0)) - 1
            used_desc[nm] = used_desc.get(nm, 0) + 1
            if wh_seeds[nm] <= 0:
                wh_seeds.pop(nm, None)
                usable.remove(nm)
        if used_desc:
            actions.append("📦 使用仓库种子：" + "、".join(f"{n}×{c}" for n, c in used_desc.items()))

        # 3) 仓库不足 → 自动购买当前用户能购买的种子并种植
        if free_left:
            buyable = [c for c in crops if int(farm.get("level", 0)) >= c["min_level"]]
            if not buyable:
                actions.append("😢 当前农场等级没有可购买的种子，剩余空地未能种植。")
            else:
                missing = len(free_left)
                actions.append(f"🛒 仓库种子不足，自动购买 {missing} 颗种子补种…")
                coins = self._coins_of(data, key)
                bought = 0
                order = sorted(buyable, key=lambda c: c["min_level"], reverse=True)
                while free_left:
                    bought_any = False
                    for crop in order:
                        if not free_left:
                            break
                        price = int(round(crop["seed_price"] * self._farm_seed_mult(farm)))
                        if coins < price:
                            continue
                        self._add_coins(data, key, -price, f"购买种子·{crop['name']}")
                        farm["total_profit"] = int(farm.get("total_profit", 0)) - price
                        i = free_left.pop(0)
                        self._plant_plot(plots[i], crop, now)
                        planted_ids.append(i)
                        highlights[i + 1] = "plant"
                        bought += 1
                        spent += price
                        coins -= price
                        bought_any = True
                        break
                    if not bought_any:
                        break
                if bought:
                    actions.append(f"🛒 自动购买并种下 {bought} 颗种子（花费 {spent} 金币）")
                if free_left:
                    actions.append(f"🍂 金币不足，剩余 {len(free_left)} 块空地未能种植。")
        self._save(data)
        # 4) 纯图片回复：收割红高亮 + 种植黄高亮 + 底部大卡片（自动化行为 + 金币变化）
        return self._safe_render_plot_status(
            name, key, data, farm, crops, self._load_fertilizers(),
            highlights=highlights,
            profit_delta=-spent,
            actions=actions,
            coins_delta=-spent if spent else None)

    def _apply_fert_minutes(self, plot, crop, fert, minutes, now):
        """对一块种植地使用化肥 minutes 分钟（不落盘）：
        推进成熟时间、记录该化肥的加速次数、按加速次数增加产量。
        返回 (加速次数, 增产百分比)。"""
        g = self._plot_growth(plot, crop, now)
        stage_sec = g["stage_sec"]
        stage_before = int(g["progress_sec"] // stage_sec) if stage_sec > 0 else 0
        advance = float(minutes) * 60.0
        plot["fert_advance"] = float(plot.get("fert_advance", 0.0)) + advance
        # 兼容旧字段（减时比例）
        base_sec = g["base_sec"]
        plot["fert_time"] = min(0.95, float(plot.get("fert_time", 0.0)) + advance / base_sec)
        progress_after = g["progress_sec"] + advance
        stage_after = int(progress_after // stage_sec) if stage_sec > 0 else 0
        crossed = max(0, stage_after - stage_before)
        acc = plot.setdefault("fert_accel", {})
        acc[fert["name"]] = int(acc.get(fert["name"], 0)) + crossed
        add_pct = 0.0
        if crossed > 0:
            ya = float(fert.get("yield_add", 0) or 0)
            if ya > 0:
                add_pct = crossed * ya
                plot["fert_yield"] = float(plot.get("fert_yield", 0.0)) + add_pct
        # 重算成熟时间与产量（成熟时间 = 种植时间 + 土地最大生长时间 - 化肥累计推进）
        _, gy, gt = self._plot_grade(int(plot.get("grade", 0)))
        total_sec = base_sec * (1 - gt)
        plot["mature_ts"] = float(plot.get("plant_ts", 0)) + total_sec - float(plot.get("fert_advance", 0.0))
        plot["yield"] = int((crop["yield"] if crop else 0) * (1 + gy + float(plot.get("fert_yield", 0.0))))
        return crossed, add_pct

    def _apply_fert_to_plots(self, plots, use_plan, fert, now, crops=None):
        """对 use_plan={地块下标: 使用分钟} 中的地块施肥（不落盘）。
        返回 (总加速次数, 使用地块数)。"""
        if crops is None:
            crops = self._load_crops()
        total_accel = 0
        n_used = 0
        for i, minutes in use_plan.items():
            plot = plots[i]
            crop = self._find_item(crops, plot.get("crop", ""))
            if not crop or minutes <= 0:
                continue
            crossed, _ = self._apply_fert_minutes(plot, crop, fert, minutes, now)
            total_accel += crossed
            n_used += 1
        return total_accel, n_used

    def _safe_render_plot_status(self, name, uid, data, farm, crops, ferts, actions=None, **kwargs):
        """安全渲染土地状态图片并返回 ("image", path)（纯图片回复）；Pillow 缺失时回退纯文本"""
        try:
            img = self._render_plot_status(name, uid, data, farm, crops, ferts,
                                           actions=actions, **kwargs)
        except Exception as e:
            logger.error(f"[插件] 渲染土地状态图片异常: {e}")
            img = None
        if img is not None and isinstance(img, tuple) and img[0] == "image":
            return img
        if actions:
            return "\n".join(actions)
        return "图片生成失败（缺少 Pillow 或字体），请查看日志。"

    def _apply_fert_use(self, data, key, name, farm, fert, minutes):
        """使用化肥 minutes 分钟（「使用」指令）：对全部生长中土地按实际可用时间分配"""
        plots = farm["plots"]
        now = datetime.now().timestamp()
        crops = self._load_crops()
        growing = [i for i, p in enumerate(plots)
                   if p.get("crop") is not None and now < p.get("mature_ts", 0)]
        if not growing:
            return "没有正在生长中的作物可以施肥。"
        wh = farm["warehouse"].setdefault("fertilizers", {})
        have = float(wh.get(fert["name"], 0) or 0)
        need_h = float(minutes) / 60.0
        if have + 1e-9 < need_h:
            return (f"{fert['name']} 库存不足（需要 {self._fmt_hours(need_h)} 小时，"
                    f"当前 {self._fmt_hours(have)} 小时）。发送「购买肥料 {fert['name']} {math.ceil(need_h)}」购买。")
        remain = float(minutes)
        use_plan = {}
        for i in growing:
            if remain <= 1e-9:
                break
            plot = plots[i]
            crop = self._find_item(crops, plot.get("crop", ""))
            if not crop:
                continue
            avail = self._fert_actual_available_min(plot, crop, fert, now)
            if avail <= 0:
                continue
            take = min(remain, avail)
            use_plan[i] = take
            remain -= take
        if not use_plan:
            return "生长中的土地都已达到该化肥的最大可加速次数。"
        total_accel, n_plots = self._apply_fert_to_plots(plots, use_plan, fert, now, crops)
        used_min = float(minutes) - remain
        wh[fert["name"]] = have - used_min / 60.0
        if wh[fert["name"]] <= 1e-9:
            wh.pop(fert["name"], None)
        self._save(data)
        return (f"✅ {name} 使用了 {fert['name']} {self._fmt_hours(used_min / 60.0)} 小时"
                f"（{used_min:.0f} 分钟，作用于 {n_plots} 块地，加速 {total_accel} 次）。\n"
                f"{self._farm_state_snippet(farm)}")

    def _handle_farm_fertilize(self, event):
        """施肥（2.0.0）：
        完整格式：施肥 <化肥名称> <土地编号> <时间（分钟）>，例：施肥 化肥 1,3 60
        快捷（结构不全）：
        - 缺化肥名称 → 按顺序先用「化肥」，达到可加速上限再用「有机化肥」
        - 缺土地编号 → 默认所有可用土地
        - 缺使用时间 → 使用到下一个成长阶段所需的化肥时间
        - 全缺失 → 按缺化肥名称的顺序，对所有可用土地使用到下一阶段所需时间
        土地编号规则（不分全半角）：逗号分隔（1，6 → 1号、6号）；括号（（20）→ 20号）；
        括号逗号分隔（（1，7）→ 1~7号区间）；裸数字 ≤ 最大地块数（3 → 3号地）。
        时间：裸数字 > 最大地块数 视为使用分钟数。"""
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            # 全缺失 → 快捷施肥
            return self._auto_fertilize(event)
        raw = parts[1].strip()
        ferts = self._load_fertilizers()
        crops = self._load_crops()
        plots = farm["plots"]
        now = datetime.now().timestamp()
        max_plots = len(plots)

        # 化肥名：第一个 token 是已知化肥名则取出
        tokens = raw.split()
        fert = None
        if tokens:
            cand = self._find_item(ferts, tokens[0])
            if cand is not None:
                fert = cand
                raw = raw[len(tokens[0]):].strip()
        if fert is None:
            # 缺少化肥名称 → 快捷路径（自动选择化肥）
            return self._auto_fertilize(event, raw=raw)

        # 解析土地编号与时间
        target_plots, time_min = self._parse_fert_targets(raw, max_plots) if raw else ([], None)
        bad = [n for n in target_plots if n < 1 or n > max_plots]
        if bad:
            return f"土地编号超出范围：{'、'.join(str(n) for n in bad)}（当前共 {max_plots} 块地）。"
        growing = [i for i, p in enumerate(plots)
                   if p.get("crop") is not None and now < p.get("mature_ts", 0)]
        if target_plots:
            tset = set(target_plots)
            targets = [i for i in growing if (i + 1) in tset]
        else:
            targets = growing
        if not targets:
            return "没有正在生长中的作物可以施肥（所选土地未种植或已成熟）。"

        wh = farm["warehouse"].setdefault("fertilizers", {})
        have = float(wh.get(fert["name"], 0) or 0)
        # 每块地实际可用时间
        use_plan = {}
        for i in targets:
            plot = plots[i]
            crop = self._find_item(crops, plot.get("crop", ""))
            if not crop:
                continue
            avail = self._fert_actual_available_min(plot, crop, fert, now)
            if avail <= 0:
                continue
            want = time_min if time_min is not None else self._fert_to_next_stage_min(plot, crop, now)
            use_plan[i] = min(want, avail)
        if not use_plan:
            return "目标土地都无法再使用该化肥（已达最大可加速次数）。"
        total_need_h = sum(use_plan.values()) / 60.0
        if have + 1e-9 < total_need_h:
            return (f"{fert['name']} 库存不足（需要 {self._fmt_hours(total_need_h)} 小时，"
                    f"当前 {self._fmt_hours(have)} 小时）。发送「购买肥料 {fert['name']} {math.ceil(total_need_h)}」购买。")
        total_accel, n_plots = self._apply_fert_to_plots(plots, use_plan, fert, now, crops)
        wh[fert["name"]] = have - total_need_h
        if wh[fert["name"]] <= 1e-9:
            wh.pop(fert["name"], None)
        self._save(data)
        used_min = sum(use_plan.values())
        actions = [f"🧪 使用 {fert['name']} {self._fmt_hours(used_min / 60.0)} 小时"
                   f"（{used_min:.0f} 分钟，作用于 {n_plots} 块地，加速 {total_accel} 次）"]
        # 纯图片回复：施肥地块蓝色高亮（#B4C7E7）+ 底部大卡片
        return self._safe_render_plot_status(
            name, key, data, farm, crops, ferts,
            highlights={i + 1: "fert" for i in use_plan},
            actions=actions)

    def _auto_fertilize(self, event, raw=None):
        """施肥快捷流程（缺化肥名 / 全缺失）：
        按顺序先使用「化肥」，某块地达到可加速上限则改用「有机化肥」，再不行任意可用化肥；
        raw 指定则解析其中的土地编号 / 时间（缺省全部土地 + 用到下一阶段所需时间）；
        库存不足的化肥自动购买（金币不足则买多少算多少）。"""
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if not farm or not farm.get("plots"):
            return "还没有土地，发送「购买土地」开垦。"
        now = datetime.now().timestamp()
        ferts = self._load_fertilizers()
        if not ferts:
            return "还没有配置任何化肥，发送「农场商店」查看。"
        crops = self._load_crops()
        plots = farm["plots"]
        max_plots = len(plots)
        target_plots, time_min = self._parse_fert_targets(raw, max_plots) if raw else ([], None)
        bad = [n for n in target_plots if n < 1 or n > max_plots]
        if bad:
            return f"土地编号超出范围：{'、'.join(str(n) for n in bad)}（当前共 {max_plots} 块地）。"
        growing = [i for i, p in enumerate(plots)
                   if p.get("crop") is not None and now < p.get("mature_ts", 0)]
        if target_plots:
            tset = set(target_plots)
            growing = [i for i in growing if (i + 1) in tset]
        if not growing:
            return "没有正在生长中的作物可以施肥。"

        def pick_fert(plot):
            # 优先「化肥」；不可用则「有机化肥」；再不行则任意一种可用化肥
            for f in ferts:
                if f["name"] == "化肥" and self._fert_remaining_accel(plot, f) > 0:
                    return f
            for f in ferts:
                if "有机" in f["name"] and self._fert_remaining_accel(plot, f) > 0:
                    return f
            for f in ferts:
                if self._fert_remaining_accel(plot, f) > 0:
                    return f
            return None

        plan = {}  # plot_idx -> (fert, minutes)
        for i in growing:
            plot = plots[i]
            crop = self._find_item(crops, plot.get("crop", ""))
            if not crop:
                continue
            f = pick_fert(plot)
            if f is None:
                continue
            if time_min is not None:
                use = min(float(time_min), self._fert_actual_available_min(plot, crop, f, now))
            else:
                use = min(float(self._fert_to_next_stage_min(plot, crop, now)),
                          self._fert_actual_available_min(plot, crop, f, now))
            if use > 0:
                plan[i] = (f, use)
        if not plan:
            return "生长中的土地都已达到各化肥的最大可加速次数。"
        # 统计需要购买的化肥（小时）
        need = {}
        for _, (f, minutes) in plan.items():
            need[f["name"]] = need.get(f["name"], 0) + minutes / 60.0
        wh = farm["warehouse"].setdefault("fertilizers", {})
        actions = []
        coins_spent = 0
        for fname, need_h in need.items():
            have = float(wh.get(fname, 0) or 0)
            if have + 1e-9 >= need_h:
                continue
            fert = self._find_item(ferts, fname)
            unit = int(fert["price"]) if fert else 0
            buy_h = need_h - have
            coins = self._coins_of(data, key)
            if unit > 0:
                buy_h = min(buy_h, coins // unit)
            if buy_h <= 0:
                continue
            spent = int(round(buy_h * unit))
            self._add_coins(data, key, -spent, f"购买肥料·{fname}")
            farm["total_profit"] = int(farm.get("total_profit", 0)) - spent
            wh[fname] = float(wh.get(fname, 0) or 0) + buy_h
            coins_spent += spent
            actions.append(f"🛒 自动购买 {fname} ×{self._fmt_hours(buy_h)} 小时（花费 {spent} 金币）")
        # 执行施肥（按实际库存扣减）
        used_plots = []
        total_min = 0.0
        total_accel = 0
        for i, (f, minutes) in plan.items():
            fname = f["name"]
            have = float(wh.get(fname, 0) or 0)
            need_this = minutes / 60.0
            if have + 1e-9 < need_this:
                minutes = have * 60.0
            if minutes <= 0:
                continue
            plot = plots[i]
            crop = self._find_item(crops, plot.get("crop", ""))
            crossed, _ = self._apply_fert_minutes(plot, crop, f, minutes, now)
            total_accel += crossed
            total_min += minutes
            wh[fname] = float(wh.get(fname, 0) or 0) - minutes / 60.0
            if wh[fname] <= 1e-9:
                wh.pop(fname, None)
            used_plots.append(i + 1)
        self._save(data)
        if not used_plots:
            return "化肥库存不足且金币不足，无法自动购买施肥。"
        actions.insert(0, f"🧪 施肥完成：对 {len(used_plots)} 块地使用化肥 {total_min:.0f} 分钟（加速 {total_accel} 次）")
        return self._safe_render_plot_status(
            name, key, data, farm, crops, ferts,
            highlights={n: "fert" for n in used_plots},
            profit_delta=-coins_spent,
            actions=actions,
            coins_delta=-coins_spent if coins_spent else None)

    def _handle_farm_harvest(self, event):
        """收割 / 收获（1.7.6）：收割成熟作物并**自动售出**，图片回复（收获状况 + 获得资金）。
        指定编号 = 收割并售出该块；不填 = 全部成熟作物。"""
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        crops = self._load_crops()
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        plots = farm["plots"]
        now = datetime.now().timestamp()
        if len(parts) >= 2:
            try:
                num = int(parts[1].strip())
            except ValueError:
                return "土地编号必须是整数。"
            if num < 1 or num > len(plots):
                return f"土地编号无效（当前共 {len(plots)} 块）。"
            targets = [num - 1]
        else:
            targets = None
        harvested, amounts, total_exp = self._harvest_mature(data, farm, crops, now, targets=targets)
        if not harvested:
            return "没有可收割的成熟作物。"
        lvl_msg = self._farm_gain_exp(farm, total_exp)
        # 自动售出本次收割的全部作物
        total_sold = 0
        wh = farm["warehouse"].setdefault("crops", {})
        for nm, cnt in amounts.items():
            c = self._find_item(crops, nm)
            gain = int(round(cnt * (float(c["crop_price"]) if c else 0.0)))
            total_sold += gain
            have = int(wh.get(nm, 0))
            if have <= cnt:
                wh.pop(nm, None)
            else:
                wh[nm] = have - cnt
        self._add_coins(data, key, total_sold, "收割售卖")
        farm["total_profit"] = int(farm.get("total_profit", 0)) + total_sold
        self._save(data)
        # 2.0.0 纯图片回复：收割地块红色高亮（#FFC5C5）+ 底部大卡片（收割/售出 + 金币变化）
        actions = [f"🌾 收割 {len(harvested)} 块地（编号 {'、'.join(str(n) for n in harvested)}），"
                   f"农场经验 +{total_exp}{lvl_msg}",
                   f"💰 自动售出本次收获，获得资金 +{total_sold} 金币"]
        steal_lines = self._steal_info_lines(farm, datetime.now().timestamp())
        return self._safe_render_plot_status(
            name, key, data, farm, crops, self._load_fertilizers(),
            highlights={n: "harvest" for n in harvested},
            profit_delta=total_sold,
            actions=actions,
            coins_delta=total_sold,
            steal_lines=steal_lines)

    # ================= 偷菜 =================
    def _steal_enabled(self, data) -> bool:
        return bool(globals().get("STEAL_ENABLED", True)) and self._feature_enabled(data, "steal")

    def _target_from_event(self, event, data):
        """解析偷菜目标用户 key：优先 @（message_obj 中的 At 组件），其次 昵称/QQ 号 文本"""
        # 1) @ 组件
        try:
            chain = getattr(getattr(event, "message_obj", None), "message", None) \
                or getattr(event, "message_obj", None)
            if chain is not None:
                comps = chain.chain if hasattr(chain, "chain") else (chain if isinstance(chain, list) else [])
                for comp in comps:
                    if type(comp).__name__ == "At":
                        qq = str(getattr(comp, "qq", "") or "")
                        if qq:
                            return qq
        except Exception:
            pass
        # 2) 文本：偷菜 <目标>
        parts = event.message_str.split(maxsplit=1)
        if len(parts) >= 2:
            target = parts[1].strip()
            if target:
                # QQ 号
                if target.isdigit():
                    return target
                # 昵称匹配（跨群共享 uid，取第一个匹配的）
                for uid, u in (data.get("users") or {}).items():
                    if u.get("name") == target or u.get("nickname") == target:
                        return uid
        return None

    def _handle_guard(self, event):
        """看家 <开/关>：开启/关闭宠物看家（2.0.1 宠物加护改为自动生效：
        宠物激活且空闲、状态档位1-2 时自动保护，本开关仅作偏好标记）"""
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or parts[1].strip() not in ("开", "关"):
            return "格式：看家 开 / 看家 关"
        data = self._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return f"{name} 还没有宠物，无法开启看家。"
        pet["guard"] = parts[1].strip() == "开"
        self._save(data)
        return (f"✅ 看家已{'开启' if pet['guard'] else '关闭'}。"
                f"（宠物加护在宠物激活且空闲、状态档位 1-2 时自动生效，不受本开关限制）")

    def _handle_steal(self, event):
        """偷菜 <@目标>：一键偷走目标所有已成熟地块的一部分作物"""
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        if not self._steal_enabled(data):
            return "⚠️ 「偷菜系统」功能已被管理员关闭，暂时无法使用。"
        # 前提：偷菜者需解锁农场
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if not farm.get("plots"):
            return "你的农场还没有土地，无法偷菜。"
        # 目标用户
        tkey = self._target_from_event(event, data)
        if not tkey or tkey == key:
            return "请 @ 一位开通农场的用户作为偷菜目标（格式：偷菜 @对方）。"
        tdata_farm = self._farm_of(data, tkey)
        if not tdata_farm:
            return "对方还没有解锁农场，无法偷菜。"
        now_ts = datetime.now().timestamp()
        crops = self._load_crops()
        r = self._do_steal(data, key, name, tkey, now_ts, crops)
        if r is None:
            return "对方农场没有可偷的成熟作物。"
        self._save(data)
        return self._steal_summary(name, key, r)

    def _do_steal(self, data, key, name, tkey, now_ts, crops):
        """对单个目标执行偷菜核心（2.0.1 重做，不落盘，调用方统一 _save）。
        返回 None 或 dict：
        {gain, events, guard, fine, tname, status}——
        gain=偷菜方金币收益；guard=宠物加护效果(catch/stop/None)；
        status=special：farm_level_fail 农场等级差限制 / stolen_out 保护地块无可偷 / catch 被抓到；
        events=结果明细（被偷方视角）；fine=罚款。"""
        tdata_farm = self._farm_of(data, tkey)
        if not tdata_farm:
            return None
        key_farm = self._farm_of(data, key)
        farm = key_farm or {"level": 0, "plots": []}
        tname = self._user_name(data, tkey) or "对方"

        # 农场等级差限制：无法向农场等级高于自己 STEAL_LEVEL_GAP 级的农场主偷菜
        level_gap = int(globals().get("STEAL_LEVEL_GAP", 10))
        if int(tdata_farm.get("level", 0)) - int(farm.get("level", 0)) >= level_gap:
            return {"gain": 0, "events": [], "guard": None, "fine": 0,
                    "tname": tname, "status": "farm_level_fail"}

        tplots = tdata_farm.get("plots", [])
        ripe = [(i, p) for i, p in enumerate(tplots)
                if p.get("crop") is not None and now_ts >= p.get("mature_ts", 0)]
        if not ripe:
            return None

        # ---- 参数（全部 WebUI 可编辑） ----
        loss_min = float(globals().get("STEAL_LOSS_MIN", 0.05))
        loss_max = float(globals().get("STEAL_LOSS_MAX", 0.20))
        protect_ratio = float(globals().get("STEAL_PROTECT_RATIO", 0.5))
        guard_catch = float(globals().get("STEAL_GUARD_CATCH", 0.10))
        guard_stop = float(globals().get("STEAL_GUARD_STOP", 0.20))
        pet_gap = int(globals().get("STEAL_PET_GAP", 10))
        pet_gap_div = float(globals().get("STEAL_PET_GAP_DIV", 2.0))
        guard_tier_max = int(globals().get("STEAL_GUARD_TIER_MAX", 3))
        scent_mult = float(globals().get("STEAL_SCENT_MULT", 3.0))
        scent_hours = float(globals().get("STEAL_SCENT_HOURS", 24))
        scent_consec = int(globals().get("STEAL_SCENT_CONSEC", 3))
        suppress_hours = float(globals().get("STEAL_SUPPRESS_HOURS", 12))
        fine_ratio = float(globals().get("STEAL_FINE_RATIO", 1.10))

        # ---- 可偷地块（跳过 保护地块 / 产量0 / 作物等级不够） ----
        events = []
        stealable = []   # (plot, crop)
        protected = False
        for _, plot in ripe:
            crop_name = plot.get("crop", "")
            c = self._find_item(crops, crop_name)
            if c is None:
                continue
            if int(c.get("min_level", 0)) > int(farm.get("level", 0)) + 5:
                events.append({"ts": now_ts, "thief_uid": key, "thief_name": name,
                               "crop": crop_name, "qty": 0, "loss": 0, "status": "level_fail"})
                continue
            yield_now = int(plot.get("yield", 0))
            if yield_now <= 0:
                continue
            orig = int(plot.get("orig_yield", 0) or yield_now)
            if yield_now < orig * protect_ratio:
                # 保护地块：当前产量低于原先 protect_ratio → 剩余作物不可再被偷
                protected = True
                events.append({"ts": now_ts, "thief_uid": key, "thief_name": name,
                               "crop": crop_name, "qty": 0, "loss": 0, "status": "protected"})
                continue
            stealable.append((plot, c))
        if not stealable:
            # 无可偷地块（全为保护地块或等级不够）→ 被偷完了
            return {"gain": 0, "events": events, "guard": None, "fine": 0,
                    "tname": tname, "status": "stolen_out"}

        # ---- 宠物加护判定（农场主宠物激活 + 空闲 + 状态档位 1~2） ----
        tpet = data.get("pets", {}).get(tkey)
        guard_effective = False
        if tpet is not None and not (now_ts < self._pet_busy_until(tpet)):
            try:
                pet_tier = self._worst_tier(tpet["satiety"], tpet["thirst"], tpet["mood"])
            except Exception:
                pet_tier = 1
            guard_effective = pet_tier < guard_tier_max

        # 气味记忆（记在偷菜者身上、针对本农场主；触发概率 ×scent_mult，不受等级压制影响）
        thief_farm = data.setdefault("farms", {}).setdefault(key, {})
        thief_scent = thief_farm.setdefault("scent_memory", {})
        has_scent = float(thief_scent.get(tkey, 0) or 0) > now_ts

        # 等级压制：偷菜者宠物等级比农场主低 pet_gap 级及以上 → 概率减半
        # （「给我站住」触发后 suppress_hours 内失效）
        thief_pet = data.get("pets", {}).get(key)
        thief_pet_lv = int(thief_pet.get("level", 0)) if thief_pet else 0
        suppress_until = float(tdata_farm.get("guard_suppress_until", 0) or 0)
        suppressed = bool(tpet) and (int(tpet.get("level", 0)) - thief_pet_lv >= pet_gap) \
            and now_ts >= suppress_until

        # 加护触发
        guard_effect = None
        if guard_effective:
            p_catch, p_stop = guard_catch, guard_stop
            if has_scent:
                # 气味记忆：概率 ×scent_mult（不受等级压制影响）
                p_catch *= scent_mult
                p_stop *= scent_mult
            elif suppressed:
                p_catch /= pet_gap_div
                p_stop /= pet_gap_div
            rnd = random.random()
            if rnd < p_catch:
                guard_effect = "catch"
            elif rnd < p_catch + p_stop:
                guard_effect = "stop"

        # ---- 连续成功计数（被偷方记录，气味记忆来源） ----
        consec = tdata_farm.setdefault("steal_consec", {})
        ce = consec.setdefault(key, {"ts": now_ts, "count": 0})
        if now_ts - ce["ts"] > 86400:
            ce["ts"] = now_ts
            ce["count"] = 0

        def _record_batch():
            t_events = tdata_farm.setdefault("steal_infos", [])
            t_events.append({
                "ts": now_ts,
                "thief_uid": key,
                "thief_name": name,
                "items": [
                    {"crop": e["crop"], "qty": e["qty"], "loss": e["loss"], "status": e["status"]}
                    for e in events
                ],
                "harvest_ts": None,
            })
            if len(t_events) > 50:
                del t_events[:len(t_events) - 50]

        def _apply_scent():
            thief_scent[tkey] = now_ts + scent_hours * 3600

        # ---- 抓到你了：偷菜失败 + 气味记忆24h + 主人体力-2~5 ----
        if guard_effect == "catch":
            for plot, _c in stealable:
                events.append({"ts": now_ts, "thief_uid": key, "thief_name": name,
                               "crop": plot.get("crop", ""), "qty": 0, "loss": 0,
                               "status": "pet_catch"})
            ce["count"] = 0  # 失败 → 连续成功清零
            if tpet is not None:
                c_min = float(globals().get("STEAL_GUARD_CATCH_STAMINA_MIN", 2))
                c_max = float(globals().get("STEAL_GUARD_CATCH_STAMINA_MAX", 5))
                tpet["stamina"] = round(max(0.0, tpet["stamina"] - random.randint(int(c_min), int(c_max))), 2)
            _apply_scent()
            _record_batch()
            return {"gain": 0, "events": events, "guard": "catch", "fine": 0,
                    "tname": tname, "status": "catch"}

        # ---- 给我站住：损失减半 + 罚款(原金额110%) + 体力-3~6 + 等级压制失效 ----
        if guard_effect == "stop":
            my_gain = 0
            orig_value = 0
            for plot, c in stealable:
                yield_now = int(plot.get("yield", 0))
                qty = max(1, int(yield_now * random.uniform(loss_min, loss_max)))
                actual = max(1, int(qty / 2))   # 农场主损失减半
                orig_value += qty * float(c["crop_price"])
                plot["yield"] = max(0, yield_now - actual)
                gain = int(round(actual * float(c["crop_price"])))
                self._add_coins(data, key, gain, f"偷菜·{plot['crop']}")
                my_gain += gain
                events.append({"ts": now_ts, "thief_uid": key, "thief_name": name,
                               "crop": plot.get("crop", ""), "qty": actual, "loss": gain,
                               "status": "pet_stop"})
            fine = int(round(orig_value * fine_ratio))
            if fine > 0:
                pay = min(fine, self._coins_of(data, key))
                if pay > 0:
                    self._add_coins(data, key, -pay, "偷菜被抓罚款")
                    self._add_coins(data, tkey, pay, "偷菜罚款赔偿")
                    fine = pay
                else:
                    fine = 0
            if tpet is not None:
                s_min = float(globals().get("STEAL_GUARD_STOP_STAMINA_MIN", 3))
                s_max = float(globals().get("STEAL_GUARD_STOP_STAMINA_MAX", 6))
                tpet["stamina"] = round(max(0.0, tpet["stamina"] - random.randint(int(s_min), int(s_max))), 2)
            tdata_farm["guard_suppress_until"] = now_ts + suppress_hours * 3600  # 等级压制失效
            if my_gain > 0:
                ce["count"] += 1
                if ce["count"] >= scent_consec:
                    _apply_scent()
            _record_batch()
            return {"gain": my_gain, "events": events, "guard": "stop", "fine": fine,
                    "tname": tname, "status": "stop"}

        # ---- 正常成功：偷走 5%-20% ----
        my_gain = 0
        for plot, c in stealable:
            yield_now = int(plot.get("yield", 0))
            qty = max(1, int(yield_now * random.uniform(loss_min, loss_max)))
            plot["yield"] = max(0, yield_now - qty)
            gain = int(round(qty * float(c["crop_price"])))
            self._add_coins(data, key, gain, f"偷菜·{plot['crop']}")
            my_gain += gain
            events.append({"ts": now_ts, "thief_uid": key, "thief_name": name,
                           "crop": plot.get("crop", ""), "qty": qty, "loss": gain,
                           "status": "success"})
        if my_gain > 0:
            ce["count"] += 1
            if ce["count"] >= scent_consec:
                _apply_scent()
        _record_batch()
        return {"gain": my_gain, "events": events, "guard": None, "fine": 0,
                "tname": tname, "status": "success" if my_gain > 0 else "stolen_out"}

    def _steal_summary(self, name, key, r):
        """偷菜方视角摘要（单个目标的结果 r，2.0.1）"""
        tname = r.get("tname", "对方")
        status = r.get("status")
        if status == "farm_level_fail":
            gap = int(globals().get("STEAL_LEVEL_GAP", 10))
            return f"🥬 {name} 想偷 {tname} 的农场，但对方农场等级高出自己 {gap} 级及以上，无法发起偷菜。"
        if status == "stolen_out":
            return (f"🥬 {name} 尝试偷取 {tname} 的农场：地块产量已低于原有 50%，进入保护状态——"
                    f"被偷完了，剩余作物无法再被偷取。")
        events = r["events"]
        fail_count = sum(1 for e in events if e["status"] in ("level_fail", "pet_catch", "protected"))
        lines = [f"🥬 {name} 对 {tname} 的农场进行了偷菜："]
        if r["gain"] > 0:
            lines.append(f"💰 偷得作物折合 {r['gain']} 金币！")
        if fail_count:
            lines.append(f"🛡️ {fail_count} 个地块未偷成（等级不足/保护地块/被宠物发现）")
        if r["guard"] == "stop":
            lines.append(f"🐾 对方宠物触发「给我站住」：损失减半，你被罚款 {r['fine']} 金币！")
        elif r["guard"] == "catch":
            lines.append("🐾 对方宠物触发「抓到你了」：偷菜失败！")
        return "\n".join(lines)

    def _handle_auto_steal(self, event):
        """自动偷菜（1.7.6）：每天最多 5 次，随机抽取 4 位可偷菜用户的农场进行偷菜。
        判定：产生金币收益 = 成功，消耗 1 次；无收益 = 失败，不消耗次数；
        被宠物拦截（触发「抓到你了」）= 当天锁定，不能再使用自动偷菜。"""
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        if not self._steal_enabled(data):
            return "⚠️ 「偷菜系统」功能已被管理员关闭，暂时无法使用。"
        err = self._farm_need(data, key, name)
        if err:
            return err
        u = self._ensure_user(data, key)
        today = date.today().isoformat()
        limit = int(globals().get("AUTO_STEAL_DAILY_LIMIT", 5))
        if u.get("auto_steal_date") != today:
            u["auto_steal_date"] = today
            u["auto_steal_used"] = 0
            u["auto_steal_blocked"] = False
        if u.get("auto_steal_blocked"):
            return "🐾 今天自动偷菜已被宠物拦截，无法再次使用（明天重置）。"
        used = int(u.get("auto_steal_used", 0))
        if used >= limit:
            return f"今天的自动偷菜次数已用完（{used}/{limit}）。"
        now_ts = datetime.now().timestamp()
        crops = self._load_crops()
        # 随机抽取 4 位「可偷菜」用户：有农场 + 有成熟作物（且产量 > 0）+ 非自己
        candidates = []
        for uid, f in (data.get("farms") or {}).items():
            if uid == key:
                continue
            if any(p.get("crop") is not None and now_ts >= p.get("mature_ts", 0)
                   and int(p.get("yield", 0)) > 0
                   for p in f.get("plots", [])):
                candidates.append(uid)
        if not candidates:
            return "没有可偷菜的用户（暂无他人有成熟作物）。"
        targets = random.sample(candidates, min(AUTO_STEAL_TARGETS, len(candidates)))

        results = []
        total_gain = 0
        blocked = False
        for tkey in targets:
            r = self._do_steal(data, key, name, tkey, now_ts, crops)
            if r is None:
                continue
            results.append(r)
            total_gain += r["gain"]
            if r["guard"] == "catch":
                blocked = True
                break  # 被宠物拦截 → 停止本轮并锁定今天

        # 被宠物拦截：今天锁定（不算次数）
        if blocked:
            u["auto_steal_date"] = today
            u["auto_steal_blocked"] = True
            self._save(data)
            lines = [f"🥬 {name} 自动偷菜被宠物拦截！",
                     "🐾 今天无法再次使用自动偷菜（明天重置）。"]
            for r in results:
                lines.append(f"· {r['tname']}：被宠物抓到，偷菜失败")
            return "\n".join(lines)

        # 有收益 = 成功，消耗 1 次
        if total_gain > 0:
            u["auto_steal_date"] = today
            u["auto_steal_used"] = used + 1
            self._save(data)
            lines = [f"🥬 {name} 自动偷菜成功！偷了 {len(results)} 位用户，共获得 {total_gain} 金币。"]
            for r in results:
                if r["guard"] == "stop":
                    lines.append(f"· {r['tname']}：+{r['gain']} 金币（对方宠物拦下一半）")
                else:
                    lines.append(f"· {r['tname']}：+{r['gain']} 金币")
            lines.append(f"今日剩余自动偷菜次数：{limit - used - 1} 次")
            return "\n".join(lines)

        # 无收益 = 失败，不消耗次数
        self._save(data)
        lines = [f"🥬 {name} 自动偷菜没有偷到任何收益（本次不消耗次数）。"]
        for r in results:
            lines.append(f"· {r['tname']}：0 金币（未偷到作物）")
        lines.append(f"今日剩余自动偷菜次数：{limit - used} 次")
        return "\n".join(lines)

    def _user_name(self, data, uid):
        u = data.get("users", {}).get(uid, {})
        return u.get("name") or u.get("nickname") or ""

    def _steal_info_lines(self, farm, now_ts):
        """生成偷菜信息表格行（被偷方视角，无框线表格）：
        用户1|白菜 * 10|损失0|失败：对方等级过低
        有效期：被偷批次作物主动收割后 24 小时"""
        infos = farm.get("steal_infos", [])
        # 过滤：harvest_ts 为空（未收割）→ 显示；已收割且 24h 内 → 显示
        alive = []
        for it in infos:
            hts = it.get("harvest_ts")
            if hts is None or now_ts - hts <= 86400:
                alive.append(it)
        if not alive:
            return []
        # 按偷菜者聚合（同一偷菜者一行，多种作物换行；2.0.3：连续重复折叠）
        by_thief = {}
        for it in alive:
            tid = it["thief_uid"]
            d = by_thief.setdefault(tid, {"name": it["thief_name"], "rows": []})
            for item in it.get("items", []):
                status = item.get("status", "success")
                qty = item.get("qty", 0)
                loss = item.get("loss", 0)
                if status == "level_fail":
                    st = "失败：对方等级过低"
                    color = "#7F7F7F"
                elif status == "protected":
                    st = "保护地块"
                    color = "#7F7F7F"
                elif status == "pet_catch":
                    st = "失败：宠物发现"
                    color = "#BF9000"
                elif status == "pet_stop":
                    st = "成功（损失减半）"
                    color = "#BF9000"
                else:
                    st = "成功"
                    color = "#C00000"
                # [作物, 数量, 损失, 状态, 颜色, 次数]
                d["rows"].append([item["crop"], qty, loss, st, color, 1])
        lines = ["🥬 偷菜记录（被偷批次收割后 24 小时内显示）："]
        for tid, d in by_thief.items():
            # 2.0.3：折叠连续重复（同一偷菜者 + 同作物 + 同成败状态）→ *N 表示次数
            merged = []
            for r in d["rows"]:
                if merged and merged[-1][0] == r[0] and merged[-1][3] == r[3]:
                    merged[-1][5] += 1
                else:
                    merged.append(r)
            for crop, qty, loss, st, color, count in merged:
                crop_txt = f"{crop} * {qty}" + (f"*{count}" if count > 1 else "")
                lines.append(f"{d['name']}|{crop_txt}|损失{loss}|{st}")
        return lines

    def _handle_farm_cancel(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：取消种植 <土地编号>"
        try:
            num = int(parts[1].strip())
        except ValueError:
            return "土地编号必须是整数。"
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        if num < 1 or num > len(farm["plots"]):
            return f"土地编号无效。"
        plot = farm["plots"][num - 1]
        if plot.get("crop") is None:
            return f"{num} 号土地本来就是空闲的。"
        self._clear_plot(plot)
        self._save(data)
        # 2.0.0 纯图片回复：取消种植地块蓝色高亮 + 底部大卡片
        actions = [f"🗑️ 已取消 {num} 号土地的种植"]
        return self._safe_render_plot_status(
            name, key, data, farm, self._load_crops(), self._load_fertilizers(),
            highlights={num: "till"},
            actions=actions)

    def _handle_farm_sell(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        crops = self._load_crops()
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        wh = farm["warehouse"].setdefault("crops", {})
        ferts = self._load_fertilizers()

        if len(parts) < 2 or not parts[1].strip():
            if not wh:
                return "仓库里没有作物。"
            n_kinds = len(wh)
            total = 0
            for nm, cnt in list(wh.items()):
                c = self._find_item(crops, nm)
                total += int(round(int(cnt) * (float(c["crop_price"]) if c else 0.0)))
            wh.clear()
            self._add_coins(data, key, total, "售卖作物")
            farm["total_profit"] = int(farm.get("total_profit", 0)) + total
            self._save(data)
            # 2.0.0 纯图片回复：卖出全部作物 + 底部大卡片
            actions = [f"💼 卖出全部作物（共 {n_kinds} 种），获得 +{total} 金币"]
            return self._safe_render_plot_status(
                name, key, data, farm, crops, ferts,
                profit_delta=total,
                actions=actions,
                coins_delta=total)
        args = parts[1].split()
        crop_name = args[0]
        if crop_name not in wh:
            return f"仓库里没有「{crop_name}」。"
        c = self._find_item(crops, crop_name)
        price = float(c["crop_price"]) if c else 0.0
        have = int(wh[crop_name])
        if len(args) >= 2:
            try:
                cnt = int(args[1])
            except ValueError:
                return "数量必须是整数。"
            if cnt <= 0:
                return "数量必须为正整数。"
            if cnt > have:
                return f"{crop_name} 只有 {have} 个。"
        else:
            cnt = have
        gain = int(round(cnt * price))
        self._add_coins(data, key, gain, f"售卖{crop_name}")
        farm["total_profit"] = int(farm.get("total_profit", 0)) + gain
        if cnt >= have:
            wh.pop(crop_name, None)
        else:
            wh[crop_name] = have - cnt
        self._save(data)
        # 2.0.0 纯图片回复：卖出作物 + 底部大卡片
        actions = [f"💼 卖出 {crop_name} ×{cnt}（单价 {self._fmt_price(price)} 金币），获得 +{gain} 金币"]
        return self._safe_render_plot_status(
            name, key, data, farm, crops, ferts,
            profit_delta=gain,
            actions=actions,
            coins_delta=gain)

    def _handle_farm_sell_seed(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split(maxsplit=1)
        crops = self._load_crops()
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        wh = farm["warehouse"].setdefault("seeds", {})

        if len(parts) < 2 or not parts[1].strip():
            if not wh:
                return "仓库里没有种子。"
            n_kinds = len(wh)
            total = 0
            for nm, cnt in list(wh.items()):
                c = self._find_item(crops, nm)
                total += int(round(int(cnt) * (float(c["seed_sell_price"]) if c else 0.0)))
            wh.clear()
            self._add_coins(data, key, total, "售卖种子")
            self._save(data)
            # 2.0.0 纯图片回复：卖出全部种子 + 底部大卡片
            actions = [f"💼 卖出全部种子（共 {n_kinds} 种），获得 +{total} 金币"]
            return self._safe_render_plot_status(
                name, key, data, farm, crops, self._load_fertilizers(),
                profit_delta=total,
                actions=actions,
                coins_delta=total)
        args = parts[1].split()
        seed_name = args[0]
        if seed_name not in wh:
            return f"仓库里没有「{seed_name}」种子。"
        c = self._find_item(crops, seed_name)
        price = float(c["seed_sell_price"]) if c else 0.0
        have = int(wh[seed_name])
        if len(args) >= 2:
            try:
                cnt = int(args[1])
            except ValueError:
                return "数量必须是整数。"
            if cnt <= 0:
                return "数量必须为正整数。"
            if cnt > have:
                return f"{seed_name} 种子只有 {have} 个。"
        else:
            cnt = have
        gain = int(round(cnt * price))
        self._add_coins(data, key, gain, f"售卖种子·{seed_name}")
        if cnt >= have:
            wh.pop(seed_name, None)
        else:
            wh[seed_name] = have - cnt
        self._save(data)
        # 2.0.0 纯图片回复：卖出种子 + 底部大卡片
        actions = [f"💼 卖出 {seed_name} 种子 ×{cnt}（单价 {self._fmt_price(price)} 金币），获得 +{gain} 金币"]
        return self._safe_render_plot_status(
            name, key, data, farm, crops, self._load_fertilizers(),
            profit_delta=gain,
            actions=actions,
            coins_delta=gain)

    def _handle_farm_shop(self, event):
        """农场商店：种子（上）+ 化肥（下）合并展示。
        农场商店 [展开] [页码]：默认按规则展示（可购 N 款 + 不可购 M 款灰卡 + 全部化肥）；
        农场商店 全部：展示所有商品（全部种子 + 全部化肥）。"""
        name = event.get_sender_name()
        key = self._user_key(event)
        parts = event.message_str.split()
        expanded = False
        all_items = False
        page = 1
        if len(parts) >= 2:
            if parts[1] == "全部":
                all_items = True
            elif parts[1] == "展开":
                expanded = True
                if len(parts) >= 3:
                    try:
                        page = max(1, int(parts[2]))
                    except ValueError:
                        page = 1
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        farm = self._farm_of(data, key)
        crops = self._load_crops()
        ferts = self._load_fertilizers()
        if not crops and not ferts:
            return "作物与肥料配置为空（请管理员在 WebUI 编辑 作物.txt / 肥料.txt）。"
        img = self._render_farm_shop(name, farm, crops, ferts, expanded, page, all_items)
        if img is not None:
            return img
        # 文本回退（同样按价格升序）
        lines = [f"{name} 的农场商店（发送「农场商店 展开」查看全部种子，发送「农场商店 全部」查看全部商品）"]
        mult = self._farm_seed_mult(farm)
        seed_list = self._farm_shop_seed_list(farm, crops, expanded, page, all_items)
        for c in sorted(seed_list, key=lambda c: (int(round(c["seed_price"] * mult)), c["name"])):
            p = int(round(c["seed_price"] * mult))
            lv = f"需Lv.{c['min_level']}" if c["min_level"] > 0 else "无等级"
            desc = f"（{c['desc']}）" if c.get("desc") else ""
            lines.append(f"🌱 {c['name']}（{lv}）{p}金币{desc} 售价{int(round(c['yield']*c['crop_price']))}金 经验{c['exp']}")
        for f in sorted(ferts, key=lambda f: (int(f["price"]), f["name"])):
            desc = f"（{f['desc']}）" if f.get("desc") else ""
            maxa = "不限" if self._fert_max_accel(f) < 0 else f"{self._fert_max_accel(f)}次"
            lines.append(f"🧪 {f['name']} {int(f['price'])}金币/时{desc} 每株可加速 {maxa}"
                         f"{f' 增产{f['yield_add']:.0f}%/次' if float(f.get('yield_add', 0) or 0) > 0 else ''}")
        return "\n".join(lines)

    def _handle_farm_seed_shop(self, event):
        # 兼容旧指令：重定向到农场商店
        return self._handle_farm_shop(event)

    def _handle_farm_fert_shop(self, event):
        # 兼容旧指令：重定向到农场商店
        return self._handle_farm_shop(event)

    def _handle_farm_warehouse(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        crops = self._load_crops()
        ferts = self._load_fertilizers()
        img = self._render_warehouse(self._farm_of(data, key), crops, ferts)
        if img is not None:
            return img
        wh = self._farm_of(data, key).get("warehouse", {})
        lines = ["农场仓库："]
        for k, label in [("crops", "作物"), ("seeds", "种子"), ("fertilizers", "肥料")]:
            lines.append(f"----{label}----")
            for nm, cnt in wh.get(k, {}).items():
                lines.append(f"{nm} ×{cnt}")
        return "\n".join(lines)

    def _handle_farm_plots(self, event):
        name = event.get_sender_name()
        key = self._user_key(event)
        data = self._load()
        err = self._farm_need(data, key, name)
        if err:
            return err
        crops = self._load_crops()
        ferts = self._load_fertilizers()
        farm = self._farm_of(data, key)
        steal_lines = self._steal_info_lines(farm, datetime.now().timestamp())
        img = self._render_plot_status(name, key, data, farm, crops, ferts,
                                       steal_lines=steal_lines)
        if img is not None:
            return img
        return "图片生成失败（缺少 Pillow 或字体），请查看日志。"

    # ================= 左轮手枪：内部逻辑 =================
