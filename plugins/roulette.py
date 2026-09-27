# -*- coding: utf-8 -*-
"""左轮手枪（3.0.0 功能插件）。自 2.3.0 modules/roulette.py 原样迁移：
装弹 / 加入 / 开始 / 开枪 / 我的战绩 / 左轮手枪帮助。
对局为内存状态（模块级 _GAMES，群号 → 游戏）；超时/满员开局由 asyncio 任务驱动，
超时开局公告经指令插件发送辅助主动推送（渲染图片 + 定时撤回）。
战绩存于 data["roulette"]；金币变动一律经 core.add_coins（自动记流水 + 贷款联动）。"""
import asyncio
import random

from astrbot.api import logger

from ..core import (ROULETTE_MAGAZINES, ROULETTE_MAX_BULLETS, ROULETTE_MIN_PLAYERS,
                    ROULETTE_MAX_PLAYERS, ROULETTE_JOIN_TIMEOUT, ROULETTE_FEE_RATE,
                    RECALL_AFTER)

NAME = "roulette"

# 一局游戏 = {群号: RouletteGame}（与 2.3.0 self._games 一致的内存状态）
_GAMES = {}

_CORE = None  # register(core) 时注入的核心框架实例（模块级辅助函数共用）


class RouletteGame:
    """一局左轮手枪游戏的内存状态"""

    def __init__(self, group_id, starter_id, starter_name, bullets, stake):
        self.group_id = group_id
        self.bullets = bullets
        self.stake = stake
        self.players = [{"id": starter_id, "name": starter_name}]
        self.status = "waiting"      # waiting -> playing -> finished
        self.magazines = []          # bool 列表，True 表示该弹匣有子弹
        self.shot_index = 0          # 下一个要用的弹匣下标
        self.turn_index = 0          # 当前回合玩家下标
        self.event = None            # 发起时的 AstrMessageEvent，供定时器主动发消息
        self.timer_task = None       # 30 秒超时任务

    @property
    def starter_id(self):
        return self.players[0]["id"]

    def player_names(self):
        return "、".join(p["name"] for p in self.players)


# ================= 战绩（data["roulette"]，原 _ensure_stat / _record 改为模块级辅助） =================
def _ensure_stat(data, key):
    return data.setdefault("roulette", {}).setdefault(key, {
        "wins": 0, "losses": 0, "net": 0, "lost_to": {}, "won_from": {},
    })


def _record(data, key, side, opp_id, opp_name, amount):
    stat = _ensure_stat(data, key)
    bucket = stat.setdefault(side, {})
    entry = bucket.setdefault(opp_id, {"name": opp_name, "amount": 0})
    entry["amount"] += amount
    entry["name"] = opp_name


# ================= 对局流程 =================
def _start_game(game) -> None:
    if game.timer_task and game.timer_task is not asyncio.current_task():
        game.timer_task.cancel()
    game.timer_task = None
    game.status = "playing"
    magazines = int(_CORE.param("ROULETTE_MAGAZINES", ROULETTE_MAGAZINES)) if _CORE else ROULETTE_MAGAZINES
    game.magazines = [False] * magazines
    for idx in random.sample(range(magazines), game.bullets):
        game.magazines[idx] = True
    game.shot_index = 0
    game.turn_index = 0


def _start_announcement(game) -> str:
    cur = game.players[0]
    magazines = int(_CORE.param("ROULETTE_MAGAZINES", ROULETTE_MAGAZINES)) if _CORE else ROULETTE_MAGAZINES
    return (f"🔫 左轮手枪游戏开始！\n"
            f"👥 玩家：{game.player_names()}\n"
            f"🔹 子弹：{game.bullets} 发（共 {magazines} 个弹匣，随机排列）\n"
            f"💰 赌注：{game.stake} 金币/人\n"
            f"🎯 当前回合：{cur['name']}（发送「开枪」）")


def _finish_game(data: dict, game: RouletteGame, loser_index: int) -> str:
    loser = game.players[loser_index]
    winners = [p for i, p in enumerate(game.players) if i != loser_index]

    loser_key = loser["id"]
    actual_loss = min(game.stake, _CORE.coins_of(loser_key))
    _CORE.add_coins(loser_key, -actual_loss, "左轮手枪·判负")

    # 双人局手续费 10%，三人局手续费 5%
    fee_rate = 0.05 if len(game.players) == 3 else float(_CORE.param("ROULETTE_FEE_RATE", ROULETTE_FEE_RATE))
    payout_total = int(actual_loss * (1 - fee_rate))
    share = payout_total // len(winners)

    loser_stat = _ensure_stat(data, loser_key)
    loser_stat["losses"] += 1
    loser_stat["net"] -= actual_loss

    lines = [f"💥 {loser['name']} 开枪：第 {game.shot_index + 1} 个弹匣——砰！有子弹！"]
    lines.append(f"😵 {loser['name']} 判负，扣除 {actual_loss} 金币。")

    winner_names = []
    for w in winners:
        wkey = w["id"]
        _CORE.add_coins(wkey, share, "左轮手枪·获胜")
        wstat = _ensure_stat(data, wkey)
        wstat["wins"] += 1
        wstat["net"] += share
        _record(data, wkey, "won_from", loser["id"], loser["name"], share)
        _record(data, loser_key, "lost_to", w["id"], w["name"], share)
        winner_names.append(w["name"])

    if len(winner_names) == 1:
        lines.append(f"🏆 {winner_names[0]} 获得 {share} 金币（已扣除 {int(fee_rate * 100)}% 手续费）。")
    else:
        lines.append(f"🏆 {('、'.join(winner_names))} 各获得 {share} 金币（已扣除 {int(fee_rate * 100)}% 手续费）。")
    return "\n".join(lines)


