# -*- coding: utf-8 -*-
# 局域网开放访问。从原 main.py 的 SignInPlugin 拆出的 Mixin，由入口类组合继承。
from .base import *  # noqa: F401,F403  常量与共享工具
from .base import _register_runtime_module  # noqa: F401
import sys as _sys

_register_runtime_module(_sys.modules[__name__])


class LanMixin:
    def _lan_gate_wrap(self, handler):
        """把非局域网端点的处理器包上访问门：未开启/本地/已解锁才放行，否则 403。
        注意：AstrBot 在调用插件 Web API 时会把请求绑定到 request 上下文，
        因此这里的 request 代理可用。处理器内部自带 self._lock，这里不再加锁。"""
        async def _gated(*args, **kwargs):
            data = self._load()
            if not self._lan_gate(data):
                return error_response("需要局域网访问密码或该设备已被禁止访问", status_code=403)
            return await handler(*args, **kwargs)
        return _gated

    # ================= 消息路由（无需前缀 / @） =================
    def _lan_conf(self, data: dict) -> dict:
        """读取局域网开放配置字典（缺失时补默认结构）。"""
        lan = data.get(LAN_DATA_KEY)
        if not isinstance(lan, dict):
            lan = {}
            data[LAN_DATA_KEY] = lan
        lan.setdefault("enabled", False)
        lan.setdefault("password_hash", None)
        lan.setdefault("records", [])
        lan.setdefault("blacklist", [])
        return lan

    @staticmethod
    def _lan_client(req=None):
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
            "is_local": _is_loopback(client_host),
            "ua": (ua or "")[:200],
        }

    def _lan_record(self, data: dict, *, ip, is_local, ok, ua=""):
        """写一条访问记录（本地访问也记录，便于管理员审计）。"""
        lan = self._lan_conf(data)
        recs = lan.setdefault("records", [])
        name = "本地" if is_local else self._lan_device_name(ua)
        recs.append({
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ip": ip,
            "name": name,
            "ok": bool(ok),
            "ua": (ua or "")[:200],
        })
        if len(recs) > LAN_MAX_RECORDS:
            del recs[: len(recs) - LAN_MAX_RECORDS]

    @staticmethod
    def _lan_device_name(ua: str) -> str:
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

    def _lan_secret(self, data: dict) -> str:
        """会话签名密钥：首次访问局域网时生成并持久化（避免重启后所有访客失联）。"""
        lan = self._lan_conf(data)
        secret = lan.get("secret")
        if not isinstance(secret, str) or len(secret) < 32:
            secret = secrets.token_hex(32)
            lan["secret"] = secret
            try:
                self._save(data)
            except Exception:
                pass
        return secret

    @staticmethod
    def _lan_sign_token(secret: str, ip: str, exp_ts: int) -> str:
        mac = hmac.new(secret.encode("utf-8"), f"{ip}:{exp_ts}".encode("utf-8"),
                       hashlib.sha256).hexdigest()[:24]
        return f"{exp_ts}.{mac}"

    @staticmethod
    def _lan_verify_token(secret: str, ip: str, token: str, now_ts: int) -> bool:
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

    def _lan_is_unlocked(self, data: dict, client: dict, now_ts: int) -> bool:
        """当前请求是否已通过局域网密码解锁（Cookie 会话有效）。"""
        lan = self._lan_conf(data)
        secret = lan.get("secret")
        if not isinstance(secret, str) or len(secret) < 32:
            return False
        try:
            token = request.cookies.get(LAN_COOKIE)
        except Exception:
            token = None
        return self._lan_verify_token(secret, client["ip"], token or "", now_ts)

    def _lan_gate(self, data: dict) -> bool:
        """局域网访问门：True=放行，False=拒绝（返回 403）。
        规则（先过 AstrBot 鉴权后，插件再加一层）：
        1) 功能关闭 → 放行；
        2) 本地访问 → 放行（免密）；
        3) 命中黑名单 → 拒绝并记录；
        4) 未解锁（无有效会话 Cookie） → 拒绝并记录；
        5) 其余放行。
        被拒绝的访问会写访问记录（含密码校验是否通过），供管理员审计；
        通过密码解锁的访问由 web_lan_unlock 记录。"""
        lan = self._lan_conf(data)
        client = self._lan_client()
        now_ts = int(datetime.now().timestamp())

        if not lan.get("enabled"):
            return True
        if client["is_local"]:
            return True
        if _lan_ip_in_blacklist(client["ip"], lan.get("blacklist") or []):
            self._lan_record(data, ip=client["ip"], is_local=False, ok=False, ua=client["ua"])
            self._save(data)
            return False
        if not self._lan_is_unlocked(data, client, now_ts):
            self._lan_record(data, ip=client["ip"], is_local=False, ok=False, ua=client["ua"])
            self._save(data)
            return False
        return True

    # ---- Web API ----
    async def web_lan_status(self):
        """读取局域网开放状态：{enabled, is_local, unlocked, password_set, records_count}"""
        async with self._lock:
            data = self._load()
            lan = self._lan_conf(data)
            client = self._lan_client()
            now_ts = int(datetime.now().timestamp())
            unlocked = client["is_local"] or (
                lan.get("enabled") and self._lan_is_unlocked(data, client, now_ts)
            )
            return json_response({
                "enabled": bool(lan.get("enabled")),
                "is_local": client["is_local"],
                "unlocked": unlocked,
                "password_set": bool(lan.get("password_hash")),
                "records_count": len(lan.get("records") or []),
                "ip": client["ip"],
            })

    async def web_lan_unlock(self):
        """输入密码解锁局域网访问：POST {password}。
        校验哈希；正确则下发签名会话 Cookie 并记录；错误/未设密码则记录并返回失败。"""
        async with self._lock:
            payload = await request.json(default={})
            password = payload.get("password") if isinstance(payload, dict) else None
            data = self._load()
            lan = self._lan_conf(data)
            client = self._lan_client()
            now_ts = int(datetime.now().timestamp())
            if client["is_local"]:
                return json_response({"unlocked": True})
            if not lan.get("enabled"):
                return error_response("局域网访问未开启", status_code=400)
            if not lan.get("password_hash"):
                self._lan_record(data, ip=client["ip"], is_local=False, ok=False, ua=client["ua"])
                self._save(data)
                return error_response("尚未设置局域网访问密码", status_code=400)
            ok = isinstance(password, str) and _lan_verify_password(lan["password_hash"], password)
            self._lan_record(data, ip=client["ip"], is_local=False, ok=ok, ua=client["ua"])
            self._save(data)
            if not ok:
                return error_response("密码错误", status_code=401)
            secret = self._lan_secret(data)
            exp_ts = now_ts + LAN_SESSION_HOURS * 3600
            token = self._lan_sign_token(secret, client["ip"], exp_ts)
            resp = json_response({"unlocked": True})
            try:
                resp.set_cookie(LAN_COOKIE, token, max_age=LAN_SESSION_HOURS * 3600,
                                httponly=True, samesite="lax", path="/")
            except Exception:
                pass
            return resp

    async def web_lan_setup(self):
        """开关/设置局域网访问密码：POST {enabled?, password?}。【仅本地服务器可调用】
        修改密码时仅保存哈希；开启功能但未设密码则要求同时提供 password。"""
        async with self._lock:
            client = self._lan_client()
            if not client["is_local"]:
                return error_response("只能在本地服务器上修改局域网访问设置", status_code=403)
            payload = await request.json(default={})
            data = self._load()
            lan = self._lan_conf(data)
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
                salt, digest = _lan_hash_password(pw)
                lan["password_hash"] = f"{salt}${digest}"
                changed["password_set"] = True
            if lan.get("enabled") and not lan.get("password_hash"):
                return error_response("开启局域网访问必须先设置访问密码", status_code=400)
            self._save(data)
            return json_response({"saved": True, **changed})

    async def web_lan_records(self):
        """读取访问记录。【仅本地】"""
        async with self._lock:
            client = self._lan_client()
            if not client["is_local"]:
                return error_response("仅本地服务器可查看访问记录", status_code=403)
            data = self._load()
            lan = self._lan_conf(data)
            return json_response({"records": list(reversed(lan.get("records") or []))})

    async def web_lan_blacklist_get(self):
        """读取黑名单（IP/CIDR 列表）。【仅本地】"""
        async with self._lock:
            client = self._lan_client()
            if not client["is_local"]:
                return error_response("仅本地服务器可管理黑名单", status_code=403)
            data = self._load()
            lan = self._lan_conf(data)
            return json_response({"blacklist": list(lan.get("blacklist") or [])})

    async def web_lan_blacklist_set(self):
        """添加/移除黑名单：POST {action: 'add'|'remove', ip}。【仅本地】"""
        async with self._lock:
            client = self._lan_client()
            if not client["is_local"]:
                return error_response("仅本地服务器可管理黑名单", status_code=403)
            payload = await request.json(default={})
            action = payload.get("action") if isinstance(payload, dict) else None
            ip = payload.get("ip") if isinstance(payload, dict) else None
            if action not in ("add", "remove") or not isinstance(ip, str) or not ip.strip():
                return error_response("参数不合法", status_code=400)
            ip = ip.strip()
            data = self._load()
            lan = self._lan_conf(data)
            blacklist = [b for b in (lan.get("blacklist") or []) if isinstance(b, str)]
            if action == "add":
                if ip not in blacklist:
                    blacklist.append(ip)
            else:
                blacklist = [b for b in blacklist if b != ip]
            lan["blacklist"] = blacklist
            self._save(data)
            return json_response({"saved": True, "blacklist": blacklist})

    def _handle_lan_url(self, event) -> str:
        """指令「管理网址」：返回局域网访问地址（仅管理员私聊机器人，且要求已开启局域网开放）。"""
        try:
            if not event.is_private_chat():
                return "请私聊机器人发送该指令获取局域网管理网址。"
        except Exception:
            pass
        data = self._load()
        lan = self._lan_conf(data)
        if not lan.get("enabled"):
            return "局域网访问尚未开启，请先在 WebUI「设置 → 局域网开放」中开启并设置密码。"
        port = 6185
        try:
            conf = self.context.astrbot_config_mgr.get_conf(None)
            dconf = getattr(conf, "dashboard", None)
            if isinstance(dconf, dict) and dconf.get("port"):
                port = int(dconf.get("port"))
            elif dconf is not None and isinstance(getattr(dconf, "get", None), type(None)):
                pass
        except Exception:
            try:
                conf = getattr(self.context, "_config", None)
                dconf = getattr(conf, "dashboard", None)
                if isinstance(dconf, dict) and dconf.get("port"):
                    port = int(dconf.get("port"))
            except Exception:
                pass
        ips = _enumerate_lan_ipv4()
        if not ips:
            return "无法获取本机局域网 IP，请检查网络连接。"
        lines = ["🌐 局域网管理网址（与本机同一局域网内的设备可访问：）"]
        for ip in ips:
            lines.append(f"http://{ip}:{port}")
        lines.append("")
        lines.append("· 本地访问 http://127.0.0.1:{} 免密".format(port))
        lines.append("· 首次从局域网设备打开后需输入管理密码")
        return "\n".join(lines)
