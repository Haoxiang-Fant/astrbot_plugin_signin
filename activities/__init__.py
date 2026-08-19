# -*- coding: utf-8 -*-
"""活动中心组件框架。

活动开发者在本目录下新建一个 .py 文件（如 my_activity.py），
定义一个继承 BaseActivity 的类并用 @register_activity 装饰即可注册，
无需修改插件主程序。插件启动时会自动扫描本目录下所有 .py 模块。

示例见 example_activity.py。编写文档见 ACTIVITY.md。
"""
import importlib.util
import os

from astrbot.api import logger

ACTIVITIES = []  # 所有已注册的活动实例（保持注册顺序）


class BaseActivity:
    """活动基类。

    子类需要实现 / 覆盖以下属性：
      id          唯一标识（小写英文，如 "double_signin"）
      name        活动名称
      start       开始时间 "YYYY-MM-DD HH:MM"，空字符串表示不限制
      end         结束时间 "YYYY-MM-DD HH:MM"，空字符串表示不限制
      desc        活动简介
      requirement 参与要求描述
      commands    该活动支持的指令表：{指令: 处理函数(event) -> str | None}
                  处理函数返回非空字符串作为回复（自动转图片发送）；
                  返回 None 表示不处理该指令（交给其他活动或主插件）。
                  处理函数也可以返回 ("image", path) 元组。
    """

    id = ""
    name = ""
    start = ""
    end = ""
    desc = ""
    requirement = ""  # 自由文本参与要求（与下方等级要求合并展示）
    fav_level_req = 0   # 参与要求：好感度等级（0 = 不限）
    farm_level_req = 0  # 参与要求：农场等级（0 = 不限）
    pet_level_req = 0   # 参与要求：宠物等级（0 = 不限）
    commands = {}
    params = {}  # 自定义可调参数：{key: {"label": str, "type": "str|int|float|bool", "desc": str, "default": ...}}

    # WebUI 内置可编辑字段（与自定义 params 一起构成参数表单）
    EDITABLE_FIELDS = (
        {"field": "start", "label": "开始时间", "type": "str", "desc": "格式 YYYY-MM-DD HH:MM，空 = 不限制"},
        {"field": "end", "label": "结束时间", "type": "str", "desc": "格式 YYYY-MM-DD HH:MM，空 = 不限制"},
        {"field": "desc", "label": "活动简介", "type": "str", "desc": ""},
        {"field": "fav_level_req", "label": "好感度等级要求", "type": "int", "desc": "需要好感度达到的等级，0 = 不限", "default": 0, "min": 0, "max": 10},
        {"field": "farm_level_req", "label": "农场等级要求", "type": "int", "desc": "需要农场达到的等级，0 = 不限", "default": 0, "min": 0, "max": 100},
        {"field": "pet_level_req", "label": "宠物等级要求", "type": "int", "desc": "需要宠物达到的等级，0 = 不限", "default": 0, "min": 0, "max": 100},
        {"field": "requirement", "label": "参与要求（文字）", "type": "str", "desc": "额外的自由文本要求描述", "default": ""},
    )

    def param_schema(self) -> list:
        """返回 WebUI 参数表单描述（内置字段 + 自定义 params），用于动态渲染与校验"""
        items = [dict(f) for f in self.EDITABLE_FIELDS]
        for key, spec in self.params.items():
            items.append({
                "field": key,
                "label": spec.get("label", key),
                "type": spec.get("type", "str"),
                "desc": spec.get("desc", ""),
                "default": spec.get("default"),
                "min": spec.get("min"),
                "max": spec.get("max"),
            })
        return items

    def validate_override(self, field: str, value):
        """校验单个字段覆盖值（含日期格式 / 数值类型与范围）。返回 (是否有效, 错误原因或空串)"""
        for spec in self.param_schema():
            if spec["field"] != field:
                continue
            t = spec["type"]
            label = spec.get("label", field)
            # 日期字段格式校验（start/end：空 = 不限制）
            if field in ("start", "end") and value not in (None, ""):
                from datetime import datetime

                try:
                    datetime.strptime(str(value), "%Y-%m-%d %H:%M")
                except ValueError:
                    return False, f"「{label}」格式应为 YYYY-MM-DD HH:MM（如 2026-08-31 04:00）"
            try:
                if t == "int":
                    v = int(value)
                elif t == "float":
                    v = float(value)
                elif t == "bool":
                    if isinstance(value, str):
                        v = value.strip().lower() in ("1", "true", "yes", "on")
                    else:
                        v = bool(value)
                else:
                    v = str(value) if value is not None else ""
            except (TypeError, ValueError):
                return False, f"「{label}」需要输入{t}类型（收到：{value!r}）"
            if spec.get("min") is not None and v < spec["min"]:
                return False, f"「{label}」不能小于 {spec['min']}"
            if spec.get("max") is not None and v > spec["max"]:
                return False, f"「{label}」不能大于 {spec['max']}"
            return True, ""
        return False, "未知字段"

    def apply_override(self, field: str, value) -> bool:
        """按参数 schema 校验并应用单个字段覆盖（WebUI 保存后调用），成功返回 True"""
        ok, err = self.validate_override(field, value)
        if not ok:
            return False
        for spec in self.param_schema():
            if spec["field"] != field:
                continue
            t = spec["type"]
            try:
                if t == "int":
                    v = int(value)
                elif t == "float":
                    v = float(value)
                elif t == "bool":
                    if isinstance(value, str):
                        v = value.strip().lower() in ("1", "true", "yes", "on")
                    else:
                        v = bool(value)
                else:
                    v = str(value) if value is not None else ""
            except (TypeError, ValueError):
                return False
            setattr(self, field, v)
            return True
        return False

    def requirement_text(self) -> str:
        """合成参与要求文本（等级要求 + 自由描述），用于活动卡片展示"""
        parts = []
        if int(self.fav_level_req or 0) > 0:
            parts.append(f"好感度 Lv.{int(self.fav_level_req)}+")
        if int(self.farm_level_req or 0) > 0:
            parts.append(f"农场 Lv.{int(self.farm_level_req)}+")
        if int(self.pet_level_req or 0) > 0:
            parts.append(f"宠物 Lv.{int(self.pet_level_req)}+")
        if self.requirement:
            parts.append(str(self.requirement))
        return "；".join(parts) if parts else "无（所有玩家均可参与）"

    def check_requirements(self, plugin, data: dict, key: str):
        """检查玩家是否满足参与要求（等级要求）。返回 (是否满足, 不满足项描述)"""
        missing = []
        fav_lv = plugin._level_of(float(data.get("users", {}).get(key, {}).get("favorability", 0.0)))
        if int(self.fav_level_req or 0) > 0 and fav_lv < int(self.fav_level_req):
            missing.append(f"好感度 Lv.{int(self.fav_level_req)}+（当前 Lv.{fav_lv}）")
        farm = data.get("farms", {}).get(key)
        farm_lv = int(farm.get("level", 0)) if isinstance(farm, dict) else 0
        if int(self.farm_level_req or 0) > 0 and farm_lv < int(self.farm_level_req):
            missing.append(f"农场 Lv.{int(self.farm_level_req)}+（当前 Lv.{farm_lv}）")
        pet = data.get("pets", {}).get(key)
        pet_lv = int(pet.get("level", 1)) if isinstance(pet, dict) else 0
        if int(self.pet_level_req or 0) > 0 and pet_lv < int(self.pet_level_req):
            missing.append(f"宠物 Lv.{int(self.pet_level_req)}+（当前 Lv.{pet_lv}）")
        return (not missing, "、".join(missing))

    def is_active_now(self, now=None) -> bool:
        """判断活动当前是否处于进行时段（管理员未勾选启用时由主插件另行过滤）"""
        from datetime import datetime

        now = now or datetime.now()
        if self.start:
            try:
                if now < datetime.strptime(self.start, "%Y-%m-%d %H:%M"):
                    return False
            except ValueError:
                pass
        if self.end:
            try:
                if now > datetime.strptime(self.end, "%Y-%m-%d %H:%M"):
                    return False
            except ValueError:
                pass
        return True

    def is_expired_now(self, now=None) -> bool:
        """活动是否已过结束时间（配置了 end 且当前时间已超过）——供 WebUI 过期提醒"""
        if not self.end:
            return False
        from datetime import datetime

        now = now or datetime.now()
        try:
            return now > datetime.strptime(self.end, "%Y-%m-%d %H:%M")
        except ValueError:
            return False

    def time_str(self) -> str:
        """活动起止时间的展示文本"""
        if not self.start and not self.end:
            return "长期进行"
        return f"{self.start or '…'} ~ {self.end or '…'}"

    def on_sign_in(self, event, data: dict, key: str, lines: list) -> None:
        """（可选）签到成功后的钩子：玩家发送「签到」完成每日签到时被调用。

        参数：
          event  AstrMessageEvent
          data   当前加载的玩家数据（修改后由主插件统一保存，无需自行 _save）
          key    当前玩家用户 ID
          lines  即将返回的签到文本行列表，可 append 追加内容（如抽奖结果）

        仅在活动「已启用 + 时间有效」时被调用。
        """
        return

    def on_redpacket_open(self, event, data: dict, key: str, gid: str, now_ts: float) -> str:
        """（可选）开红包前的钩子：玩家发送「开 / 开红包 / 抢红包」时被调用（清理过期红包之前）。

        参数：
          event  AstrMessageEvent
          data   当前加载的玩家数据（可向 data["redpackets"] 追加系统红包等）
          key    当前玩家用户 ID
          gid    当前群 ID（字符串）
          now_ts 当前时间戳

        返回非空字符串作为提示（如「红包雨开始啦！」），会显示在开红包结果顶部。
        仅在活动「已启用 + 时间有效」时被调用。
        """
        return ""


def register_activity(cls):
    """类装饰器：把一个活动类实例化并注册到 ACTIVITIES"""
    ACTIVITIES.append(cls())
    return cls


def load_all():
    """扫描本目录下所有活动模块并导入（模块内通过 @register_activity 注册）"""
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    for fn in sorted(os.listdir(pkg_dir)):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        path = os.path.join(pkg_dir, fn)
        spec = importlib.util.spec_from_file_location(f"act_{fn[:-3]}", path)
        if spec is None or spec.loader is None:
            continue
        try:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            logger.warning(f"[插件] 加载活动模块 {fn} 失败: {e}")
    return ACTIVITIES