async def _proactive_send(event, title, text):
    """后台定时器主动发消息：优先渲染成图片，失败回退文本；发送后 RECALL_AFTER 秒撤回。
    （2.3.0 行为：超时开局公告必须主动推送；发送/撤回复用指令插件的底层辅助）"""
    img = _CORE.image.text(title, text.splitlines()) if _CORE is not None else None
    try:
        from ..modules_send import _send_with_mid, recall_message_later
        chain = event.image_result(img[1]) if img is not None else event.plain_result(text)
        mid = await _send_with_mid(event, chain)
        if mid:
            after = float(_CORE.param("RECALL_AFTER", RECALL_AFTER))
            asyncio.create_task(recall_message_later(event, mid, after))
    except Exception as e:
        logger.error(f"[roulette] 主动消息发送失败: {e}")


def _schedule_timeout(game) -> None:
    timeout = int(_CORE.param("ROULETTE_JOIN_TIMEOUT", ROULETTE_JOIN_TIMEOUT))
    min_players = int(_CORE.param("ROULETTE_MIN_PLAYERS", ROULETTE_MIN_PLAYERS))

    async def _timeout():
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        async with _CORE.lock:
            g = _GAMES.get(game.group_id)
            if g is not game or g.status != "waiting":
                return
            if len(g.players) >= min_players:
                _start_game(g)
                text = _start_announcement(g)
            else:
                _GAMES.pop(g.group_id, None)
                text = f"⏰ {timeout} 秒超时，无人加入，游戏已结束，无事发生。"
        await _proactive_send(game.event, "左轮手枪", text)

    game.timer_task = asyncio.create_task(_timeout())


