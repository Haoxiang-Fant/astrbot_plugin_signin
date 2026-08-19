# -*- coding: utf-8 -*-
"""定时红包雨活动：每天 8:00 / 12:00 / 16:00 / 20:00 开启一轮红包雨，
有效期 1 小时（至开启时间一小时后），单轮 1000 金币分 10 个红包。

总金额（amount）/ 红包个数（count）可在 WebUI「活动中心」修改；
开启时间（rain_times）/ 有效期（rain_hours）在 WebUI 插件配置页修改。
"""
from datetime import date, datetime, timedelta

from activities import BaseActivity, register_activity


@register_activity
class RedpacketRainActivity(BaseActivity):
    id = "redpacket_rain"
    name = "定时红包雨"
    start = ""
    end = ""
    desc = "每天 8:00 / 12:00 / 16:00 / 20:00 开启红包雨，1 小时内可抢！金额与个数可在活动中心调整。"
    requirement = "无（所有玩家均可参与）"
    commands = {}
    # 自定义可调参数（WebUI 活动中心可改）
    params = {
        "amount": {"label": "单轮总金额（金币）", "type": "int", "desc": "每轮红包雨的总金币数", "default": 1000, "min": 10, "max": 1000000},
        "count": {"label": "红包个数", "type": "int", "desc": "每轮红包雨拆分的红包个数", "default": 10, "min": 1, "max": 100},
    }

    plugin = None

    def attach(self, plugin):
        self.plugin = plugin

    def on_redpacket_open(self, event, data, key, gid, now_ts):
        """开红包前懒生成当轮红包雨（每个时段每天只生成一次），返回提示或空字符串"""
        p = self.plugin
        # 归一化为 int 小时（防御配置为 float，如 8.0 / "8.5"）
        times = []
        for x in (getattr(p, "rain_times", None) or []):
            try:
                t = int(x)
            except (TypeError, ValueError):
                continue
            if 0 <= t < 24:
                times.append(t)
        times = sorted(set(times))
        if not times:
            return ""
        # 总金额 / 个数：活动中心参数（amount / count），未设置时回退主插件配置
        amount = max(1, int(getattr(self, "amount", 0) or 0) or int(getattr(p, "rain_amount", 1000)))
        count = max(1, int(getattr(self, "count", 0) or 0) or int(getattr(p, "rain_count", 10)))
        hours = max(0.5, float(getattr(p, "rain_hours", 1) or 1))
        base = datetime.fromtimestamp(now_ts)

        # 找当前时间所属的开启时段：开启点 <= now < 开启点 + hours 小时
        active_slot = None
        for t in times:
            start = base.replace(hour=t, minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=hours)
            if start.timestamp() <= now_ts < end.timestamp():
                active_slot = t
                break
        if active_slot is None:
            return ""

        # 每个时段每天只生成一次
        today = date.today().isoformat()
        gen = data.setdefault("rain_generated", {})
        slots = gen.setdefault(today, [])
        if active_slot in slots:
            return ""

        rp = {
            "id": f"rain-{today}-{active_slot}",
            "group_id": "rain",  # 全局红包：所有群共享同一奖池
            "owner_uid": f"system:rain:{today}:{active_slot}",  # 系统红包：过期剩余作废
            "owner_name": "红包雨",
            "count": count,
            "total": amount,
            "remain": amount,
            "left": count,
            "claimed": {},
            "created_ts": now_ts,
            "expires_ts": now_ts + int(hours * 3600),
            "rain": True,
        }
        data.setdefault("redpackets", []).append(rp)
        slots.append(active_slot)
        return (f"🌧️ 红包雨开始啦！{amount} 金币分 {count} 个红包，"
                f"有效期至 {datetime.fromtimestamp(now_ts + int(hours * 3600)).strftime('%H:%M')}，"
                f"发送「开」抢！")
