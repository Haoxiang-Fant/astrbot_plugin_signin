# 接口描述文件（3.0.0 插件通信规范）

> 万物皆插件。每个插件只负责自己的数据处理；跨插件能力一律通过核心框架的接口完成。
> 本文件是第一方/第三方插件开发与维护的唯一接口契约。

## 1. 架构总览

```
AstrBot 平台
   │ 消息事件
   ▼
指令插件（main.py）── 消息接入 / 发送 / 撤回 / 文本转图片调度
   ▼
核心框架（core.py）── 挂载插件 / 指令处理 / 用户识别 / 用户鉴权 / 金币变化 / 数据管理 / 插件通信 / 定时任务
   ▼                        ▼
功能插件（plugins/*.py）  图片响应模块（plugins/image/）── 统一图片输出
```

- **核心框架**自我处理：用户识别、金币变化、用户鉴权、指令处理、插件调用；其余数据流转发到对应插件。
- **功能插件**只处理自己的业务数据；需要别人的数据/能力时走 `core.service()` 或 `core.on/emit`。
- **图片响应模块**接收各插件的渲染请求，统一渲染与存图；图片格式（版式内容）由发起插件定义。

## 2. 插件模块契约

每个功能插件是 `plugins/` 下一个 Python 模块，**必须**提供：

```python
NAME = "pet"                    # 插件名（唯一）

def register(core):
    """挂载入口：注册指令、服务、事件订阅、定时任务。"""
    @core.command("宠物", feature="pet")
    def handle_pet(event): ...

    core.expose("pet", PetApi(core))     # 向其他插件暴露接口对象
    core.on("coins.gain", lambda **kw: ...)   # 订阅联动事件
    core.daily("pet_settle", fn)          # 每日结算任务
    core.interval(60, fn)                 # 周期任务
    core.add_help("宠物", [("宠物", "查看宠物总览"), ...])  # 「游戏帮助」菜单段
```

- 指令处理器签名：`def handler(event) -> 回复`（同步或异步均可）。
- **禁止**插件之间直接 `import` 对方模块；一律 `core.service("名字")`。
- 共享常量从核心导入：`from ..core import PILL_NAME, PET_MAX_HEALTH, ...`（值为 2.3.0 同款；
  WebUI 运行参数覆盖经 `core.param("KEY")` 读取，常量仅作缺省）。

## 3. 回复协议（所有指令处理器统一返回值）

| 返回值 | 含义 |
|---|---|
| `str` | 纯文本；指令在 `core.IMAGE_COMMANDS` 中时自动转图片 |
| `("image", path)` | 图片 |
| `("image_text", 文本, path)` | 文本 + 图片（组件不可用回退纯文本） |
| `None` | 不回复 |

## 4. 核心框架接口（core 对象）

### 4.1 指令处理
| 接口 | 说明 |
|---|---|
| `core.command(*heads, feature=None, admin=False)` | 装饰器注册指令；feature=功能开关模块 key；admin=仅管理员 |
| `core.dispatch(head, event)` | 鉴权→开关→路由（指令插件调用；业务插件不直接调用） |
| `core.expand_alias(head)` | 同义口令展开（一步，不递归；`data["alias_cmds"]` 可由 WebUI 编辑） |
| `core.register_fallback(fn)` | 动态指令兜底路由 `fn(head, event)`（第三方插件挂载用） |

### 4.2 用户识别与鉴权
| 接口 | 说明 |
|---|---|
| `core.user_key(event)` | 用户唯一 key（跨群共享） |
| `core.user_name(event)` | 昵称（有效期内自定义昵称优先） |
| `core.custom_name_of(key)` | 自定义昵称（过期返回 ""） |
| `core.ensure_user(key)` | 取/建用户记录 `{"coins":0,"favorability":0.0}` |
| `core.chat_sid(event)` | 聊天 UMO |
| `core.perm_of(sid)` | 聊天三态权限 → ("allow"/"deny"/"partial", {被禁模块}) |
| `core.feature_enabled(key)` | 功能总开关（WebUI 可关） |
| `core.is_admin(event)` | 管理员判定（ADMIN_UIDS → 群主/管理员 → AstrBot 主人） |
| `core.level_of(fav)` | 好感度等级 `min(10, fav // 10)` |

### 4.3 金币变化（唯一出入口，流水插件自动记录）
| 接口 | 说明 |
|---|---|
| `core.add_coins(key, amount, reason, skip_repay=False)` | 加/扣金币，返回新余额。联动：加币前 emit `coins.pre_add`（可改 `payload["amount"]`，银行贷款插件扣减还款）；变动后 emit `coins.gain(key, amount, balance, reason)` |
| `core.pay_coins(key, amount, reason)` | 消费金币：余额不足返回 `None`，成功返回新余额 |
| `core.coins_of(key)` / `core.coin_line(key)` | 余额 / “💰 当前金币：N” 提示行 |

