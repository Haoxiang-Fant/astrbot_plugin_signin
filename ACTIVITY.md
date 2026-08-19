# 活动中心组件编写文档

活动中心（指令「活动」）是本插件提供的一个**活动框架**：管理员在 WebUI 勾选想要激活的活动模块，玩家发送「活动」即可查看当前正在进行的活动。后续新增活动时，**只需要在 `activities/` 目录下新增一个 Python 文件**，无需修改插件主程序。

---

## 一、目录结构

```
astrbot_plugin_signin/
├── main.py                  # 插件主程序（无需修改）
├── activities/
│   ├── __init__.py          # 活动框架：BaseActivity 基类 + 注册机制
│   ├── example_activity.py  # 活动模板示例（复制修改）
│   ├── daily_draw.py        # 真实示例：双倍签到（签到运行两次）
│   └── redpacket_rain.py    # 真实示例：定时红包雨（on_redpacket_open 钩子）
│   └── my_activity.py       # ← 你的活动组件写在这里（任意文件名）
```

插件启动时会**自动扫描并加载** `activities/` 目录下所有 `.py` 文件（`__init__.py` 除外）。

## 二、编写一个活动

每个活动 = 一个类，继承 `BaseActivity`，并用 `@register_activity` 装饰：

```python
# activities/my_activity.py
from activities import BaseActivity, register_activity


@register_activity
class MyActivity(BaseActivity):
    id = "my_activity"          # 唯一标识（小写英文，必填）
    name = "我的活动"            # 活动名称
    start = ""                  # 开始时间 "YYYY-MM-DD HH:MM"，空 = 不限制
    end = ""                    # 结束时间 "YYYY-MM-DD HH:MM"，空 = 不限制
    desc = "活动简介，展示在活动卡片上"
    requirement = "参与要求，如：好感度 Lv.5 以上"   # 无要求可写"无（所有玩家均可参与）"
    commands = {}               # 可选：活动自定义指令
```

保存文件后**重启插件**（或重载插件）即完成注册。

## 三、活动卡片显示内容

玩家发送「活动」时，每个「已启用 + 时间有效」的活动会以卡片形式展示：

```
📌 我的活动
🕐 长期进行                     ← start ~ end
📝 活动简介                     ← desc
✅ 参与要求：好感度 Lv.5 以上    ← requirement
💬 相关指令：xxx / yyy          ← commands 里的指令（若有）
```

- **时间判断**：`start` / `end` 为空字符串表示不限制；格式为 `"YYYY-MM-DD HH:MM"`。
- **启用控制**：管理员在 WebUI「活动中心」页勾选活动后才会对玩家展示并生效（未勾选 = 未启用）。

## 四、给活动添加自定义指令

活动可以注册自己的指令，例如「新年红包」「双倍签到」等。在类里定义 `commands`：

```python
# activities/my_activity.py
from activities import BaseActivity, register_activity


@register_activity
class MyActivity(BaseActivity):
    id = "my_activity"
    name = "我的活动"
    start = ""
    end = ""
    desc = "活动简介"
    requirement = "无（所有玩家均可参与）"

    def _on_hello(self, event):
        # event: AstrMessageEvent，可直接复用主插件的各种能力（见下文）
        return f"你好 {event.get_sender_name()}！欢迎参加「{self.name}」"

    commands = {"活动打卡": _on_hello}   # 玩家发送「活动打卡」即触发
```

规则：

1. `commands` 是一个 `{指令字符串: 处理函数}` 的字典；处理函数签名固定为 `fn(event) -> str | None`。
2. 返回**非空字符串** = 回复内容（主插件会自动渲染成图片并 15 秒后撤回）；返回 `None` = 不处理，交给其他活动或主插件。
3. 也可以返回 `("image", 路径)` 元组，直接发送指定图片。
4. 指令**无需斜杠/前缀**，与插件现有指令一致；当活动未启用或不在活动期内，指令不会被触发。
5. 处理函数内如需读写玩家数据，直接读取 `data.json`（建议复用主插件的方法，见下文）。

### 在签到后触发（on_sign_in 钩子）

