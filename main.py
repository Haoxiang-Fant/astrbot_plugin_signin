# -*- coding: utf-8 -*-
# 入口模块：仅负责 签到 与 指令路由调度；各功能实现已拆分为 modules/ 包（Mixin）。
import asyncio
import sys as _sys

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from .modules.base import *  # noqa: F401,F403  常量与共享工具
from .modules.base import (
    CoreMixin,
    _load_fonts, _text_measurer, _ensure_pillow, _make_wrapper, _save_temp_image,
    _parse_kv_sections, _load_benchmark_items, _load_activity_modules,
    _build_text_image_chain, _resolve_file_value, _chain_to_onebot_segments,
    _send_with_mid, _parse_item_qty, _register_runtime_module, _sync_runtime_global,
)
from .modules.farm import FarmMixin
from .modules.pet import PetMixin
from .modules.bank import BankMixin
from .modules.redpacket import RedpacketMixin
from .modules.activities import ActivityMixin
from .modules.loans import LoanMixin
from .modules.roulette import RouletteMixin
from .modules.rank import RankMixin
from .modules.lan import LanMixin
from .modules.webui import WebUIMixin

# main.py 自身也参与运行时参数同步（签到等逻辑直接读取模块常量）
_register_runtime_module(_sys.modules[__name__])


