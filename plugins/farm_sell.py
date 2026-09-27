# -*- coding: utf-8 -*-
"""农场售卖（3.0.0 功能插件）。自 2.3.0 modules/farm.py 售卖分支原样迁移：
售卖 [作物] [数量]（2.2.0：优先售卖仓库内的作物；仅当仓库没有任何作物时触发
「先收割后售卖」机制——收割全部成熟作物入库后直接售出；不填=卖出全部仓库作物）、
售卖种子 [种子] [数量]（不填=卖出全部仓库种子；无先收割机制）。
作物/种子价格取自 crops 配置（产量加成在收割时已计入数量，售卖按配置单价结算）；
收割成熟作物经 farm 服务 harvest_mature（None-safe，服务缺失时视为无可收割），
农场经验结算经 farm 服务 add_exp，农场记录读写经 farm 服务 farm_of（缺失时直接操作
core.data["farms"]，结构不变：warehouse{crops,seeds,fertilizers}/tools）。
图片回复经 core.image.render_plot_status（缺失/异常回退 2.3.0 同款文本）；
回复文本与 2.3.0 逐字一致。

其它插件经 core.service("farm_sell") 调用：
  - sell_crops(key, name, qty)   卖出仓库作物（name=作物名，None/""=全部；
                                 仓库无作物时先收割后售卖），返回回复
  - sell_seeds(key, name, qty)   卖出仓库种子（name=种子名，None/""=全部），返回回复
"""
from astrbot.api import logger

from .. import core as _core_mod
from ..core import FARM_UNLOCK_COST, FARM_MAX_LEVEL, FARM_EXP_BASE

NAME = "farm_sell"

_CORE = None  # register(core) 时注入的核心框架实例


# ================= 作物 / 肥料配置（game_items.json，缺省回退旧版 txt） =================
def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _norm_crop_entry(d):
    """规范化一条作物配置（扁平 dict，键与 2.3.0 _parse_crop_fert 输出一致）"""
    if not isinstance(d, dict):
        return None
    name = str(d.get("name", "")).strip()
    if not name:
        return None
    return {
        "name": name,
        "desc": str(d.get("desc", "") or ""),
        "seed_price": _f(d.get("seed_price", 0)),
        "seed_sell_price": _f(d.get("seed_sell_price", 0)),
        "yield": int(_f(d.get("yield", 0))),
        "crop_price": _f(d.get("crop_price", 0)),
        "exp": int(_f(d.get("exp", 0))),
        "min_level": int(_f(d.get("min_level", 0))),
        "grow_minutes": int(_f(d.get("grow_minutes", 0))),
    }


def _norm_fert_entry(d):
    """规范化一条肥料配置（2.0.0：max_accel 为可加速次数，替代 max_uses）"""
    if not isinstance(d, dict):
        return None
    name = str(d.get("name", "")).strip()
    if not name:
        return None
    max_accel = d.get("max_accel", d.get("max_uses", -1))
    try:
        max_accel = int(float(max_accel))
    except (TypeError, ValueError):
        max_accel = -1
    return {
        "name": name,
        "desc": str(d.get("desc", "") or ""),
        "price": int(_f(d.get("price", 0))),
        "time_reduce": _f(d.get("time_reduce", 0)),
        "yield_add": _f(d.get("yield_add", 0)),
        "max_uses": int(_f(d.get("max_uses", -1))),
        "max_accel": max_accel,
    }


def _load_crops():
    """作物配置：core.items()["crops"]（game_items.json）；为空时回退解析 作物.txt"""
    flat = _CORE.items() or {}
    crops = [c for c in (_norm_crop_entry(d) for d in (flat.get("crops") or [])) if c]
    if crops:
        return crops
    return _parse_crop_fert(_core_mod.CROP_FILE, "作物")


def _load_fertilizers():
    """肥料配置：core.items()["ferts"]（game_items.json）；为空时回退解析 肥料.txt"""
    flat = _CORE.items() or {}
    ferts = [f_ for f_ in (_norm_fert_entry(d) for d in (flat.get("ferts") or [])) if f_]
    if ferts:
        return ferts
    return _parse_crop_fert(_core_mod.FERT_FILE, "肥料")


