# -*- coding: utf-8 -*-
"""图片响应模块 · 贷款套餐一览渲染器。自 2.3.0 modules/loans.py _render_loan_packages 原样迁移（纯函数化）。
套餐信息为静态文案（2.3.0 同款）：图片包不读运行参数/核心框架，
特别贷款额度与日利率按 2.3.0 常量硬编码（2500 / 1.0%/日，与 core.LOAN_SPECIAL_AMOUNT / LOAN_SPECIAL_RATE 一致）；
自定义套餐（代码 3~10，game_items.json loans / 贷款套餐.txt）由银行贷款插件的概览逻辑维护，不在本静态清单内。"""
from .common import DS_TEXT, DS_MUTED
from .generic import rich

__all__ = ["render_loan_packages"]

_LOAN_SPECIAL_AMOUNT = 2500  # 2.3.0 同款：特别贷款固定额度（不发放金币）
_LOAN_SPECIAL_RATE = 1.0     # 2.3.0 同款：特别贷款日利率


def _loan_packages_info(data=None, key=None):
    """所有贷款套餐信息，用于「借款」无参数时的概览（2.3.0 _loan_packages_info 同款静态文案；
    签名保留 data/key 以对齐调用形状）。"""
    info = []
    info.append({
        "code": 0, "name": "特别贷款（强制解锁）",
        "max": f"{_LOAN_SPECIAL_AMOUNT}（固定，不发放金币）",
        "rate": f"{_LOAN_SPECIAL_RATE}%/日",
        "note": "贷款 2500 用于强制解锁农场+宠物，不发放金币；已开通宠物/农场不可用；30 天内还清，逾期重锁并收取仓库价值 10%",
    })
    general = " / ".join([f"{lv}级:{amt}" for lv, amt in
                          [(0, 3000), (11, 10000), (26, 20000), (51, 40000), (76, 100000)]])
    info.append({
        "code": 1, "name": "一般贷款",
        "max": f"按农场等级（农场{general}）",
        "rate": "2%~5%/日随机（宠物等级减免）",
        "note": "最近 4:00 后开始计息，15 天逾期",
    })
    short = " / ".join([f"{lv}级:{amt}" for lv, amt in
                        [(0, 1000), (3, 2000), (6, 3000), (9, 4000), (10, 6000)]])
    info.append({
        "code": 2, "name": "短期贷款",
        "max": f"按好感度等级（好感{short}）",
        "rate": "6%/日",
        "note": "10 天免息期，之后每日 6%",
    })
    return info


def render_loan_packages(data, key, info=None):
    """借款套餐一览图（2.3.0 同款；渲染环境不可用时返回 None，调用方回退文本一览）。
    info: 可选的完整套餐信息列表（含自定义套餐 3~10）；缺省用内置三项。"""
    pkgs = list(info) if info else _loan_packages_info(data, key)
    rows = []
    rows.append([("格式：借款 <套餐代码> <金额>", DS_MUTED, False)])
    rows.append([("每日累计贷款上限 = 2 × 套餐上限", DS_MUTED, False)])
    rows.append([("", (0, 0, 0), False)])
    for pkg in pkgs:
        rows.append([(f"[套餐 {pkg['code']} - {pkg['name']}]", DS_MUTED, False)])
        rows.append([(f"最大可借：{pkg['max']}", DS_TEXT, False)])
        rows.append([(f"日利率：{pkg['rate']}", DS_TEXT, False)])
        if pkg.get("note"):
            rows.append([(f"说明：{pkg['note']}", DS_MUTED, False)])
        rows.append([("", (0, 0, 0), False)])
    img = rich("借款（贷款套餐一览）", rows)
    if img is not None:
        return img
    lines = ["借款（贷款套餐一览）：", "格式：借款 <套餐代码> <金额>", "每日累计贷款上限 = 2 × 套餐上限"]
    for pkg in pkgs:
        lines.append(f"[套餐 {pkg['code']} - {pkg['name']}]")
        lines.append(f"最大可借：{pkg['max']}")
        lines.append(f"日利率：{pkg['rate']}")
        if pkg.get("note"):
            lines.append(f"说明：{pkg['note']}")
    return "\n".join(lines)
