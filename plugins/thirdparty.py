# -*- coding: utf-8 -*-
"""第三方活动挂载插件（3.0.0）：活动模块动态加载 / 活动中心 / 动态指令兜底 / 活动 tick。

2.3.0 移植：base.py::_load_activity_modules（activities/ 目录动态加载）、
modules/activities.py 的 ActivityMixin（活动中心展示、活动自定义指令分发）、
webui.py::_load_activity_configs（活动参数覆盖回放）、pet.py 的红包雨定时巡检段。

活动经 attach(bridge) 拿到主插件能力：bridge 为旧版 2.x 方法名适配器
（与签到插件 _LegacyPluginBridge 同构；插件间禁止 import，故独立实现）；
仅当 act.plugin 尚未挂靠时才 attach，签到等插件可先行挂上自己的适配器。
「活动」「活动中心」渲染活动中心图（image/activity.py::render_activity_image，文本回退）；
未注册指令经 core.register_fallback 分发给「已启用 + 时间有效」活动的 commands；
core.interval(60, ...) 周期巡检调用活动 tick（红包雨定时生成等）。
"""
import importlib
import os
import sys
from datetime import datetime

from astrbot.api import logger

NAME = "thirdparty"


# ================= 活动模块动态加载（2.3.0 base.py::_load_activity_modules 移植） =================
def _load_activity_modules(plugin_dir):
    """动态加载插件目录下 thirdparty/ 中的第三方活动模块。返回 (活动实例列表, 是否可用)。
    第三方插件与第一方插件（plugins/）分目录存放；把插件根目录插入 sys.path
    并以顶层名 `thirdparty` 导入组件框架，保证活动模块内
    `from thirdparty import BaseActivity, register_activity` 可用。"""
    try:
        pkg_dir = os.path.join(plugin_dir, "thirdparty")
        if not os.path.exists(os.path.join(pkg_dir, "__init__.py")):
            return [], False
        if plugin_dir not in sys.path:
            sys.path.insert(0, plugin_dir)
        # 先清掉缓存，保证插件重载后活动模块被重新注册
        sys.modules.pop("thirdparty", None)
        pkg = importlib.import_module("thirdparty")
        acts = pkg.load_all()
        logger.info(f"[插件] 活动中心加载完成，共 {len(acts)} 个第三方活动模块")
        return acts, True
    except Exception as e:
        logger.warning(f"[插件] 活动中心初始化失败: {e}")
        return [], False


class _ActivityBridge:
    """旧版活动组件适配器：活动经 attach(plugin) 拿到的主插件能力（保留 2.x 方法名）。
    data 形参仅为兼容旧签名，实际一律操作核心唯一数据 core.data。"""

    def __init__(self, core):
        self._core = core

    # ---- 数据存取 ----
    def _load(self):
        return self._core.data

    def _save(self, data=None):
        self._core.save()

    def _ensure_user(self, data, key):
        return self._core.ensure_user(key)

    def _coins_of(self, data, key):
        return self._core.coins_of(key)

    def _add_coins(self, data, key, amount, reason="", skip_repay=False):
        return self._core.add_coins(key, amount, reason, skip_repay=skip_repay)

    def _coin_line(self, data, key):
        return self._core.coin_line(key)

    # ---- 用户 / 等级 ----
    def _user_key(self, event):
        return self._core.user_key(event)

    def _level_of(self, fav):
        return self._core.level_of(fav)

    # ---- 签到（双倍签到等活动复用；经签到服务调用，不直接 import 对方模块） ----
    def _apply_signin_once(self, data, key, today):
        svc = self._core.service("signin")
        fn = getattr(svc, "apply_signin_once", None) if svc is not None else None
        if not callable(fn):
            raise AttributeError("签到服务（signin）未挂载，活动签到钩子不可用")
        return fn(data, key, today)


def _apply_activity_configs(core, acts):
    """从 data["activity_config"] 加载每个活动的参数覆盖（起始/结束时间、简介、要求、
    自定义参数）并应用到活动实例（2.3.0 webui._load_activity_configs 移植；
    WebUI 保存时已即时应用，此处兜底保证启动后生效，修改无需重启）。"""
    try:
        configs = core.data.get("activity_config") or {}
        for act in acts:
            cfg = configs.get(getattr(act, "id", ""))
            if not isinstance(cfg, dict):
                continue
            for field, value in cfg.items():
                try:
                    act.apply_override(field, value)
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"[插件] 加载活动参数失败: {e}")