### 4.4 数据管理（单文件 data.json，命名空间与 2.3.0 完全兼容）
| 接口 | 说明 |
|---|---|
| `core.data` | 全量数据 dict（users/pets/farms/bank/loans/roulette/ledger/redpackets/activities/alias_cmds/lan/settle_dates/features/params/umos/group_members/group_names/activity_config） |
| `core.save()` | 写盘（records.json 剥离由 format_convert 插件完成；调试模式下仅更新内存） |
| `core.param(key, default=None)` | 运行参数：params 覆盖 → core 常量 → default |
| `core.set_param(key, val)` | 写运行参数并同步全部模块全局（WebUI 用） |
| `core.items()` / `core.save_items(flat)` | game_items.json（jobs/plays/shop/crops/ferts/loans）读写 |

### 4.5 插件间通信
| 接口 | 说明 |
|---|---|
| `core.expose(name, api_obj)` | 暴露接口对象（每插件一个服务名，等于插件 NAME） |
| `core.service(name)` | 取接口对象；未挂载返回 None（调用方必须判空降级） |
| `core.on(event, cb)` / `core.emit(event, **payload)` | 联动事件（同步调用；cb 签名 `cb(**payload)`） |

内置联动事件：
- `coins.pre_add`：回调签名 **`cb(payload)`**（payload 为 dict `{key, amount, reason, skip_repay}`，
  订阅者可**原地修改** `payload["amount"]`——银行贷款插件据此扣减还款；其余事件为 `cb(**payload)`）
- `coins.gain` `{key, amount, balance, reason}`
- 插件可自行约定新事件，命名建议 `模块.动作`（如 `signin.after`、`farm.stolen`）。

### 4.6 定时任务（核心统一巡检协程，60s 粒度）
| 接口 | 说明 |
|---|---|
| `core.daily(flag, fn)` | 每日幂等任务：`fn(data, now) -> bool`；返回 True 才写 `data["settle_dates"][flag]=今天`，失败次日自动重试；fn 自行改数据并 `core.save()` |
| `core.at(hour, flag, fn)` | 同上，但需 `now.hour >= hour` 才触发（如银行 4 点结算、贷款 23 点自动还款） |
| `core.interval(seconds, fn)` | 周期任务：`fn()`（自动打工 60s 巡检等）；fn 自行加锁（`async with core.lock`）与存盘 |

### 4.7 帮助菜单
`core.add_help(title, sections)` — sections 为 `[(指令, 说明), ...]`；「游戏帮助」聚合全部插件的段。

## 5. 图片响应模块接口（core.image）

统一入口（`from .plugins import image` 已挂为 `core.image`）：

| 接口 | 用途（返回 `("image", path)` 或 `None`） |
|---|---|
| `image.text(title, lines, force_width=None)` | 标题+正文行（自动换行/加宽重排） |
| `image.rich(title, rows)` | 富文本：rows 每行 `[(text, color, strike), ...]` |
| `image.snapshot(title, modules, header_right=None)` | 瀑布卡片：卡片 `(标题, 行列表, 高亮?)` 或并排行 `[卡1, 卡2, (w1,w2)]`；行支持 `(文本, 颜色[, 边框色])` 与 `("__bar__", 比例, 填充色, 标签)` |
| `image.build_help(title, sections)` | 帮助菜单（失败回退纯文本 str） |

专用格式渲染器（格式由对应插件定义，渲染统一在本模块）：

| 函数 | 所在子模块 | 调用方插件 |
|---|---|---|
| `render_pet_status(name, uid, data, pet, slot_msg=None, ...)` | image/pet.py | 宠物插件 |
| `render_work_play(kind, name, pet, items, coins=None)` | image/pet.py | 打工/玩耍插件 |
| `render_shop(name, categories, inventory, coins=None)` | image/pet.py | 宠物商店插件 |
| `render_bag(name, header, groups, footer)` | image/pet.py | 仓库插件 |
| `render_seed_shop(name, farm, crops)` | image/farm.py | 农场商店插件 |
| `render_farm_shop(name, farm, crops, ferts, expanded=False, page=1, all_items=False)` | image/farm.py | 农场商店插件 |
| `render_warehouse(farm, crops, ferts)` | image/farm.py | 仓库插件 |
| `render_plot_status(name, uid, data, farm, crops, ferts, steal_lines=None, ...)` | image/farm.py | 农场插件 |
| `render_rank(title, rows, hl_color, force_width=None)` | image/rank.py | 排行榜插件 |
| `render_loan_packages(data, key)` | image/loan.py | 银行贷款插件 |
| `render_activity_image(activities)` | image/activity.py | 第三方挂载插件 |

