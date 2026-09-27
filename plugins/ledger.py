# -*- coding: utf-8 -*-
"""金币流水（3.0.0 功能插件）。自 2.3.0 modules/bank.py「金币账单」部分原样迁移：
查询流水 / 流水查询 / 消费记录；流水数据存于 data["ledger"][key]（由 core.add_coins 自动记录，最多 200 条）。
2.0.3：连续的重复内容（同时间 + 同原因 + 同金额）折叠为一条，*N 用黄色表示。
图片走图片响应模块 rich 渲染（缺失 Pillow 等渲染环境时回退纯文本，文案不变）。"""
from ..core import LEDGER_SHOW
from .image.common import DS_GOLD, DS_TEXT_2

NAME = "ledger"


def register(core):
    # ================= 金币账单 =================
    def handle_ledger(event):
        """查询流水 / 流水查询 / 消费记录：图片展示金币变动流水（只记发生金额变动的操作）。
        2.0.3：连续的重复内容（同时间 + 同原因 + 同金额）折叠为一条，*N 用黄色表示。"""
        name = core.user_name(event)
        key = core.user_key(event)
        data = core.data
        ledger = data.get("ledger", {}).get(key, [])
        if not ledger:
            return f"{name} 还没有金币流水记录（金币发生变动时才会记录）。"
        show = int(core.param("LEDGER_SHOW", LEDGER_SHOW) or 30)
        # 2.0.3：折叠连续的重复条目（时间/原因/金额均相同）→ 保留组内最新一条（首条显示）的余额 + 计数
        merged = []  # [时间, 原因, 金额, 余额, 次数]
        for rec in reversed(ledger[-show:]):
            delta = int(rec.get("delta", 0))
            ts = str(rec.get("ts", ""))[:16]
            reason = rec.get("reason", "")
            if merged and merged[-1][0] == ts and merged[-1][1] == reason and merged[-1][2] == delta:
                merged[-1][4] += 1
            else:
                merged.append([ts, reason, delta, int(rec.get("balance", 0) or 0), 1])
        # 渲染（*N 黄色 #FFC000）
        rows = []
        texts = []
        for ts, reason, delta, balance, count in merged:
            sign = "+" if delta >= 0 else ""
            base = f"{ts} {reason}{sign}{delta}"
            tail = f"（余额 {balance}）"
            texts.append(base + (f"*{count}" if count > 1 else "") + tail)
            segs = [(base, DS_TEXT_2, False)]
            if count > 1:
                segs.append((f"*{count}", DS_GOLD, False))
            segs.append((tail, DS_TEXT_2, False))
            rows.append(segs)
        img = core.image.rich(f"{name} 的金币账单（最近 {min(show, len(ledger))} 条）", rows)
        if img is not None:
            return img
        return "\n".join(texts)

    core.command("查询流水", feature="ledger")(handle_ledger)
    core.command("流水查询", feature="ledger")(handle_ledger)
    core.command("消费记录", feature="ledger")(handle_ledger)
