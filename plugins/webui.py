# -*- coding: utf-8 -*-
"""WebUI 主插件（3.0.0 万物皆插件）。从 2.3.0 的 modules/webui.py + modules/lan.py
（lan 局域网开放 Mixin）与 main.py 的 Web API 注册段移植。

职责（NAME="webui"，经 register(core) 挂载）：
  1. 局域网访问门：把全部非 lan/* 端点包上 lan_gate_wrap（会话 Cookie 门：未开启/本地回环/
     已解锁才放行，黑名单拒绝并记录；lan/* 端点自身承担鉴权与记录职责，保持裸处理器）；
  2. 注册 Web API（/astrbot_plugin_signin3/<path>）：
     params GET/POST                 运行参数读取/保存（RUNTIME_PARAMS 协议，敏感参数不回显）
     alias/list GET, alias/save POST 同义口令读取/保存（标准指令表取 core.all_heads()）
     debug/status GET, debug/toggle POST  调试模式状态/开关（core._debug_unlocked / core.debug）
     feature/status GET/POST         功能开关（FEATURE_MODULES，存 data["features"]）
     activities GET/POST             活动启用状态与参数覆盖（经 core.service("thirdparty")，None 安全）
     group/names/sync POST           OneBot 平台群成员昵称同步（排行榜默认昵称）
     lan/status GET, lan/unlock POST, lan/setup POST, lan/records GET,
     lan/blacklist GET/POST          局域网开放管理（仅本地改密码/记录/黑名单）
  3. 「管理网址」聊天指令（admin=True）：返回局域网访问地址（仅私聊）；
  4. 挂接辅助模块：webui_conf（后台配置 CRUD 集群，另一文件，接受 lan_gate 包装器）
     与 webui_records（运行记录 7 端点，保持裸处理器不套门）。

与 2.3.0 的差异（JSON 回复形状保持一致，pages/admin 消费方不变）：
  - self._load()/_save(d) → core.data / core.save()；self._lock → core.lock；
    self._sync_runtime_global(k,v) → core.set_param(k,v)（同时写入 data["params"] 并同步模块全局）；
    self._is_admin → core.command(admin=True)；self._chat_sid/_touch_umos/_perm_of 由 core 承载。
  - 运行参数当前值经 core.param(key, default) 读取（params 覆盖 → core 常量 → 配置 → 缺省）。
  - 功能开关存储键由 2.3.0 的 data["feature_switches"] 改为 3.0 约定的 data["features"]
    （core.feature_enabled 同款），回复形状不变。
  - 保存前自动备份 _history_backup：3.0 核心暂未提供该能力，改为探测 core.history_backup
    （存在且可调用才调用；缺省无操作）。
  - 调试开关直接读写 core.debug 属性（2.3.0 的 _debug_data 内存缓存由 3.0 core 数据层承担，
    本插件不再管理缓存）。
  - 活动实例列表经 core.service("thirdparty")（未挂载时 activities 端点返回空列表）。
  - webui_records 的 7 个 records/* 端点保持裸注册（旧版 main.py 注册处对它们同样未包门）。
"""
import asyncio
import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
from datetime import datetime

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from ..core import FEATURE_MODULES, LAN_DATA_KEY, PLUGIN_NAME

NAME = "webui"

_CORE = None  # register(core) 时注入（处理器经 _core() 访问 core）


def _core():
    return _CORE


# 2.2.0：敏感运行参数 —— 不回传当前值；保存时留空 = 保持不变
_SENSITIVE_PARAMS = ("DEBUG_PASSWORD",)

# ================= 局域网管理页面开放（1.7.9，自 2.3.0 modules/base.py 51-58 原样复制） =================
LAN_COOKIE = "astrbot_signin_lan"   # 局域网访客会话 Cookie 名
LAN_SESSION_HOURS = 12              # 输入正确密码后的免密会话时长（小时）
LAN_MAX_RECORDS = 500               # 访问记录上限（超出丢弃最旧）
# 本地访问（loopback）免密；修改密码/开关/查看记录/管理黑名单仅限本地服务器
_LAN_SALT_LEN = 16


# ================= 局域网工具（自 2.3.0 modules/base.py 51-146 原样复制，去掉前导下划线） =================
def lan_hash_password(password, salt=None):
    """PBKDF2-SHA256 哈希密码。返回 (salt_hex, hash_hex)。不存储明文。"""
    if salt is None:
        salt = secrets.token_hex(_LAN_SALT_LEN)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), 120_000)
    return salt, digest.hex()


def lan_verify_password(stored, password):
    """校验密码：stored 形如 'salt$hash'，否则直接失败。"""
    if not isinstance(stored, str) or not isinstance(password, str):
        return False
    if "$" not in stored:
        return False
    salt, want = stored.split("$", 1)
    try:
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  bytes.fromhex(salt), 120_000).hex()
    except Exception:
        return False
    return hmac.compare_digest(got, want)


def is_loopback(host):
    """判断 client_host 是否为本地回环地址（x 为空/解析失败按非本地处理视为 False）。"""
    if not host:
        return False
    host = host.strip()
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    try:
        addr = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped.is_loopback
    return False


def lan_ip_in_blacklist(ip, blacklist):
    """判断 IP 是否命中黑名单（支持精确 IP 或 CIDR）。"""
    if not ip or not blacklist:
        return False
    ip = ip.split("%")[0]
    for entry in blacklist:
        if not isinstance(entry, str) or not entry.strip():
            continue
        entry = entry.strip()
        if entry == ip:
            return True
        try:
            if "/" in entry and ipaddress.ip_address(ip) in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def enumerate_lan_ipv4():
    """枚举本机局域网 IPv4 地址（用于「管理网址」指令返回访问地址）。"""
    ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not is_loopback(ip) and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # 兜底：通过 UDP 连接探测拿本机出口地址（不会真正发包）
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                if ip and not is_loopback(ip):
                    ips.append(ip)
            finally:
                s.close()
        except Exception:
            pass
    return ips


# ================= 局域网访问门与会话（自 2.3.0 modules/lan.py LanMixin 移植） =================
def lan_conf(data: dict) -> dict:
    """读取局域网开放配置字典（缺失时补默认结构）。"""
    lan = data.get(LAN_DATA_KEY)
    if not isinstance(lan, dict):
        lan = {}
        data[LAN_DATA_KEY] = lan
    lan.setdefault("enabled", False)
    lan.setdefault("password_hash", None)
    lan.setdefault("records", [])
    lan.setdefault("blacklist", [])
    # 2.2.2：连续密码错误计数与锁定标记（仅本地主机可解除）
    lan.setdefault("fail_count", 0)
    lan.setdefault("locked", False)
    return lan


def lan_client(req=None):
    """取当前请求的客户端信息：IP、是否本地。req 缺省用 AstrBot 的 request 代理。"""
    r = req if req is not None else request
    client_host = None
    ua = ""
    try:
        client_host = r.client_host
    except Exception:
        pass
    try:
        hdrs = r.headers
        ua = hdrs.get("user-agent", "") if hdrs else ""
    except Exception:
        pass
    return {
        "ip": client_host or "unknown",
        "is_local": is_loopback(client_host),
        "ua": (ua or "")[:200],
    }


def lan_record(core, data: dict, *, ip, is_local, ok, ua=""):
    """写一条访问记录（本地访问也记录，便于管理员审计）。"""
    lan = lan_conf(data)
    recs = lan.setdefault("records", [])
    name = "本地" if is_local else lan_device_name(ua)
    recs.append({
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ip": ip,
        "name": name,
        "ok": bool(ok),
        "ua": (ua or "")[:200],
    })
    if len(recs) > LAN_MAX_RECORDS:
        del recs[: len(recs) - LAN_MAX_RECORDS]


def lan_device_name(ua: str) -> str:
    """从 User-Agent 简单推断设备名（移动端/桌面）。"""
    ua = (ua or "").lower()
    if "mobile" in ua or "android" in ua or "iphone" in ua:
        return "移动端"
    if "macintosh" in ua or "mac os" in ua:
        return "macOS"
    if "windows" in ua:
        return "Windows"
    if "linux" in ua:
        return "Linux"
    return "其它设备"


def lan_secret(core, data: dict) -> str:
    """会话签名密钥：首次访问局域网时生成并持久化（避免重启后所有访客失联）。"""
    lan = lan_conf(data)
    secret = lan.get("secret")
    if not isinstance(secret, str) or len(secret) < 32:
        secret = secrets.token_hex(32)
        lan["secret"] = secret
        try:
            core.save()
        except Exception:
            pass
    return secret


def lan_sign_token(secret: str, ip: str, exp_ts: int) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{ip}:{exp_ts}".encode("utf-8"),
                   hashlib.sha256).hexdigest()[:24]
    return f"{exp_ts}.{mac}"


