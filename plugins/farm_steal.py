# -*- coding: utf-8 -*-
"""农场偷菜插件（3.0.0 功能插件）。

自 2.3.0 modules/farm.py `_handle_steal` / `_handle_auto_steal` / `_do_steal`
（旧 1991-2341 行）原样迁移：
  - 偷菜 <@对方>：一键偷走目标农场所有已成熟地块 5%~20% 的作物（STEAL_LOSS_MIN/MAX
    可经 WebUI 运行参数覆盖），折算金币入账；
  - 自动偷菜：每日最多 AUTO_STEAL_DAILY_LIMIT 次，随机抽取 AUTO_STEAL_TARGETS 位
    可偷用户；产生金币收益 = 成功消耗 1 次；被宠物「抓到你了」= 当天锁定；
  - 看家拦截经 core.service("farm_guard").intercept(data, thief, target, gain)
    （未挂载/异常 → 无守护，与旧版无宠物行为一致）：
    「抓到你了」→ 本轮无收益（不扣产量不入账）；「给我站住」→ 损失减半 +
    罚款（原金额 110%，扣偷菜者并转赔农场主，余额不足部分作废）；
    体力消耗按拦截方返回的 (lo, hi) 区间对农场主宠物结算。
与 2.3.0 的差异（v3 简化）：不再保留农场等级差限制 / 保护地块 / 偷菜操作日志
（farm_logs）。steal_infos 记录与 2.3.0 同款（{ts, thief_uid, thief_name,
items:[{crop, qty, loss, status}], harvest_ts}，上限 50 条）——读取方
（_steal_info_lines / 仓库偷菜表 / WebUI records）均消费旧字段。回复文案与 2.3.0 逐字一致。
"""
import random
from datetime import date, datetime

from astrbot.api import logger

from ..core import AUTO_STEAL_DAILY_LIMIT, AUTO_STEAL_TARGETS, FARM_UNLOCK_COST

NAME = "farm_steal"


