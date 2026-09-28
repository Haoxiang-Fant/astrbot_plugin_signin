# astrbot_plugin_signin · 3.0.1（万物皆插件）

群签到娱乐系统 3.0.0 插件化重构版。功能继承 2.3.0（签到 / 好感度 / 宠物养成 / 农场 / 金币银行 /
贷款 / 红包 / 左轮手枪 / 排行榜 / 流水 / WebUI 等），架构从「Mixin 单体」重构为「核心框架 + 功能插件」。

## 架构

```
AstrBot 平台
   │ 消息事件
   ▼
指令插件 main.py ──── 消息接入 / 发送 / 撤回 / 文本转图片调度
   ▼
核心框架 core.py ──── 插件挂载 / 指令处理 / 用户识别 / 用户鉴权 / 金币变化 / 数据管理 / 插件通信 / 定时任务
   ▼                     ▼
功能插件 plugins/*.py   图片响应模块 plugins/image/ ── 统一图片输出（格式由插件定义）
```

### 核心思想：每个插件只负责对应的数据处理

- **核心框架**自我处理：用户识别、金币变化、用户鉴权、指令处理、插件调用；其余数据流转发到对应插件。
- **指令插件**：从 AstrBot 获取消息 → 核心框架处理 → 发送回复；继承同义口令（别名）功能。
- **功能插件**：只处理自己的业务数据；跨插件能力一律走 `core.service()` 接口调用或 `core.on/emit` 联动事件。
- **图片响应模块**：接收各插件的渲染请求统一渲染发图；图片格式（版式内容）由发起插件定义。
- **第三方插件挂载**：`activities/` 目录的活动模块即第三方插件示例，由 thirdparty 插件动态挂载。

插件清单与接口契约见 **[API.md](API.md)**（接口描述文件 / 插件通信规范）。

## 功能插件

| 插件 | 职责（继承自 2.3.0 对应模块） |
|---|---|
| signin / affection | 每日签到奖励 / 好感度与等级体系 |
| pet + pet_shop / pet_work / pet_play / pet_item | 宠物属性·经验·状态档位·忙碌·虚弱·每日结算；商店 / 打工 / 玩耍 / 道具 |
| pet_auto_care / pet_auto_work | 自动照顾 / 自动打工（固定巡检） |
| farm + farm_shop / farm_sell / farm_item / farm_guard / farm_steal | 农场信息·土地·种植·收割；商店 / 售卖 / 道具 / 宠物看家 / 偷菜 |
| warehouse | 仓库：背包与农场仓库（道具与收获） |
| redpacket / roulette | 金币红包 / 左轮手枪 |
| bank_deposit / bank_loan | 银行存款 / 银行贷款（含逾期处置与自动还款） |
| ledger / rank | 金币流水 / 排行榜 |
| datamgr / migrate / format_convert / update | 数据管理（导出导入）/ 数据迁移（2.x→3.0）/ 数据格式转化（records 分文件）/ 自动更新（首启按哈希清单清理 2.x 残留） |
| webui / debug | WebUI（含局域网开放）/ 调试模式 |
| thirdparty | 第三方插件挂载（活动中心） |

## 数据兼容

- 数据目录：`data/plugin_data/astrbot_plugin_signin`（与 2.x 同目录，升级数据原地可用）。
- **用户数据一人一文件**：`user_data/<uid>.json`（user/pet/farm/bank/loans/roulette/ledger 随人走）。
- **设置/参数/商店价格独立文件**：`settings.json` / `params.json` / `shop_prices.json`；群共享 `runtime.json`；备份配置 `backup_data/`。
- 旧布局（data.json + records.json）由 format_convert 插件一次性转译为新布局（旧文件保留，回滚即用）；旧格式转译（跨群合并/宠物等级重算/自动化贷款合并）由 migrate 插件幂等补跑。
- `game_items.json`（打工/玩耍/商店/作物/肥料/贷款套餐数值）沿用，WebUI 继续可视化编辑。
- 宠物结构完整继承：属性、经验、状态档位（PET_ATTR_MAX_RANGES）、忙碌状态、虚弱机制。
- 自动更新：update 插件在 3.0.0 首启按 `manifest.sha256` 哈希清单清理插件目录内 2.x 残留（仅一次）。

## 开发

- 新增功能插件：在 `plugins/` 下实现 `NAME` + `register(core)`，在 `main.py` 的挂载清单登记。
- 接口规范：`core.command / core.expose / core.service / core.on / core.emit / core.daily / core.at / core.interval`，
  详见 `API.md`。
- 第三方插件：实现同款模块接口，放 `thirdparty/` 目录，经 thirdparty 插件挂载（参考 `thirdparty/` 内示例）。

## 更新日志

- **v3.0.1**：签到响应图重绘为参考效果图版式（墨绿信息面板 + 白卡瀑布 + 双色进度条）；新增模块禁用开关 `disabled_modules` 与 OPPOSans-H.ttf；签到成果（签到/我的签到）一律走新版渲染、脏数据不再回退旧版图；昵称显示上限 12 个全角字符（恰好 12 不省略）；插件更名 astrbot_plugin_signin。
- **v3.0.0**：万物皆插件重构（core.py 核心框架 + 31 个功能插件 + 图片响应模块），数据零转换兼容，详见 [UPDATE.md](UPDATE.md)。