活动还可以在**玩家每日签到成功时**自动触发，例如「双倍签到」「签到彩蛋」等。在类里实现 `on_sign_in` 方法：

```python
class MyActivity(BaseActivity):
    id = "my_activity"
    name = "我的活动"
    start = ""
    end = "2026-12-31 00:00"
    desc = "活动简介"
    requirement = "无（所有玩家均可参与）"

    plugin = None

    def attach(self, plugin):
        self.plugin = plugin

    def on_sign_in(self, event, data, key, lines):
        """签到成功后调用：data 为玩家数据（修改后主插件统一保存），
        lines 为即将返回的签到文本行列表，可 append 追加内容。"""
        lines.append("")
        lines.append("🎁 活动彩蛋：签到额外获得 10 金币！")
        self.plugin._add_coins(data, key, 10, "活动·彩蛋")
```

规则：

1. `on_sign_in(self, event, data, key, lines)` 仅在活动**已启用且时间有效**时被调用。
2. 直接修改 `data`（金币用 `plugin._add_coins(...)` 会同时记入金币账单）；主插件会在签到流程末尾统一 `_save`，**无需自行保存**。
3. `lines` 是列表，用 `append` / `extend` 追加展示内容，会出现在签到回复的末尾。
4. 未实现该方法的活动跳过钩子，不影响签到流程；钩子内异常只记录日志，不会中断签到。
5. 参考实现：插件自带的 `activities/daily_draw.py`——**双倍签到**活动，通过 `plugin._apply_signin_once(data, key, today)` 把签到程序再完整运行一次（金币 / 好感度 / 宠物经验 / 属性丸全部翻倍），实现「签到运行两次」。

### 在开红包时触发（on_redpacket_open 钩子）

活动还可以在**玩家发送「开 / 开红包 / 抢红包」时**自动触发（在清理过期红包之前），例如「定时红包雨」这类懒生成的系统红包。在类里实现 `on_redpacket_open` 方法：

```python
class MyActivity(BaseActivity):
    id = "my_activity"
    name = "我的活动"
    start = ""
    end = ""
    desc = "活动简介"
    requirement = "无（所有玩家均可参与）"

    plugin = None

    def attach(self, plugin):
        self.plugin = plugin

    def on_redpacket_open(self, event, data, key, gid, now_ts):
        """开红包前被调用。可向 data["redpackets"] 追加系统红包等。
        返回非空字符串作为提示（显示在开红包结果顶部），返回空字符串表示无提示。"""
        if now_ts % 2 == 0:  # 示例条件
            return ""
        data.setdefault("redpackets", []).append({
            "id": "my-rain", "group_id": gid,
            "owner_uid": "system:my", "owner_name": "我的红包",
            "count": 5, "total": 500, "remain": 500, "left": 5,
            "claimed": {}, "created_ts": now_ts, "expires_ts": now_ts + 3600,
        })
        return "🎁 我的活动红包来了！"
```

规则：

1. `on_redpacket_open(self, event, data, key, gid, now_ts)` 仅在活动**已启用且时间有效**时被调用；`gid` 为群 ID 字符串，`now_ts` 为当前时间戳。
2. 向 `data["redpackets"]` 追加的红包即成为普通可抢红包；**系统红包**的 `owner_uid` 以 `system:` 开头（如 `"system:rain:..."`），过期后剩余金额**直接作废**（不会退款给任何人）；普通玩家红包过期才退回发起人。
3. **全局红包**：若想让红包**所有群共享同一奖池**（如红包雨），把 `group_id` 设为固定值 `"rain"`（任意非群 ID 的值）并加 `"rain": True` 标记即可——抢红包时不再按群隔离，`claimed` 按用户全局去重，每位用户每轮只能抢一次，A 群抢过再到 B 群抢会提示「你已经抢过了」。
4. 懒生成：可在钩子里用 `data` 中的记录（如 `rain_generated`）保证每个时段/每天只生成一次。
5. 返回的提示会显示在开红包结果顶部（如图片里的「🌧️ 红包雨开始啦！」）。
6. 参考实现：插件自带的 `activities/redpacket_rain.py`——**定时红包雨**活动（每天 8/12/16/20 点开启，**全局红包**，金额/个数/时间/有效期可在 WebUI 插件配置页修改）。