def lan_verify_token(secret: str, ip: str, token: str, now_ts: int) -> bool:
    if not isinstance(token, str) or "." not in token:
        return False
    exp_s, mac = token.split(".", 1)
    try:
        exp_ts = int(exp_s)
    except ValueError:
        return False
    if now_ts > exp_ts:
        return False
    expect = hmac.new(secret.encode("utf-8"), f"{ip}:{exp_ts}".encode("utf-8"),
                      hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(mac, expect)


def lan_is_unlocked(core, data: dict, client: dict, now_ts: int) -> bool:
    """当前请求是否已通过局域网密码解锁（Cookie 会话有效）。"""
    lan = lan_conf(data)
    secret = lan.get("secret")
    if not isinstance(secret, str) or len(secret) < 32:
        return False
    try:
        token = request.cookies.get(LAN_COOKIE)
    except Exception:
        token = None
    return lan_verify_token(secret, client["ip"], token or "", now_ts)


def lan_gate(core, data: dict) -> bool:
    """局域网访问门：True=放行，False=拒绝（返回 403）。
    规则（先过 AstrBot 鉴权后，插件再加一层）：
    1) 功能关闭 → 放行；
    2) 本地访问 → 放行（免密）；
    3) 命中黑名单 → 拒绝并记录；
    4) 未解锁（无有效会话 Cookie） → 拒绝并记录；
    5) 其余放行。
    被拒绝的访问会写访问记录，供管理员审计；通过密码解锁的访问由 web_lan_unlock 记录。"""
    lan = lan_conf(data)
    client = lan_client()
    now_ts = int(datetime.now().timestamp())

    if not lan.get("enabled"):
        return True
    if client["is_local"]:
        # 2.2.2：本地主机免密访问，同时解除可能存在的登录锁定（只有本地能解除）
        if lan.get("locked") or lan.get("fail_count"):
            lan["locked"] = False
            lan["fail_count"] = 0
            core.save()
        return True
    # 2.2.2：锁定期间拒绝一切局域网访问（含已有会话）
    if lan.get("locked"):
        lan_record(core, data, ip=client["ip"], is_local=False, ok=False, ua=client["ua"])
        core.save()
        return False
    if lan_ip_in_blacklist(client["ip"], lan.get("blacklist") or []):
        lan_record(core, data, ip=client["ip"], is_local=False, ok=False, ua=client["ua"])
        core.save()
        return False
    if not lan_is_unlocked(core, data, client, now_ts):
        lan_record(core, data, ip=client["ip"], is_local=False, ok=False, ua=client["ua"])
        core.save()
        return False
    return True


def lan_gate_wrap(core, handler):
    """把非局域网端点的处理器包上访问门：未开启/本地/已解锁才放行，否则 403。
    注意：AstrBot 在调用插件 Web API 时会把请求绑定到 request 上下文，
    因此这里的 request 代理可用。处理器内部自带 core.lock，这里不再加锁。"""
    async def _gated(*args, **kwargs):
        if not lan_gate(core, core.data):
            return error_response("需要局域网访问密码或该设备已被禁止访问", status_code=403)
        return await handler(*args, **kwargs)
    return _gated


def handle_lan_url(core, event) -> str:
    """指令「管理网址」：返回局域网访问地址（仅管理员私聊机器人，且要求已开启局域网开放）。"""
    try:
        if not event.is_private_chat():
            return "请私聊机器人发送该指令获取局域网管理网址。"
    except Exception:
        pass
    data = core.data
    lan = lan_conf(data)
    if not lan.get("enabled"):
        return "局域网访问尚未开启，请先在 WebUI「设置 → 局域网开放」中开启并设置密码。"
    port = 6185
    try:
        conf = core.context.astrbot_config_mgr.get_conf(None)
        dconf = getattr(conf, "dashboard", None)
        if isinstance(dconf, dict) and dconf.get("port"):
            port = int(dconf.get("port"))
    except Exception:
        try:
            conf = getattr(core.context, "_config", None)
            dconf = getattr(conf, "dashboard", None)
            if isinstance(dconf, dict) and dconf.get("port"):
                port = int(dconf.get("port"))
        except Exception:
            pass
    ips = enumerate_lan_ipv4()
    if not ips:
        return "无法获取本机局域网 IP，请检查网络连接。"
    lines = ["🌐 局域网管理网址（与本机同一局域网内的设备可访问：）"]
    for ip in ips:
        lines.append(f"http://{ip}:{port}")
    lines.append("")
    lines.append("· 本地访问 http://127.0.0.1:{} 免密".format(port))
    lines.append("· 首次从局域网设备打开后需输入管理密码")
    return "\n".join(lines)


def _history_backup(core, reason):
    """保存前自动备份（2.3.0 self._history_backup 的 3.0 替身）：
    核心或数据管理插件提供了 core.history_backup 时才调用，缺省无操作。"""
    fn = getattr(core, "history_backup", None)
    if callable(fn):
        try:
            fn(reason)
        except Exception as e:
            logger.warning(f"[WebUI] 保存前自动备份失败: {e}")


# ================= 挂载入口 =================
def register(core):
    """注册 WebUI 主 API（非 lan/* 端点统一包 LAN 访问门）+「管理网址」指令，
    最后挂接 webui_conf（配置 CRUD 集群）与 webui_records（运行记录端点）。"""
    global _CORE
    _CORE = core

    # 环境守卫：无 context / 无注册 API（如裸导入自检）时跳过 Web 注册，仅保留聊天指令
    _web_ok = bool(core.context) and hasattr(core.context, "register_web_api")
    if not _web_ok:
        logger.warning("[WebUI] core.context 不可用，跳过 Web API 注册（仅挂载「管理网址」指令）")

    endpoints = [
        ("params", "GET", web_get_params, "读取运行参数"),
        ("params", "POST", web_save_params, "保存运行参数"),
        ("alias/list", "GET", web_get_aliases, "读取同义口令"),
        ("alias/save", "POST", web_save_aliases, "保存同义口令"),
        ("debug/status", "GET", web_debug_status, "调试模式状态"),
        ("debug/toggle", "POST", web_debug_toggle, "开关调试模式"),
        ("feature/status", "GET", web_get_feature_status, "读取功能开关"),
        ("feature/status", "POST", web_save_feature_status, "保存功能开关"),
        ("activities", "GET", web_get_activities, "读取活动模块启用状态"),
        ("activities", "POST", web_save_activities, "保存活动模块启用状态"),
        ("group/names/sync", "POST", web_sync_group_names, "同步全部群聊的成员昵称（排行榜默认昵称）"),
        # 局域网开放（1.7.9）：这些端点自身承担鉴权/记录职责，不套用访问门
        ("lan/status", "GET", web_lan_status, "局域网开放：读取状态"),
        ("lan/unlock", "POST", web_lan_unlock, "局域网开放：输入密码解锁"),
        ("lan/setup", "POST", web_lan_setup, "局域网开放：开关/设置密码（仅本地）"),
        ("lan/records", "GET", web_lan_records, "局域网开放：访问记录（仅本地）"),
        ("lan/blacklist", "GET", web_lan_blacklist_get, "局域网开放：黑名单（仅本地）"),
        ("lan/blacklist", "POST", web_lan_blacklist_set, "局域网开放：添加/移除黑名单（仅本地）"),
    ]
    for path, method, handler, desc in endpoints:
        # 旧版 main.py 同款规则：lan/* 保持裸处理器，其余全部包 LAN 访问门
        wrapped = handler if path.startswith("lan/") else lan_gate_wrap(core, handler)
        if _web_ok:
            core.context.register_web_api(f"/{PLUGIN_NAME}/{path}", wrapped, [method], desc)

    # 「管理网址」：仅管理员可用的聊天指令（2.3.0 main.py _ADMIN_ONLY_HEADS + _ROUTE 移植）
    @core.command("管理网址", admin=True)
    def _cmd_lan_url(event):
        return handle_lan_url(core, event)

    # ---- 挂接 1：后台配置 CRUD 集群（另一文件；接受可选 lan_gate 包装器并应用于其端点） ----
    if _web_ok:
        try:
            from .webui_conf import register as _reg_conf
            _reg_conf(core, lan_gate=lambda handler: lan_gate_wrap(core, handler))
        except Exception as e:
            logger.error(f"[WebUI] webui_conf 配置集群挂接失败: {e}")

        # ---- 挂接 2：运行记录 Web API（records/* 7 端点保持裸处理器，不套 LAN 门） ----
        try:
            from .webui_records import register as _reg_rec
            _reg_rec(core)
        except Exception as e:
            logger.error(f"[WebUI] webui_records 运行记录端点挂接失败: {e}")


# ============ WebUI 运行参数（协议：GET/POST /astrbot_plugin_signin3/params） ============
# 每项：key=模块常量名（保存后经 core.set_param 同步全部模块全局、立即生效），attr=2.3.0 实例属性（3.0 不再使用）
# type 支持 int / float / bool / string；min/max 为校验范围；group 为一级折叠分组，subgroup 为二级折叠分组
# （2.3.0 modules/base.py 571-924 原样复制）
RUNTIME_PARAMS = [
    # ---- 撤回设置 ----
    {"key": "RECALL_ENABLED", "label": "全局撤回开关", "type": "bool", "group": "撤回设置", "subgroup": "通用",
     "desc": "全局总开关：开启 = 启用撤回功能（各指令按下方开关决定是否撤回）；关闭 = 所有消息都不撤回", "default": True},
    {"key": "RECALL_AFTER", "label": "消息撤回秒数", "type": "int", "group": "撤回设置", "subgroup": "通用",
     "desc": "插件消息发送后多少秒撤回（0 = 不撤回）", "default": 15, "min": 0, "max": 600},
    {"key": "RECALL_ACTIVITY", "label": "「活动」指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「活动」指令的图片回复正常撤回，不勾选则不撤回", "default": False},
    {"key": "RECALL_SHOP", "label": "「商店」指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「商店」指令（宠物商店）的图片回复正常撤回，不勾选则不撤回", "default": False},
    {"key": "RECALL_SEED_SHOP", "label": "「种子商店」指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「种子商店」指令的图片回复正常撤回，不勾选则不撤回", "default": False},
    {"key": "RECALL_FERT_SHOP", "label": "「肥料商店」指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「肥料商店」指令的图片回复正常撤回，不勾选则不撤回", "default": False},
    {"key": "RECALL_FARM_SHOP", "label": "「农场商店」指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「农场商店」指令的图片回复正常撤回，不勾选则不撤回（默认不撤回）", "default": False},
    {"key": "RECALL_HELP", "label": "帮助指令撤回", "type": "bool", "group": "撤回设置", "subgroup": "单指令开关",
     "desc": "勾选后「签到帮助/宠物帮助/农场帮助/左轮手枪帮助/游戏帮助」的图片回复正常撤回，不勾选则不撤回", "default": False},
    # ---- 签到 ----
    {"key": "MIN_COINS", "label": "签到最少金币", "type": "int", "group": "签到", "subgroup": "金币",
     "desc": "每日签到随机获得金币的下限", "default": 30, "min": 1, "max": 10000, "attr": "min_coins"},
    {"key": "MAX_COINS", "label": "签到最多金币", "type": "int", "group": "签到", "subgroup": "金币",
     "desc": "每日签到随机获得金币的上限", "default": 300, "min": 1, "max": 100000, "attr": "max_coins"},
    {"key": "SIGNIN_NO_REWARD_CHANCE", "label": "奖池·无奖品概率", "type": "float", "group": "签到", "subgroup": "奖池概率",
     "desc": "签到额外奖励抽中「什么都不送」的概率（0.4 = 40%；三项总和超过 1 时自动按比例归一）", "default": 0.4, "min": 0, "max": 1, "attr": "signin_no_reward_chance"},
    {"key": "SIGNIN_PILL_CHANCE", "label": "奖池·属性丸概率", "type": "float", "group": "签到", "subgroup": "奖池概率",
     "desc": "签到额外奖励抽中「属性丸」的概率（0.3 = 30%；三项总和超过 1 时自动按比例归一）", "default": 0.3, "min": 0, "max": 1, "attr": "signin_pill_chance"},
    {"key": "SIGNIN_BALL_CHANCE", "label": "奖池·农场经验球概率", "type": "float", "group": "签到", "subgroup": "奖池概率",
     "desc": "签到额外奖励抽中「农场经验球」的概率（0.3 = 30%；三项总和超过 1 时自动按比例归一）", "default": 0.3, "min": 0, "max": 1, "attr": "signin_ball_chance"},
    # ---- 宠物 ----
    {"key": "PET_UNLOCK_COST", "label": "解锁宠物价格", "type": "int", "group": "宠物", "subgroup": "解锁",
     "desc": "领养宠物所需金币", "default": 1000, "min": 0, "max": 1000000, "attr": "pet_unlock_cost"},
    {"key": "PET_SIGNIN_EXP_MIN", "label": "签到宠物最小经验", "type": "float", "group": "宠物", "subgroup": "宠物经验",
     "desc": "每日签到宠物获得的最小经验", "default": 10.0, "min": 0, "max": 1000, "attr": "pet_signin_exp_min"},
    {"key": "PET_SIGNIN_EXP_MAX", "label": "签到宠物最大经验", "type": "float", "group": "宠物", "subgroup": "宠物经验",
     "desc": "每日签到宠物获得的最大经验", "default": 60.0, "min": 0, "max": 5000, "attr": "pet_signin_exp_max"},
    {"key": "PILL_DROP_CHANCE", "label": "属性丸掉落概率", "type": "float", "group": "宠物", "subgroup": "属性丸",
     "desc": "签到额外奖励中抽中属性丸的概率（30% 即 0.3）", "default": 0.3, "min": 0, "max": 1, "attr": "pill_drop_chance"},
    {"key": "PILL_DROP_MIN", "label": "属性丸最少掉落", "type": "int", "group": "宠物", "subgroup": "属性丸",
     "desc": "签到奖励中属性丸的最少数量", "default": 1, "min": 1, "max": 99, "attr": "pill_drop_min"},
    {"key": "PILL_DROP_MAX", "label": "属性丸最多掉落", "type": "int", "group": "宠物", "subgroup": "属性丸",
     "desc": "签到奖励中属性丸的最多数量", "default": 5, "min": 1, "max": 99, "attr": "pill_drop_max"},
    {"key": "PILL_ATTR_COUNT", "label": "属性丸提升属性数", "type": "int", "group": "宠物", "subgroup": "属性丸",
     "desc": "使用属性丸时随机提升的属性种类数量", "default": 2, "min": 1, "max": 5, "attr": "pill_attr_count"},
    {"key": "PILL_BOOST_MIN", "label": "属性丸提升下限", "type": "float", "group": "宠物", "subgroup": "属性丸",
     "desc": "属性丸单个属性提升的最小值", "default": 5.0, "min": 0, "max": 100, "attr": "pill_boost_min"},
    {"key": "PILL_BOOST_MAX", "label": "属性丸提升上限", "type": "float", "group": "宠物", "subgroup": "属性丸",
     "desc": "属性丸单个属性提升的最大值", "default": 20.0, "min": 0, "max": 500, "attr": "pill_boost_max"},
    {"key": "PILL_DAILY_LIMIT", "label": "属性丸每日使用上限", "type": "int", "group": "宠物", "subgroup": "属性丸",
     "desc": "属性丸每天最多可使用次数", "default": 3, "min": 1, "max": 50, "attr": "pill_daily_limit"},
    {"key": "MONEY_EVENT_CHANCE", "label": "玩耍捡钱概率", "type": "float", "group": "宠物", "subgroup": "玩耍",
     "desc": "玩耍触发「捡到钱了」的概率（0.01 = 1%）", "default": 0.01, "min": 0, "max": 1, "attr": "money_event_chance"},
    {"key": "MONEY_EVENT_GAIN", "label": "玩耍捡钱金额", "type": "int", "group": "宠物", "subgroup": "玩耍",
     "desc": "触发「捡到钱了」获得的金币", "default": 100, "min": 0, "max": 100000, "attr": "money_event_gain"},
    {"key": "MONEY_EVENT_MAX_PER_DAY", "label": "玩耍捡钱每日上限", "type": "int", "group": "宠物", "subgroup": "玩耍",
     "desc": "「捡到钱了」每个周期最多触发次数", "default": 2, "min": 1, "max": 100, "attr": "money_event_max_per_day"},
    {"key": "WORK_CARD_COLS", "label": "打工列表每行卡片数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「打工」列表图片一行展示的卡片数量", "default": 2, "min": 1, "max": 4},
    {"key": "PLAY_CARD_COLS", "label": "玩耍列表每行卡片数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「玩耍」列表图片一行展示的卡片数量", "default": 2, "min": 1, "max": 4},
    {"key": "WORK_PLAY_CARD_WIDTH", "label": "打工玩耍卡片宽度", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「打工/玩耍」列表每张内容卡片的宽度（默认 522 = 原 290 的 180%）", "default": 522, "min": 290, "max": 800},
    {"key": "WORK_SHOW_DOABLE", "label": "打工列表·可进行显示个数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「打工」（不带参数）策略一：可进行项目中等级要求最高的显示数量；「打工 全部」显示全部", "default": 6, "min": 1, "max": 50},
    {"key": "WORK_SHOW_LOCKED", "label": "打工列表·不可进行显示个数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「打工」（不带参数）策略一：不可进行项目中等级要求最低的显示数量", "default": 2, "min": 0, "max": 50},
    {"key": "PLAY_SHOW_DOABLE", "label": "玩耍列表·可进行显示个数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「玩耍」（不带参数）策略一：可进行项目中等级要求最高的显示数量；「玩耍 全部」显示全部", "default": 6, "min": 1, "max": 50},
    {"key": "PLAY_SHOW_LOCKED", "label": "玩耍列表·不可进行显示个数", "type": "int", "group": "宠物", "subgroup": "打工玩耍",
     "desc": "「玩耍」（不带参数）策略一：不可进行项目中等级要求最低的显示数量", "default": 2, "min": 0, "max": 50},
    {"key": "PET_ATTR_MAX_RANGES", "label": "属性上限·健康值范围", "type": "string", "group": "宠物", "subgroup": "属性",
     "desc": "不同健康值范围内 饱食/口渴/体力/心情 的属性上限；格式：健康值下限=饱食,口渴,体力,心情，竖线分隔、按健康值从高到低匹配（如 140=200,200,200,120|80=120,120,120,100|40=100,100,100,100|0=80,80,60,80）",
     "default": "140=200,200,200,120|80=120,120,120,100|40=100,100,100,100|0=80,80,60,80"},
    # ---- 商店（独立折叠分组） ----
    {"key": "SHOP_CARD_COLS", "label": "宠物商店每行卡片数", "type": "int", "group": "商店", "subgroup": "宠物商店",
     "desc": "宠物商店（商店指令）一行展示的卡片数量", "default": 3, "min": 2, "max": 6},
    {"key": "FARM_SHOP_COLS", "label": "农场商店每行卡片数", "type": "int", "group": "商店", "subgroup": "农场商店",
     "desc": "农场商店一行展示的卡片数量", "default": 4, "min": 2, "max": 6},
    {"key": "FARM_SHOP_SHOW_BUY", "label": "农场商店·可购种子显示个数", "type": "int", "group": "商店", "subgroup": "农场商店",
     "desc": "「农场商店」（不带参数）默认展示的可以购买（等级足够）的种子数量", "default": 9, "min": 1, "max": 99},
    {"key": "FARM_SHOP_SHOW_LOCKED", "label": "农场商店·不可购灰卡个数", "type": "int", "group": "商店", "subgroup": "农场商店",
     "desc": "「农场商店」（不带参数）默认展示的不能购买（等级不足）的种子灰卡数量；「农场商店 全部」显示全部商品", "default": 3, "min": 0, "max": 99},
    {"key": "SHOP_PRICE_PAD", "label": "商店价格底边距（像素）", "type": "int", "group": "商店", "subgroup": "通用",
     "desc": "商店卡片价格与卡片底部/分割线的距离 N", "default": 4, "min": 0, "max": 30},
    # ---- 商店（补充）2.0.3：缺货自动购买；2.0.4：价格刷新改为固定时段折扣 ----
    {"key": "AUTO_BUY_SHORT_ENABLED", "label": "缺货自动购买开关", "type": "bool", "group": "商店", "subgroup": "通用",
     "desc": "开启后：使用道具时仓库不足则自动购买足额道具并使用（默认关闭）", "default": False},
    {"key": "AUTO_BUY_SHORT_MULT", "label": "缺货自动购买价格倍率", "type": "float", "group": "商店", "subgroup": "通用",
     "desc": "缺货自动购买道具的价格倍率（1.0 = 原价）", "default": 1.0, "min": 0.1, "max": 10},
    {"key": "SHOP_PRICE_FLOAT_ENABLED", "label": "宠物商店价格浮动开关", "type": "bool", "group": "商店", "subgroup": "宠物商店",
     "desc": "开启后：宠物商店价格固定偶数点（0/2/4/…/22 整点）刷新、同窗口（2 小时）内稳定；特价时段（10/12/18/0 时窗口）随机选取 MIN~MAX 种商品按 LO~HI 倍率打折，其余时段全部回原价（默认关闭）", "default": False},
    {"key": "SHOP_PRICE_DISCOUNT_MIN", "label": "特价时段·最少折扣商品数", "type": "int", "group": "商店", "subgroup": "宠物商店",
     "desc": "特价时段（10/12/18/0 时窗口）随机选取的打折商品最少数量", "default": 2, "min": 0, "max": 20},
    {"key": "SHOP_PRICE_DISCOUNT_MAX", "label": "特价时段·最多折扣商品数", "type": "int", "group": "商店", "subgroup": "宠物商店",
     "desc": "特价时段随机选取的打折商品最多数量", "default": 5, "min": 0, "max": 20},
    {"key": "SHOP_PRICE_DISCOUNT_LO", "label": "折扣倍率下限（二折）", "type": "float", "group": "商店", "subgroup": "宠物商店",
     "desc": "折扣价格 = 原价 × [下限, 上限]（0.2 = 打二折）", "default": 0.2, "min": 0.05, "max": 1},
    {"key": "SHOP_PRICE_DISCOUNT_HI", "label": "折扣倍率上限（八折）", "type": "float", "group": "商店", "subgroup": "宠物商店",
     "desc": "折扣价格 = 原价 × [下限, 上限]（0.8 = 打八折）", "default": 0.8, "min": 0.05, "max": 1},
    {"key": "SHOP_PRICE_RECORD_MAX", "label": "价格变动·保留记录条数", "type": "int", "group": "商店", "subgroup": "宠物商店",
     "desc": "WebUI「运行记录 → 商店价格」保留的最近价格变动记录条数（每次刷新窗口记一条）", "default": 60, "min": 1, "max": 500},
    {"key": "SEED_DISCOUNT_ENABLED", "label": "种子每日折扣开关", "type": "bool", "group": "商店", "subgroup": "农场商店",
     "desc": "开启后：每天有概率让 1~3 款种子打八折（肥料不受影响；默认关闭）", "default": False},
    {"key": "SEED_DISCOUNT_CHANCE", "label": "种子折扣发生概率", "type": "float", "group": "商店", "subgroup": "农场商店",
     "desc": "每天触发种子折扣的概率（0.5 = 50%）", "default": 0.5, "min": 0, "max": 1},
    {"key": "SEED_DISCOUNT_MIN", "label": "种子折扣最少款数", "type": "int", "group": "商店", "subgroup": "农场商店",
     "desc": "每天被打折的种子最少数量", "default": 1, "min": 1, "max": 20},
    {"key": "SEED_DISCOUNT_MAX", "label": "种子折扣最多款数", "type": "int", "group": "商店", "subgroup": "农场商店",
     "desc": "每天被打折的种子最多数量", "default": 3, "min": 1, "max": 20},
    {"key": "SEED_DISCOUNT_PCT", "label": "种子折扣倍率", "type": "float", "group": "商店", "subgroup": "农场商店",
     "desc": "折扣后的价格倍率（0.8 = 打八折）", "default": 0.8, "min": 0.05, "max": 1},
    # ---- 宠物（补充）2.2.7 自动照顾重写 ----
    {"key": "AUTO_FEED_ENABLED", "label": "自动照顾总开关", "type": "bool", "group": "宠物", "subgroup": "自动照顾",
     "desc": "总开关（默认关闭）：开启后，用「自动照顾 开/关」启用了自动照顾的用户，五属性每次变化（打工/玩耍/使用道具/治疗后）与每日结算时自动检查，任一属性处于第 3/4 档即触发照顾（健康补到最大健康×目标百分比，饱食/口渴/心情/体力按档位补满）；开启自动照顾会同步开启自动打工", "default": False},
    {"key": "AUTO_BUY_PRICE_MULT", "label": "自动照顾·购买价格倍率", "type": "float", "group": "宠物", "subgroup": "自动照顾",
     "desc": "自动化购买道具的价格 = 用户手动购买价格 × 该倍率（1.1 = 高 10%）；优先使用用户持有的道具，仓库没有才购买", "default": 1.1, "min": 0.1, "max": 10},
    {"key": "AUTO_WORK_ENABLED", "label": "自动打工总开关", "type": "bool", "group": "宠物", "subgroup": "自动打工",
     "desc": "总开关（默认开启）：开启后，开启了自动照顾且基准金币 > 0 的用户自动打工（报酬最接近基准金币的项目；打工完成后自动进入下一轮；报酬优先偿还自动化贷款）", "default": True},
    # ---- 宠物（补充）2.2.7 自动照顾目标 ----
    {"key": "AUTO_FEED_TARGET_HEALTH_PCT", "label": "自动照顾·健康目标百分比", "type": "float", "group": "宠物", "subgroup": "自动照顾",
     "desc": "健康补到目标 = 最大健康值 × 该百分比（0.8 = 最大健康的 80%）；其余属性按档位补满", "default": 0.8, "min": 0.1, "max": 1.0},
    # ---- 通用（2.2.1：管理员鉴权） ----
    {"key": "ADMIN_UIDS", "label": "管理员 UID（逗号分隔）", "type": "string", "group": "通用", "subgroup": "鉴权",
     "desc": "可调用数据管理类指令（查看/保存后台配置、导出/导入数据、管理网址）的管理员 UID，多个用逗号分隔；留空时回退到 OneBot 群角色（群主/管理员）与 AstrBot 主人配置判定", "default": ""},
    # ---- 固定结算（2.1.0：固定刷新时间） ----
    {"key": "DAILY_SETTLE_HOUR", "label": "插件数据结算时间（时）", "type": "int", "group": "固定结算", "subgroup": "每日结算",
     "desc": "每天该整点固定开始结算插件数据：全部宠物每日结算 + 自动购买「每日结算」触发（默认 0 = 零点）", "default": 0, "min": 0, "max": 23},
    {"key": "BANK_SETTLE_HOUR", "label": "银行存款结算时间（时）", "type": "int", "group": "固定结算", "subgroup": "每日结算",
     "desc": "每天该整点结算银行存款数据：解锁到期存单并发放利息（默认 4 = 四点）", "default": 4, "min": 0, "max": 23},
    {"key": "DAILY_SETTLE_LOOP_INTERVAL", "label": "固定结算·巡检间隔（秒）", "type": "int", "group": "固定结算", "subgroup": "每日结算",
     "desc": "固定结算循环每隔多少秒巡检一次当前时间（到达设定整点后触发结算；默认 60）", "default": 60, "min": 5, "max": 3600},
    # ---- 背包（独立折叠分组） ----
    {"key": "BAG_CARD_COLS", "label": "背包每行卡片数", "type": "int", "group": "背包", "subgroup": "卡片显示",
     "desc": "「背包」图片一行展示的卡片数量", "default": 5, "min": 3, "max": 7},
    # ---- 金币红包 ----
    {"key": "REDPACKET_DAILY_LIMIT", "label": "红包每日发送上限", "type": "int", "group": "金币红包", "subgroup": "发送",
     "desc": "每位玩家每天最多发送金币红包的次数", "default": 4, "min": 1, "max": 50},
    {"key": "REDPACKET_TTL", "label": "红包有效期（秒）", "type": "int", "group": "金币红包", "subgroup": "有效期",
     "desc": "红包发出后多少秒内有效，超时剩余自动退回", "default": 600, "min": 60, "max": 86400},
    {"key": "WEAK_HEAL_COST", "label": "治疗虚弱宠物费用", "type": "int", "group": "宠物", "subgroup": "虚弱治疗",
     "desc": "发送「治疗宠物」使虚弱宠物恢复所需金币", "default": 500, "min": 0, "max": 100000},
    {"key": "AUTO_STEAL_TARGETS", "label": "自动偷菜目标数", "type": "int", "group": "偷菜", "subgroup": "自动偷菜",
     "desc": "「自动偷菜」每次随机抽取的目标用户数量", "default": 4, "min": 1, "max": 10},
    # ---- 农场 ----
    {"key": "FARM_UNLOCK_COST", "label": "解锁农场价格", "type": "int", "group": "农场", "subgroup": "价格",
     "desc": "解锁农场所需金币（赠送 2 块地）", "default": 1500, "min": 0, "max": 1000000},
    {"key": "FARM_PLOT_COST", "label": "购买土地价格", "type": "int", "group": "农场", "subgroup": "价格",
     "desc": "开垦一块新土地所需金币", "default": 800, "min": 0, "max": 1000000},
    {"key": "FARM_MAX_PLOTS", "label": "最大土地数量", "type": "int", "group": "农场", "subgroup": "土地",
     "desc": "农场最多可拥有的土地块数", "default": 24, "min": 2, "max": 99},
    {"key": "FARM_PLOT_CARD_WIDTH", "label": "农场卡片宽度", "type": "int", "group": "农场", "subgroup": "卡片显示",
     "desc": "「土地状态/我的农场」每张土地卡片宽度（默认 270 = 原 360 的 75%）", "default": 270, "min": 160, "max": 420},
    {"key": "FARM_PLOT_COLS", "label": "农场卡片列数", "type": "int", "group": "农场", "subgroup": "卡片显示",
     "desc": "「土地状态/我的农场」一行显示的卡片数量", "default": 4, "min": 2, "max": 8},
    {"key": "EXP_BALL_DAILY_LIMIT", "label": "经验球每日使用上限", "type": "int", "group": "农场", "subgroup": "农场经验球",
     "desc": "农场经验球每天最多可使用次数", "default": 3, "min": 1, "max": 50, "attr": "exp_ball_daily_limit"},
    {"key": "EXP_BALL_MIN_PCT", "label": "经验球增加百分比下限", "type": "float", "group": "农场", "subgroup": "农场经验球",
     "desc": "使用农场经验球获得升级所需总经验的最小百分比（0.05 = 5%）", "default": 0.05, "min": 0, "max": 1, "attr": "exp_ball_min_pct"},
    {"key": "EXP_BALL_MAX_PCT", "label": "经验球增加百分比上限", "type": "float", "group": "农场", "subgroup": "农场经验球",
     "desc": "使用农场经验球获得升级所需总经验的最大百分比（0.20 = 20%）", "default": 0.20, "min": 0, "max": 2, "attr": "exp_ball_max_pct"},
    # ---- 偷菜（2.0.1 重做） ----
    {"key": "STEAL_ENABLED", "label": "偷菜功能开关", "type": "bool", "group": "偷菜", "subgroup": "通用",
     "desc": "全局总开关：关闭后「偷菜」指令提示功能未开启", "default": True},
    {"key": "STEAL_LOSS_MIN", "label": "偷菜收益比例下限", "type": "float", "group": "偷菜", "subgroup": "规则",
     "desc": "偷菜成功获得地块当前产量收益的比例下限（0.05 = 5%），农场主损失对应收益", "default": 0.05, "min": 0.01, "max": 1},
    {"key": "STEAL_LOSS_MAX", "label": "偷菜收益比例上限", "type": "float", "group": "偷菜", "subgroup": "规则",
     "desc": "偷菜成功获得地块当前产量收益的比例上限（0.20 = 20%）", "default": 0.20, "min": 0.01, "max": 1},
    {"key": "STEAL_PROTECT_RATIO", "label": "保护地块产量阈值", "type": "float", "group": "偷菜", "subgroup": "规则",
     "desc": "地块当前产量低于原有产量该比例（0.5 = 50%）即进入保护状态，剩余作物不可再被偷（提示「被偷完了」）", "default": 0.5, "min": 0.1, "max": 1},
    {"key": "STEAL_LEVEL_GAP", "label": "农场等级差限制", "type": "int", "group": "偷菜", "subgroup": "规则",
     "desc": "偷菜者无法向农场等级高于自己该级数的农场主发起偷菜", "default": 10, "min": 1, "max": 100},
    # ---- 宠物加护 ----
    {"key": "STEAL_GUARD_CATCH", "label": "加护·抓到你了概率", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "宠物激活且空闲、状态档位1-2时，偷菜触发「抓到你了」（偷菜失败+气味记忆24h+主人宠物体力-2~5）的概率（0.1 = 10%）", "default": 0.10, "min": 0, "max": 1},
    {"key": "STEAL_GUARD_STOP", "label": "加护·给我站住概率", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「给我站住」（农场主损失减半+体力-3~6+等级压制失效12h+偷菜者缴原金额110%罚款）的概率（0.2 = 20%）", "default": 0.20, "min": 0, "max": 1},
    {"key": "STEAL_GUARD_CATCH_STAMINA_MIN", "label": "抓到你了·主人体力下降下限", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「抓到你了」时农场主宠物体力随机下降的最小值", "default": 2, "min": 0, "max": 200},
    {"key": "STEAL_GUARD_CATCH_STAMINA_MAX", "label": "抓到你了·主人体力下降上限", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「抓到你了」时农场主宠物体力随机下降的最大值", "default": 5, "min": 0, "max": 200},
    {"key": "STEAL_GUARD_STOP_STAMINA_MIN", "label": "给我站住·主人体力下降下限", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「给我站住」时农场主宠物体力随机下降的最小值", "default": 3, "min": 0, "max": 200},
    {"key": "STEAL_GUARD_STOP_STAMINA_MAX", "label": "给我站住·主人体力下降上限", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「给我站住」时农场主宠物体力随机下降的最大值", "default": 6, "min": 0, "max": 200},
    {"key": "STEAL_FINE_RATIO", "label": "给我站住·罚款比例", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「给我站住」时偷菜者需缴纳的罚款为偷菜原金额的比例（1.1 = 110%）", "default": 1.10, "min": 0.5, "max": 5},
    {"key": "STEAL_SUPPRESS_HOURS", "label": "等级压制失效时长（小时）", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "触发「给我站住」后，等级压制失效的时长", "default": 12, "min": 0, "max": 168},
    {"key": "STEAL_PET_GAP", "label": "宠物等级压制级差", "type": "int", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "偷菜者宠物等级比农场主宠物等级低该级数及以上时，宠物加护触发概率减半", "default": 10, "min": 1, "max": 100},
    {"key": "STEAL_PET_GAP_DIV", "label": "等级压制概率除数", "type": "float", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "等级压制生效时宠物加护触发概率除以该值（2 = 减半）", "default": 2.0, "min": 1.1, "max": 10},
    {"key": "STEAL_GUARD_TIER_MAX", "label": "加护失效状态档位", "type": "int", "group": "偷菜", "subgroup": "宠物加护",
     "desc": "宠物状态档位达到该档及以上时宠物加护失效（3 = 三、四档失效）", "default": 3, "min": 2, "max": 4},
    # ---- 气味记忆 ----
    {"key": "STEAL_SCENT_CONSEC", "label": "气味记忆·连续成功次数", "type": "int", "group": "偷菜", "subgroup": "气味记忆",
     "desc": "24小时内同一偷菜者对同一农场主连续成功偷菜达到该次数即被施加气味记忆", "default": 3, "min": 1, "max": 50},
    {"key": "STEAL_SCENT_HOURS", "label": "气味记忆·持续时长（小时）", "type": "float", "group": "偷菜", "subgroup": "气味记忆",
     "desc": "气味记忆效果的持续时长", "default": 24, "min": 1, "max": 168},
    {"key": "STEAL_SCENT_MULT", "label": "气味记忆·加护概率倍数", "type": "float", "group": "偷菜", "subgroup": "气味记忆",
     "desc": "生效时偷菜者偷取施加者触发宠物加护的概率倍数（3 = 3倍；不受等级压制影响）", "default": 3.0, "min": 1, "max": 20},
    {"key": "AUTO_STEAL_DAILY_LIMIT", "label": "自动偷菜每日成功次数", "type": "int", "group": "偷菜", "subgroup": "自动偷菜",
     "desc": "自动偷菜每天最多成功次数（失败不消耗次数；被宠物抓到则当天锁定）", "default": 5, "min": 1, "max": 20},
    # ---- 左轮手枪 ----
    {"key": "ROULETTE_JOIN_TIMEOUT", "label": "左轮加入超时（秒）", "type": "int", "group": "左轮手枪", "subgroup": "规则",
     "desc": "左轮手枪开局后等待加入的超时秒数", "default": 30, "min": 10, "max": 300},
    # ---- 贷款 ----
    {"key": "LOAN_SPECIAL_AMOUNT", "label": "特别贷款金额", "type": "int", "group": "贷款", "subgroup": "特别贷款",
     "desc": "套餐 0 强制解锁贷款金额（不发放金币，作为解锁服务费）", "default": 2500, "min": 0, "max": 100000},
    # ---- 金币账单 ----
    {"key": "LEDGER_SHOW", "label": "金币账单展示条数", "type": "int", "group": "金币账单", "subgroup": "展示",
     "desc": "「查询流水」最多展示最近多少条", "default": 30, "min": 5, "max": 200},
    # ---- 签到 · 好感度（补充） ----
    {"key": "MIN_FAV", "label": "签到最少好感度", "type": "float", "group": "签到", "subgroup": "好感度",
     "desc": "每次签到最少增加的好感度", "default": 0.01, "min": 0, "max": 10, "attr": "min_fav"},
    {"key": "MAX_FAV", "label": "签到最多好感度", "type": "float", "group": "签到", "subgroup": "好感度",
     "desc": "每次签到最多增加的好感度", "default": 1.0, "min": 0, "max": 100, "attr": "max_fav"},
    {"key": "MAX_LEVEL", "label": "好感度最高等级", "type": "int", "group": "签到", "subgroup": "好感度",
     "desc": "好感度最高等级（初始为 0）", "default": 10, "min": 1, "max": 100},
    {"key": "LEVEL_STEP", "label": "好感度每级所需点数", "type": "float", "group": "签到", "subgroup": "好感度",
     "desc": "好感度每满该值提升一级", "default": 10.0, "min": 1, "max": 1000, "attr": "level_step"},
    # ---- 签到（补充）2.0.0：宠物结算各属性值档位变化范围 ----
    {"key": "PET_SETTLE_RANGES", "label": "宠物结算·各档位属性变化范围", "type": "string", "group": "签到", "subgroup": "宠物结算范围",
     "desc": "每日结算时宠物各属性按状态档位随机变化。2.0.1 固定四档 T1~T4（按饱食/口渴/心情最差档：1 最好 ~ 4 最差），每档定义全部五属性变化范围。格式：档位=属性最低~最高，逗号分隔；属性名：饱食/口渴/体力/心情/健康。示例：T1=饱食-15~-10,口渴-15~-10,体力100~120,心情3~7,健康5~10|T2=饱食-15~-10,口渴-15~-10,体力100~120,心情3~7,健康0.1~6|T3=饱食-20~-15,口渴-20~-15,体力80~120,心情1~2.5,健康-10~-4|T4=饱食-25~-20,口渴-25~-20,体力40~60,心情-5~-2,健康-15~-8",
     "default": "T1=饱食-15~-10,口渴-15~-10,体力100~120,心情3~7,健康5~10|T2=饱食-15~-10,口渴-15~-10,体力100~120,心情3~7,健康0.1~6|T3=饱食-20~-15,口渴-20~-15,体力80~120,心情1~2.5,健康-10~-4|T4=饱食-25~-20,口渴-25~-20,体力40~60,心情-5~-2,健康-15~-8",
     "attr": "pet_settle_ranges"},
    # （2.2.7 统一数值管理：档位判定阈值不再单独配置，由属性最大值数据推导，见 pet.py _TIER_PCTS）
    {"key": "PET_SETTLE_HEALTH_EXCHANGE", "label": "宠物结算·超扣转健康抵扣比例", "type": "int", "group": "签到", "subgroup": "宠物结算范围",
     "desc": "每日结算扣减的属性若超出结算前属性值，超出部分按「N 属性点 = 1 健康」用健康值抵扣（默认 4：少 4 点属性点扣 1 点健康；超出部分向上取整）",
     "default": 4, "min": 1, "max": 100},
    # ---- 左轮手枪（补充） ----
    {"key": "ROULETTE_MAGAZINES", "label": "弹匣数量", "type": "int", "group": "左轮手枪", "subgroup": "规则",
     "desc": "左轮手枪弹匣容量", "default": 7, "min": 3, "max": 20},
    {"key": "ROULETTE_MAX_BULLETS", "label": "子弹数量上限", "type": "int", "group": "左轮手枪", "subgroup": "规则",
     "desc": "单局最多装的子弹数量", "default": 6, "min": 1, "max": 10},
    {"key": "ROULETTE_MIN_PLAYERS", "label": "最少玩家人数", "type": "int", "group": "左轮手枪", "subgroup": "规则",
     "desc": "开局所需的最少玩家人数", "default": 2, "min": 2, "max": 5},
    {"key": "ROULETTE_MAX_PLAYERS", "label": "最多玩家人数", "type": "int", "group": "左轮手枪", "subgroup": "规则",
     "desc": "一局最多参与的玩家人数", "default": 3, "min": 2, "max": 6},
    {"key": "ROULETTE_FEE_RATE", "label": "手续费比例", "type": "float", "group": "左轮手枪", "subgroup": "规则",
     "desc": "左轮手枪抽取的手续费比例（0.1 = 10%，3 人局固定 5%）", "default": 0.1, "min": 0, "max": 0.5},
    # ---- 宠物（补充） ----
    {"key": "PET_MAX_LEVEL", "label": "宠物最大等级", "type": "int", "group": "宠物", "subgroup": "解锁",
     "desc": "宠物等级上限", "default": 100, "min": 10, "max": 999},
    {"key": "PET_MAX_HEALTH", "label": "健康度最大值", "type": "float", "group": "宠物", "subgroup": "属性",
     "desc": "宠物健康度上限", "default": 200.0, "min": 50, "max": 1000},
    {"key": "PILL_NAME", "label": "属性丸道具名", "type": "string", "group": "宠物", "subgroup": "属性丸",
     "desc": "签到获得的属性丸名称", "default": "属性丸"},
    {"key": "EXP_BALL_NAME", "label": "农场经验球道具名", "type": "string", "group": "农场", "subgroup": "农场经验球",
     "desc": "签到获得的农场经验球名称", "default": "农场经验球"},
    {"key": "ITEM_TO_COIN", "label": "道具转金币单价", "type": "int", "group": "宠物", "subgroup": "属性丸",
     "desc": "未开通对应功能时，属性丸/经验球自动转换为金币的单价", "default": 10, "min": 1, "max": 100000},
    # ---- 农场（补充） ----
    {"key": "FARM_FREE_PLOTS", "label": "解锁赠送土地数", "type": "int", "group": "农场", "subgroup": "价格",
     "desc": "解锁农场时赠送的土地数量", "default": 2, "min": 1, "max": 10},
    {"key": "FARM_MAX_LEVEL", "label": "农场最大等级", "type": "int", "group": "农场", "subgroup": "土地",
     "desc": "农场等级上限", "default": 100, "min": 10, "max": 999},
    {"key": "FARM_EXP_BASE", "label": "农场升级经验基数", "type": "float", "group": "农场", "subgroup": "土地",
     "desc": "升到 N 级需 1000×N 经验（此值即基数 1000）", "default": 1000.0, "min": 100, "max": 100000},
    {"key": "FARM_UPGRADE_COSTS", "label": "土地升级费用（逗号分隔）", "type": "list", "group": "农场", "subgroup": "土地",
     "desc": "土地从当前等级升到下一级的金币，依次为 贫瘠→红→普通→肥沃→黑", "default": "1000,1500,2000,3000"},
    {"key": "FARM_GRADE_BONUSES", "label": "土地等级加成（表格）", "type": "string", "group": "农场", "subgroup": "土地",
     "desc": "不同土地等级的 产量加成/时间减免（百分比）与升级价格（在「土地等级加成」表格中编辑，升级价格随表格一并保存到 FARM_UPGRADE_COSTS）；格式：等级名=产量%,时间%，竖线分隔",
     "default": "贫瘠土地=0,0|红土地=100,0|普通土地=200,10|肥沃土地=250,20|黑土地=400,35"},
    # ---- 农场（补充）2.0.0：作物成长阶段 ----
    {"key": "CROP_LEVEL_RANGES", "label": "作物等级划分（分钟上限，逗号分隔）", "type": "list", "group": "农场", "subgroup": "成长阶段",
     "desc": "按贫瘠土地上的成熟分钟数划分作物等级：0~第1个数=一级，依此类推。如 0,240,480,720,1440 表示 0-240/241-480/481-720/721-1440", "default": "0,240,480,720,1440"},
    {"key": "CROP_LEVEL_STAGES", "label": "各等级成长阶段数（逗号分隔）", "type": "list", "group": "农场", "subgroup": "成长阶段",
     "desc": "一级/二级/三级/四级作物在总生长周期内划分的成长阶段数量（化肥每次加速推进一个阶段）", "default": "4,5,5,6"},
    # ---- 贷款（补充） ----
    {"key": "LOAN_SPECIAL_RATE", "label": "特别贷款日息", "type": "float", "group": "贷款", "subgroup": "特别贷款",
     "desc": "特别贷款每日利息（% / 日）", "default": 1.0, "min": 0, "max": 100, "attr": "loan_special_rate"},
    {"key": "LOAN_SPECIAL_DAYS", "label": "特别贷款限期（天）", "type": "int", "group": "贷款", "subgroup": "特别贷款",
     "desc": "特别贷款还款限期天数", "default": 30, "min": 1, "max": 365, "attr": "loan_special_days"},
    {"key": "LOAN_SPECIAL_TAKE", "label": "特别贷款逾期收取比例", "type": "float", "group": "贷款", "subgroup": "特别贷款",
     "desc": "特别贷款逾期后收取仓库价值的比例（0.2 = 20%）", "default": 0.2, "min": 0, "max": 1},
    {"key": "LOAN_COIN_DEDUCT", "label": "逾期金币自动扣款比例", "type": "float", "group": "贷款", "subgroup": "规则",
     "desc": "有逾期贷款时，获得金币自动扣款还款的比例（0.2 = 20%）", "default": 0.2, "min": 0, "max": 1},
    {"key": "LOAN_FAV_DROP_SPECIAL", "label": "特别贷款逾期好感度降低范围", "type": "list", "group": "贷款", "subgroup": "规则",
     "desc": "特别贷款逾期每日好感度降低的随机范围（逗号分隔，如 1.0,1.5）", "default": "1.0,1.5"},
    {"key": "LOAN_FAV_DROP_NORMAL", "label": "一般逾期好感度降低范围", "type": "list", "group": "贷款", "subgroup": "规则",
     "desc": "一般逾期每日好感度降低的随机范围（逗号分隔，如 1.01,1.25）", "default": "1.01,1.25"},
    {"key": "LOAN_OVERDUE_YEAR_LIMIT", "label": "每年最多逾期次数", "type": "int", "group": "贷款", "subgroup": "规则",
     "desc": "每年逾期次数达到该值后禁用贷款功能", "default": 4, "min": 1, "max": 100},
    {"key": "LOAN_GENERAL_OVERDUE_DAYS", "label": "一般套餐逾期天数", "type": "int", "group": "贷款", "subgroup": "规则",
     "desc": "一般/自定义套餐的还款限期天数", "default": 15, "min": 1, "max": 365},
    {"key": "LOAN_SHORT_GRACE_DAYS", "label": "短期套餐免息天数", "type": "int", "group": "贷款", "subgroup": "规则",
     "desc": "短期套餐免息期天数", "default": 10, "min": 1, "max": 365},
    {"key": "LOAN_SHORT_RATE", "label": "短期套餐逾期日利率", "type": "float", "group": "贷款", "subgroup": "规则",
     "desc": "短期套餐逾期后的日利率（% / 日）", "default": 6.0, "min": 0, "max": 100},
    {"key": "LOAN_DAILY_MULT", "label": "每日累计贷款倍数", "type": "float", "group": "贷款", "subgroup": "规则",
     "desc": "每日累计贷款上限 = 该值 × 套餐上限", "default": 2.0, "min": 1, "max": 100},
    {"key": "LOAN_FARM_ROLLBACK_DAYS", "label": "逾期农场回退天数", "type": "int", "group": "贷款", "subgroup": "规则",
     "desc": "逾期超过该天数后农场回退至初始状态", "default": 30, "min": 1, "max": 365},
    {"key": "LOAN_AUTO_TIME", "label": "每日自动处理时间（时,分）", "type": "list", "group": "贷款", "subgroup": "规则",
     "desc": "每日自动卖仓库/自动签到还款的时间（逗号分隔，如 23,0）", "default": "23,0"},
    # ---- 红包雨（1.7.5 起迁移至 WebUI「活动中心」配置，运行参数面板不再展示） ----
    # ---- 调试 / 通用 ----
    {"key": "DEBUG_PASSWORD", "label": "调试模式口令", "type": "string", "group": "调试", "subgroup": "口令",
     "desc": "管理员在对话框输入此口令解锁 WebUI 调试按钮（重启后失效）", "default": "88224646"},
    {"key": "TEMP_IMAGE_TTL", "label": "临时图片保留秒数", "type": "int", "group": "调试", "subgroup": "图片",
     "desc": "生成的临时图片超过该秒数后清理（0 表示不清理）", "default": 600, "min": 0, "max": 86400},
    # ---- 图片输出（2.2.6） ----
    {"key": "MIN_IMG_RATIO", "label": "输出图片最小长宽比", "type": "float", "group": "图片输出", "subgroup": "比例范围",
     "desc": "所有回复图片宽/高比的下限，过窄图片自动加宽或两侧补背景色（4:3 ≈ 1.333，0 = 不限制）",
     "default": 4 / 3, "min": 0, "max": 3},
    {"key": "MAX_IMG_RATIO", "label": "输出图片最大长宽比", "type": "float", "group": "图片输出", "subgroup": "比例范围",
     "desc": "所有回复图片宽/高比的上限（应大于下限），过扁图片自动上下补背景色（16:9 ≈ 1.778，0 = 不限制）",
     "default": 16 / 9, "min": 0, "max": 5},
    # ---- 排行榜 ----
    {"key": "RANK_DISPLAY", "label": "排行榜展示名次", "type": "int", "group": "排行榜", "subgroup": "通用",
     "desc": "每个排行榜最多展示的名次数（前 N 名）", "default": 20, "min": 5, "max": 100},
    {"key": "RANK_HIGHLIGHT_COLOR", "label": "我的名次高亮颜色", "type": "string", "group": "排行榜", "subgroup": "通用",
     "desc": "查询人自己在榜上的高亮颜色（十六进制，如 #92D050）", "default": "#92D050"},
    {"key": "RANK_TEXT_COLOR", "label": "普通行文字颜色", "type": "string", "group": "排行榜", "subgroup": "通用",
     "desc": "本群（非自己）用户的文字颜色（十六进制，如 #000000）", "default": "#000000"},
    {"key": "RANK_MASKED_COLOR", "label": "脱敏行文字颜色", "type": "string", "group": "排行榜", "subgroup": "通用",
     "desc": "非本群（脱敏 ** 显示）用户的文字颜色（十六进制，如 #7F7F7F）", "default": "#7F7F7F"},
    {"key": "RANK_SEP_COLOR", "label": "分割线颜色", "type": "string", "group": "排行榜", "subgroup": "通用",
     "desc": "行间分割线颜色（十六进制，如 #D9D9D9）", "default": "#D9D9D9"},
    {"key": "RANK_IMAGE_SCALE", "label": "图片宽度倍数", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "排行榜图片宽度 = 内容宽度 × 该倍数（默认 2.5 = 原来的 250%）", "default": 2.5, "min": 1.0, "max": 10.0},
    {"key": "RANK_BAR_LIGHTEN", "label": "进度条颜色浅化比例", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "进度条颜色比文字颜色浅的比例（默认 0.2 = 浅 20%）", "default": 0.2, "min": 0.0, "max": 0.9},
    {"key": "RANK_NAME_MAX_CHARS", "label": "名字显示最大字符数", "type": "int", "group": "排行榜", "subgroup": "通用",
     "desc": "用户名超过该字符数时截断，超出部分用 ... 代替", "default": 6, "min": 1, "max": 20},
    {"key": "RANK_BAR_GAP_LEFT_MULT", "label": "进度条左侧留白倍数", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "进度条左侧留白 = 基准 12px × 该倍数（2 = 原先的 200%）", "default": 2.0, "min": 0.5, "max": 10.0},
    {"key": "RANK_BAR_GAP_RIGHT_MULT", "label": "进度条右侧留白倍数", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "进度条右侧留白 = 基准 12px × 该倍数（3 = 原先的 300%）", "default": 3.0, "min": 0.5, "max": 10.0},
    {"key": "RANK_ROW_SEP_PCT", "label": "行分割线高度占比", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "行间分割线高度 = 文字行高 × 该比例（默认 0.03 = 原先 10% 的 30%）", "default": 0.03, "min": 0.0, "max": 0.5},
    {"key": "GROUP_MEMBER_TTL_HOURS", "label": "群成员标记有效期（小时）", "type": "float", "group": "排行榜", "subgroup": "通用",
     "desc": "用户在本群触发插件功能后被标记为本群成员的时长，超时后按非本群用户脱敏显示", "default": 48, "min": 1, "max": 720},
    {"key": "RANK_COIN_COIN_W", "label": "金币权重（持有金币）", "type": "float", "group": "排行榜", "subgroup": "金币排行",
     "desc": "金币排行积分 = 持有金币 × 该权重 + 银行存款 × 存款权重", "default": 1.0, "min": 0, "max": 1000},
    {"key": "RANK_COIN_BANK_W", "label": "银行存款权重", "type": "float", "group": "排行榜", "subgroup": "金币排行",
     "desc": "金币排行积分 = 持有金币 × 金币权重 + 银行存款 × 该权重", "default": 1.0, "min": 0, "max": 1000},
    {"key": "RANK_PET_EXP_W", "label": "宠物经验权重", "type": "float", "group": "排行榜", "subgroup": "宠物排行",
     "desc": "宠物排行积分 = 经验 × 该权重 + 健康度 × 健康权重 + 其它属性 × 属性权重", "default": 2.0, "min": 0, "max": 1000},
    {"key": "RANK_PET_HEALTH_W", "label": "宠物健康度权重", "type": "float", "group": "排行榜", "subgroup": "宠物排行",
     "desc": "宠物排行中健康度的权重", "default": 1.5, "min": 0, "max": 1000},
    {"key": "RANK_PET_ATTR_W", "label": "其它属性权重", "type": "float", "group": "排行榜", "subgroup": "宠物排行",
     "desc": "宠物排行中饱食+口渴+体力+心情 总和的权重", "default": 0.5, "min": 0, "max": 1000},
    {"key": "RANK_FARM_EXP_W", "label": "农场经验权重", "type": "float", "group": "排行榜", "subgroup": "农场排行",
     "desc": "农场排行积分 = 经验 × 该权重 + (土地数-2) × 土地权重 + 等级分合计 × 等级权重", "default": 2.0, "min": 0, "max": 1000},
    {"key": "RANK_FARM_PLOT_W", "label": "土地数权重", "type": "float", "group": "排行榜", "subgroup": "农场排行",
     "desc": "农场排行中（拥有土地数 - 2）× 该权重", "default": 400.0, "min": 0, "max": 100000},
    {"key": "RANK_FARM_GRADE_W", "label": "土地等级分权重", "type": "float", "group": "排行榜", "subgroup": "农场排行",
     "desc": "农场排行中土地等级分合计 × 该权重", "default": 0.5, "min": 0, "max": 1000},
    {"key": "RANK_PLOT_SCORES", "label": "土地等级分（逗号分隔）", "type": "list", "group": "排行榜", "subgroup": "农场排行",
     "desc": "各土地等级的累计升级分数：贫瘠→红→普通→肥沃→黑（如 0,1000,2500,4500,7500）", "default": "0,1000,2500,4500,7500"},
]
PARAM_KEYS = {p["key"]: p for p in RUNTIME_PARAMS}


# ================= Web API：运行参数 =================
def _apply_runtime_params(core, params: dict):
    """校验并应用运行参数：经 core.set_param 写入 data["params"] 并同步全部模块全局（立即生效）。
    返回 (applied, errors)；errors 为 {key: 未生效原因}，供 WebUI 提示管理员。
    （2.3.0 webui.py _apply_runtime_params 移植：globals 更新与实例属性同步由 core.set_param 承担）"""
    applied = {}
    errors = {}
    for spec in RUNTIME_PARAMS:
        key = spec["key"]
        if key not in params:
            continue
        raw = params[key]
        label = spec["label"]
        try:
            # 2.2.0：密码类参数留空 = 保持不变（前端不回显当前值）
            if key in _SENSITIVE_PARAMS and not str(raw).strip():
                continue
            # 2.2.1：环境变量 SIGNIN_DEBUG_PASSWORD 优先级最高，运行参数不再覆盖（key 无害化）
            if key in _SENSITIVE_PARAMS and os.environ.get("SIGNIN_DEBUG_PASSWORD"):
                continue
            if key in _SENSITIVE_PARAMS and len(str(raw).strip()) < 4:
                errors[key] = f"「{label}」至少需要 4 位"
                continue
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
                        parts.append(core.f(s))
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
        # 3.0：写入 data["params"] 持久化，并同步 core 与已挂载插件的模块全局（立即生效）
        core.set_param(key, val)
        applied[key] = val
    return applied, errors


async def web_get_params():
    """读取运行参数：返回参数 schema 列表（含当前值），前端据此渲染表单。
    2.2.0：敏感参数（调试口令等）不回传当前值，前端只显示「已设置」，修改时提交新值。
    当前值经 core.param(key, default)（params 覆盖 → core 常量 → 配置 → 缺省）。"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        items = []
        for spec in RUNTIME_PARAMS:
            key = spec["key"]
            cur = core.param(key, spec.get("default"))
            item = {
                "key": key,
                "label": spec["label"],
                "type": spec["type"],
                "group": spec.get("group", "其他"),
                "subgroup": spec.get("subgroup", "通用"),
                "desc": spec.get("desc", ""),
                "value": cur,
                "min": spec.get("min"),
                "max": spec.get("max"),
            }
            # 2.2.0：密码类参数不向访问终端回传任何值（哈希校验只在插件本地进行）
            if key in _SENSITIVE_PARAMS:
                item["value"] = ""
                item["masked"] = True
            items.append(item)
        return json_response({"params": items})


async def web_save_params():
    """保存运行参数：POST {params: {key: value}}，校验后立即生效并持久化"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        incoming = payload.get("params")
        if not isinstance(incoming, dict):
            return error_response("params 必须是对象", status_code=400)
        _history_backup(core, "保存前自动备份")
        applied, errors = _apply_runtime_params(core, incoming)
        core.save()
        return json_response({"saved": True, "applied": applied, "errors": errors})


# ================= Web API：同义口令 =================
async def web_get_aliases():
    """读取同义口令与全部标准指令（供下拉选择；3.0 标准指令表 = core.all_heads()）"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        data = core.data
        return json_response({
            "aliases": dict(data.get("alias_cmds") or {}),
            "heads": sorted(core.all_heads()),
        })


async def web_save_aliases():
    """保存同义口令：POST {aliases: {同义词: 标准指令}}，校验后立即生效并持久化"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        incoming = payload.get("aliases")
        if not isinstance(incoming, dict):
            return error_response("aliases 必须是对象", status_code=400)
        heads = core.all_heads()
        cleaned, errors = {}, {}
        for k, v in incoming.items():
            alias = str(k).strip()
            target = str(v).strip()
            if not alias or not target:
                errors[alias or "(空)"] = "同义词与目标指令不能为空"
                continue
            if alias in heads:
                errors[alias] = f"「{alias}」已是标准指令，不能作为同义词"
                continue
            if target not in heads:
                errors[alias] = f"「{target}」不是可用的标准指令"
                continue
            if alias in cleaned:
                errors[alias] = "同义词重复"
                continue
            cleaned[alias] = target
        if errors:
            return error_response(f"保存失败：{errors}", status_code=400)
        _history_backup(core, "保存前自动备份")
        core.data["alias_cmds"] = cleaned
        core.save()
        return json_response({"saved": True, "aliases": cleaned})


# ================= Web API：调试模式 =================
async def web_debug_status():
    """调试模式状态：{unlocked: 是否已输入口令, enabled: 是否已开启}
    （3.0：口令解锁标记 core._debug_unlocked 由 debug 插件设置；开关为 core.debug 属性）"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        return json_response({"unlocked": bool(getattr(core, "_debug_unlocked", False)),
                              "enabled": bool(getattr(core, "debug", False))})


async def web_debug_toggle():
    """开关调试模式（需先输入口令解锁）。开启后无限资源且不写盘；退出后回到开启前状态"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        if not getattr(core, "_debug_unlocked", False):
            return error_response("未解锁调试模式（请在对话框输入口令）", status_code=403)
        core.debug = not bool(getattr(core, "debug", False))
        # 2.3.0 的 _debug_data 内存缓存由 3.0 core 数据层承担，这里只翻开关
        # 退出时不重置 unlocked（按钮保留，重启插件后才消失）
        state = "开启" if core.debug else "退出"
        return json_response({"enabled": bool(core.debug), "state": state})


# ================= Web API：功能开关 =================
# 3.0 存储键为 data["features"]（core.feature_enabled 同款；2.3.0 为 feature_switches）
async def web_get_feature_status():
    """读取功能开关：返回所有模块 + 当前开关状态"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        switches = core.data.get("features", {})
        return json_response({
            "modules": [
                {"key": m["key"], "label": m["label"],
                 "enabled": bool(switches.get(m["key"], True))}
                for m in FEATURE_MODULES
            ]
        })


async def web_save_feature_status():
    """保存功能开关：{switches: {key: bool}}"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        switches = payload.get("switches")
        if not isinstance(switches, dict):
            return error_response("switches 必须是对象", status_code=400)
        _history_backup(core, "保存前自动备份")
        cur = dict(core.data.get("features", {}))
        for m in FEATURE_MODULES:
            if m["key"] in switches:
                cur[m["key"]] = bool(switches[m["key"]])
        core.data["features"] = cur
        core.save()
        return json_response({"saved": True})


# ================= Web API：活动中心 =================
def _activities_of(core):
    """活动实例列表（3.0 经 core.service("thirdparty")；服务未挂载返回 []）"""
    svc = core.service("thirdparty")
    if svc is None:
        return []
    acts = getattr(svc, "activities", None)
    if acts is None:
        loader = getattr(svc, "load_activities", None)
        acts = loader() if callable(loader) else []
    return list(acts or [])


async def web_get_activities():
    """返回所有已注册活动：启用状态 + 参数表单 schema + 当前值（含覆盖）"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        data = core.data
        enabled = data.get("activities", {})
        items = []
        for act in _activities_of(core):
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


async def web_save_activities():
    """保存活动配置：{enabled: {id: bool}, configs?: {id: {字段: 值}}}"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    acts = _activities_of(core)
    async with core.lock:
        payload = await request.json(default={})
        enabled = payload.get("enabled")
        if not isinstance(enabled, dict):
            return error_response("enabled 必须是对象", status_code=400)
        data = core.data
        _history_backup(core, "保存前自动备份")
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
                act = next((a for a in acts if a.id == aid), None)
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
        core.save()
        return json_response({"saved": True, "errors": errors})


# ================= Web API：群成员昵称同步 =================
def _collect_bots(core):
    """收集支持 OneBot call_action 的平台机器人（aiocqhttp 等），供 WebUI 同步群昵称使用。
    （2.3.0 modules/rank.py _collect_bots 移植）"""
    bots = []
    pm = getattr(core.context, "platform_manager", None)
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


async def web_sync_group_names():
    """WebUI 按钮：拉取所有群聊的成员昵称（get_group_list → get_group_member_list），
    存入 data.group_names 作为排行榜用户默认昵称；返回统计结果。"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        bots = _collect_bots(core)
        if not bots:
            return error_response(
                "未找到支持群成员接口的平台机器人（需要 aiocqhttp / OneBot 适配器，且机器人已连接）",
                status_code=400,
            )
        data = core.data
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
            core.save()
        msg = f"已同步 {groups} 个群、共 {members} 名成员昵称（作为排行榜默认昵称）"
        logger.info(f"[插件] WebUI 同步群昵称：{msg}")
        return json_response({"ok": True, "groups": groups, "members": members, "msg": msg})


# ================= Web API：局域网开放（不套访问门，自身承担鉴权/记录职责；lan.py 移植） =================
async def web_lan_status():
    """读取局域网开放状态：{enabled, is_local, unlocked, password_set, records_count, ip}"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        data = core.data
        lan = lan_conf(data)
        client = lan_client()
        now_ts = int(datetime.now().timestamp())
        unlocked = client["is_local"] or (
            lan.get("enabled") and lan_is_unlocked(core, data, client, now_ts)
        )
        return json_response({
            "enabled": bool(lan.get("enabled")),
            "is_local": client["is_local"],
            "unlocked": unlocked,
            "password_set": bool(lan.get("password_hash")),
            "records_count": len(lan.get("records") or []),
            "ip": client["ip"],
        })


async def web_lan_unlock():
    """输入密码解锁局域网访问：POST {password}。
    校验哈希；正确则下发签名会话 Cookie 并记录；错误/未设密码则记录并返回失败。
    2.2.2：连续输错 5 次锁定局域网登录（只有本地主机访问能解除）；
    第 3 次起提示剩余机会。密码错误改用 400——不再返回 401
    （AstrBot 前端把 401 当作登录态失效，会强制退出管理员的 AstrBot 登录）。"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        payload = await request.json(default={})
        password = payload.get("password") if isinstance(payload, dict) else None
        data = core.data
        lan = lan_conf(data)
        client = lan_client()
        now_ts = int(datetime.now().timestamp())
        if client["is_local"]:
            # 本地免密；顺手解除锁定（只有本地能解除）
            if lan.get("locked") or lan.get("fail_count"):
                lan["locked"] = False
                lan["fail_count"] = 0
                core.save()
            return json_response({"unlocked": True})
        if not lan.get("enabled"):
            return error_response("局域网访问未开启", status_code=400)
        if not lan.get("password_hash"):
            lan_record(core, data, ip=client["ip"], is_local=False, ok=False, ua=client["ua"])
            core.save()
            return error_response("尚未设置局域网访问密码", status_code=400)
        # 2.2.2：锁定期间不接受任何解锁尝试（即使密码正确）
        if lan.get("locked"):
            lan_record(core, data, ip=client["ip"], is_local=False, ok=False, ua=client["ua"])
            core.save()
            return error_response("密码连续错误次数过多，局域网登录已锁定，请在本地主机（127.0.0.1）打开后台解除锁定",
                                  status_code=403)
        ok = isinstance(password, str) and lan_verify_password(lan["password_hash"], password)
        if not ok:
            fails = int(lan.get("fail_count", 0) or 0) + 1
            lan["fail_count"] = fails
            if fails >= 5:
                lan["locked"] = True
                msg = "密码连续错误 5 次，局域网登录已锁定，请在本地主机（127.0.0.1）打开后台解除锁定"
            elif fails >= 3:
                msg = f"密码错误，还有 {5 - fails} 次机会（连续错 5 次将锁定局域网登录）"
            else:
                msg = "密码错误，请重试"
            lan_record(core, data, ip=client["ip"], is_local=False, ok=False, ua=client["ua"])
            core.save()
            return error_response(msg, status_code=400)
        lan["fail_count"] = 0
        lan_record(core, data, ip=client["ip"], is_local=False, ok=True, ua=client["ua"])
        core.save()
        secret = lan_secret(core, data)
        exp_ts = now_ts + LAN_SESSION_HOURS * 3600
        token = lan_sign_token(secret, client["ip"], exp_ts)
        resp = json_response({"unlocked": True})
        try:
            resp.set_cookie(LAN_COOKIE, token, max_age=LAN_SESSION_HOURS * 3600,
                            httponly=True, samesite="lax", path="/")
        except Exception:
            pass
        return resp


async def web_lan_setup():
    """开关/设置局域网访问密码：POST {enabled?, password?}。【仅本地服务器可调用】
    修改密码时仅保存哈希；开启功能但未设密码则要求同时提供 password。"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        client = lan_client()
        if not client["is_local"]:
            return error_response("只能在本地服务器上修改局域网访问设置", status_code=403)
        payload = await request.json(default={})
        data = core.data
        lan = lan_conf(data)
        changed = {}
        if "enabled" in payload and isinstance(payload, dict):
            lan["enabled"] = bool(payload["enabled"])
            changed["enabled"] = lan["enabled"]
        if isinstance(payload, dict) and "password" in payload:
            pw = payload["password"]
            if not isinstance(pw, str):
                return error_response("密码必须是字符串", status_code=400)
            if pw == "" or len(pw) < 4:
                return error_response("密码至少 4 位", status_code=400)
            salt, digest = lan_hash_password(pw)
            lan["password_hash"] = f"{salt}${digest}"
            changed["password_set"] = True
        if lan.get("enabled") and not lan.get("password_hash"):
            return error_response("开启局域网访问必须先设置访问密码", status_code=400)
        core.save()
        return json_response({"saved": True, **changed})


async def web_lan_records():
    """读取访问记录。【仅本地】"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        client = lan_client()
        if not client["is_local"]:
            return error_response("仅本地服务器可查看访问记录", status_code=403)
        data = core.data
        lan = lan_conf(data)
        return json_response({"records": list(reversed(lan.get("records") or []))})


async def web_lan_blacklist_get():
    """读取黑名单（IP/CIDR 列表）。【仅本地】"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        client = lan_client()
        if not client["is_local"]:
            return error_response("仅本地服务器可管理黑名单", status_code=403)
        data = core.data
        lan = lan_conf(data)
        return json_response({"blacklist": list(lan.get("blacklist") or [])})


async def web_lan_blacklist_set():
    """添加/移除黑名单：POST {action: 'add'|'remove', ip}。【仅本地】"""
    core = _core()
    if core is None:
        return error_response("WebUI 模块未初始化（register 未调用）", status_code=500)
    async with core.lock:
        client = lan_client()
        if not client["is_local"]:
            return error_response("仅本地服务器可管理黑名单", status_code=403)
        payload = await request.json(default={})
        action = payload.get("action") if isinstance(payload, dict) else None
        ip = payload.get("ip") if isinstance(payload, dict) else None
        if action not in ("add", "remove") or not isinstance(ip, str) or not ip.strip():
            return error_response("参数不合法", status_code=400)
        ip = ip.strip()
        data = core.data
        lan = lan_conf(data)
        blacklist = [b for b in (lan.get("blacklist") or []) if isinstance(b, str)]
        if action == "add":
            if ip not in blacklist:
                blacklist.append(ip)
        else:
            blacklist = [b for b in blacklist if b != ip]
        lan["blacklist"] = blacklist
        core.save()
        return json_response({"saved": True, "blacklist": blacklist})