## 6. 插件服务目录（core.service 名 → 职责）

| 服务名 | 插件 | 关键接口（对象方法） |
|---|---|---|
| `signin` | 签到插件 | `signin_snapshot_lines(key)` 等签到数据 |
| `affection` | 好感度插件 | `add(key, delta, reason)` / `level_of(key)` / `total_of(key)` |
| `pet` | 宠物插件 | `pet_of(key)` / `gain_exp(key, exp)->升级行` / `weak_guard(key)->提示或None` / `busy_until(pet)` / `attr_max(health)` / `state_snippet(pet)` / `settle_display_lines(pet)` / `bring_up_to_date(pet, today)` |
| `pet_shop` | 宠物商店插件 | 商品读取与购买（被「购买」指令联动） |
| `pet_item` | 宠物道具插件 | `use_item(key, name, qty)`（「使用」指令联动） |
| `pet_work` / `pet_play` | 打工/玩耍 | `start(data, key, job=None)` 等 |
| `pet_auto_care` / `pet_auto_work` | 自动照顾/自动打工 | `care_due(key)` / `care_run(key, trigger)` / `auto_work_enabled_for(key)` |
| `farm` | 农场种地 | `farm_of(key)` / `add_exp(key, exp)` / `plot_status_lines(farm)` 等 |
| `farm_shop` | 农场商店 | `buy_seed(key, name, qty)` / `buy_fert(key, name, hours)` / `fert_list()` |
| `farm_sell` | 农场售卖 | `sell_crops(key, name, qty)` / `sell_seeds(...)` |
| `farm_item` | 农场道具 | `use_item(key, name, qty)`（经验球等） |
| `farm_guard` | 宠物守护 | `intercept(data, thief_key, target_key, gain=0)` → `{"guard": "catch"/"stop"/None, "fine": int, "stamina": (lo,hi)}`（旧版三态：抓住/给我站住/无加护） |
| `farm_steal` | 偷菜 | `steal(event, target_key)` |
| `warehouse` | 仓库插件 | `bag_view(key)` / `farm_warehouse_view(key)` |
| `redpacket` / `roulette` | 红包 / 左轮 | 各自游戏逻辑 |
| `bank_deposit` / `bank_loan` | 存款 / 贷款 | `settle(key)` / `has_overdue(key, ts)` / `repay_slice(key, amount)` |
| `ledger` / `rank` | 流水 / 排行榜 | `entries(kind, data)` / `rank_score_coins(key)` |
| `thirdparty` | 第三方挂载 | `load_activities()` / `activity_command(head, event)` |
| `datamgr` / `format_convert` / `migrate` | 数据管理/格式转化/迁移 | 导出导入 / split_records·merge_records / check_and_migrate |

> 服务对象的方法以各插件实现为准；调用方对 `None` 与 `AttributeError` 一律降级处理。

## 7. 数据格式（与 2.3.0 兼容，迁移零转换）

- 数据目录：`data/plugin_data/astrbot_plugin_signin`（AstrBot 规范，与 2.x 同目录，升级数据原地可用）；插件目录仅含本体与基准数据（2.x 残留由 update 插件首启按哈希清单清理）。

**3.0 数据布局：**
- **用户数据（一人一文件）**：`user_data/<uid>.json`，内含
  `user / pet / farm / bank / loans / roulette / ledger` 七个键（即该用户在原七个命名空间下的全部数据，
  含 attr_log / auto_*_logs / signin_logs 等记录字段，随人走）。
- **设置/参数/商店价格（各自单独文件）**：`settings.json`（features / alias_cmds / activities /
  activity_config）、`params.json`（运行参数）、`shop_prices.json`（shop_price_records）。
- **群共享**：`runtime.json`（redpackets / settle_dates / group_members / group_names / lan / umos）。
- **备份配置**：`backup_data/`（历史回溯版本 + 导出备份 + 导入暂存）。
- **旧布局转译**：`data.json` + `records.json`（2.2.2 标准）由 format_convert 插件一次性
  转译为新布局，旧文件保留供回滚；插件代码统一读写内存 `core.data`，不感知布局。
- `game_items.json`：jobs / plays / shop / crops / ferts / loans
- 宠物结构完整继承：level/exp/health/satiety/thirst/stamina/mood/weak/weak_streak/
  busy_until/busy_activity/busy_item/inventory/last_settle + 状态档位（PET_ATTR_MAX_RANGES）。
- 农场结构完整继承：level/exp/plots（crop/mature_ts/grade）/warehouse(crops/seeds/fertilizers)/tools/steal_infos。
