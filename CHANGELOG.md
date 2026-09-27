# 更新日志 · v3.0.0（本版本）

> 签到娱乐系统 3.0.0 · 万物皆插件重构版
> 基线：2.3.0（astrbot_plugin_signin）。本版本为架构重构 + 全功能继承，业务规则与回复文案保持 2.3.0 口径。
> 全部历史版本日志见 [UPDATE.md](UPDATE.md)。

## 架构重构

- **单体 → 插件化**：2.3.0 的 main.py + 13 个 Mixin 模块（约 700KB）拆分为
  **核心框架（core.py）+ 指令插件（main.py）+ 28 个功能插件（plugins/）+ 图片响应模块（plugins/image/）**。
- **核心框架自我处理**：用户识别、金币变化、用户鉴权、指令处理、插件调用；
  其余数据流按职责转发到对应插件，每个插件只处理自己的业务数据。
- **插件间通信**：
  - 接口调用：`core.expose(name, api)` 暴露服务，`core.service(name)` 调用（未挂载返回 None，调用方降级）；
  - 联动事件：`core.on/emit`——`coins.pre_add`（单参可变 payload，银行贷款扣减还款）、`coins.gain`（流水/背包基线）；
  - 禁止插件间直接 import。
- **指令处理收口**：同义口令展开（继承 2.3.0 别名表，WebUI 可编辑）、管理员鉴权、
  功能总开关、聊天三态权限（allow/deny/partial）统一在核心唯一入口完成。
- **金币唯一出入口**：`core.add_coins / pay_coins`，自动记流水（≤200 条），
  加币前后发联动事件供贷款自动扣减等插件挂钩。
- **定时任务统一巡检**：核心单巡检协程承载全部结算——周期任务由插件自行加锁；
  每日任务核心持锁执行、按 flag 幂等（失败次日自动重试）：
  `pet_settle`（0 点宠物结算）、`auto_care_daily`（自动照顾每日触发）、
  `bank_settle`（4 点存款结算）、`loan_repay`（23 点自动还款）、`loan_daily`（贷款逾期处置）；
  周期任务：红包过期清理（60s）、红包雨活动 tick（60s）、自动打工巡检（60s）。

## 功能插件清单

签到 / 好感度 / 宠物核心 / 宠物商店 / 宠物道具 / 打工 / 玩耍 / 自动照顾 / 自动打工 /
仓库 / 农场种地 / 农场商店 / 农场售卖 / 农场道具 / 宠物守护（看家）/ 偷菜 /
金币红包 / 左轮手枪 / 银行存款 / 银行贷款 / 流水 / 排行榜 / 数据管理 /
WebUI（含局域网开放·历史回溯·运行记录）/ 调试模式 / 数据迁移 / 数据格式转化 / 第三方挂载（活动中心）。

- 宠物插件完整继承：属性五维、经验等级、状态档位（PET_ATTR_MAX_RANGES）、忙碌状态、
  虚弱/虚弱连续天数、attr_log 记录、每日结算健康互换机制。
- 农场插件完整继承：土地等级/产量时间加成、成长阶段模型、仓库三类库存、
  快捷种地/快捷施肥、偷菜 5%~20% 与看家三态守卫（抓住/给我站住/无加护）。
- 第三方插件：`activities/` 目录活动模块即第三方样例，经 thirdparty 插件动态挂载
  （自动附加 2.x 方法名桥接对象，旧活动零改动运行）。

## 图片响应模块（统一图片输出）

- 全部图片输出收口到 `plugins/image/`：通用渲染器 `text / rich / snapshot / build_help`，
  专用渲染器 12 个（签到快照、宠物状态、打工玩耍、宠物商店、背包、种子商店、农场商店、
  农场仓库、土地状态、排行榜、贷款套餐、活动中心），由图片模块自动发现聚合为 `core.image.render_*`。
- 图片格式（版式内容）由发起插件定义，渲染与存图统一在本模块（设计系统/字体/去 Emoji/长宽比限制沿用 2.3.0）。
- 渲染失败统一回退纯文本，回退文案与 2.3.0 逐字一致。

## 数据兼容与迁移

