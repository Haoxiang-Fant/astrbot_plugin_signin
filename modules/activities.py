# -*- coding: utf-8 -*-
# 活动中心。从原 main.py 的 SignInPlugin 拆出的 Mixin，由入口类组合继承。
from .base import *  # noqa: F401,F403  常量与共享工具
from .base import _register_runtime_module  # noqa: F401
import sys as _sys

_register_runtime_module(_sys.modules[__name__])


class ActivityMixin:
    def _handle_activity_center(self, event) -> str:
        """活动：以图片展示当前正在进行的活动（管理员在 WebUI 勾选启用），每个活动一个卡片、一行一卡"""
        if not self._activities:
            return "当前没有配置任何活动模块。"
        data = self._load()
        enabled = data.get("activities", {})
        active = [a for a in self._activities if enabled.get(a.id, False) and a.is_active_now()]
        if not active:
            return "当前没有进行中的活动。"
        img = self._render_activity_image(active)
        if img is not None:
            return img
        # 回退文本
        lines = ["🎯 活动中心：", ""]
        for a in active:
            lines.append(f"📌 {a.name}")
            lines.append(f"🕐 {a.time_str()}")
            lines.append(f"📝 {a.desc}")
            lines.append(f"✅ 参与要求：{a.requirement_text()}")
            if a.commands:
                lines.append(f"💬 相关指令：{' / '.join(a.commands.keys())}")
            lines.append("")
        return "\n".join(lines)

    def _render_activity_image(self, activities):
        """活动中心：每个活动一个矩形卡片，一行一卡（名称/时间/简介/要求/指令）；文字自动换行、卡片高度自适应"""
        Image, ImageDraw = _ensure_pillow()
        if Image is None:
            return None
        # 字号语义：标题 32 / 名称 26 / 正文 18
        fonts = _load_fonts(32, 26, 18)
        if fonts is None:
            return None
        title_font, name_font, body_font = fonts

        pad = 20
        title_h = 52
        card_gap = 14
        inner = 12
        name_h = 40
        line_h = 30

        tw = _text_measurer()
        if tw is None:
            return None

        # 每张卡片的原始内容行（名称 / 时间 / 简介 / 要求 [+指令]）
        def _raw_lines(a):
            lines = [f"📌 {a.name}", f"🕐 {a.time_str()}",
                     f"📝 {a.desc}", f"✅ 参与要求：{a.requirement_text()}"]
            if a.commands:
                lines.append(f"💬 相关指令：{' / '.join(a.commands.keys())}")
            return lines

        # 图片宽度：取未换行最长行 + 卡片内边距余量（保证正常文本不提前换行），
        # 设最大宽度上限（超长文本才自动换行，防止图片过宽/溢出）；textlength 返回 float，需转 int
        max_w = 520
        for a in activities:
            for ln in _raw_lines(a):
                max_w = max(max_w, tw(ln, name_font if ln.startswith("📌") else body_font))
        width = min(int(max_w) + pad * 2 + inner * 2 + 4, 900)
        content_w = width - pad * 2 - inner * 2

        wrap = _make_wrapper(tw, content_w)

        # 预计算每个卡片换行后的行列表与高度
        card_plans = []  # (rows, height)；rows = (text, font, color, is_name)
        for a in activities:
            rows = []
            for ln in wrap(f"📌 {a.name}", name_font):
                rows.append((ln, name_font, (20, 20, 20), True))
            for ln in [f"🕐 {a.time_str()}", f"📝 {a.desc}", f"✅ 参与要求：{a.requirement}"]:
                for wl in wrap(ln, body_font):
                    rows.append((wl, body_font, (70, 70, 70), False))
            if a.commands:
                for wl in wrap(f"💬 相关指令：{' / '.join(a.commands.keys())}", body_font):
                    rows.append((wl, body_font, (70, 70, 70), False))
            h = inner * 2 + sum(name_h if is_name else line_h for _, _, _, is_name in rows)
            card_plans.append((rows, h))

        height = pad * 2 + title_h + sum(h for _, h in card_plans) + card_gap * (len(activities) - 1)

        img = Image.new("RGB", (width, height), (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((pad, pad), "🎯 活动中心", font=title_font, fill=(20, 20, 20))
        y = pad + title_h

        for rows, ch in card_plans:
            # 卡片外框
            d.rectangle([pad, y, width - pad, y + ch], outline=(200, 200, 200), width=2)
            yy = y + inner
            for text, font, color, is_name in rows:
                d.text((pad + inner, yy), text, font=font, fill=color)
                yy += name_h if is_name else line_h
            y += ch + card_gap

        return _save_temp_image(img, "_act_", "活动中心")

    def _activity_command(self, head: str, event) -> str:
        """活动模块自定义指令分发：仅处理「已启用 + 时间有效 + 满足参与要求」的活动指令"""
        if not self._activities:
            return None
        data = self._load()
        key = self._user_key(event)
        enabled = data.get("activities", {})
        for act in self._activities:
            if not enabled.get(act.id, False):
                continue
            if not act.is_active_now():
                continue
            if head not in act.commands:
                continue
            ok, missing = act.check_requirements(self, data, key)
            if not ok:
                return f"⚠️ 活动「{act.name}」未满足参与要求（{missing}）。"
            fn = act.commands.get(head)
            if fn:
                try:
                    r = fn(event)
                    if r:
                        return r
                except Exception as e:
                    logger.error(f"[插件] 活动 {act.id} 指令「{head}」处理异常: {e}")
        return None

    def _sign_in_activity_hooks(self, event, data: dict, key: str, lines: list) -> None:
        """签到成功后的活动钩子分发（仅调用「已启用 + 时间有效 + 满足参与要求」活动的 on_sign_in）"""
        if not self._activities:
            return
        enabled = data.get("activities", {})
        for act in self._activities:
            if not enabled.get(act.id, False):
                continue
            if not act.is_active_now():
                continue
            ok, missing = act.check_requirements(self, data, key)
            if not ok:
                lines.append("")
                lines.append(f"⚠️ 活动「{act.name}」未满足参与要求（{missing}），本次签到不触发。")
                continue
            fn = getattr(act, "on_sign_in", None)
            if fn:
                try:
                    fn(event, data, key, lines)
                except Exception as e:
                    logger.error(f"[插件] 活动 {act.id} 签到钩子处理异常: {e}")

    # ================= 银行贷款 =================