### 在主插件中复用能力

活动模块与主插件运行在**同一个进程**。通过给活动类加 `attach(plugin)` 方法拿到主插件实例（主插件初始化时会自动调用）：

```python
class MyActivity(BaseActivity):
    plugin = None

    def attach(self, plugin):
        self.plugin = plugin

    def on_sign_in(self, event, data, key, lines):
        p = self.plugin          # 主插件实例
        coins = p._coins_of(data, key)
        ...
```

> ⚠️ **不要 `import main`**（会循环依赖）。所有能力一律通过 `plugin` 实例方法 / 传入的 `data` 访问。

#### 完整 API 参考（`plugin` 为主插件实例）

**数据存取**（`data` 为 `plugin._load()` 返回的完整数据 dict）

| 方法 | 说明 |
| --- | --- |
| `plugin._load()` | 读取全部数据 dict（含 `users` / `roulette` / `pets` / `bank` / `farms` / `loans` / `ledger` / `redpackets` / `activities` / `activity_config` / `params`） |
| `plugin._save(data)` | 保存数据（写回 `data.json`） |
| `plugin._ensure_user(data, key)` | 取 / 建用户记录 `{"coins": 0, "favorability": 0.0}` |
| `plugin._coins_of(data, key)` | 读取用户金币（int） |
| `plugin._add_coins(data, key, amount, reason="")` | 金币变动（正=获得，负=消费）并**自动记入金币账单**，返回新余额；逾期贷款时自动扣 20% 还款 |
| `plugin._coin_line(data, key)` | 返回「💰 当前金币：X」提示行 |

**用户 / 事件**

| 方法 | 说明 |
| --- | --- |
| `plugin._user_key(event)` | 获取用户 ID（跨群共享；静态方法） |

**等级与加成**

| 方法 | 说明 |
| --- | --- |
| `plugin._level_of(favorability)` | 好感度 → 等级（Lv.0~10；静态方法） |
| `plugin._fav_multipliers(fav_level)` | 返回 `(正面效果倍率, 负面效果倍率)` |

**宠物**

| 方法 | 说明 |
| --- | --- |
| `plugin._apply_exp(pet)` | 按经验升级宠物等级，返回升级提示字符串（可能为空） |
| `plugin._bring_pet_up_to_date(pet, today)` | 宠物懒结算到今日（`today` 如 `"2026-08-18"`） |
| `plugin._settle_display_lines(pet)` | 返回昨日结算展示行列表（可能为空） |
| `plugin._clamp_attrs(pet)` | 将各属性钳制到健康度决定的上限内 |
| `plugin._attr_max(health)` | 返回 `(饱食上限, 口渴上限, 体力上限, 心情上限)`（静态方法） |
| `plugin._pet_state_snippet(pet)` | 宠物当前状态摘要行（「🐾 名字：饱食 80/100 …」） |

**农场**

| 方法 | 说明 |
| --- | --- |
| `plugin._ensure_farm(data, key)` | 取 / 建农场记录 |
| `plugin._new_plot()` | 新建一块土地 dict（静态方法） |
| `plugin._plot_grade(grade)` | 返回 `(土地名, 产量加成, 时间减少)`（静态方法） |
| `plugin._plot_free(plot)` | 土地是否空闲（静态方法） |
| `plugin._farm_state_snippet(farm)` | 农场状态摘要行（等级 / 土地数 / 仓库余量） |
| `plugin._load_crops()` / `plugin._load_fertilizers()` | 读取作物 / 肥料配置列表 |
| `plugin._find_item(items, name)` | 按名称查找配置项，找不到返回 `None`（静态方法） |

**配置**

| 方法 | 说明 |
| --- | --- |
| `plugin._load_config()` | 读取 `后台.txt`，返回 `{"jobs": [...], "plays": [...], "shop": [...]}` |

**渲染（返回图片）**