# ================= 指令处理器（register 内闭包，core 就地捕获） =================
def register(core):
    global _CORE
    _CORE = core

    def handle_load(event):
        gid = event.get_group_id()
        if not gid:
            return "左轮手枪游戏只能在群里玩哦～"
        if _GAMES.get(gid):
            g = _GAMES[gid]
            return f"本群已有一局左轮手枪游戏进行中（{g.player_names()}），请等它结束。"

        uid = core.user_key(event)
        name = core.user_name(event)
        parts = event.message_str.split()
        max_bullets = int(core.param("ROULETTE_MAX_BULLETS", ROULETTE_MAX_BULLETS))
        if len(parts) != 3:
            return f"格式：装弹 <子弹数量 1~{max_bullets}> <金币>，例如：装弹 3 100"

        try:
            bullets = int(parts[1])
            stake = int(parts[2])
        except ValueError:
            return f"子弹数量和金币必须是整数。格式：装弹 <子弹数量 1~{max_bullets}> <金币>"

        if not (1 <= bullets <= max_bullets):
            return f"子弹数量必须在 1~{max_bullets} 之间。"
        if stake < 1:
            return "金币必须为正整数。"

        if core.coins_of(uid) < stake:
            return f"你的金币不足（当前 {core.coins_of(uid)}，需要 {stake}）。"

        game = RouletteGame(gid, uid, name, bullets, stake)
        game.event = event
        _GAMES[gid] = game
        _schedule_timeout(game)

        magazines = int(core.param("ROULETTE_MAGAZINES", ROULETTE_MAGAZINES))
        min_players = int(core.param("ROULETTE_MIN_PLAYERS", ROULETTE_MIN_PLAYERS))
        max_players = int(core.param("ROULETTE_MAX_PLAYERS", ROULETTE_MAX_PLAYERS))
        join_timeout = int(core.param("ROULETTE_JOIN_TIMEOUT", ROULETTE_JOIN_TIMEOUT))
        return (f"🔫 {name} 发起了一局左轮手枪游戏！\n"
                f"🔹 子弹：{bullets} 发（共 {magazines} 个弹匣，随机排列）\n"
                f"💰 赌注：{stake} 金币/人\n"
                f"👥 发送「加入」参与（至少 {min_players} 人，最多 {max_players} 人）\n"
                f"⏳ 超时时间：{join_timeout} 秒。超时后无人加入则游戏结束，无事发生。")

    def handle_join(event):
        gid = event.get_group_id()
        if not gid:
            return "左轮手枪游戏只能在群里玩哦～"
        uid = core.user_key(event)
        name = core.user_name(event)
        game = _GAMES.get(gid)
        if not game or game.status != "waiting":
            return "当前没有正在等待加入的左轮手枪游戏。"
        if any(p["id"] == uid for p in game.players):
            return f"{name}，你已经在这局游戏里了。"
        max_players = int(core.param("ROULETTE_MAX_PLAYERS", ROULETTE_MAX_PLAYERS))
        if len(game.players) >= max_players:
            return "本局人数已满。"

        if core.coins_of(uid) < game.stake:
            return f"{name} 金币不足，无法加入（需要 {game.stake} 金币）。"

        game.players.append({"id": uid, "name": name})
        if len(game.players) >= max_players:
            _start_game(game)
            return _start_announcement(game)

        return (f"✅ {name} 加入了游戏！\n"
                f"👥 当前玩家（{len(game.players)}/{max_players}）：{game.player_names()}\n"
                f"发起人可发送「开始」立即开始，或等待更多玩家加入。")

    def handle_start(event):
        gid = event.get_group_id()
        uid = core.user_key(event)
        game = _GAMES.get(gid)
        if not game or game.status != "waiting":
            return "当前没有待开始的左轮手枪游戏。"
        if uid != game.starter_id:
            return "只有发起人可以开始游戏。"
        min_players = int(core.param("ROULETTE_MIN_PLAYERS", ROULETTE_MIN_PLAYERS))
        if len(game.players) < min_players:
            return f"至少需要 {min_players} 名玩家才能开始。"
        _start_game(game)
        return _start_announcement(game)

    def handle_shoot(event):
        gid = event.get_group_id()
        uid = core.user_key(event)
        name = core.user_name(event)
        game = _GAMES.get(gid)
        if not game:
            return "当前没有进行中的左轮手枪游戏。"
        if game.status != "playing":
            return "游戏还没开始，等待玩家加入或发送「开始」。"

        cur = game.players[game.turn_index]
        if cur["id"] != uid:
            return f"还没轮到你开枪，当前是 {cur['name']} 的回合。"

        shot_num = game.shot_index + 1
        if not game.magazines[game.shot_index]:
            game.shot_index += 1
            game.turn_index = (game.turn_index + 1) % len(game.players)
            nxt = game.players[game.turn_index]
            return (f"🔫 {name} 开枪：第 {shot_num} 个弹匣——咔嚓，是空膛！\n"
                    f"🎯 轮到 {nxt['name']}（发送「开枪」）")

        data = core.data
        text = _finish_game(data, game, game.turn_index)
        core.save()
        _GAMES.pop(gid, None)
        return text

    def handle_stats(event):
        name = core.user_name(event)
        key = core.user_key(event)
        data = core.data
        stat = data.get("roulette", {}).get(key)

        if not stat or (stat.get("wins", 0) + stat.get("losses", 0)) == 0:
            return (f"{name} 还没有左轮手枪战绩，\n"
                    f"发送「装弹 <子弹数量 1~6> <金币>」开局吧～")

        wins = stat.get("wins", 0)
        losses = stat.get("losses", 0)
        total = wins + losses
        rate = wins / total * 100
        net = stat.get("net", 0)
        net_str = f"+{net}" if net >= 0 else str(net)

        lines = [
            f"📊 {name} 的左轮手枪战绩：",
            f"🎮 局数：{total}（胜 {wins} / 负 {losses}）",
            f"📈 胜率：{rate:.1f}%",
            f"💰 净收益：{net_str} 金币",
        ]
        lost_to = stat.get("lost_to", {})
        won_from = stat.get("won_from", {})
        if lost_to:
            worst = max(lost_to.items(), key=lambda kv: kv[1]["amount"])
            lines.append(f"👊 拿走你最多金币的人：{worst[1]['name']}（{worst[1]['amount']} 金币）")
        else:
            lines.append("👊 拿走你最多金币的人：无")
        if won_from:
            best = max(won_from.items(), key=lambda kv: kv[1]["amount"])
            lines.append(f"💸 你拿走最多金币的人：{best[1]['name']}（{best[1]['amount']} 金币）")
        else:
            lines.append("💸 你拿走最多金币的人：无")
        return "\n".join(lines)

    def handle_help(event):
        sections = [
            ("左轮手枪", [
                ("装弹 <子弹数 1~6> <金币>", "发起一局游戏"),
                ("加入", "加入当前这局游戏"),
                ("开始", "发起人提前开始（≥2 人）"),
                ("开枪", "轮到你的回合时开枪"),
                ("我的战绩", "查看胜率 / 净收益 / 金币往来"),
            ]),
        ]
        return core.image.build_help("左轮手枪帮助", sections)

    core.command("装弹", feature="roulette")(handle_load)
    core.command("加入", feature="roulette")(handle_join)
    core.command("开始", feature="roulette")(handle_start)
    core.command("开枪", feature="roulette")(handle_shoot)
    core.command("我的战绩", feature="roulette")(handle_stats)
    core.command("左轮手枪帮助", feature="roulette")(handle_help)

    core.add_help("左轮手枪", [
        ("装弹 <子弹数 1~6> <金币>", "发起一局游戏"),
        ("加入", "加入当前这局游戏"),
        ("开始", "发起人提前开始（≥2 人）"),
        ("开枪", "轮到你的回合时开枪"),
        ("我的战绩", "查看胜率 / 净收益 / 金币往来"),
    ])