def _activity_command(core, acts, bridge, head, event):
    """活动模块自定义指令分发：仅处理「已启用 + 时间有效 + 满足参与要求」的活动指令
    （2.3.0 ActivityMixin::_activity_command 移植）。返回回复值或 None。"""
    if not acts:
        return None
    data = core.data
    key = core.user_key(event)
    enabled = data.get("activities", {})
    for act in acts:
        if not enabled.get(getattr(act, "id", ""), False):
            continue
        if not act.is_active_now():
            continue
        if head not in act.commands:
            continue
        ok, missing = act.check_requirements(bridge, data, key)
        if not ok:
            return f"⚠️ 活动「{act.name}」未满足参与要求（{missing}）。"
        fn = act.commands.get(head)
        if fn:
            try:
                r = fn(event)
                if r:
                    return r
            except Exception as e:
                logger.error(f"[插件] 活动 {getattr(act, 'id', '?')} 指令「{head}」处理异常: {e}")
    return None


class ThirdpartyApi:
    """第三方活动挂载服务（其它插件经 core.service("thirdparty") 调用）"""

    def __init__(self, core, acts, bridge):
        self._core = core
        self._acts = acts
        self._bridge = bridge

    @property
    def activities(self):
        """已加载的活动实例列表（保持注册顺序）"""
        return self._acts

    def load_activities(self):
        """已加载的活动实例列表（与 activities 属性同源）"""
        return self._acts

    def is_active(self, activity_id):
        """活动当前是否可用：管理员已启用（data["activities"]）且 处于进行时段"""
        enabled = self._core.data.get("activities", {})
        act = next((a for a in self._acts if getattr(a, "id", "") == activity_id), None)
        if act is None:
            return False
        return bool(enabled.get(activity_id, False)) and bool(act.is_active_now())

    def activity_command(self, head, event):
        """活动模块自定义指令分发（回复值或 None）"""
        return _activity_command(self._core, self._acts, self._bridge, head, event)


def register(core):
    plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    acts, _ok = _load_activity_modules(plugin_dir)
    bridge = _ActivityBridge(core)

    # 活动挂靠主插件能力（仅当尚未挂靠时；签到插件可先行挂上自己的适配器）
    for act in acts:
        attach = getattr(act, "attach", None)
        if callable(attach) and getattr(act, "plugin", None) is None:
            try:
                attach(bridge)
            except Exception as e:
                logger.warning(f"[插件] 活动 {getattr(act, 'id', '?')} attach 失败: {e}")
    # 应用 WebUI 保存过的活动参数覆盖（时间/要求/自定义参数）
    _apply_activity_configs(core, acts)

    # ================= 「活动」「活动中心」：活动中心展示 =================
    @core.command("活动", "活动中心", feature="activity")
    def handle_activity_center(event):
        """活动：以图片展示当前正在进行的活动（管理员在 WebUI 勾选启用），
        每个活动一个卡片、一行一卡（渲染缺失/失败时回退文本列表）。"""
        if not acts:
            return "当前没有配置任何活动模块。"
        enabled = core.data.get("activities", {})
        active = [a for a in acts if enabled.get(a.id, False) and a.is_active_now()]
        if not active:
            return "当前没有进行中的活动。"
        render = getattr(core.image, "render_activity_image", None)
        img = None
        if callable(render):
            try:
                img = render(active)
            except Exception as e:
                logger.error(f"[插件] 活动中心图片渲染失败: {e}")
                img = None
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

    # ================= 动态活动指令兜底路由 =================
    def _fallback(head, event):
        return _activity_command(core, acts, bridge, head, event)

    core.register_fallback(_fallback)

    # ================= 周期任务：活动 tick 巡检（红包雨定时生成等，60s 粒度） =================
    async def activity_tick_job():
        async with core.lock:
            data = core.data
            now_ts = datetime.now().timestamp()
            enabled = data.get("activities", {})
            for act in acts:
                if not enabled.get(getattr(act, "id", ""), False):
                    continue
                if not act.is_active_now():
                    continue
                tick = getattr(act, "tick", None)
                if tick is None:
                    continue
                try:
                    changed = tick(data, now_ts)
                except Exception as e:
                    logger.warning(f"[插件] 活动 {getattr(act, 'id', '?')} 定时巡检异常: {e}")
                    continue
                if changed:
                    core.save()

    core.interval(60, activity_tick_job)

    core.expose("thirdparty", ThirdpartyApi(core, acts, bridge))