@register("astrbot_plugin_signin", "sishijiu", "群签到 + 左轮手枪 + 宠物养成 + 金币银行 + 农场", "2.0.2")
class SignInPlugin(Star, FarmMixin, PetMixin, BankMixin, RedpacketMixin, ActivityMixin, LoanMixin, RouletteMixin, RankMixin, LanMixin, WebUIMixin, CoreMixin):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        def _get(key, default, cast=int):
            v = self.config.get(key, default)
            try:
                return cast(v)
            except (TypeError, ValueError):
                return default

        # 从 WebUI 配置页（_conf_schema.json）读取设置，未配置时用默认值
        # 格式：(属性名, 配置键, 默认值, 类型转换函数)
        _INT = int
        _FLOAT = float
        _config_map = [
            ("min_coins", "min_coins", MIN_COINS, _INT),
            ("max_coins", "max_coins", MAX_COINS, _INT),
            ("pet_unlock_cost", "pet_unlock_cost", PET_UNLOCK_COST, _INT),
            ("pet_signin_exp_min", "pet_signin_exp_min", PET_SIGNIN_EXP_MIN, _FLOAT),
            ("pet_signin_exp_max", "pet_signin_exp_max", PET_SIGNIN_EXP_MAX, _FLOAT),
            ("pill_drop_chance", "pill_drop_chance", PILL_DROP_CHANCE, _FLOAT),
            ("pill_drop_min", "pill_drop_min", PILL_DROP_MIN, _INT),
            ("pill_drop_max", "pill_drop_max", PILL_DROP_MAX, _INT),
            ("pill_daily_limit", "pill_daily_limit", PILL_DAILY_LIMIT, _INT),
            ("exp_ball_daily_limit", "exp_ball_daily_limit", EXP_BALL_DAILY_LIMIT, _INT),
            ("pill_attr_count", "pill_attr_count", PILL_ATTR_COUNT, _INT),
            ("pill_boost_min", "pill_boost_min", PILL_BOOST_MIN, _FLOAT),
            ("pill_boost_max", "pill_boost_max", PILL_BOOST_MAX, _FLOAT),
            ("exp_ball_min_pct", "exp_ball_min_pct", EXP_BALL_MIN_PCT, _FLOAT),
            ("exp_ball_max_pct", "exp_ball_max_pct", EXP_BALL_MAX_PCT, _FLOAT),
            ("money_event_chance", "money_event_chance", MONEY_EVENT_CHANCE, _FLOAT),
            ("money_event_gain", "money_event_gain", MONEY_EVENT_GAIN, _INT),
            ("money_event_max_per_day", "money_event_max_per_day", MONEY_EVENT_MAX_PER_DAY, _INT),
            ("min_fav", "min_fav", MIN_FAV, _FLOAT),
            ("max_fav", "max_fav", MAX_FAV, _FLOAT),
            ("level_step", "level_step", LEVEL_STEP, _FLOAT),
            # 2.0.0：签到额外奖励池概率（互斥奖池，总和自动归一）与宠物结算档位范围
            ("signin_no_reward_chance", "signin_no_reward_chance", SIGNIN_NO_REWARD_CHANCE, _FLOAT),
            ("signin_pill_chance", "signin_pill_chance", SIGNIN_PILL_CHANCE, _FLOAT),
            ("signin_ball_chance", "signin_ball_chance", SIGNIN_BALL_CHANCE, _FLOAT),
            ("pet_settle_ranges", "pet_settle_ranges", "", str),
            ("pet_settle_tiers", "pet_settle_tiers", "", str),
            ("loan_special_rate", "loan_special_rate", LOAN_SPECIAL_RATE, _FLOAT),
            ("loan_special_days", "loan_special_days", LOAN_SPECIAL_DAYS, _INT),
            ("rain_amount", "rain_amount", RAIN_AMOUNT, _INT),
            ("rain_count", "rain_count", RAIN_COUNT, _INT),
            ("rain_hours", "rain_hours", RAIN_HOURS, _INT),
        ]
        for attr, key, default, cast in _config_map:
            setattr(self, attr, _get(key, default, cast))
        _rt = str(self.config.get("rain_times", RAIN_TIMES)).replace("，", ",")
        _rain_t = []
        for _x in _rt.split(","):
            _x = _x.strip()
            try:
                _v = int(float(_x))  # 兼容 "8" / "8.0" / 8.0 等
            except (TypeError, ValueError):
                continue
            if 0 <= _v < 24:
                _rain_t.append(_v)
        self.rain_times = sorted(set(_rain_t)) or [8, 12, 16, 20]

        self._lock = asyncio.Lock()       # 保护数据文件 + 游戏内存状态
        self._games = {}                  # group_id -> RouletteGame

        # 调试模式：口令解锁 WebUI 按钮（不持久化，重启消失）；开启后无限资源且不写盘
        self._debug = False
        self._debug_unlocked = False
        self._debug_data = None  # 调试模式内存数据缓存

        # 活动中心：动态加载 activities/ 目录下的活动模块
        self._activities, _ = _load_activity_modules()
        for _act in self._activities:
            _attach = getattr(_act, "attach", None)
            if callable(_attach):
                try:
                    _attach(self)
                except Exception as e:
                    logger.warning(f"[插件] 活动 {_act.id} attach 失败: {e}")
        # 应用 WebUI 保存过的活动参数覆盖（时间/要求/自定义参数）
        self._load_activity_configs()

        # 一次性迁移旧数据：按群（gid:uid）→ 跨群（uid）
        self._migrate_legacy_data()

        # 1.7.7：商店/打工/玩耍数值从旧版 txt 迁移为 game_items.json（只迁一次）
        self._migrate_items_to_json()

        # 应用 WebUI 保存过的运行参数（覆盖默认常量，无需重启）
        self._load_runtime_params()

        # 注册 WebUI Pages 的后端 API（数据驱动批量注册）
        _web_apis = [
            ("backend/config", "GET", self.web_get_backend_config, "读取打工/玩耍数值（game_items.json）"),
            ("backend/config", "POST", self.web_save_backend_config, "保存打工/玩耍数值（game_items.json）"),
            ("petshop", "GET", self.web_get_petshop, "读取宠物商店商品（game_items.json）"),
            ("petshop", "POST", self.web_save_petshop, "保存宠物商店商品（game_items.json）"),
            ("feature/status", "GET", self.web_get_feature_status, "读取功能开关"),
            ("feature/status", "POST", self.web_save_feature_status, "保存功能开关"),
            ("data/export", "GET", self.web_export_data, "导出全部数据（存档+自定义配置）"),
            ("data/import", "POST", self.web_import_data, "导入全部数据（存档+自定义配置）"),
            ("farm/crops", "GET", self.web_get_crops, "读取作物配置（game_items.json）"),
            ("farm/crops", "POST", self.web_save_crops, "保存作物配置（game_items.json）"),
            ("farm/ferts", "GET", self.web_get_ferts, "读取肥料配置（game_items.json）"),
            ("farm/ferts", "POST", self.web_save_ferts, "保存肥料配置（game_items.json）"),
            ("items/apply_benchmark", "POST", self.web_apply_benchmark_items, "农场与宠物道具恢复为 Benchmark 默认数据"),
            ("loan/packages", "GET", self.web_get_loan_pkgs, "读取贷款套餐（game_items.json）"),
            ("loan/packages", "POST", self.web_save_loan_pkgs, "保存贷款套餐（game_items.json）"),
            ("activities", "GET", self.web_get_activities, "读取活动模块启用状态"),
            ("activities", "POST", self.web_save_activities, "保存活动模块启用状态"),
            ("params", "GET", self.web_get_params, "读取运行参数"),
            ("params", "POST", self.web_save_params, "保存运行参数"),
            ("debug/status", "GET", self.web_debug_status, "调试模式状态"),
            ("debug/toggle", "POST", self.web_debug_toggle, "开关调试模式"),
            ("group/names/sync", "POST", self.web_sync_group_names, "同步全部群聊的成员昵称（排行榜默认昵称）"),
            ("alias/list", "GET", self.web_get_aliases, "读取同义口令"),
            ("alias/save", "POST", self.web_save_aliases, "保存同义口令"),
            # 局域网开放（1.7.9）：这些端点自身承担鉴权/记录职责，不套用访问门
            ("lan/status", "GET", self.web_lan_status, "局域网开放：读取状态"),
            ("lan/unlock", "POST", self.web_lan_unlock, "局域网开放：输入密码解锁"),
            ("lan/setup", "POST", self.web_lan_setup, "局域网开放：开关/设置密码（仅本地）"),
            ("lan/records", "GET", self.web_lan_records, "局域网开放：访问记录（仅本地）"),
            ("lan/blacklist", "GET", self.web_lan_blacklist_get, "局域网开放：黑名单（仅本地）"),
            ("lan/blacklist", "POST", self.web_lan_blacklist_set, "局域网开放：添加/移除黑名单（仅本地）"),
        ]
        for path, method, handler, desc in _web_apis:
            if not path.startswith("lan/"):
                handler = self._lan_gate_wrap(handler)
            context.register_web_api(f"/{PLUGIN_NAME}/{path}", handler, [method], desc)

    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        if not text:
            return
        head = text.split(maxsplit=1)[0]
        async with self._lock:
            # 贷款逾期懒处理（标记逾期 + 每日好感度降低 / 23 点自动卖仓库签到还款）
            data = self._load()
            key = event.get_sender_id()
            # 同义口令展开：别名 → 标准指令（一步展开，不递归；别名可被 WebUI 编辑）
            head = self._expand_alias(data, head)
            dirty = False
            if data.get("loans", {}).get(key):
                sync_c = self._loan_sync(data, key)
                daily_c = self._loan_daily_process(data, key)
                if sync_c or daily_c:
                    dirty = True
            # 本群成员注册：响应前标记（含本群昵称），确保首次查询排行榜时已能作为本群成员显示真名
            gid = event.get_group_id()
            if gid:
                self._mark_group_member(data, gid, str(key), event.get_sender_name())
                dirty = True
            # 排行榜：每次查询时通过平台 API 刷新在榜用户的本群昵称（仅已标记用户，不改 48h 时间戳）
            if gid and head in RANK_KINDS:
                _show = int(globals().get("RANK_DISPLAY", 20) or 20)
                _entries = self._rank_entries(RANK_KINDS[head], data)
                await self._refresh_rank_names(event, data, gid, [e[1] for e in _entries[:_show]])
                dirty = True
            if dirty:
                self._save(data)
            reply = self._route(head, event)
        if reply is None:
            return
        if isinstance(reply, tuple) and len(reply) == 2 and reply[0] == "image":
            chain = event.image_result(reply[1])
        elif isinstance(reply, tuple) and len(reply) == 3 and reply[0] == "image_text":
            # 「文本 + 图片」组合回复（如种植后附加土地状态图）；组件不可用时回退纯文本
            chain = _build_text_image_chain(reply[1], reply[2])
            if chain is None:
                logger.warning("[插件] 消息链组件不可用，种植附加图片回退为纯文本")
                chain = event.plain_result(reply[1])
        else:
            # 指定指令的文本响应自动转成图片（签到/左轮/银行）
            img = self._to_image_for_command(head, reply)
            chain = event.image_result(img[1]) if img is not None else event.plain_result(reply)
        # 发送并安排 RECALL_AFTER 秒后撤回；优先走适配器底层发送以可靠拿到 message_id
        try:
            mid = await _send_with_mid(event, chain)
            logger.info(f"[插件] 消息已发送，message_id={mid!r}")
            if mid:
                if self._should_recall(head):
                    asyncio.create_task(self._recall_later(event, mid))
                else:
                    logger.info(f"[插件] 指令「{head}」设置为不撤回，跳过撤回")
            else:
                logger.warning("[插件] 未能获取 message_id，无法安排撤回")
        except Exception as e:
            logger.error(f"[插件] 主动发送失败，改用响应管线: {e}")
            yield chain

    def _should_recall(self, head: str) -> bool:
        """判断该指令的回复是否需要撤回。

        全局总开关（RECALL_ENABLED）为总开关：关闭 → 所有消息都不撤回；
        开启 → 按各指令的单独开关决定（RECALL_EXEMPT 中配置了开关的指令按其开关，
        未单独配置的指令默认撤回）。"""
        if not bool(globals().get("RECALL_ENABLED", True)):
            return False  # 全局总开关关闭：所有消息不撤回
        key = RECALL_EXEMPT.get(head)
        if key is None:
            return True  # 未单独配置开关的指令默认撤回
        return bool(globals().get(key, False))

    async def _recall_later(self, event, mid):
        """RECALL_AFTER 秒后撤回消息：优先走适配器原生撤回接口（参考 astrbot_plugin_music）"""
        try:
            await asyncio.sleep(RECALL_AFTER)
        except asyncio.CancelledError:
            return
        try:
            # 1) aiocqhttp：event.bot.delete_msg（OneBot v11 标准撤回接口）
            if AiocqhttpMessageEvent is not None and isinstance(event, AiocqhttpMessageEvent):
                bot = getattr(event, "bot", None)
                delete_msg = getattr(bot, "delete_msg", None)
                if delete_msg is not None:
                    try:
                        await delete_msg(message_id=int(mid))
                        logger.info(f"[插件] 撤回成功（event.bot.delete_msg, mid={mid!r}）")
                        return
                    except Exception as e:
                        logger.error(f"[插件] event.bot.delete_msg 撤回失败: {e}")
            # 2) QQ 官方机器人：botpy 原生撤回（照搬音乐插件做法）
            try:
                from botpy.http import Route
                from botpy.message import (
                    C2CMessage,
                    DirectMessage,
                    GroupMessage,
                    Message as BotpyMessage,
                )
            except Exception:
                Route = GroupMessage = C2CMessage = DirectMessage = BotpyMessage = None
            if Route is not None:
                source = getattr(getattr(event, "message_obj", None), "raw_message", None)
                bot = getattr(event, "bot", None)
                try:
                    route_path = None
                    route_params = {}
                    if isinstance(source, GroupMessage):
                        route_path = "/v2/groups/{group_openid}/messages/{message_id}"
                        route_params["group_openid"] = source.group_openid
                    elif isinstance(source, C2CMessage):
                        route_path = "/v2/users/{openid}/messages/{message_id}"
                        route_params["openid"] = source.author.user_openid
                    elif isinstance(source, DirectMessage):
                        route_path = "/dms/{guild_id}/messages/{message_id}"
                        route_params["guild_id"] = source.guild_id
                    elif isinstance(source, BotpyMessage):
                        await bot.api.recall_message(
                            channel_id=source.channel_id, message_id=str(mid)
                        )
                        logger.info("[插件] 撤回成功（botpy api.recall_message）")
                        return
                    if route_path:
                        await bot.api._http.request(
                            Route("DELETE", route_path, message_id=str(mid), **route_params)
                        )
                        logger.info("[插件] 撤回成功（botpy DELETE 路由）")
                        return
                except Exception as e:
                    logger.error(f"[插件] QQ官方撤回失败: {e}")
            # 3) 通用兜底：platform / event 的撤回类方法
            platform = getattr(event, "platform", None)
            if platform is not None and hasattr(platform, "recall_message"):
                await platform.recall_message(mid)
                logger.info("[插件] 撤回成功（platform.recall_message）")
                return
            if hasattr(event, "recall_message"):
                await event.recall_message(mid)
                logger.info("[插件] 撤回成功（event.recall_message）")
                return
            if platform is not None:
                for name in ("delete_message", "delete_msg", "recall_msg"):
                    fn = getattr(platform, name, None)
                    if fn:
                        await fn(mid)
                        logger.info(f"[插件] 撤回成功（{name}）")
                        return
            cands = []
            if platform is not None:
                cands = [a for a in dir(platform) if any(k in a.lower() for k in ("recall", "delete", "withdraw"))]
            logger.warning(f"[插件] 未找到可用撤回接口。event 属性: {[a for a in dir(event) if not a.startswith('_')][:40]}；platform 相关方法: {cands}")
        except Exception as e:
            logger.error(f"[插件] 撤回消息失败: {e}")

    def _to_image_for_command(self, head: str, reply):
        # 所有文本回复都转为图片；未映射标题的指令用指令名作标题
        # 调试模式下停用文字转图片（直接发文本，便于排查 bug）
        if getattr(self, "_debug", False):
            return None
        if not isinstance(reply, str) or not reply.strip():
            return None
        title = IMAGE_COMMANDS.get(head, head)
        return self._render_text_image(title, reply.splitlines())

    _ROUTE_NO_ARG = {
        "签到帮助": "_handle_help_signin",
        "游戏帮助": "_handle_help_game",
        "宠物帮助": "_handle_help_pet",
        "农场帮助": "_handle_help_farm",
        "左轮手枪帮助": "_handle_help_roulette",
        "查看后台配置": "_handle_view_config",
        "导出数据": "_handle_export_data",
    }
    # 带 event 参数的指令 → 方法名
    _ROUTE_WITH_EVENT = {
        "签到": "_handle_sign_in",
        "我的签到": "_handle_my_info",
        "装弹": "_handle_load",
        "加入": "_handle_join",
        "开始": "_handle_start",
        "开枪": "_handle_shoot",
        "我的战绩": "_handle_stats",
        "解锁宠物": "_handle_unlock_pet",
        "宠物": "_handle_pet_status",
        "更改宠物名字": "_handle_rename_pet",
        "治疗宠物": "_handle_weak_heal",
        "打工": "_handle_work",
        "玩耍": "_handle_play",
        "商店": "_handle_shop",
        "购买": "_handle_buy",
        "使用": "_handle_use_item",
        "背包": "_handle_bag",
        "保存后台配置": "_handle_save_backend_config",
        "导入数据": "_handle_import_data",
        "存款": "_handle_bank_deposit",
        "取款": "_handle_bank_withdraw",
        "银行统计": "_handle_bank_stats",
        "借款": "_handle_loan_borrow",
        "还款": "_handle_loan_repay",
        "我的贷款": "_handle_my_loans",
        "我的征信": "_handle_my_credit",
        "金币红包": "_handle_redpacket_send",
        "活动": "_handle_activity_center",
        "解锁农场": "_handle_farm_unlock",
        "购买土地": "_handle_farm_buy_land",
        "土地升级": "_handle_farm_upgrade",
        "种子商店": "_handle_farm_seed_shop",
        "农场商店": "_handle_farm_shop",
        "购买种子": "_handle_farm_buy_seed",
        "肥料商店": "_handle_farm_fert_shop",
        "购买肥料": "_handle_farm_buy_fert",
        "施肥": "_handle_farm_fertilize",
        "取消种植": "_handle_farm_cancel",
        "土地状态": "_handle_farm_plots",
        "我的农场": "_handle_farm_plots",
        "农场仓库": "_handle_farm_warehouse",
        "售卖种子": "_handle_farm_sell_seed",
        "售卖": "_handle_farm_sell",
        "偷菜": "_handle_steal",
        "自动偷菜": "_handle_auto_steal",
        "看家": "_handle_guard",
        "金币排行": "_handle_rank_coins",
        "宠物排行": "_handle_rank_pet",
        "农场排行": "_handle_rank_farm",
        "管理网址": "_handle_lan_url",
    }
    # 多个指令映射到同一处理方法
    _ROUTE_MULTI = {
        ("查询流水", "流水查询", "消费记录"): "_handle_ledger",
        ("开", "开红包", "抢红包"): "_handle_redpacket_open",
        ("种植", "种地"): "_handle_farm_plant",
        ("收割", "收获"): "_handle_farm_harvest",
    }

    def _route(self, head: str, event: AstrMessageEvent):
        # 功能开关拦截：对应模块关闭时返回提示（帮助类指令不受影响）
        mod = self.FEATURE_CMD_MAP.get(head)
        if mod is not None:
            data = self._load()
            if not self._feature_enabled(data, mod):
                label = next((m["label"] for m in self.FEATURE_MODULES if m["key"] == mod), mod)
                return f"⚠️ 「{label}」功能已被管理员关闭，暂时无法使用。"
        # 虚弱宠物守卫：宠物虚弱期间宠物功能被锁定（查看/改名/治疗不受影响），
        # 发送「治疗宠物」花 WEAK_HEAL_COST 金币即可重新激活
        if head in _PET_WEAK_LOCKED_HEADS:
            _d = self._load()
            _pet = _d.get("pets", {}).get(self._user_key(event))
            if _pet and _pet.get("weak"):
                return (f"😷 {event.get_sender_name()} 的宠物处于虚弱状态，宠物功能已锁定！\n"
                        f"发送「治疗宠物」（{WEAK_HEAL_COST} 金币）即可重新激活宠物。")
        # 无参数指令（帮助/配置/导出）
        no_arg = self._ROUTE_NO_ARG.get(head)
        if no_arg is not None:
            return getattr(self, no_arg)()
        # 带 event 参数的指令
        with_event = self._ROUTE_WITH_EVENT.get(head)
        if with_event is not None:
            return getattr(self, with_event)(event)
        # 多指令映射
        for keys, method_name in self._ROUTE_MULTI.items():
            if head in keys:
                return getattr(self, method_name)(event)
        # 调试模式口令（仅管理员在对话框输入）：解锁 WebUI 的调试按钮
        if head == DEBUG_PASSWORD:
            self._debug_unlocked = True
            return "🔓 调试模式已解锁：WebUI 后台将显示「开启调试模式」按钮（重启后消失）。"
        # 「加钱」：仅调试模式可用，获得 50000 金币
        if head == "加钱":
            if not getattr(self, "_debug", False):
                return "「加钱」仅在调试模式下可用。"
            key = self._user_key(event)
            data = self._load()
            self._add_coins(data, key, 50000, "调试加钱")
            self._save(data)
            return f"💰 调试模式：已获得 50000 金币（当前 {self._coins_of(data, key)}）。"
        # 活动模块自定义指令（已启用且时间有效时才处理）
        return self._activity_command(head, event)

    # ================= 数据存取 =================
    def _handle_sign_in(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)
        today = date.today().isoformat()

        data = self._load()
        user = data.get("users", {}).get(key)

        if user and user.get("last_date") == today:
            coins = user.get("coins", 0)
            fav = float(user.get("favorability", 0.0))
            lv = self._level_of(fav)
            reply = (f"{name}，你今天已经签到过啦～\n"
                     f"💰 当前金币：{coins}\n"
                     f"💗 当前好感度：{fav:.2f}（Lv.{lv}）")
            # 今天已签过：仍结算到期的存单
            settled, bank_paid = self._bank_settle(data, key)
            if settled > 0:
                self._save(data)
                reply += f"\n🏦 {settled} 笔存单已解锁，利息 +{bank_paid} 已自动入账，本金可发送「取款」取出。"
            return reply

        if user is None:
            user = self._ensure_user(data, key)

        lines = [f"✅ {name} 签到成功！"]
        lines += self._apply_signin_once(data, key, today)
        user["last_date"] = today

        pet = data.get("pets", {}).get(key)
        if pet:
            settle_lines = self._settle_display_lines(pet)
            if settle_lines:
                lines.append("")
                lines.extend(settle_lines)

        # ---- 银行：解锁到期的存单并发放利息 ----
        settled, bank_paid = self._bank_settle(data, key)
        if settled > 0:
            lines.append(f"🏦 {settled} 笔存单已解锁，利息 +{bank_paid} 已自动入账，本金可发送「取款」取出。")

        # ---- 活动钩子：已启用且时间有效的活动可在签到后追加内容（如双倍签到） ----
        self._sign_in_activity_hooks(event, data, key, lines)

        self._save(data)
        return "\n".join(lines)

    def _signin_reward_chances(self):
        """签到额外奖励池概率（WebUI 可编辑）：返回 (无奖品, 属性丸, 经验球)；
        三者总和超过 1 时按比例归一，保证互斥奖池总和恒为 1。"""
        no_r = float(getattr(self, "signin_no_reward_chance", None) if getattr(self, "signin_no_reward_chance", None) is not None
                     else globals().get("SIGNIN_NO_REWARD_CHANCE", 0.40))
        pill_r = float(getattr(self, "signin_pill_chance", None) if getattr(self, "signin_pill_chance", None) is not None
                       else globals().get("SIGNIN_PILL_CHANCE", 0.30))
        ball_r = float(getattr(self, "signin_ball_chance", None) if getattr(self, "signin_ball_chance", None) is not None
                       else globals().get("SIGNIN_BALL_CHANCE", 0.30))
        total = no_r + pill_r + ball_r
        if total <= 1e-9:
            return 0.40, 0.30, 0.30
        if total > 1.0:
            return no_r / total, pill_r / total, ball_r / total
        return no_r, pill_r, ball_r

    def _apply_signin_once(self, data: dict, key: str, today: str) -> list:
        """执行一次完整签到奖励（金币/好感度/宠物经验/属性丸），返回提示行列表。

        供每日签到和「双倍签到」等活动复用；不更新 last_date，
        不处理宠物结算显示 / 银行 / 活动钩子（避免递归）。
        2.0.0：金币范围 / 好感度范围 / 特殊道具获取率均可在 WebUI 设置中编辑。
        """
        user = self._ensure_user(data, key)
        coins_got = random.randint(self.min_coins, self.max_coins)
        fav_got = round(random.uniform(float(self.min_fav), float(self.max_fav)), 2)

        old_fav = float(user.get("favorability", 0.0))
        old_lv = self._level_of(old_fav)

        self._add_coins(data, key, coins_got, "每日签到")
        new_fav = round(old_fav + fav_got, 2)
        user["favorability"] = new_fav

        new_lv = self._level_of(new_fav)

        lines = [
            f"💰 获得金币：+{coins_got}（当前 {self._coins_of(data, key)}）",
            f"💗 好感度：+{fav_got:.2f}（当前 {new_fav:.2f}）",
        ]
        if new_lv > old_lv:
            lines.append(f"🎉 好感度突破 {int(new_lv * LEVEL_STEP)}，等级提升至 Lv.{new_lv}！")
        else:
            lines.append(f"🏅 当前好感等级：Lv.{new_lv}")

        no_ch, pill_ch, ball_ch = self._signin_reward_chances()

        pet = data.get("pets", {}).get(key)
        if pet:
            self._bring_pet_up_to_date(pet, today)

            exp_got = round(random.uniform(self.pet_signin_exp_min, self.pet_signin_exp_max), 2)
            pet["exp"] = round(float(pet.get("exp", 0.0)) + exp_got, 2)
            lvl_msg = self._apply_exp(pet)
            lines.append(f"🐾 宠物经验：+{exp_got:.1f}{lvl_msg}")

        # 额外奖励池（互斥）：概率在 WebUI 可编辑，总和恒为 1。
        # 2.0.1 归属：属性丸=宠物特殊道具（需解锁宠物）；农场经验球=农场特殊道具（需开通农场）。
        farm = data.get("farms", {}).get(key)
        r = random.random()
        if r < no_ch:
            pass  # 不送任何东西
        elif r < no_ch + pill_ch:
            if pet:
                pills = random.randint(self.pill_drop_min, self.pill_drop_max)
                inv = pet.setdefault("inventory", {})
                inv[PILL_NAME] = int(inv.get(PILL_NAME, 0)) + pills
                lines.append(f"💊 运气不错，获得 {pills} 个属性丸（发送「使用 属性丸」使用）！")
            else:
                cnt = random.randint(self.pill_drop_min, self.pill_drop_max)
                gain = cnt * ITEM_TO_COIN
                self._add_coins(data, key, gain, "签到奖励转金币")
                lines.append(f"🔄 抽到属性丸 ×{cnt}（未解锁宠物，自动转为 {gain} 金币）")
        else:
            if farm:
                balls = random.randint(self.pill_drop_min, self.pill_drop_max)
                tools = farm.setdefault("tools", {})
                tools[EXP_BALL_NAME] = int(tools.get(EXP_BALL_NAME, 0)) + balls
                lines.append(f"🏵️ 运气不错，获得 {balls} 个农场经验球（发送「使用 {EXP_BALL_NAME}」使用）！")
            else:
                cnt = random.randint(self.pill_drop_min, self.pill_drop_max)
                gain = cnt * ITEM_TO_COIN
                self._add_coins(data, key, gain, "签到奖励转金币")
                lines.append(f"🔄 抽到农场经验球 ×{cnt}（未开通农场，自动转为 {gain} 金币）")

        return lines

    def _handle_my_info(self, event: AstrMessageEvent) -> str:
        name = event.get_sender_name()
        key = self._user_key(event)

        data = self._load()
        user = data.get("users", {}).get(key)
        if not user:
            return f"{name} 还没有签到记录，发送「签到」开始吧～"
        coins = user.get("coins", 0)
        fav = float(user.get("favorability", 0.0))
        lv = self._level_of(fav)
        last = user.get("last_date", "无")
        lines = [
            f"{name} 的签到信息：",
            f"💰 金币：{coins}",
            f"💗 好感度：{fav:.2f}",
            f"🏅 好感等级：Lv.{lv}",
            f"📅 上次签到：{last}",
        ]
        return "\n".join(lines)

    # ================= 宠物：结算逻辑 =================
    def _handle_help_signin(self):
        sections = [
            ("签到", [
                ("签到", "每日签到，获得金币 / 好感度 / 宠物经验 / 属性丸 / 农场经验球"),
                ("我的签到", "查看金币与好感度"),
                ("签到帮助", "查看签到模块指令"),
                ("游戏帮助", "查看全部模块指令"),
            ]),
        ]
        return self._build_help("签到帮助", sections)

    def _handle_help_pet(self):
        sections = [
            ("宠物", [
                ("解锁宠物", f"花 {self.pet_unlock_cost} 金币领养宠物（每人限一只）"),
                ("宠物", "查看宠物总览（状态/属性/排行/最近变化/当前项目）"),
                ("更改宠物名字 <名字>", "给宠物起名"),
                ("打工 / 打工 <名称>", "打工赚金币与经验（完成后返回宠物总览图）"),
                ("玩耍 / 玩耍 <名称>", "玩耍赚经验与心情（完成后返回宠物总览图）"),
                ("商店", "查看宠物商店"),
                ("购买 <道具名> [数量]", "购买道具（不填数量 = 1 个）"),
                ("使用 <道具名> [数量]", "使用道具（不填数量 = 1 个，结果合入宠物总览图）"),
                ("背包", "查看背包"),
                ("治疗宠物", "治疗虚弱宠物（花 500 金币，所有数值恢复 40；仅虚弱状态可用）"),
            ]),
        ]
        return self._build_help("宠物帮助", sections)

    def _handle_help_roulette(self):
        sections = [
            ("左轮手枪", [
                ("装弹 <子弹数 1~6> <金币>", "发起一局游戏"),
                ("加入", "加入当前这局游戏"),
                ("开始", "发起人提前开始（≥2 人）"),
                ("开枪", "轮到你的回合时开枪"),
                ("我的战绩", "查看胜率 / 净收益 / 金币往来"),
            ]),
        ]
        return self._build_help("左轮手枪帮助", sections)

    def _handle_help_farm(self):
        sections = [
            ("农场", [
                ("解锁农场", "花 1500 金币解锁农场（赠 2 块地）"),
                ("购买土地", "花 800 金币开垦新土地（最多 24 块）"),
                ("土地升级 <编号>", "升级土地等级"),
                ("农场商店 [展开] [页]", "查看种子+化肥（展开=全部种子翻页）"),
                ("购买 <种子名>种子 [数量]", "购买种子（必须带「种子」后缀，也可用「购买种子」）；「购买 <化肥名> <小时数>」按小时购买化肥（最小 1 小时）"),
                ("种植 <作物> [起] [止/数量]", "种植（不填=种子够则种满空闲地，不够则全部种完）"),
                ("种地 / 种植（不填作物）", "快捷种地：先收割成熟 → 仓库随机种子自动种 → 缺则自动购买 → 种满"),
                ("施肥 <肥料> <土地编号> <分钟>", "施肥（2.0.0 起按分钟使用；缺肥料名→先用「化肥」再「有机化肥」；缺土地→全部可用地；缺时间→用到下一成长阶段所需时间；土地编号支持 1，6 / （20） / （1，7） / 裸数字≤最大地块数）"),
                ("施肥（不填肥料）", "快捷施肥：所有种植中作物使用化肥推进到下一成长阶段（不可用则有机化肥，缺失自动购买）"),
                ("收割 [编号] / 收获", "收割成熟作物并自动售出（不填=全部）"),
                ("取消种植 <编号>", "取消种植"),
                ("土地状态 / 农场仓库", "查看土地与仓库（农场指令回复均为纯图片：黄=种植 红=收割 蓝=开垦/施肥/升级）"),
                ("售卖 / 售卖种子", "出售作物 / 种子"),
            ]),
        ]
        return self._build_help("农场帮助", sections)

    def _handle_help_game(self):
        sections = [
            ("签到", [
                ("签到", "每日签到，获得金币 / 好感度 / 宠物经验 / 属性丸 / 农场经验球"),
                ("我的签到", "查看金币与好感度"),
                ("签到帮助", "查看签到模块指令"),
                ("游戏帮助", "查看全部模块指令（本菜单）"),
            ]),
            ("左轮手枪", [
                ("装弹 <子弹数 1~6> <金币>", "发起一局游戏"),
                ("加入", "加入当前这局游戏"),
                ("开始", "发起人提前开始（≥2 人）"),
                ("开枪", "轮到你的回合时开枪"),
                ("我的战绩", "查看胜率 / 净收益 / 金币往来"),
            ]),
            ("宠物", [
                ("解锁宠物", f"花 {self.pet_unlock_cost} 金币领养宠物"),
                ("宠物", "查看宠物属性 / 等级 / 经验"),
                ("更改宠物名字 <名字>", "给宠物起名"),
                ("打工 / 打工 <名称>", "打工赚金币与经验"),
                ("玩耍 / 玩耍 <名称>", "玩耍赚经验与心情"),
                ("商店", "查看宠物商店"),
                ("购买 / 使用 <道具名> [数量]", "购买 / 使用道具（不填数量 = 1 个）"),
                ("背包", "查看背包"),
                ("治疗宠物", "治疗虚弱宠物（花 500 金币，所有数值恢复 40；仅虚弱状态可用）"),
            ]),
            ("金币银行", [
                ("存款 <金额>", "存钱生息（不填=存最大可存金额）"),
                ("取款 <金额>", "取出本金（不填=全部）"),
                ("银行统计", "查看存款次数 / 存单 / 利息 / 额度"),
                ("借款 <套餐> <金额>", "贷款（0=特别 / 1=一般 / 2=短期 / 3~10=自定义）"),
                ("还款 <套餐> [金额]", "还款（不填套餐=还全部）"),
                ("我的贷款 / 我的征信", "查看贷款账单 / 征信"),
            ]),
            ("农场", [
                ("解锁农场 / 购买土地", "解锁农场 / 开垦土地"),
                ("土地升级 <编号>", "升级土地等级"),
                ("农场商店 [展开] [页]", "查看种子+化肥（展开=全部种子翻页）"),
                ("购买 <种子名>种子 [数量]", "购买种子（必须带「种子」后缀）；「购买 <化肥名> [数量]」购买化肥"),
                ("种地 / 种植 / 施肥 / 收割 / 收获", "快捷种地（自动播种）/ 快捷施肥（自动购买）/ 收割并自动售出"),
                ("土地状态 / 我的农场", "查看土地与仓库（农场属性）"),
                ("售卖 / 售卖种子", "出售作物 / 种子"),
                ("偷菜 <@对方>", "偷走对方成熟作物（10%~20%）"),
                ("自动偷菜", "每天 5 次：随机偷 4 位用户的成熟作物（无收益不扣次数，被宠物抓到当天锁定）"),
                ("看家 开 / 看家 关", "开启/关闭宠物看家防护"),
            ]),
            ("数据管理", [
                ("查看后台配置 / 保存后台配置", "管理后台配置"),
                ("导出数据 / 导入数据", "数据导入导出"),
            ]),
        ]
        return self._build_help("游戏帮助（全部指令）", sections)

    # ================= 数据管理（后台.txt 编辑 / 数据导入导出） =================
    def _handle_view_config(self) -> str:
        """查看后台配置（1.7.7：数值存于 game_items.json，这里渲染为旧版文本格式供查看）"""
        text = self._cfg_to_backend_text()
        if not text.strip():
            return "当前没有打工/玩耍配置（可在 WebUI 后台管理页编辑）。"
        return f"当前打工/玩耍配置（WebUI 表格编辑，存储于 game_items.json）：\n{text}"

    def _handle_save_backend_config(self, event: AstrMessageEvent) -> str:
        """保存后台配置（兼容旧版 txt 文本：解析后写入 game_items.json，商店部分不受影响）"""
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return "格式：保存后台配置 <内容>（先「查看后台配置」复制全文，改好后粘贴到指令后）"
        sections = _parse_kv_sections_text(parts[1].strip(), "后台", types=("打工", "玩耍"))
        if not sections:
            return "❌ 没有解析到 [打工:xxx] / [玩耍:xxx] 段落，格式未变化。"
        cfg = {"jobs": [], "plays": [], "shop": []}
        for sec in sections:
            cfg["jobs" if sec["type"] == "打工" else "plays"].append(sec)
        norm = self._normalize_config(cfg)
        flat_new = self._items_normalized_to_flat(norm)
        flat = self._read_items_json() or {"jobs": [], "plays": [], "shop": [], "crops": [], "ferts": [], "loans": []}
        flat["jobs"] = flat_new["jobs"]
        flat["plays"] = flat_new["plays"]
        ok, msg = self._write_items_json(flat)
        return f"✅ {msg}（打工 {len(flat['jobs'])} / 玩耍 {len(flat['plays'])} 条）" if ok else f"❌ {msg}"

    def _handle_export_data(self) -> str:
        """导出全部数据（存档 + 自定义配置）到 plugin_data 备份文件，小数据直接返回内容"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = os.path.join(os.path.dirname(DATA_FILE), f"signin_export_{ts}.json")
        files = {fn: self._read_file(path) for fn, path in self._exportable_files()}
        try:
            with open(bak, "w", encoding="utf-8") as f:
                json.dump({"files": files}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"❌ 导出失败: {e}"
        s = json.dumps({"files": files}, ensure_ascii=False)
        if len(s) <= 3500:
            return f"✅ 数据已导出到：{bak}\n内容：\n{s}"
        return f"✅ 数据已导出到：{bak}\n数据较大（{len(s)} 字符），请直接到上述路径取文件。"

    def _handle_import_data(self, event: AstrMessageEvent) -> str:
        """导入全部数据（兼容新版 files 打包与旧版仅 data.json 的 content）"""
        parts = event.message_str.split(maxsplit=1)
        if len(parts) >= 2 and parts[1].strip():
            raw = parts[1].strip()
        else:
            imp = os.path.join(os.path.dirname(DATA_FILE), "data_import.json")
            if not os.path.exists(imp):
                return "请把要导入的数据保存为插件目录下的 data_import.json，或直接发送「导入数据 <JSON内容>」。"
            try:
                with open(imp, "r", encoding="utf-8") as f:
                    raw = f.read()
            except Exception as e:
                return f"❌ 读取 data_import.json 失败: {e}"
        try:
            parsed = json.loads(raw)
        except Exception as e:
            return f"❌ JSON 格式错误: {e}"
        files = parsed.get("files") if isinstance(parsed, dict) else None
        if isinstance(files, dict):
            ok, result = self._import_files(files)
            if not ok:
                return f"❌ {result}"
            return f"✅ 导入成功（{len(result)} 个文件已还原）！"
        ok, msg = self._write_data_text(raw)
        return "✅ 导入成功！" if ok else f"❌ {msg}"

    # ================= 金币银行 =================
