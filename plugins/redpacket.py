# -*- coding: utf-8 -*-
"""金币红包（3.0.0 功能插件）。自 2.3.0 modules/redpacket.py 原样迁移：
金币红包（发）/ 开红包（开 / 抢红包），红包数据存于 data["redpackets"]（结构不变）。
过期清理由 core.interval(60, ...) 周期任务承载（系统红包作废 / 玩家红包退回发起人），
开红包时懒清理兜底；红包雨等活动的定时生成属 thirdparty 插件（activities tick），不在本插件。"""
import random
from datetime import datetime

from astrbot.api import logger

from ..core import REDPACKET_DAILY_LIMIT, REDPACKET_TTL

NAME = "redpacket"


# ================= 红包金额算法（1.7.5 动态期望平衡，整数分制） =================
def _redpacket_draw_amount(remain_total: int, n: int) -> int:
    """动态期望平衡算法（单位：分；本插件货币为整数金币，1 分 = 1 金币）。

    铁律：
    - 全部变量为整数（分），严禁浮点金额；
    - 全量发放后实得之和严格等于初始总额（误差 0）；
    - 任意时刻期望恒等于 剩余总额 / 剩余个数（顺序无偏）；
    - n > 25 直接报错，不做计算。

    算法：A 区（高暴击 [1.5, upper_limit]）+ B 区（普通 [0.5, 1.3]，E_B=0.9）；
    动态反推进入 A 区的概率 P_A 使期望恒为 1（P_A_raw = 0.1/(E_A-0.9)，截断至 0.6）。
    并发说明：调用方在全局 asyncio.Lock（core.lock）内完成
    「读取余额 → 计算 → 扣减 → 写回」，天然满足『严禁先查询再修改』。
    """
    n = int(n)
    remain_total = int(remain_total)
    if n > 25:
        raise ValueError("红包个数不能超过 25。")
    if n <= 0 or remain_total <= 0:
        return 0
    # 第一步：边界与基准
    if n == 1:
        return remain_total  # 全部归最后一人
    safe_max = remain_total - (n - 1)  # 必须 -(n-1)，为剩余每人预留至少 1 分
    base = remain_total // n  # 基准奖金（向下取整，整数）

    # 第二步：加成区倍率
    upper_limit = min(3.0, safe_max / base) if base > 0 else 1.0
    if upper_limit < 1.5:
        upper_limit = 1.0  # 有效区间收缩 → 无效暴击
    e_b = 0.9
    e_a = (1.5 + upper_limit) / 2  # A 区实际期望（仅 upper_limit>=1.5 时有意义）

    # 第三步：动态概率反推（期望恒定 = 1）
    if upper_limit >= 1.5 and e_a > e_b:
        p_a_raw = (1.0 - e_b) / (e_a - e_b)  # = 0.1 / (E_A - 0.9)
        if p_a_raw <= 0:
            p_a = 0.0  # 纯普通模式
        elif p_a_raw > 0.6:
            p_a = 0.6  # 暴击概率上限，防失衡
        else:
            p_a = p_a_raw
    else:
        p_a = 0.0

    # 第四步：随机倍率 → 理论金额
    rand = random.random()
    if rand < p_a:
        multiplier = random.uniform(1.5, upper_limit)  # A 区
    else:
        multiplier = random.uniform(0.5, 1.3)  # B 区
    raw_amount = int(base * multiplier)  # 去尾截断

    # 第五步：截断与安全兜底
    if raw_amount < 1:
        raw_amount = 1
    if raw_amount > safe_max:
        raw_amount = safe_max
    return int(raw_amount)


def _redpacket_draw(rp: dict, uid: str) -> int:
    """按规则计算并登记当前用户抢到的红包金额，返回发放金额（调用方负责入账）。
    抢完（left 变 0）时记录 finished_ts，供「来晚一步」60 秒倒计时提示使用。"""
    left = int(rp.get("left", 0))
    remain = int(rp.get("remain", 0))
    amount = _redpacket_draw_amount(remain, left)
    if amount < 1:
        amount = remain  # 兜底（理论不可达：n>=1 且 remain>=n 时 amount 至少 1）
    rp.setdefault("claimed", {})[uid] = amount
    rp["remain"] = int(rp.get("remain", 0)) - amount
    rp["left"] = left - 1
    if rp["left"] <= 0:
        rp["finished_ts"] = datetime.now().timestamp()  # 抢完时刻（60 秒倒计时起点）
    return amount