| 方法 | 说明 |
| --- | --- |
| `plugin._render_text_image(title, lines)` | 渲染文字图片，返回 `("image", path)` 或 `None`（缺 Pillow/字体时） |
| `plugin._render_rich_image(title, rows)` | 渲染富文本图片；`rows` 每行是 `[(text, color, strike), ...]`，如 `[[("金币 +100", (192,0,0), False)]]` |
| `plugin._effect_desc(effects)` | 道具效果描述文本（如「饱食+20 体力+10」） |

**格式化**

| 方法 | 说明 |
| --- | --- |
| `plugin._fmt_duration(sec)` | 秒 → 「X天X小时 / X小时X分 / X分钟 / X秒」（静态方法） |
| `plugin._fmt_price(v)` | 价格格式化（静态方法） |

**签到**

| 方法 | 说明 |
| --- | --- |
| `plugin._apply_signin_once(data, key, today)` | 运行一次完整签到奖励（金币 / 好感度 / 宠物经验 / 属性丸），返回提示行列表；双倍签到类活动直接复用 |

#### 常用常量

主插件 `main.py` 顶部的模块常量**不能直接 import**（会循环依赖），需要时：
- 已有实例属性的直接读：`plugin.min_coins`、`plugin.max_coins`、`plugin.pet_unlock_cost`、`plugin.pet_signin_exp_min`、`plugin.pet_signin_exp_max`、`plugin.pill_drop_chance`、`plugin.pill_drop_min`、`plugin.pill_drop_max`、`plugin.money_event_gain`、`plugin.money_event_max_per_day`、`plugin.rain_times`、`plugin.rain_amount`、`plugin.rain_count`、`plugin.rain_hours`
- 其余（如 `PILL_NAME = "属性丸"`、`ATTR_LABELS`、`ATTR_SHORT`、`MAX_LEVEL = 10`、`LEVEL_STEP = 10.0`、`PET_EXP_PER_LEVEL = 100.0`、`PET_MAX_LEVEL = 100`、`MIN_FAV`、`MAX_FAV`）在活动内自行定义或直接硬编码

#### 调试建议

- **日志**：`from astrbot.api import logger`，用 `logger.info / warning / error` 输出，会出现在 AstrBot 日志中
- **异常隔离**：活动指令 / 签到钩子 / 红包钩子里的异常会被主插件捕获并记录（`logger.error("[插件] 活动 xxx …处理异常: …")`），**不会中断主流程**；但建议活动内部对关键步骤 `try/except`，便于定位
- **返回约定**：
  - 指令处理函数返回**非空字符串** → 自动渲染成图片并发送（按该指令的撤回设置决定是否撤回）；返回 `("image", path)` 直接发图；返回 `None` 表示不处理
  - `on_sign_in` 钩子通过 `lines.append(...)` 追加到签到回复末尾
  - `on_redpacket_open` 钩子返回字符串显示在开红包结果顶部（返回空串无提示）
- **本地测试**：可参照插件自带 `activities/daily_draw.py`、`activities/redpacket_rain.py` 的写法——直接实例化活动类，构造 `data` dict 调用方法验证逻辑；`plugin` 可传入一个真实 `SignInPlugin` 实例（在测试桩环境下），或为方法打桩
- **WebUI 调试**：活动中心可实时改参数（起止时间 / 参与要求 / 自定义参数）、查看过期提醒；保存后立即生效，无需重启

> 说明：若需在指令处理函数中直接拿到主插件实例（非 `attach` 场景），活动类内 `self.plugin` 已在初始化时由主插件注入。

## 五、在 WebUI 启用 / 停用活动

1. 打开插件的 WebUI 后台管理页（AstrBot 管理后台 → 本插件的 Pages 页面）。
2. 切换到「**活动中心**」标签页 → 点击「加载」。
3. 勾选想要激活的活动 → 点击「保存」。
4. 玩家发送「活动」即可看到进行中的活动卡片。

启用状态保存在 `plugin_data/astrbot_plugin_signin/data.json` 的 `activities` 字段中，重启后保留。

## 六、活动参数（WebUI 可改）

每个活动在 WebUI「活动中心」页除了启用开关，还可以直接修改以下**内置字段**（保存后立即生效，无需改代码）：