class FarmStealApi:
    """农场偷菜服务接口（其它插件经 core.service("farm_steal") 调用）"""

    def __init__(self, core):
        self._core = core

    # ---- 内部工具 ----
    @staticmethod
    def _load_crops(core):
        """作物数值表（game_items.json 经核心缓存读取）"""
        return (core.items() or {}).get("crops") or []

    @staticmethod
    def _find_item(items, name):
        return next((x for x in items if x.get("name") == name), None)

    @staticmethod
    def _user_name(core, data, uid):
        """目标显示名：自定义昵称 → 用户记录 name/nickname → 对方"""
        custom = core.custom_name_of(uid)
        if custom:
            return custom
        u = (data.get("users") or {}).get(uid) or {}
        return u.get("name") or u.get("nickname") or "对方"

    @staticmethod
    def _farm_need(core, key, name):
        if not (core.data.get("farms") or {}).get(key):
            return f"{name} 还没有农场，发送「解锁农场」（需 {FARM_UNLOCK_COST} 金币）解锁。"
        return None

    @staticmethod
    def _record_infos(tdata_farm, thief_key, thief_name, now_ts, events):
        """被偷方 steal_infos 追加（2.3.0 同款格式，旧 2142-2155；读取方
        _steal_info_lines / 仓库偷菜表 / WebUI records 均消费旧字段）。
        harvest_ts 置 None（被偷批次收割后由农场侧回填）；上限 50 条。"""
        infos = tdata_farm.setdefault("steal_infos", [])
        infos.append({
            "ts": now_ts,
            "thief_uid": thief_key,
            "thief_name": thief_name,
            "items": [
                {"crop": e["crop"], "qty": e["qty"], "loss": e["loss"], "status": e["status"]}
                for e in events
            ],
            "harvest_ts": None,
        })
        if len(infos) > 50:
            del infos[:len(infos) - 50]

    @staticmethod
    def _apply_guard_stamina(data, target_key, guard_r):
        """按拦截方返回的体力区间对农场主宠物结算（旧 2167-2170 / 2201-2204）"""
        rng = (guard_r or {}).get("stamina")
        if not rng:
            return
        tpet = (data.get("pets") or {}).get(target_key)
        if tpet is None:
            return
        stamina = float(tpet.get("stamina", 0) or 0)
        tpet["stamina"] = round(max(0.0, stamina - random.randint(int(rng[0]), int(rng[1]))), 2)

    def _do_steal(self, data, key, name, tkey, now_ts, crops):
        """对单个目标执行偷菜核心（旧 2025-2232 迁移，不落盘，调用方统一 core.save()）。
        返回 None（对方无成熟作物）或 dict：
        {gain, events, guard, fine, tname, status}——gain=偷菜方金币收益；
        guard=宠物加护效果(catch/stop/None)；status=catch 被抓到 / stop 拦下一半 /
        success 正常得手 / stolen_out 被偷完（v3 无保护地块，仅为兼容保留）；
        events=结果明细（被偷方视角）；fine=罚款（已扣后的实缴金额）。"""
        core = self._core
        tdata_farm = (data.get("farms") or {}).get(tkey)
        if not tdata_farm:
            return None
        tname = self._user_name(core, data, tkey)

        # 可偷地块：已成熟且仍有产量（v3 简化：跳过 保护地块 / 等级差 判定）
        ripe = [p for p in (tdata_farm.get("plots") or [])
                if p.get("crop") is not None and now_ts >= float(p.get("mature_ts", 0) or 0)
                and int(p.get("yield", 0) or 0) > 0]
        if not ripe:
            return None

        # ---- 参数（全部 WebUI 可编辑） ----
        loss_min = float(core.param("STEAL_LOSS_MIN", 0.05))
        loss_max = float(core.param("STEAL_LOSS_MAX", 0.20))

        # ---- 计划偷取量（先算后扣：拦截结果决定扣减与入账，旧 2214-2225 同款随机） ----
        plan = []   # (plot, crop_name, price, qty, gain)
        orig_value = 0
        for plot in ripe:
            crop_name = plot.get("crop", "")
            yield_now = int(plot.get("yield", 0) or 0)
            qty = max(1, int(yield_now * random.uniform(loss_min, loss_max)))
            c = self._find_item(crops, crop_name)
            price = float(c.get("crop_price", 0) or 0) if c else 0.0
            gain = int(round(qty * price))
            plan.append((plot, crop_name, price, qty, gain))
            orig_value += gain

        # ---- 看家拦截（farm_guard 未挂载 → 无守护，与旧版无宠物行为一致） ----
        guard_r = None
        gsvc = core.service("farm_guard")
        intercept = getattr(gsvc, "intercept", None) if gsvc is not None else None
        if intercept is not None:
            try:
                guard_r = intercept(data, key, tkey, orig_value)
            except Exception as e:
                logger.error(f"[farm_steal] 看家拦截判定异常: {e}")
                guard_r = None
        guard = (guard_r or {}).get("guard")
        fine = int((guard_r or {}).get("fine", 0) or 0)

        # ---- 抓到你了：偷菜失败（不扣产量不入账）+ 气味记忆24h + 主人体力-2~5（旧 2160-2174） ----
        if guard == "catch":
            events = [{"ts": now_ts, "thief_uid": key, "thief_name": name,
                       "crop": crop_name, "qty": 0, "loss": 0, "status": "pet_catch"}
                      for plot, crop_name, _price, _qty, _gain in plan]
            self._apply_guard_stamina(data, tkey, guard_r)
            self._record_infos(tdata_farm, key, name, now_ts, events)
            return {"gain": 0, "events": events, "guard": "catch", "fine": 0,
                    "tname": tname, "status": "catch"}

        # ---- 给我站住：损失减半 + 罚款(原金额110%) + 体力-3~6 + 等级压制失效（旧 2176-2212） ----
        if guard == "stop":
            my_gain = 0
            events = []
            for plot, crop_name, price, qty, _gain in plan:
                actual = max(1, int(qty / 2))   # 农场主损失减半
                yield_now = int(plot.get("yield", 0) or 0)
                plot["yield"] = max(0, yield_now - actual)
                gain = int(round(actual * price))
                if gain:
                    core.add_coins(key, gain, f"偷菜·{crop_name}")
                my_gain += gain
                events.append({"ts": now_ts, "thief_uid": key, "thief_name": name,
                               "crop": crop_name, "qty": actual, "loss": gain,
                               "status": "pet_stop"})
            if fine > 0:
                pay = min(fine, core.coins_of(key))
                if pay > 0:
                    core.add_coins(key, -pay, "偷菜被抓罚款")
                    core.add_coins(tkey, pay, "偷菜罚款赔偿")
                    fine = pay
                else:
                    fine = 0
            self._apply_guard_stamina(data, tkey, guard_r)
            self._record_infos(tdata_farm, key, name, now_ts, events)
            return {"gain": my_gain, "events": events, "guard": "stop", "fine": fine,
                    "tname": tname, "status": "stop"}

        # ---- 正常成功：偷走 5%-20%（旧 2214-2232） ----
        my_gain = 0
        events = []
        for plot, crop_name, _price, qty, gain in plan:
            yield_now = int(plot.get("yield", 0) or 0)
            plot["yield"] = max(0, yield_now - qty)
            if gain:
                core.add_coins(key, gain, f"偷菜·{crop_name}")
            my_gain += gain
            events.append({"ts": now_ts, "thief_uid": key, "thief_name": name,
                           "crop": crop_name, "qty": qty, "loss": gain,
                           "status": "success"})
        self._record_infos(tdata_farm, key, name, now_ts, events)
        return {"gain": my_gain, "events": events, "guard": None, "fine": 0,
                "tname": tname, "status": "success" if my_gain > 0 else "stolen_out"}

    def _steal_summary(self, name, key, r):
        """偷菜方视角摘要（单个目标的结果 r，旧 2234-2255 逐字迁移）"""
        tname = r.get("tname", "对方")
        status = r.get("status")
        if status == "farm_level_fail":
            gap = int(self._core.param("STEAL_LEVEL_GAP", 10))
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

    # ---- 对外接口 ----
    def steal(self, event, target_key=None):
        """偷菜入口（「偷菜」指令与第三方联动）：target_key 缺省时从 event 解析 @/文本目标。
        返回回复文本（str）。"""
        core = self._core
        name = core.user_name(event)
        key = core.user_key(event)
        data = core.data
        err = self._farm_need(core, key, name)
        if err:
            return err
        farm = (data.get("farms") or {}).get(key)
        if not farm.get("plots"):
            return "你的农场还没有土地，无法偷菜。"
        # 目标用户
        tkey = target_key or _target_from_event(event, data)
        if not tkey or tkey == key:
            return "请 @ 一位开通农场的用户作为偷菜目标（格式：偷菜 @对方）。"
        if not (data.get("farms") or {}).get(tkey):
            return "对方还没有解锁农场，无法偷菜。"
        now_ts = datetime.now().timestamp()
        crops = self._load_crops(core)
        r = self._do_steal(data, key, name, tkey, now_ts, crops)
        if r is None:
            return "对方农场没有可偷的成熟作物。"
        core.save()
        return self._steal_summary(name, key, r)