def _redpacket_cleanup_expired(data: dict, now_ts: float) -> int:
    """清理过期红包：系统红包（owner_uid 以 system: 开头）过期剩余直接作废，
    玩家红包退回发起人；全局红包（rain 标记）不受群隔离。
    返回退回的玩家红包数量（0 = 无变动）。由周期清理任务定期执行 + 开红包时兜底调用。"""
    if _CORE is None:  # 未挂载（理论不可达）：不做任何变动，避免退款丢失
        return 0
    rps = data.setdefault("redpackets", [])
    refunds = []
    keep = []
    for rp in rps:
        owner = rp.get("owner_uid", "")
        is_system = isinstance(owner, str) and owner.startswith("system:")
        is_expired = float(rp.get("expires_ts", 0)) <= now_ts
        if is_expired and is_system:
            continue  # 系统红包过期：作废不保留
        if is_expired and int(rp.get("remain", 0)) > 0:
            refunds.append(rp)
        else:
            keep.append(rp)
    if not refunds and len(keep) == len(rps):
        return 0
    data["redpackets"] = keep
    for rp in refunds:
        _CORE.add_coins(rp.get("owner_uid"), int(rp.get("remain", 0)), "红包过期退回")
    return len(refunds)


_CORE = None  # register(core) 时注入的核心框架实例