- `data.json` 命名空间与 2.3.0 逐字段一致（users/pets/farms/bank/loans/roulette/ledger/
  redpackets/activities/alias_cmds/lan/settle_dates/group_members/group_names），
  3.0 新增 `features / params / umos`。
- **数据迁移插件**：首次启动从 2.x 数据目录自动复制 data.json / records.json /
  game_items.json / 各 txt 配置 / 草稿 / 历史配置（目标缺失才复制，幂等）。
- **数据格式转化插件**：records.json 分文件标准沿用（attr_log / auto_*_logs / signin_logs /
  shop_price_records 写盘剥离、读取回填），业务代码无感知。
- `game_items.json`（打工/玩耍/商店/作物/肥料/贷款套餐）沿用，WebUI 继续可视化编辑；
  `Benchmark data/` 模板保留（一键恢复默认数值）。

## WebUI

- 43 个后端端点全部移植（params / feature / perm / alias / activities / petshop /
  backend-config / crops / ferts / loan-packages / apply_benchmark / draft / history 六件套 /
  lan 五件套 / records 七件套 / debug / group-names-sync），JSON 回复形状与旧版一致，
  `pages/admin` 前端沿用。
- 运行参数保存即生效：`core.set_param` 同步核心与全部已挂载插件的模块全局，无需重启。
- 局域网开放沿用：会话 Cookie + 回环免密 + 黑名单 + 访问记录；非 lan 端点统一套访问门。

## 接口描述文件

- 新增 `API.md`：插件模块契约、回复协议、核心接口、图片响应模块接口、
  服务目录、联动事件、数据格式——第一方/第三方插件开发与维护的唯一契约。

## 自动更新模块（update 插件）

- **激活条件**：版本号为 3.0.0 且首次启动 3.0.0（数据目录无标记）；两条件同时满足才执行，且仅此一次。
- 按构建期生成的 `manifest.sha256`（全部 61 个插件文件的 sha256）逐文件对比清理插件目录：
  不在清单中的 2.x 残留/无关文件删除，清空后空目录一并移除；在清单中但哈希不一致的保留并记警告（无源副本不做自动修复）。
- 只清理插件本体目录，绝不触碰用户数据目录。

## 插件位置（第一方 / 第三方分离）

- 第一方插件：`plugins/`；第三方插件：`thirdparty/`（原 activities/ 目录更名，活动模块
  `from thirdparty import BaseActivity, register_activity`）。两目录互不混放。

## 数据布局（3.0）

- **用户数据**：每用户一个文件 `user_data/<uid>.json`（内含 user/pet/farm/bank/loans/roulette/ledger），
  不再把所有用户塞进一个文件；内存聚合不变，28 个功能插件零改动。
- **设置/参数/商店价格**：各自单独文件——`settings.json`（功能开关/同义口令/活动）、
  `params.json`（运行参数）、`shop_prices.json`（商店价格记录）；群共享数据 `runtime.json`。
- **备份配置**：`backup_data/`（历史回溯版本 + 导出备份 + 导入暂存）。
- 旧布局（data.json/records.json）由格式转化插件一次性转译为新布局，旧文件保留供回滚。

## 插件数据文件位置

- 按 AstrBot 规范，全部运行数据存放于 `data/plugin_data/astrbot_plugin_signin`
  （与 2.x 同目录，升级后数据原地可用）；插件目录内仅保留插件本体与基准数据（Benchmark data）。

## 相对 2.3.0 的行为差异（已知，均为有意）

1. 结算任务按域拆分为独立幂等 flag（原为单一 plugin flag），单域失败不再影响其他域的当日标记。
2. 银行结算触发小时在插件挂载时读取一次，运行中修改该参数需重启生效。
3. `records/*` 端点未套 LAN 访问门（旧版套）：本机使用不受影响，远程访问建议开启网络层隔离。
4. 「游戏帮助」由核心聚合各插件 `add_help` 段动态生成。
5. 排行榜在榜昵称刷新移入排行榜指令处理器内（原在消息入口统一刷新）。
6. 调试模式标志挂于核心实例属性（`core.debug`），不持久化，重启消失（同旧行为）。
7. 历史/回溯的设置快照键 `feature_switches` → `features`（读取兼容旧键）。