def _target_from_event(event, data):
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


def register(core):
    api = FarmStealApi(core)

    # ================= 偷菜 <@目标>（旧 1991-2023） =================
    @core.command("偷菜", feature="farm_steal")
    def handle_steal(event):
        """偷菜 <@目标>：一键偷走目标所有已成熟地块的一部分作物"""
        return api.steal(event)

    # ================= 自动偷菜（旧 2257-2341） =================
    @core.command("自动偷菜", feature="farm_steal")
    def handle_auto_steal(event):
        """自动偷菜（1.7.6）：每天最多 AUTO_STEAL_DAILY_LIMIT 次，随机抽取
        AUTO_STEAL_TARGETS 位可偷菜用户的农场进行偷菜。
        判定：产生金币收益 = 成功，消耗 1 次；无收益 = 失败，不消耗次数；
        被宠物拦截（触发「抓到你了」）= 当天锁定，不能再使用自动偷菜。"""
        core = api._core
        name = core.user_name(event)
        key = core.user_key(event)
        data = core.data
        err = api._farm_need(core, key, name)
        if err:
            return err
        u = core.ensure_user(key)
        today = date.today().isoformat()
        limit = int(core.param("AUTO_STEAL_DAILY_LIMIT", AUTO_STEAL_DAILY_LIMIT))
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
        crops = api._load_crops(core)
        # 随机抽取「可偷菜」用户：有农场 + 有成熟作物（且产量 > 0）+ 非自己
        candidates = []
        for uid, f in (data.get("farms") or {}).items():
            if uid == key:
                continue
            if any(p.get("crop") is not None and now_ts >= float(p.get("mature_ts", 0) or 0)
                   and int(p.get("yield", 0) or 0) > 0
                   for p in (f.get("plots") or [])):
                candidates.append(uid)
        if not candidates:
            return "没有可偷菜的用户（暂无他人有成熟作物）。"
        targets = random.sample(candidates,
                                min(int(core.param("AUTO_STEAL_TARGETS", AUTO_STEAL_TARGETS)),
                                    len(candidates)))

        results = []
        total_gain = 0
        blocked = False
        for tkey in targets:
            r = api._do_steal(data, key, name, tkey, now_ts, crops)
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
            core.save()
            lines = [f"🥬 {name} 自动偷菜被宠物拦截！",
                     "🐾 今天无法再次使用自动偷菜（明天重置）。"]
            for r in results:
                lines.append(f"· {r['tname']}：被宠物抓到，偷菜失败")
            return "\n".join(lines)

        # 有收益 = 成功，消耗 1 次
        if total_gain > 0:
            u["auto_steal_date"] = today
            u["auto_steal_used"] = used + 1
            core.save()
            lines = [f"🥬 {name} 自动偷菜成功！偷了 {len(results)} 位用户，共获得 {total_gain} 金币。"]
            for r in results:
                if r["guard"] == "stop":
                    lines.append(f"· {r['tname']}：+{r['gain']} 金币（对方宠物拦下一半）")
                else:
                    lines.append(f"· {r['tname']}：+{r['gain']} 金币")
            lines.append(f"今日剩余自动偷菜次数：{limit - used - 1} 次")
            return "\n".join(lines)

        # 无收益 = 失败，不消耗次数
        core.save()
        lines = [f"🥬 {name} 自动偷菜没有偷到任何收益（本次不消耗次数）。"]
        for r in results:
            lines.append(f"· {r['tname']}：0 金币（未偷到作物）")
        lines.append(f"今日剩余自动偷菜次数：{limit - used} 次")
        return "\n".join(lines)

    core.expose("farm_steal", api)
    core.add_help("农场偷菜", [
        ("偷菜 @对方", "偷走对方成熟作物的一部分（5%~20%），折算为金币"),
        ("自动偷菜", f"随机偷最多 {AUTO_STEAL_TARGETS} 位用户，每日 {AUTO_STEAL_DAILY_LIMIT} 次"),
    ])