| 字段 | 说明 |
| --- | --- |
| `start` | 开始时间 `"YYYY-MM-DD HH:MM"`，空 = 不限制 |
| `end` | 结束时间 `"YYYY-MM-DD HH:MM"`，空 = 不限制 |
| `desc` | 活动简介 |
| `fav_level_req` | 参与要求：好感度等级（0 = 不限，最高 10） |
| `farm_level_req` | 参与要求：农场等级（0 = 不限，最高 100） |
| `pet_level_req` | 参与要求：宠物等级（0 = 不限，最高 100） |
| `requirement` | 参与要求：额外的自由文字描述 |

- **参与要求 = 大类**：好感度 / 农场 / 宠物等级要求 + 自由文字，会合并展示在活动卡片与「活动」指令中
- **要求校验自动生效**：玩家不满足等级要求时，活动不会触发——签到钩子会在签到回复中提示「未满足参与要求」；活动指令会直接回复不满足原因；开红包钩子（如红包雨）跳过
- 活动代码中可用 `self.fav_level_req` 等读取（已应用 WebUI 覆盖），或调用 `self.check_requirements(plugin, data, key)` 自行校验

活动还可以声明**自定义参数**，在类里定义 `params` 字典即可：

```python
class MyActivity(BaseActivity):
    id = "my_activity"
    name = "我的活动"
    start = ""
    end = ""
    desc = "活动简介"
    requirement = "无（所有玩家均可参与）"
    params = {
        "times": {"label": "签到倍数", "type": "int", "desc": "奖励运行次数", "default": 2},
        "bonus": {"label": "额外金币", "type": "int", "desc": "每次签到额外金币", "default": 10},
        "notice": {"label": "是否通知", "type": "bool", "desc": "勾选后签到末尾显示提示", "default": True},
    }

    def on_sign_in(self, event, data, key, lines):
        # 直接以 self.xxx 读取（已被覆盖为 WebUI 设置的值）
        n = int(getattr(self, "times", 2))
        ...
```

- `params` 每项：`label`（显示名）、`type`（`str` / `int` / `float` / `bool`）、`desc`（说明）、`default`（默认值），可选 `min` / `max`（数值范围校验）
- WebUI 保存的覆盖值写入 `data.json` 的 `activity_config` 字段；插件启动时自动重新应用，**修改无需重启**
- 时间覆盖直接影响「活动」指令的展示与 `is_active_now()` 判断（过期活动自动隐藏）
- 参考实现：`activities/daily_draw.py` 的 `times`（签到倍数）、`activities/redpacket_rain.py` 的 `amount`（红包雨总金额）/ `count`（红包个数）参数

## 六、完整示例（限时双倍金币活动）

```python
# activities/double_coins.py
from activities import BaseActivity, register_activity


@register_activity
class DoubleCoinsActivity(BaseActivity):
    id = "double_coins"
    name = "双倍打工金币"
    start = "2025-01-01 00:00"   # 改成你的活动时段
    end = "2025-02-01 00:00"     # 空字符串表示长期
    desc = "活动期间，宠物打工获得的金币翻倍！"
    requirement = "需要已解锁宠物"

    plugin = None

    def attach(self, plugin):
        self.plugin = plugin

    def _on_check(self, event):
        key = self.plugin._user_key(event)
        data = self.plugin._load()
        pet = data.get("pets", {}).get(key)
        if not pet:
            return "你还没有宠物，先「解锁宠物」吧～"
        return f"🎉 活动进行中：打工金币翻倍！当前宠物「{pet.get('name')}」Lv.{pet.get('level')}"

    commands = {"活动详情": _on_check}
```

## 七、注意事项

- 活动模块内的**异常不会影响主插件**：加载失败会被跳过并记录警告；指令处理异常只记录日志，不中断其他指令。
- 时间格式必须为 `"YYYY-MM-DD HH:MM"`；解析失败时该字段视为不限制。
- `id` 全局唯一，建议用活动英文名；同名会互相覆盖启用状态。
- 活动模块中可以直接 `import` 第三方库（与 AstrBot 同进程环境）。
- 不要在活动模块里 import 主插件 `main.py`（会循环依赖）；如需主插件能力，使用上文 `attach` 方式或直接操作 `data.json`。
