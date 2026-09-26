# -*- coding: utf-8 -*-
# 权限管理 Mixin（2.3.0）：每个聊天（sid）的插件权限——完全允许 / 部分禁止 / 完全禁止。
# 提供 sid 构造、权限读取与鉴权门（_perm_gate，_route 唯一入口调用）+ WebUI 后端 API。
from .base import *  # noqa: F401,F403  常量与共享工具
from .base import _register_runtime_module  # noqa: F401

import sys as _sys

_register_runtime_module(_sys.modules[__name__])


class PermMixin:
    # 数据存 data.json 的 "perm"：{sid: {"mode": "allow"|"deny"|"partial", "banned": [功能key]}}
    # 未配置的 sid = 完全允许（默认），因此 "allow" 不落盘。
    PERM_MODES = ("allow", "deny", "partial")
    _PERM_DENY_REPLY = None  # 完全禁止 → 完全不响应（on_message / _route 返回 None 即静默）
    _PERM_PARTIAL_REPLY = "该功能暂时关闭"

    def _chat_sid(self, event) -> str:
        """聊天的权限 key（sid）= AstrBot 会话唯一标识 UMO（platform_id:message_type:session_id），
        与「获取UMO」登记的 key 同源；事件缺失 umo 时回退
        `平台名:group:群号` / `平台名:private:对方号`。"""
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if umo:
            return umo
        plat = ""
        for attr in ("get_platform_id", "get_platform_name"):
            fn = getattr(event, attr, None)
            if callable(fn):
                try:
                    plat = str(fn() or "")
                except Exception:
                    plat = ""
                if plat:
                    break
        if not plat:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            plat = umo.split(":", 1)[0] if umo else ""
        try:
            gid = str(event.get_group_id() or "")
        except Exception:
            gid = ""
        if gid:
            return f"{plat}:group:{gid}"
        try:
            uid = str(event.get_sender_id() or "")
        except Exception:
            uid = ""
        return f"{plat}:private:{uid}"

    def _perm_of(self, data: dict, sid: str):
        """读取 sid 的权限配置：返回 (mode, banned:set)。未配置/非法值 = 完全允许。"""
        p = (data.get("perm") or {}).get(sid)
        if not isinstance(p, dict):
            return "allow", set()
        mode = p.get("mode")
        if mode not in self.PERM_MODES:
            mode = "allow"
        banned = {str(b) for b in (p.get("banned") or []) if isinstance(b, str)}
        return mode, banned

    def _is_activity_cmd(self, head: str) -> bool:
        """head 是否为某个活动模块的自定义指令（用于权限按「活动系统」拦截）"""
        for act in (getattr(self, "_activities", None) or []):
            if head in (getattr(act, "commands", None) or {}):
                return True
        return False

    def _perm_gate(self, head: str, event):
        """每次使用插件功能前的鉴权（2.3.0）。返回 (blocked, reply)：
        - 完全允许： (False, None) 放行；
        - 完全禁止： (True, None) → 调用方返回 None 即完全不响应；
        - 部分禁止：调用了被禁功能 → (True, "该功能暂时关闭")；未映射指令仅当
          确为活动指令时按「活动系统」判定（避免对无关消息误报），其余放行。"""
        mode, banned = self._perm_of(self._load(), self._chat_sid(event))
        if mode == "deny":
            return True, self._PERM_DENY_REPLY
        if mode != "partial":
            return False, None
        mod = self.FEATURE_CMD_MAP.get(head)
        if mod is None:
            mod = "activity" if self._is_activity_cmd(head) else None
            if mod is None:
                return False, None
        if mod in banned:
            return True, self._PERM_PARTIAL_REPLY
        return False, None

    # ================= UMO 自动登记（2.3.0） =================
    _UMO_REFRESH_INTERVAL = 300  # 聊天最后活跃时间戳刷新间隔（秒），避免每条消息都改写

    def _touch_umos(self, data: dict, event) -> bool:
        """自动登记来过消息的聊天 sid（UMO）→ data["umos"]，供权限管理列表展示
        （完全禁止的聊天同样登记，保证管理员始终可见可管理）。
        返回是否有变更（调用方据此置 dirty 写盘）。"""
        sid = self._chat_sid(event)
        if not sid:
            return False
        book = data.get("umos")
        if not isinstance(book, dict):
            book = {}
            data["umos"] = book
        try:
            gid = str(event.get_group_id() or "")
        except Exception:
            gid = ""
        etype = "group" if gid else "private"
        ts = datetime.now().timestamp()
        info = book.get(sid)
        if not isinstance(info, dict):
            book[sid] = {"name": "" if gid else str(event.get_sender_name() or ""),
                         "type": etype, "platform": sid.split(":", 1)[0], "ts": ts}
            return True
        changed = False
        if not info.get("type"):
            info["type"] = etype
            changed = True
        if ts - float(info.get("ts", 0) or 0) > self._UMO_REFRESH_INTERVAL:
            info["ts"] = ts
            changed = True
        return changed

    @staticmethod
    def _sid_type(sid: str) -> str:
        """从 sid（UMO 或回退格式）推断聊天类型（group/private/未知），供列表徽章展示"""
        low = str(sid).lower()
        if ":group" in low:
            return "group"
        if ":private" in low or "friendmessage" in low:
            return "private"
        return ""

    def _platform_insts(self):
        """全部平台实例（平台 id + bot），供权限管理发现机器人所在聊天"""
        pm = getattr(self.context, "platform_manager", None)
        if pm is None:
            return []
        insts = getattr(pm, "get_insts", lambda: [])()
        return insts or getattr(pm, "platform_insts", []) or []

    async def web_get_perm_list(self):
        """权限管理：聊天列表 = 自动登记的 UMO（data["umos"]，随消息流自动增长）
        + 已配置权限但尚未登记的 sid。纯读取；「获取UMO」按钮（web_sync_umos）负责
        从平台补拉机器人所在聊天。"""
        async with self._lock:
            data = self._load()
            found = {}
            for sid, info in (data.get("umos") or {}).items():
                if isinstance(info, dict):
                    found[sid] = {"sid": sid, "name": str(info.get("name") or ""),
                                  "type": str(info.get("type") or ""),
                                  "platform": str(info.get("platform") or sid.split(":", 1)[0])}
            for sid in (data.get("perm") or {}):
                if sid not in found:
                    found[sid] = {"sid": sid, "name": "", "type": self._sid_type(sid),
                                  "platform": sid.split(":", 1)[0]}
            sids = []
            for sid, info in found.items():
                mode, banned = self._perm_of(data, sid)
                sids.append({**info, "mode": mode, "banned": sorted(banned)})
            sids.sort(key=lambda x: (x["type"], x["sid"]))
            return json_response({
                "features": [{"key": m["key"], "label": m["label"]} for m in self.FEATURE_MODULES],
                "sids": sids,
            })

    async def _discover_umos(self) -> dict:
        """从平台拉取机器人所在聊天（get_group_list / get_friend_list，仅 OneBot 类平台支持）。
        返回 {umo: {"name","type","platform"}}，key 与运行时 UMO 同格式
        （GroupMessage/FriendMessage 为 AstrBot MessageType 的取值）。"""
        found = {}

        async def _call_list(call, action):
            try:
                ret = await call(action)
            except Exception as e:
                logger.debug(f"[插件] 获取UMO {action} 失败: {e}")
                return []
            if isinstance(ret, list):
                return ret
            if isinstance(ret, dict) and isinstance(ret.get("data"), list):
                return ret["data"]
            return []

        for inst in self._platform_insts():
            bot = getattr(inst, "bot", None)
            call = getattr(bot, "call_action", None) or getattr(getattr(bot, "api", None), "call_action", None)
            pid = str(getattr(inst, "id", "") or getattr(inst, "name", "") or "")
            if call is None or not pid:
                continue
            for g in await _call_list(call, "get_group_list"):
                if isinstance(g, dict) and g.get("group_id"):
                    umo = f"{pid}:GroupMessage:{g['group_id']}"
                    found[umo] = {"name": str(g.get("group_name") or g.get("remark") or ""),
                                  "type": "group", "platform": pid}
            for f in await _call_list(call, "get_friend_list"):
                if isinstance(f, dict) and f.get("user_id"):
                    umo = f"{pid}:FriendMessage:{f['user_id']}"
                    found[umo] = {"name": str(f.get("remark") or f.get("nickname") or ""),
                                  "type": "private", "platform": pid}
        return found

    async def web_sync_umos(self):
        """手动触发「获取UMO」：从平台拉取机器人所在聊天并入 UMO 登记（补全名称/类型）。
        拉取不到（平台不支持/未连接）时保留已自动登记的聊天。"""
        found = await self._discover_umos()
        async with self._lock:
            data = self._load()
            book = data.get("umos")
            if not isinstance(book, dict):
                book = {}
            added = 0
            for umo, info in found.items():
                cur = book.get(umo)
                if not isinstance(cur, dict):
                    book[umo] = {**info, "ts": datetime.now().timestamp()}
                    added += 1
                else:
                    if info.get("name") and cur.get("name") != info["name"]:
                        cur["name"] = info["name"]
                    cur["type"] = info["type"]
                    cur["platform"] = info["platform"]
            data["umos"] = book
            self._save(data)
            logger.info(f"[插件] WebUI 获取UMO：平台返回 {len(found)} 个，新登记 {added} 个，合计 {len(book)} 个聊天")
            return json_response({"ok": True, "discovered": len(found),
                                  "new": added, "total": len(book)})

    async def web_save_perms(self):
        """保存聊天权限：{perms: {sid: {mode, banned}}}。mode=allow 的不落盘（默认状态）；
        部分禁止的 banned 仅保留合法功能 key；全部为 allow 时清除 perm 键。"""
        async with self._lock:
            payload = await request.json(default={})
            perms_in = payload.get("perms")
            if not isinstance(perms_in, dict):
                return error_response("perms 必须是对象", status_code=400)
            valid_keys = {m["key"] for m in self.FEATURE_MODULES}
            clean = {}
            for sid, p in perms_in.items():
                sid = str(sid).strip()
                if not sid or not isinstance(p, dict):
                    continue
                mode = p.get("mode")
                if mode not in self.PERM_MODES:
                    continue
                if mode == "allow":
                    continue  # 完全允许 = 默认，不存储
                banned = sorted({str(b) for b in (p.get("banned") or []) if b in valid_keys}) \
                    if mode == "partial" else []
                clean[sid] = {"mode": mode, "banned": banned}
            data = self._load()
            self._history_backup(data, "保存前自动备份")
            if clean:
                data["perm"] = clean
            else:
                data.pop("perm", None)
            self._save(data)
            logger.info(f"[插件] WebUI 保存聊天权限：{len(clean)} 个聊天")
            return json_response({"saved": True, "chats": len(clean)})