def _parse_crop_fert(path, kind):
    """旧版 txt 配置回退解析（[类型:名称]+key=value）"""
    items = _CORE.parse_kv_sections(path, kind)
    result = []
    for it in items:
        d = it["data"]
        if kind == "作物":
            result.append({
                "name": it["name"],
                "desc": d.get("描述", ""),
                "seed_price": _f(d.get("种子价格", 0)),
                "seed_sell_price": _f(d.get("种子卖出价格", 0)),
                "yield": int(_f(d.get("产量", 0))),
                "crop_price": _f(d.get("成熟作物价格", 0)),
                "exp": int(_f(d.get("收获经验值", 0))),
                "min_level": int(_f(d.get("最低农场等级要求", 0))),
                "grow_minutes": int(_f(d.get("成熟时间", 0))),
            })
        else:
            result.append({
                "name": it["name"],
                "desc": d.get("描述", ""),
                "price": int(_f(d.get("肥料价格", 0))),
                "time_reduce": _f(d.get("减少时间", 0)),
                "yield_add": _f(d.get("增加产量", 0)),
                "max_uses": int(_f(d.get("最大使用次数", -1))),
                "max_accel": int(_f(d.get("可加速次数", d.get("最大使用次数", -1)))),
            })
    return result


def _find_item(items, name):
    return next((x for x in items if x["name"] == name), None)


def _fmt_price(v):
    """价格显示：整数不带小数点，小数保留最多 2 位并去掉末尾 0"""
    v = float(v)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _fmt_plot_nums(nums):
    """格式化地块编号列表（1-based）：连续 → 编号 1~8；不连续 → 编号 1、3、5；单块 → 编号 3"""
    nums = sorted(set(int(n) for n in nums))
    if not nums:
        return "编号 无"
    if len(nums) == 1:
        return f"编号 {nums[0]}"
    if all(nums[j + 1] - nums[j] == 1 for j in range(len(nums) - 1)):
        return f"编号 {nums[0]}~{nums[-1]}"
    return "编号 " + "、".join(str(n) for n in nums)


# ================= farm 服务复用（None-safe，缺失时本地同款实现） =================
def _farm_api():
    try:
        return _CORE.service("farm")
    except Exception:
        return None


def _farm_of(key):
    """取用户农场 dict；无农场返回 None（优先 farm 服务）"""
    api = _farm_api()
    fn = getattr(api, "farm_of", None) if api is not None else None
    if fn is not None:
        try:
            return fn(key)
        except Exception:
            pass
    return _CORE.data.get("farms", {}).get(key)


def _farm_need(key, name):
    if not _farm_of(key):
        return f"{name} 还没有农场，发送「解锁农场」（需 {FARM_UNLOCK_COST} 金币）解锁。"
    return None


def _farm_gain_exp(farm, amount) -> str:
    """农场加经验（farm 服务缺失时的本地同款实现）；调用方负责 core.save()"""
    if int(farm.get("level", 0)) >= FARM_MAX_LEVEL:
        return ""
    farm["exp"] = float(farm.get("exp", 0.0)) + amount
    level = int(farm.get("level", 0))
    old = level
    while level < FARM_MAX_LEVEL:
        need = FARM_EXP_BASE * (level + 1)
        if farm["exp"] >= need:
            farm["exp"] = round(farm["exp"] - need, 2)
            level += 1
        else:
            break
    farm["level"] = level
    if level > old:
        return f"\n🎉 农场升级！Lv.{old} → Lv.{level}"
    return ""


def _gain_exp(key, farm, amount):
    """农场加经验（优先 farm 服务 add_exp，自动存盘）。返回升级提示行（无升级为 ""）"""
    api = _farm_api()
    fn = getattr(api, "add_exp", None) if api is not None else None
    if fn is not None:
        try:
            return fn(key, amount)
        except Exception:
            pass
    return _farm_gain_exp(farm, amount)


def _harvest(key):
    """收割全部成熟作物入库（优先 farm 服务 harvest_mature，自动存盘）。
    返回 (harvested 编号列表[1-based], {作物:数量}, 总经验)；服务缺失/无可收割 → ([], {}, 0)"""
    api = _farm_api()
    fn = getattr(api, "harvest_mature", None) if api is not None else None
    if fn is not None:
        try:
            return fn(key)
        except Exception as e:
            logger.error(f"[farm_sell] 收割服务调用异常: {e}")
    return [], {}, 0