def register(core):
    global _CORE
    _CORE = core

    # ================= 周期任务：过期红包清理（60s 粒度，核心巡检协程承载） =================
    async def redpacket_cleanup_job():
        async with core.lock:
            data = core.data
            before = len(data.get("redpackets") or [])
            try:
                refunded = _redpacket_cleanup_expired(data, datetime.now().timestamp())
            except Exception as e:
                logger.error(f"[redpacket] 过期红包清理异常: {e}")
                return
            if refunded > 0 or len(data.get("redpackets") or []) != before:
                core.save()

    core.interval(60, redpacket_cleanup_job)

    # ================= 金币红包（发） =================
    def handle_redpacket_send(event):
        """金币红包 <红包个数> <总金额>：出资人为发送者，每人每天最多发 REDPACKET_DAILY_LIMIT 次"""
        name = core.user_name(event)
        key = core.user_key(event)
        gid = event.get_group_id()
        if not gid:
            return "金币红包只能在群里发哦～"
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            return "格式：金币红包 <红包个数> <总金额>，例如：金币红包 5 500"
        args = parts[1].split()
        if len(args) < 2:
            return "格式：金币红包 <红包个数> <总金额>，例如：金币红包 5 500"
        try:
            count = int(args[0])
            total = int(args[1])
        except ValueError:
            return "红包个数和总金额必须是整数。"
        if count < 1:
            return "红包个数至少为 1。"
        if count > 25:
            return "红包个数最多 25 个。"
        if total < count:
            return f"总金额至少 {count} 金币（每个红包至少 1 金币）。"

        data = core.data
        user = core.ensure_user(key)
        today = datetime.now().date().isoformat()
        if user.get("rp_date") != today:
            user["rp_date"] = today
            user["rp_sent"] = 0
        sent = int(user.get("rp_sent", 0))
        daily_limit = int(core.param("REDPACKET_DAILY_LIMIT", REDPACKET_DAILY_LIMIT))
        if sent >= daily_limit:
            return f"你今天已经发过 {daily_limit} 次红包了（每天最多 {daily_limit} 次），明天再来吧。"
        if core.coins_of(key) < total:
            return f"金币不足（需要 {total}，当前 {core.coins_of(key)}）。"

        core.add_coins(key, -total, "发红包")
        user["rp_sent"] = sent + 1
        now_ts = datetime.now().timestamp()
        ttl = int(core.param("REDPACKET_TTL", REDPACKET_TTL))
        # 发新红包：清理本群已抢完的旧红包（取消「来晚一步」倒计时状态）
        data.setdefault("redpackets", [])
        data["redpackets"] = [rp for rp in data["redpackets"]
                              if not (str(rp.get("group_id")) == str(gid) and int(rp.get("remain", 0)) <= 0)]
        rp = {
            "id": f"{now_ts:.0f}-{random.randint(1000, 9999)}",
            "group_id": str(gid),
            "owner_uid": key,
            "owner_name": name,
            "count": count,
            "total": total,
            "remain": total,
            "left": count,
            "claimed": {},
            "created_ts": now_ts,
            "expires_ts": now_ts + ttl,
        }
        data.setdefault("redpackets", []).append(rp)
        core.save()
        lines = [
            f"🧧 {name} 发了一个金币红包！",
            f"💰 金额：{total} 金币（{count} 个）",
            f"⏰ 有效期：{ttl // 60} 分钟，超时剩余金额自动退回",
            f"💬 发送「开」或「开红包」即可抢！",
            f"{core.coin_line(key)}",
        ]
        img = core.image.text("金币红包", lines)
        if img is not None:
            return img
        return "\n".join(lines)

    # ================= 开红包（开 / 开红包 / 抢红包） =================
    def handle_redpacket_open(event):
        """开 / 开红包 / 抢红包：打开当前群所有能开的红包（每位用户每轮只能开一次），并懒清理过期红包。
        抢完 60 秒内提示「来晚一步」，否则无红包提示「本群暂时没有红包」。
        （红包雨等活动钩子由 thirdparty 插件负责，本插件不再分发。）"""
        name = core.user_name(event)
        key = core.user_key(event)
        gid = event.get_group_id()
        if not gid:
            return "红包只能在群里开哦～"
        data = core.data
        now_ts = datetime.now().timestamp()

        # 懒清理：过期红包。系统红包（owner_uid 以 system: 开头）过期剩余直接作废，
        # 玩家红包退回发起人；全局红包（rain 标记）不受群隔离（周期清理任务也会定期清理）
        refunds = _redpacket_cleanup_expired(data, now_ts)

        openable = [rp for rp in data["redpackets"]
                    if (str(rp.get("group_id")) == str(gid) or rp.get("rain"))
                    and int(rp.get("remain", 0)) > 0
                    and float(rp.get("expires_ts", 0)) > now_ts
                    and key not in rp.get("claimed", {})]
        if not openable:
            # 先持久化清理结果（可能移除了过期红包 / 作废的系统红包）
            core.save()
            # 本群未过期且仍有剩余的红包（该用户已抢过 → 每轮只能开一次）
            claimed_live = [rp for rp in data["redpackets"]
                            if (str(rp.get("group_id")) == str(gid) or rp.get("rain"))
                            and int(rp.get("remain", 0)) > 0
                            and float(rp.get("expires_ts", 0)) > now_ts]
            if claimed_live:
                return f"{name} 你已经抢过了（每位用户每轮红包只能开一次）。"
            # 抢完 60 秒内（倒计时中）
            finished = [rp for rp in data["redpackets"]
                        if (str(rp.get("group_id")) == str(gid) or rp.get("rain"))
                        and float(rp.get("expires_ts", 0)) > now_ts
                        and int(rp.get("remain", 0)) <= 0
                        and now_ts - float(rp.get("finished_ts", 0)) <= 60]
            if refunds:
                lines = [f"↩️ {refunds} 个过期红包的剩余金额已退回给发起人。"]
                if finished:
                    lines.append("来晚一步，红包被人抢空了～")
                img = core.image.text("金币红包", lines)
                if img is not None:
                    return img
                return "\n".join(lines)
            if finished:
                return "来晚一步，红包被人抢空了～（60 秒内如有人发新红包即可再抢）"
            return "本群暂时没有红包，让群友发一个「金币红包 <个数> <总金额>」吧～"

        results = []
        for rp in openable:
            try:
                amount = _redpacket_draw(rp, key)
            except ValueError as e:
                # 动态期望平衡算法上限 25：异常的旧数据红包跳过不发放（余额保留）
                logger.error(f"[redpacket] 红包金额计算失败，跳过: {e}")
                continue
            core.add_coins(key, amount, f"抢红包（{rp.get('owner_name', '')}）")
            results.append((rp, amount))
        core.save()

        lines = [f"🧧 {name} 打开了 {len(results)} 个红包："]
        for rp, amount in results:
            tail = "（已抢完）" if int(rp.get("left", 0)) <= 0 else ""
            lines.append(f"· {rp.get('owner_name', '')} 的红包：+{amount} 金币{tail}")
        lines.append(f"{core.coin_line(key)}")
        if refunds:
            lines.append("")
            lines.append(f"↩️ {refunds} 个过期红包的剩余金额已退回给发起人。")
        img = core.image.text("金币红包", lines)
        if img is not None:
            return img
        return "\n".join(lines)

    core.command("金币红包", feature="redpacket")(handle_redpacket_send)
    core.command("开", "开红包", "抢红包", feature="redpacket")(handle_redpacket_open)

    # ================= 服务暴露（红包雨等第三方活动复用核心机制） =================
    class RedpacketApi:
        """红包服务：过期清理与开包算法（thirdparty 活动插件可复用）"""

        def cleanup_expired(self, now_ts=None):
            """清理过期红包（默认当前时间；调用方需自行持有 core.lock 并 core.save()）"""
            data = core.data
            return _redpacket_cleanup_expired(data, datetime.now().timestamp() if now_ts is None else now_ts)

        def draw(self, rp, uid):
            """在红包 rp 上为用户 uid 计算并登记抢包金额（返回发放金额，入账由调用方 core.add_coins 完成）"""
            return _redpacket_draw(rp, uid)

    core.expose("redpacket", RedpacketApi())
