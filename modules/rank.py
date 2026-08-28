# -*- coding: utf-8 -*-
# 排行榜。从原 main.py 的 SignInPlugin 拆出的 Mixin，由入口类组合继承。
from .base import *  # noqa: F401,F403  常量与共享工具
from .base import _register_runtime_module  # noqa: F401
import sys as _sys

_register_runtime_module(_sys.modules[__name__])


class RankMixin:
    @staticmethod
    def _parse_hex_color(s, default=(55, 86, 35)):
        """#RRGGBB → (r, g, b)；非法值返回默认色（默认 #375623）"""
        s = str(s or "").strip().lstrip("#")
        if len(s) == 6:
            try:
                return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                pass
        return default

    @staticmethod
    def _lighten_color(color, pct=0.2):
        """颜色向白色方向浅化 pct（0.2 = 浅 20%）：progress = c + (255 - c) * pct"""
        pct = max(0.0, min(0.9, float(pct)))
        return tuple(int(c + (255 - int(c)) * pct) for c in color)

    @staticmethod
    def _mask_name(name, in_group):
        """排行榜展示名：非本群用户脱敏为「第一个字 * 最后一个字」（≥2 字时，如 李四明 → 李*明），
        获得到昵称信息后即用首尾字，不整行打星；本群用户显示原名。"""
        if in_group:
            return name
        s = str(name)
        if len(s) <= 1:
            return s  # 单字名无法遮掩
        return s[0] + "*" + s[-1]

    @staticmethod
    def _fit_name(name, max_chars=None):
        """排行榜名字显示长度固定：超过 max_chars（默认 RANK_NAME_MAX_CHARS，6）个字符时，
        无法显示的部分用 ... 代替（如「超长名字测试用」→「超长名字测...」）。"""
        if max_chars is None:
            max_chars = int(globals().get("RANK_NAME_MAX_CHARS", 6) or 6)
        disp = str(name)
        if max_chars >= 1 and len(disp) > max_chars:
            disp = disp[:max_chars] + "..."
        return disp

    def _mark_group_member(self, data: dict, gid, uid, name="") -> None:
        """本群成员注册：用户在本群触发插件功能即标记（带本群昵称，覆盖为最新时间戳）。
        存储结构：group_members[gid][uid] = {"ts": 时间戳, "name": 本群昵称}；48 小时后失效。"""
        members = data.setdefault("group_members", {})
        members.setdefault(str(gid), {})[str(uid)] = {
            "ts": datetime.now().timestamp(),
            "name": str(name or "").strip(),
        }

    def _is_group_member(self, data: dict, gid, uid) -> bool:
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
        ttl = float(globals().get("GROUP_MEMBER_TTL_HOURS", 48) or 48) * 3600
        return datetime.now().timestamp() - float(ts) <= ttl

    def _group_member_name(self, data: dict, gid, uid) -> str:
        """取用户在本群的昵称（标记时记录）；旧数据（裸时间戳）或未标记时返回空串。"""
        if not gid:
            return ""
        g = data.get("group_members", {}).get(str(gid)) or {}
        info = g.get(str(uid)) if isinstance(g, dict) else None
        if isinstance(info, dict):
            return str(info.get("name", "") or "").strip()
        return ""

    def _update_group_member_name(self, data: dict, gid, uid, name) -> None:
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

    def _group_default_name(self, data: dict, gid, uid) -> str:
        """排行榜默认昵称：WebUI「同步全部群聊昵称」得到的数据（group_names[gid][uid]）。
        实时标记昵称（group_members）优先于它；未同步到该群/用户时返回空串。"""
        if not gid:
            return ""
        g = data.get("group_names", {}).get(str(gid)) or {}
        return str(g.get(str(uid), "") or "").strip()

    async def _refresh_rank_names(self, event, data: dict, gid, uids) -> int:
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
                        self._update_group_member_name(data, gid, uid, nick)
                        updated += 1
            except Exception as e:
                logger.debug(f"[插件] 刷新群成员昵称失败 uid={uid}: {e}")

        await asyncio.gather(*[_one(u) for u in uids])
        return updated

    def _collect_bots(self):
        """收集支持 OneBot call_action 的平台机器人（aiocqhttp 等），供 WebUI 同步群昵称使用"""
        bots = []
        pm = getattr(self.context, "platform_manager", None)
        if pm is None:
            return bots
        insts = getattr(pm, "get_insts", lambda: [])()
        if not insts:
            insts = getattr(pm, "platform_insts", []) or []
        for inst in insts:
            bot = getattr(inst, "bot", None)
            if bot is not None and callable(getattr(bot, "call_action", None)):
                bots.append(bot)
        return bots

    # ================= 局域网开放（1.7.9） =================
    @staticmethod
    def _rank_plot_score_total(plots):
        """土地等级分合计：每块地块按累计升级花费计分（贫瘠 0 / 红 1000 / 普通 2500 / 肥沃 4500 / 黑 7500，
        分数表在 WebUI「排行榜 → 农场排行」可调），地块超界按最高级计分。"""
        raw = globals().get("RANK_PLOT_SCORES", (0, 1000, 2500, 4500, 7500))
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

    def _rank_entries(self, kind: str, data: dict):
        """计算全部玩家的排行条目，返回按积分降序的 [(score, uid, name), ...]。
        kind: "coins" 金币排行 / "pet" 宠物排行 / "farm" 农场排行。"""
        entries = []
        if kind == "coins":
            cw = float(globals().get("RANK_COIN_COIN_W", 1.0))
            bw = float(globals().get("RANK_COIN_BANK_W", 1.0))
            for uid, user in (data.get("users") or {}).items():
                if not isinstance(user, dict):
                    continue
                bank = data.get("bank", {}).get(uid)
                dep = sum(self._dep_amount(d) for d in (bank.get("deposits", []) if isinstance(bank, dict) else []))
                score = self._coins_of(data, uid) * cw + dep * bw
                entries.append((score, str(uid), self._user_name(data, uid) or str(uid)))
        elif kind == "pet":
            ew = float(globals().get("RANK_PET_EXP_W", 2.0))
            hw = float(globals().get("RANK_PET_HEALTH_W", 1.5))
            aw = float(globals().get("RANK_PET_ATTR_W", 0.5))
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
            ew = float(globals().get("RANK_FARM_EXP_W", 2.0))
            pw = float(globals().get("RANK_FARM_PLOT_W", 400.0))
            gw = float(globals().get("RANK_FARM_GRADE_W", 0.5))
            for uid, farm in (data.get("farms") or {}).items():
                if not isinstance(farm, dict):
                    continue
                exp = float(farm.get("exp", 0) or 0)
                plots = farm.get("plots") or []
                score = exp * ew + max(0, len(plots) - 2) * pw + self._rank_plot_score_total(plots) * gw
                entries.append((score, str(uid), self._user_name(data, uid) or str(uid)))
        # 积分降序；同分按用户 ID 稳定排序
        entries.sort(key=lambda e: (-e[0], e[1]))
        return entries

    @staticmethod
    def _fmt_score(v):
        """积分显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0"""
        v = float(v)
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return f"{v:.2f}".rstrip("0").rstrip(".")

    def _render_rank_image(self, title, rows, hl_color):
        """排行榜图片：每行「<名次> <用户名/宠物名> [进度条] <积分(右对齐)>」。
        rows: [(rank, name, score_str, is_me, ratio, masked), ...]（已按积分降序、取前 N 名）；
        ratio：进度条填充比例（第一名恒为 1.0，其它 = 积分/第一名积分，0~1）；
        masked：该行是否为非本群（脱敏）用户。
        文字颜色三态：高亮自己 RANK_HIGHLIGHT_COLOR / 脱敏行 RANK_MASKED_COLOR / 普通行 RANK_TEXT_COLOR；
        进度条颜色 = 文字颜色浅 RANK_BAR_LIGHTEN；分割线颜色 RANK_SEP_COLOR，高度 = 行高 × RANK_ROW_SEP_PCT（默认 3%）。
        名字显示固定 RANK_NAME_MAX_CHARS 个字符，超出部分用 ... 代替；
        进度条左侧留白 = 基准 12px × RANK_BAR_GAP_LEFT_MULT（默认 200%）、右侧 = × RANK_BAR_GAP_RIGHT_MULT（默认 300%）；
        每行文字行高固定 line_h、分割线紧跟行底，文字与分割线间距一致；
        图片宽度 = 内容宽度 × RANK_IMAGE_SCALE（默认 2.5 = 原来的 250%），高度自适应。"""
        Image, ImageDraw = _ensure_pillow()
        if Image is None:
            return None
        fonts = _load_fonts(36, 24)
        if fonts is None:
            return None
        title_font, body_font = fonts
        tw = _text_measurer()
        if tw is None:
            return None

        pad = 24
        title_h = 60
        line_h = 40
        bar_h = 12          # 进度条高度
        rank_col = 64       # 名次列宽
        gap_base = 12.0     # 进度条留白基准（左右各 12px 为“原先的”）
        scale = float(globals().get("RANK_IMAGE_SCALE", 2.5) or 2.5)
        lighten = float(globals().get("RANK_BAR_LIGHTEN", 0.2) or 0.2)
        name_max_chars = int(globals().get("RANK_NAME_MAX_CHARS", 6) or 6)
        gap_left = gap_base * float(globals().get("RANK_BAR_GAP_LEFT_MULT", 2.0) or 2.0)
        gap_right = gap_base * float(globals().get("RANK_BAR_GAP_RIGHT_MULT", 3.0) or 3.0)
        sep_pct = float(globals().get("RANK_ROW_SEP_PCT", 0.03) or 0.03)
        text_color_s = self._parse_hex_color(globals().get("RANK_TEXT_COLOR", "#000000"), (0, 0, 0))
        masked_color_s = self._parse_hex_color(globals().get("RANK_MASKED_COLOR", "#7F7F7F"), (127, 127, 127))
        sep_color = self._parse_hex_color(globals().get("RANK_SEP_COLOR", "#D9D9D9"), (217, 217, 217))

        prepared = []
        score_w_max = 0
        name_w_max = 0
        for rank, name, score_str, is_me, ratio, masked in rows:
            # 名字显示长度固定：超过 name_max_chars 个字符的部分用 ... 代替
            disp = self._fit_name(name, name_max_chars)
            sw = tw(score_str, body_font)
            score_w_max = max(score_w_max, sw)
            name_w_max = max(name_w_max, tw(disp, body_font))
            prepared.append((rank, disp, score_str, is_me, ratio, masked))

        # 宽度 = 内容宽度 × 倍数（默认 250%）；高度 = 标题 + 行 × 行高 + 行间分割线高度（间距恒定）
        base_w = pad * 2 + rank_col + 8 + name_w_max + gap_left + score_w_max
        width = int(base_w * scale)
        sep_h = int(line_h * max(0.0, min(0.5, sep_pct)))
        n = len(prepared)
        height = int(pad * 2 + title_h + line_h * n + sep_h * max(0, n - 1))

        score_x = width - pad                  # 积分右对齐的右边缘
        name_x = pad + rank_col + 8            # 名字列起点（分割线也从这里开始）
        bar_x0 = int(name_x + name_w_max + gap_left)
        bar_x1 = int(score_x - score_w_max - gap_right)
        if bar_x1 - bar_x0 < 12:
            bar_x1 = bar_x0 + 12              # 极小图兜底：进度条至少 12px
        sep_x1 = int(score_x)                  # 分割线右端 = 积分右边缘

        img = Image.new("RGB", (width, height), (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((int(pad), int(pad)), title, font=title_font, fill=(20, 20, 20))
        y = pad + title_h
        hl = self._parse_hex_color(hl_color)
        bar_w = bar_x1 - bar_x0
        for idx, (rank, disp, score_str, is_me, ratio, masked) in enumerate(prepared):
            # 文字颜色三态：高亮自己 / 脱敏行 / 普通行
            if is_me:
                text_color = hl
            elif masked:
                text_color = masked_color_s
            else:
                text_color = text_color_s
            bar_color = self._lighten_color(text_color, lighten)
            d.text((int(pad), int(y)), str(rank), font=body_font, fill=text_color)
            d.text((int(name_x), int(y)), disp, font=body_font, fill=text_color)
            # 进度条：轨道浅灰 + 填充色（文字浅 20%，高亮行即高亮色浅 20%）
            bar_y = y + (line_h - bar_h) // 2
            d.rectangle([bar_x0, bar_y, bar_x1, bar_y + bar_h], fill=(238, 238, 238), outline=(205, 205, 205))
            fill_w = int(bar_w * max(0.0, min(1.0, ratio)))
            if fill_w > 0:
                d.rectangle([bar_x0 + 1, bar_y + 1, bar_x0 + fill_w, bar_y + bar_h - 1], fill=bar_color)
            d.text((int(score_x - tw(score_str, body_font)), int(y)), score_str, font=body_font, fill=text_color)
            y += line_h
            # 行间分割线（仅 用户名 → 积分 范围）：高度 = 行高 × RANK_ROW_SEP_PCT（默认 3%），
            # 紧跟文字行底，文字与分割线间距对所有行一致
            # 注意 Pillow rectangle 坐标含端点，绘制 [y, y+sep_h-1] 使实际高度精确 = sep_h
            if idx < n - 1 and sep_h > 0:
                d.rectangle([int(name_x), int(y), sep_x1, int(y + sep_h - 1)], fill=sep_color)
                y += sep_h
        return _save_temp_image(img, "_rank_", "排行榜")

    def _is_in_group(self, data: dict, gid, uid) -> bool:
        """本群判定：以 WebUI 同步的全群名单（group_names[gid]）为准，
        并兼容 48h 活跃标记（group_members[gid]，未同步/刚加入时也能识别）；无群上下文视为非本群。"""
        if not gid:
            return False
        g = data.get("group_names", {}).get(str(gid)) or {}
        if isinstance(g, dict) and str(uid) in g:
            return True
        return self._is_group_member(data, gid, uid)

    def _pick_any_group_name(self, data: dict, uid) -> str:
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

    def _build_rank(self, kind: str, event) -> str:
        """组装排行榜响应：图片优先，渲染失败回退为文本。
        全局榜 + 本群判定（WebUI 同步名单 group_names，兼容 48h 活跃标记）：
        非本群用户脱敏为「首字*尾字」（绝不用 ***，多群昵称不一致时随机取一个群的昵称）。"""
        titles = {"coins": "💰 金币排行榜", "pet": "🐾 宠物排行榜", "farm": "🌾 农场排行榜"}
        data = self._load()
        me = str(self._user_key(event))
        gid = event.get_group_id()          # 当前群（私聊无群 → 全员按非本群处理）
        show = int(globals().get("RANK_DISPLAY", 20) or 20)
        hl = globals().get("RANK_HIGHLIGHT_COLOR", "#92D050")
        entries = self._rank_entries(kind, data)
        if not entries:
            return "还没有玩家上榜，快发送「签到」「解锁宠物」「解锁农场」参与吧～"
        top_score = entries[0][0]           # 第一名积分：进度条基准（第一名恒 100%）
        rows = []
        for i, (score, uid, name) in enumerate(entries[:show], start=1):
            # 本群判定：WebUI 同步名单（group_names）为主，兼容 48h 活跃标记
            in_group = self._is_in_group(data, gid, uid)
            # 名字来源（优先级）：
            #   本群 → 实时记录昵称 / 同步本群昵称 / 存档昵称（宠物名）
            #   非本群 → 任意群昵称（多群不一致随机）/ 存档昵称（宠物名）/ uid 兜底
            #           → 一律脱敏「首字*尾字」，绝不用 ***
            if in_group:
                gname = self._group_member_name(data, gid, uid)
                gdef = self._group_default_name(data, gid, uid)
                base = gname or gdef or name
            else:
                base = self._pick_any_group_name(data, uid) or name
            # 没有任何昵称（兜底就是用户 ID）时按用户 ID 做「首字*尾字」脱敏，同样不使用 ***
            disp = self._mask_name(base, in_group)
            ratio = 1.0 if i == 1 else (min(1.0, score / top_score) if top_score > 0 else 0.0)
            rows.append((i, disp, self._fmt_score(score), uid == me, ratio, not in_group))
        img = self._render_rank_image(titles[kind], rows, hl)
        if img is not None:
            return img
        # 文本回退（附进度百分比）
        lines = [titles[kind] + f"（前 {show} 名）", ""]
        for rank, name, score_str, is_me, ratio, masked in rows:
            tag = "（本群之外）" if masked else ""
            lines.append(f"{rank}. {name}{tag} {score_str} [{int(ratio * 100)}%]" + (" ← 你" if is_me else ""))
        return "\n".join(lines)

    def _handle_rank_coins(self, event):
        return self._build_rank("coins", event)

    def _handle_rank_pet(self, event):
        return self._build_rank("pet", event)

    def _handle_rank_farm(self, event):
        return self._build_rank("farm", event)