def _user_display_name(key):
    """服务调用无 event，按数据解析用户显示名：自定义昵称 → 用户记录 name/nickname → key"""
    custom = _CORE.custom_name_of(key)
    if custom:
        return custom
    u = (_CORE.data.get("users") or {}).get(key) or {}
    return u.get("name") or u.get("nickname") or str(key)


# ================= 图片回复（土地状态；渲染器缺失/异常回退文本） =================
def _is_image(img):
    return isinstance(img, tuple) and len(img) >= 2 and img[0] == "image"


def _plot_status_reply(name, key, farm, crops=None, ferts=None, steal_lines=None,
                       highlights=None, actions=None, profit_delta=0, coins_delta=None):
    """土地状态图片回复（纯图片）；render_plot_status 缺失/异常时回退 2.3.0 同款文本。"""
    if crops is None:
        crops = _load_crops()
    if ferts is None:
        ferts = _load_fertilizers()
    render = getattr(_CORE.image, "render_plot_status", None)
    if render is not None:
        try:
            img = render(name, key, _CORE.data, farm, crops, ferts,
                         steal_lines=steal_lines, highlights=highlights,
                         actions=actions, profit_delta=profit_delta,
                         coins_delta=coins_delta)
        except Exception as e:
            logger.error(f"[farm_sell] 渲染土地状态图片异常: {e}")
            img = None
        if _is_image(img):
            return img
    if actions:
        return "\n".join(str(a) for a in actions)
    return "图片生成失败（缺少 Pillow 或字体），请查看日志。"


# ================= 售卖作物 =================
def _sell_crops_core(key, name, crop_name=None, cnt=None):
    """售卖核心逻辑（2.2.0）：优先售卖仓库内的作物；仅当仓库没有任何作物时，
    触发「先收割后售卖」机制——收割全部成熟作物入库后直接售出。
    crop_name=None = 卖出全部仓库作物；cnt=None = 全部数量。"""
    err = _farm_need(key, name)
    if err:
        return err
    farm = _farm_of(key)
    crops = _load_crops()
    wh = farm["warehouse"].setdefault("crops", {})
    ferts = _load_fertilizers()

    # ---- 不填作物：卖出全部仓库作物；仓库无作物 → 先收割后售卖 ----
    if crop_name is None:
        if wh:
            n_kinds = len(wh)
            total = 0
            for nm, cnt_ in list(wh.items()):
                c = _find_item(crops, nm)
                total += int(round(int(cnt_) * (float(c["crop_price"]) if c else 0.0)))
            wh.clear()
            _CORE.add_coins(key, total, "售卖作物")
            farm["total_profit"] = int(farm.get("total_profit", 0)) + total
            _CORE.save()
            # 2.0.0 纯图片回复：卖出全部作物 + 底部大卡片
            actions = [f"💼 卖出全部作物（共 {n_kinds} 种），获得 +{total} 金币"]
            return _plot_status_reply(
                name, key, farm, crops=crops, ferts=ferts,
                profit_delta=total,
                actions=actions,
                coins_delta=total)
        # 仓库无作物 → 先收割后售卖（卖出全部收获）
        return _harvest_and_sell(key, name, farm, crops, ferts)

    # ---- 指定作物 ----
    if crop_name in wh:
        # 仓库有该作物 → 直接卖仓库
        c = _find_item(crops, crop_name)
        price = float(c["crop_price"]) if c else 0.0
        have = int(wh[crop_name])
        if cnt is None:
            cnt = have
        if cnt > have:
            return f"{crop_name} 只有 {have} 个。"
        gain = int(round(cnt * price))
        _CORE.add_coins(key, gain, f"售卖{crop_name}")
        farm["total_profit"] = int(farm.get("total_profit", 0)) + gain
        if cnt >= have:
            wh.pop(crop_name, None)
        else:
            wh[crop_name] = have - cnt
        _CORE.save()
        # 2.0.0 纯图片回复：卖出作物 + 底部大卡片
        actions = [f"💼 卖出 {crop_name} ×{cnt}（单价 {_fmt_price(price)} 金币），获得 +{gain} 金币"]
        return _plot_status_reply(
            name, key, farm, crops=crops, ferts=ferts,
            profit_delta=gain,
            actions=actions,
            coins_delta=gain)
    if wh:
        # 仓库有其它作物但没有指定作物 → 仓库非空，不触发收割
        return f"仓库里没有「{crop_name}」。"
    # 仓库没有任何作物 → 先收割后售卖指定作物
    return _harvest_and_sell(key, name, farm, crops, ferts, crop_name, cnt)


def _harvest_and_sell(key, name, farm, crops, ferts, crop_name=None, cnt=None):
    """2.2.0「售卖」仓库无作物时的先收割后售卖机制：
    收割全部成熟作物入库，然后售出（crop_name=None = 卖出全部收获；否则卖出指定作物）。
    返回土地状态图片（收割红高亮 + 底部大卡片）。"""
    harvested, amounts, total_exp = _harvest(key)
    if not harvested:
        if crop_name:
            return f"仓库里没有「{crop_name}」，且没有可收割的成熟作物。"
        return "仓库里没有作物，且没有可收割的成熟作物。"
    lvl_msg = _gain_exp(key, farm, total_exp)
    wh = farm["warehouse"].setdefault("crops", {})
    if crop_name is None:
        # 卖出全部收获（仓库在本机制触发前为空，本次收获全部售出）
        total = 0
        for nm, cnt2 in amounts.items():
            c = _find_item(crops, nm)
            total += int(round(int(cnt2) * (float(c["crop_price"]) if c else 0.0)))
            wh.pop(nm, None)
        _CORE.add_coins(key, total, "售卖作物")
        farm["total_profit"] = int(farm.get("total_profit", 0)) + total
        _CORE.save()
        actions = [f"🌾 仓库无作物，先收割 {len(harvested)} 块地（{_fmt_plot_nums(harvested)}），农场经验 +{total_exp}{lvl_msg}",
                   f"💼 收割后直接售出全部收获，获得 +{total} 金币"]
        return _plot_status_reply(
            name, key, farm, crops=crops, ferts=ferts,
            highlights={n: "harvest" for n in harvested},
            profit_delta=total,
            actions=actions,
            coins_delta=total)
    # 卖出指定作物（收割所得；其余收获继续留在仓库）
    c = _find_item(crops, crop_name)
    price = float(c["crop_price"]) if c else 0.0
    have = int(amounts.get(crop_name, 0))
    if have <= 0:
        # 收割了但没有这种作物 → 入库留存，提示用「售卖」卖出全部
        _CORE.save()
        actions = [f"🌾 仓库无作物，先收割 {len(harvested)} 块地（{_fmt_plot_nums(harvested)}），农场经验 +{total_exp}{lvl_msg}",
                   f"📦 收获已存入仓库，但本次没有「{crop_name}」可卖（发送「售卖」可卖出全部）"]
        return _plot_status_reply(
            name, key, farm, crops=crops, ferts=ferts,
            highlights={n: "harvest" for n in harvested},
            actions=actions)
    sell_cnt = have if cnt is None else min(cnt, have)
    gain = int(round(sell_cnt * price))
    _CORE.add_coins(key, gain, f"售卖{crop_name}")
    farm["total_profit"] = int(farm.get("total_profit", 0)) + gain
    if sell_cnt >= have:
        wh.pop(crop_name, None)
    else:
        wh[crop_name] = have - sell_cnt
    _CORE.save()
    actions = [f"🌾 仓库无作物，先收割 {len(harvested)} 块地（{_fmt_plot_nums(harvested)}），农场经验 +{total_exp}{lvl_msg}",
               f"💼 收割后卖出 {crop_name} ×{sell_cnt}（单价 {_fmt_price(price)} 金币），获得 +{gain} 金币"]
    return _plot_status_reply(
        name, key, farm, crops=crops, ferts=ferts,
        highlights={n: "harvest" for n in harvested},
        profit_delta=gain,
        actions=actions,
        coins_delta=gain)


def _handle_sell(event):
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    parts = event.message_str.split(maxsplit=1)
    crop_name = None
    cnt = None
    if len(parts) >= 2 and parts[1].strip():
        args = parts[1].split()
        crop_name = args[0]
        if len(args) >= 2:
            try:
                cnt = int(args[1])
            except ValueError:
                return "数量必须是整数。"
            if cnt <= 0:
                return "数量必须为正整数。"
    return _sell_crops_core(key, name, crop_name, cnt)


# ================= 售卖种子 =================
def _sell_seeds_core(key, name, seed_name=None, cnt=None):
    """售卖仓库种子。cnt 可为 int（服务调用）、原始数字串（指令参数）或 None（全部）；
    校验顺序与 2.3.0 一致：先查仓库有无该种子，再解析/校验数量。"""
    err = _farm_need(key, name)
    if err:
        return err
    farm = _farm_of(key)
    crops = _load_crops()
    wh = farm["warehouse"].setdefault("seeds", {})

    if seed_name is None:
        if not wh:
            return "仓库里没有种子。"
        n_kinds = len(wh)
        total = 0
        for nm, cnt_ in list(wh.items()):
            c = _find_item(crops, nm)
            total += int(round(int(cnt_) * (float(c["seed_sell_price"]) if c else 0.0)))
        wh.clear()
        _CORE.add_coins(key, total, "售卖种子")
        _CORE.save()
        # 2.0.0 纯图片回复：卖出全部种子 + 底部大卡片
        actions = [f"💼 卖出全部种子（共 {n_kinds} 种），获得 +{total} 金币"]
        return _plot_status_reply(
            name, key, farm, crops=crops, ferts=_load_fertilizers(),
            profit_delta=total,
            actions=actions,
            coins_delta=total)
    if seed_name not in wh:
        return f"仓库里没有「{seed_name}」种子。"
    c = _find_item(crops, seed_name)
    price = float(c["seed_sell_price"]) if c else 0.0
    have = int(wh[seed_name])
    if cnt is not None:
        if isinstance(cnt, str):
            try:
                cnt = int(cnt)
            except ValueError:
                return "数量必须是整数。"
            if cnt <= 0:
                return "数量必须为正整数。"
        if cnt > have:
            return f"{seed_name} 种子只有 {have} 个。"
    else:
        cnt = have
    gain = int(round(cnt * price))
    _CORE.add_coins(key, gain, f"售卖种子·{seed_name}")
    if cnt >= have:
        wh.pop(seed_name, None)
    else:
        wh[seed_name] = have - cnt
    _CORE.save()
    # 2.0.0 纯图片回复：卖出种子 + 底部大卡片
    actions = [f"💼 卖出 {seed_name} 种子 ×{cnt}（单价 {_fmt_price(price)} 金币），获得 +{gain} 金币"]
    return _plot_status_reply(
        name, key, farm, crops=crops, ferts=_load_fertilizers(),
        profit_delta=gain,
        actions=actions,
        coins_delta=gain)


def _handle_sell_seed(event):
    name = _CORE.user_name(event)
    key = _CORE.user_key(event)
    parts = event.message_str.split(maxsplit=1)
    seed_name = None
    cnt_raw = None
    if len(parts) >= 2 and parts[1].strip():
        args = parts[1].split()
        seed_name = args[0]
        if len(args) >= 2:
            cnt_raw = args[1]
    # 数量解析延后到 _sell_seeds_core（与 2.3.0 一致：先查仓库再校验数量）
    return _sell_seeds_core(key, name, seed_name, cnt_raw)


# ================= 挂载入口 =================
def register(core):
    global _CORE
    _CORE = core

    core.command("售卖", feature="farm_sell")(_handle_sell)
    core.command("售卖种子", feature="farm_sell")(_handle_sell_seed)

    core.add_help("农场售卖", [
        ("售卖 [作物] [数量]", "卖出仓库作物（不填=全部；仓库无作物时先收割成熟再售卖）"),
        ("售卖种子 [种子] [数量]", "卖出仓库种子（不填=全部）"),
    ])

    # ================= 服务暴露（其他插件经 core.service("farm_sell") 调用） =================
    class FarmSellApi:
        """农场售卖服务：仓库作物/种子卖出（供自动化等插件联动）"""

        def sell_crops(self, key, name, qty=None):
            """卖出仓库作物：name=作物名（None/""=全部仓库作物；仓库无作物时先收割后售卖），
            qty=数量（None=全部）。返回回复（2.3.0 同款）。"""
            crop_name = str(name).strip() if name is not None else None
            if crop_name == "":
                crop_name = None
            cnt = None if qty is None else int(qty)
            return _sell_crops_core(key, _user_display_name(key), crop_name, cnt)

        def sell_seeds(self, key, name, qty=None):
            """卖出仓库种子：name=种子名（None/""=全部仓库种子），qty=数量（None=全部）。
            返回回复（2.3.0 同款）。"""
            seed_name = str(name).strip() if name is not None else None
            if seed_name == "":
                seed_name = None
            cnt = None if qty is None else int(qty)
            return _sell_seeds_core(key, _user_display_name(key), seed_name, cnt)

    core.expose("farm_sell", FarmSellApi())
