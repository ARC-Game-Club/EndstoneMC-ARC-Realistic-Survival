# ARC Realistic Survival - 真实生存插件
[![Codacy Grade](https://app.codacy.com/project/badge/Grade/035827370d734c539602adbeca85f6d4)](https://app.codacy.com/gh/DEVILENMO/EndstoneMC-ARC-Realistic-Survival/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)
[![Version](https://img.shields.io/badge/version-v0.3.33-blue)](https://github.com/DEVILENMO/EndstoneMC-ARC-Realistic-Survival)


一个为 Endstone 服务器打造的真实生存插件，添加口渴值、营养学、丧尸病毒、物品效果等功能，让生存体验更加真实有趣。

## ✨ 功能特性

### 🚰 口渴值系统
- **动态口渴值**：玩家口渴值范围 0–100，耗尽后停留在 0 并进入严重脱水计时
- **自动衰减**：口渴值会随时间自动降低
- **移动加速消耗**：玩家移动时口渴值消耗速度会增加
- **移速联动**：口渴 >80 **+20%** 移速；30～80 无加成；<30 **-20%**；<15 **-50%**。再乘基速倍率（默认 **1.0** = 原版 `walk_speed` 0.10）；相对叠加，进服会按原版基速重算防叠乘
- **严重脱水**：口渴到 0 后开始计时，持续满 1 小时给予 `instant_damage` 255 秒杀
- **创造/旁观旁路**：切到创造或旁观时口渴/营养/感染显示为正常值并停止变动；切回生存/冒险时恢复原先数值
- **数据持久化**：玩家口渴值会自动保存到数据库
- **实时提示**：通过弹窗显示当前口渴值

### 🍎 营养学系统
- **四种营养素**：维生素 A、维生素 C、铁、蛋白质，各自独立 0-100 数值
- **缺素病症**：长期偏食触发夜盲症、坏血病、贫血、肌无力
- **症状分级**：健康 / 轻症 / 中症 / 重症，仅在等级变化时 Toast 提示
- **食物绑定**：每种食物可配置四种营养素加成（SQLite `nutrition_items` 表）
- **原生 API**：通过 Endstone `Effect` 与 `AttributeModifier` 实现减益，不污染实体 NBT

### 🧟 丧尸病毒系统
- **开关**：`infection_enabled`（默认 `false`）；关闭后不产生感染、不恶化、侧边栏不显示感染行
- **感染值 0-100**：被配置的生物攻击会增加感染值
- **可配置感染源**：支持精确实体（如 `minecraft:zombie`）或整命名空间（如 `minecraft:`），单独实体优先于命名空间规则
- **临界恶化**：默认超过 50 后每分钟 +5，低于 50 每分钟 -2
- **丧尸化**：感染满 100 时先清零感染并落库，再控制台 `kill` 击杀玩家、原地生成丧尸（清零不依赖是否杀死成功）

### 📊 弧光核心侧边栏（v0.3.12）
- 启动时向 `arc_core` 注册专属页面 **`ars_health`（真实生存）**
- 显示口渴、四种营养素及严重度；**仅在 `infection_enabled=true` 时显示感染行**
- 数值变化、进服、创造旁路切换时自动推送；需弧光核心 **v0.9.0+**
- 玩家可用 `/sidebar next` 翻到该页（页面数 ≥ 2 时也会自动轮播）

### 🍺 物品效果系统
- **自定义物品效果**：通过配置文件自定义任意物品的效果
- **口渴值变化**：消耗物品可以增加或减少口渴值
- **药水效果**：支持给予玩家药水效果（速度、力量等）
- **效果持续时间**：可配置效果持续时间

### ⚙️ 配置管理
- **游戏内配置面板**：使用 `/ars` 命令打开可视化配置界面
- **热重载**：使用 `/ars reload` 命令无需重启服务器即可重载配置
- **灵活配置**：支持配置衰减速度、移动倍率、初始值等参数

## 📋 系统要求

- **Python 版本**：推荐 Python 3.13+
- **Endstone API**：0.10+
- **依赖插件**：
  - （可选）`arc_core` 或 `umoney` - 用于经济系统集成

## 📦 安装方法

### 方法一：使用预编译的 wheel 文件

1. 下载最新的 `.whl` 文件
2. 将文件放入服务器的 `plugins` 目录
3. 重启服务器

### 方法二：从源码构建

```bash
# 克隆仓库
git clone https://github.com/DEVILENMO/EndStone-ARC-RealisticSurvival.git
cd EndStone-ARC-RealisticSurvival

# 构建插件
pip install build
python -m build

# 安装到服务器
cp dist/*.whl /path/to/server/plugins/
```

## 🔧 配置说明

### 主配置文件 (settings.yml)

插件首次运行会在 `ARCRealisticSurvival/settings.yml` 自动生成配置文件：

```yaml
thirst_tick_seconds: 10          # 口渴值衰减间隔（秒）
thirst_decay_per_tick: 1         # 每次衰减的口渴值
thirst_moving_multiplier: 2.0    # 移动时的衰减倍率
thirst_initial: 100              # 玩家初始口渴值
thirst_speed_bonus_threshold: 80    # 口渴 > 该值时加速
thirst_speed_bonus: 0.20             # 加速幅度（+20%）
thirst_speed_slow_threshold: 30      # 口渴 < 该值时减速
thirst_speed_slow: -0.20             # 减速幅度（-20%）
thirst_speed_severe_threshold: 15    # 口渴 < 该值时严重减速
thirst_speed_severe: -0.50           # 严重减速幅度（-50%）
thirst_fatal_seconds: 3600       # 口渴=0 持续该秒数后瞬间伤害秒杀
walk_speed_base_multiplier: 1.0  # 基速倍率（相对原版 0.10；默认 1.0）
nutrition_tick_seconds: 300      # 营养衰减间隔（秒）
nutrition_decay_per_tick: 1      # 每次衰减的营养值
nutrition_initial: 100           # 玩家初始营养值
nutrition_warn_cooldown_seconds: 300  # 症状提示冷却（秒）
infection_enabled: false         # 感染系统开关（默认关；丧尸服请设 true）
infection_tick_seconds: 12        # 感染 tick 间隔（秒）
infection_threshold: 50           # 恶化临界值
infection_growth_per_minute: 5    # 超临界每分钟增长
infection_decay_per_minute: 2     # 低于临界每分钟下降
infection_zombie_entity: minecraft:zombie  # 丧尸化时生成的实体（单值兼容）
infection_zombie_entities: zombie:zombie,zombie:zombie_runner,...  # 逗号分隔，满值随机生成其一
```

### 感染源配置 (infection_sources)

存储在 SQLite 表 `infection_sources`（首次启动自动播种常见僵尸类生物）：

| match_pattern | 含义 | 示例 delta |
|---------------|------|------------|
| `minecraft:zombie` | 精确匹配该实体 | +5/击 |
| `minecraft:` | 匹配整个命名空间下所有实体 | +2/击 |

单独实体配置优先于命名空间规则。

### 营养与缺素病

| 营养素 | 缺素病 | 主要症状 |
|--------|--------|----------|
| 维生素 A | 夜盲症 | 夜间随机短时黑暗 |
| 维生素 C | 坏血病 | 周期性掉血 + 虚弱 |
| 铁 | 贫血 | 最大生命下降 + 更易饥饿 |
| 蛋白质 | 肌无力 | 攻击下降 + 挖掘变慢 |

症状阈值：健康 ≥60，轻症 30-59，中症 10-29，重症 <10。

食物营养与口渴配置存储在 SQLite 表 `nutrition_items` / `thirst_items`。丧尸服可用 `scripts/seed_zombie_server_food.py` 批量写入 sgs_farm 模组与原版食物。

### 物品效果配置 (thirst_items)

在 `ARCRealisticSurvival/thirst_items.txt` 中配置物品效果：

```text
# 格式：物品ID|口渴值变动|效果名称|持续时间（秒）
# 效果名称和持续时间可选

# 示例：
COOKED_BEEF|-10                    # 熟牛肉减少10点口渴值
WATER_BOTTLE|50                    # 水瓶增加50点口渴值
COLA|50|SPEED|30                   # 可乐增加50口渴值并给予30秒速度效果
ENERGY_DRINK|40|STRENGTH|60        # 能量饮料增加40口渴值并给予60秒力量效果
```

### 语言文件

插件支持多语言，语言文件位于 `ARCRealisticSurvival/` 目录：
- `CN.txt` - 简体中文
- `EN.txt` - English

## 🎮 使用方法

### 命令列表

| 命令 | 权限 | 描述 |
|------|------|------|
| `/ars` | 全员（common） | 打开个人状态（口渴/营养/感染）；**仅 OP** 可见「配置管理」 |
| `/ars reload` | OP（config） | 重载配置 |
| `/ars nutrition` | 全员 | 打开营养学面板（查看四条营养值与食物表） |
| `/ars nutriset <玩家> <营养素> <0-100>` | OP | 调试：设置玩家指定营养素 |
| `/ars infection` | 全员 | 打开感染面板（感染值与感染源表） |
| `/ars infectset <玩家> <0-100>` | OP | 调试：设置玩家感染值 |
| `/heal <玩家>` | OP/控制台 | 管理用：治愈缺素并将四项营养设为 80（**不影响感染**；物品模组不用） |
| `/purify <玩家> <数量>` | 自身 / OP | 净化感染：感染值减少指定数量 |
| `/thirstadd <玩家> <增量>` | 自身 / OP | **物品模组对接**：增减口渴值 |
| `/nutriadd <玩家> <营养素\|all> <增量>` | 自身 / OP | **物品模组对接**：增减营养（`vitamin_a` / `vitamin_c` / `iron` / `protein` / `all`） |
| `/arseffect <玩家> <物品ID>` | 自身 / OP | **物品模组主入口**：按内置目录一次性施加口渴/营养/净化 |

> 普通玩家仅能对**自己**使用 `/thirstadd`、`/nutriadd`、`/purify`、`/arseffect`；改他人需 OP。权限节点：`arc_realistic_survival.command.item`（默认允许）。

### 与 ARC 真实生存物品包对接

行为包消耗药品/饮水后会执行：

```text
/arseffect <玩家名> arc:bottled_water
```

插件内置目录（`pack_effects.py`）示例：

| 物品 ID | 效果 |
|---------|------|
| `arc:bottled_water` | 口渴 +42 |
| `arc:vitamin_a_pill` | 维A +30 |
| `arc:vitamin_c_tablet` | 维C +30 |
| `arc:iron_supplement` | 铁 +30 |
| `arc:protein_shot` | 蛋白 +30 |
| `arc:multivitamin` | 四项各 +14 |
| `arc:field_ration_med` | 四项各 +8，口渴 +10 |
| `arc:recovery_injection` | 四项各 +20 |
| `arc:antiviral_weak` | 感染 -15（等同 purify） |
| `arc:antiviral_strong` | 感染 -40 |
| `arc:purge_serum` | 感染 -85 |

也可拆开调用，例如：`/thirstadd Steve 42`、`/nutriadd Steve vitamin_a 30`、`/purify Steve 15`。

### 权限节点

- `arc_realistic_survival.command.common` - `/ars` 个人状态等（默认：全体，对齐弧光核心）
- `arc_realistic_survival.command.config` - reload / 调试 set；配置管理按钮仅 `is_op` 显示（默认：OP）
- `arc_realistic_survival.command.admin` - `/heal` 及对任意玩家使用对接指令（默认：OP）
- `arc_realistic_survival.command.item` - 对自身使用对接指令（默认：全体）

### 游戏机制

1. **口渴值衰减**：
   - 静止状态：每 10 秒（默认）减少 1 点口渴值
   - 移动状态：衰减速度翻倍
   - 口渴最低为 0（上限 100）；到 0 后不再继续扣成负数
   - 移速 = 基速倍率（默认 1.0）× 口渴分段因子（>80→1.2；30–80→1.0；<30→0.8；<15→0.5），相对叠加到 `walk_speed`
   - 口渴为 0 持续满 1 小时：`instant_damage` 255

2. **补充口渴值**：
   - 消耗配置文件中设置的物品
   - 口渴值会实时显示在屏幕上
   - **死亡重置**为初始口渴值

3. **物品效果**：
   - 消耗特定物品可获得临时增益效果
   - 支持所有原版药水效果（通过 Endstone 原生 `Effect` API）

4. **营养学**：
   - 默认每 5 分钟四种营养素各 -1
   - 进食匹配 `nutrition_items` 的食物可补充对应营养
   - 使用 `/ars nutrition` 查看当前状态与食物营养表
   - **死亡不重置**，重生后按存档数值恢复缺素症状

5. **丧尸病毒**：
   - 被配置生物攻击增加感染值
   - 超过临界值持续恶化，满值丧尸化
   - **死亡重置**感染值为 0（丧尸化 / 普通死亡 / 重生均强制清零落库）
   - 使用 `/ars infection` 查看当前感染状态

## 🗄️ 数据存储

插件使用 SQLite 数据库存储玩家数据：

- **数据库位置**：`ARCRealisticSurvival/ars_survival.db`
- **存储内容**：
  - 玩家口渴值
  - 玩家四种营养素数值
  - 玩家感染值
  - 口渴/营养/感染物品与来源配置表
  - 最后更新时间
  - 玩家名称

数据会在以下情况自动保存：
- 玩家退出服务器
- 定时任务循环
- 服务器关闭

## 🔌 与其他插件集成

### 经济系统

插件支持以下经济插件：
- **ARC Core** (优先)
- **UMoney**

如果安装了以上任一插件，可实现经济系统相关功能。

## 🛠️ 开发

### 项目结构

```
src/endstone_arc_realistic_survival/
├── __init__.py              # 插件入口
├── arc_realistic_survival.py # 主插件逻辑
├── NutritionManager.py      # 营养学系统
├── ZombieVirusManager.py    # 丧尸病毒系统
├── pack_effects.py          # ARC 物品包效果目录（/arseffect）
├── DatabaseManager.py       # 数据库管理器
├── LanguageManager.py       # 语言管理器
└── SettingManager.py        # 设置管理器
```

### 构建插件

```bash
# 安装依赖
pip install -r requirements.txt

# 构建
python -m build
```

## 📝 更新日志

### v0.3.33
- 口渴/营养/感染改为内存管理：定时衰减、吃药吃食物不再每 tick 写库；退出、关服、死亡等关键点再落库
- 落库改为 `INSERT ... ON CONFLICT DO UPDATE`，不再先 SELECT
- 三套定时器合并为统一 20-tick（1 秒）循环，仍按各自 `*_tick_seconds` 间隔衰减
- 感染满值：每秒检测都执行 kill，不清零；死亡才清零；首次满值才提示并刷丧尸
- 口渴移速仅在数值变化且因子分段变化时写入，不再每秒重挂

### v0.3.32
- 口渴移速改为分段阈值：>80 +20%，30～80 无加成，<30 -20%，<15 -50%（原线性 45%～115%）
- 修复物品双重提示：`/arseffect` 吃药只保留「服用了…」toast，净化/感染/口渴底层不再重复弹窗
- 口渴物品 toast 显示实际增量；已满时提示「口渴已满」
- 插件 consume 事件识别 ARC 物品包并应用效果（水瓶等），2s 防重，不依赖 BP 调指令

### v0.3.31
- 感染满值击杀只保留控制台 `kill`，去掉 `kill @s` / `suicide` 回退

### v0.3.30
- 感染满值丧尸化改为控制台 `kill`（失败再回退 `kill @s` / `suicide`），不再用 `health=0`（满血时只会像扣 20 血且未必真正死亡）

### v0.3.29
- 修复定时任务 purecall 崩服：丧尸化延迟任务改用 `persist_by_xuid` 落库；口渴改速后恢复疾跑经 `_run_player_task` 重取在线玩家

### v0.3.28
- 修复口渴到 0 后仍继续衰减成负数（如 -104），导致喝水看起来加不上的问题：口渴钳制在 0–100，严重脱水改为在 0 时计时；进服自动修正历史负值

### v0.3.27
- `/arseffect` 顶部 Toast 改为中文：第一行「服用了{药品名}」，第二行「维生素A+30」等营养/口渴/感染变动摘要

### v0.3.26
- 修复载入配置时 `thirst_initial=100.0` 等浮点字符串导致 `int()` 报错；整数配置统一 `int(float(...))` 解析

### v0.3.25
- 口渴改 `walk_speed` 时保留疾跑：改前记录 `is_sprinting`，改后同 tick 与下一 tick 写回，避免掉口渴时被拉回走路速度

### v0.3.24
- 修复进食/饮水刷「命令权限级别错误：effect」：药水效果回退改为控制台 `dispatch_command`，不再让玩家自己执行 `/effect`

### v0.3.23
- `/ars` 对齐弧光核心：全员打开个人状态；仅 OP 显示「配置管理」按钮

### v0.3.22
- 口渴移速范围改为 **45%～115%**（口渴 0→因子 0.45，100→因子 1.15；配置 `thirst_speed_at_zero=-0.55` / `thirst_speed_at_full=0.15`）

### v0.3.21
- 物品模组对接指令：`/thirstadd`、`/nutriadd`、`/arseffect`；`/purify` 允许对自身调用
- 内置 ARC 真实生存物品效果目录（`pack_effects.py`）：饮水、缺素药、综合营养、抗病毒
- `/heal` 仍为管理用一键营养 80，物品逻辑不使用

### v0.3.20
- 进食/饮水 `[consume]` 诊断日志默认关闭；仅 `thirst_consume_debug=true` 时输出

### v0.3.19
- 移速基速倍率改回默认 `1.0`（不再默认 1.5）
- 修复进退服后 walk_speed 可能被重复乘除、越来越慢（乌龟速）的问题：进服按原版 0.10 重算

### v0.3.18
- 侧边栏营养严重度改为完整文案：健康 / 轻症 / 中症 / 重症

### v0.3.17
- 移速基速加快 50%：新增 `walk_speed_base_multiplier`（默认 1.5，相对原版 walk_speed 0.10）

### v0.3.16
- 侧边栏重点色改为 §6 橙金，与弧光核心灰橙配色一致

### v0.3.15
- 感染系统可开关：`infection_enabled` 默认 `false`；关闭后不跑感染逻辑，侧边栏也不显示感染

### v0.3.14
- 侧边栏 `ars_health` 配色与文案对齐弧光核心（§8/§7/§f/§b，中文标签冒号格式）

### v0.3.13
- 新增高级命令（仅 OP/控制台）：`/heal <玩家>` 治愈缺素并将营养设为 80；`/purify <玩家> <数量>` 净化感染值

### v0.3.12
- ✅ **弧光核心侧边栏**：注册 `ars_health` 页面，展示口渴、营养四项与严重度、丧尸感染值/状态；数值变化时自动推送（需 arc_core ≥ 0.9.0）

### v0.3.11
- 强化死亡清零感染：任意死亡立刻清零落库；同时清掉创造模式快照中的感染，避免切回生存被旧值写回

### v0.3.10
- 感染满值：先清零落库再击杀；清零与是否成功死亡无关，避免感染残留

### v0.3.9
- 口渴移速改为「调整因子」相对叠加：`walk_speed = walk_speed / 旧因子 × 新因子`，兼容其他插件的移速修改

### v0.3.8
- 口渴移速改用官方 `Player.walk_speed`（默认 0.10），不再使用 AttributeModifier
- 修复因感染丧尸化/死亡后感染值可能未清零：死亡与重生均强制清零并落库

### v0.3.7
- 口渴移速改为 0–100 线性映射：0 为 -75%，100 为 +125%（不再用 70/30 阈值）
- 口渴允许低于 0；低于 0 持续满 1 小时给予瞬间伤害 255
- 每个口渴 tick 重挂移速修饰符，避免掉到 0 后修饰符丢失导致「健步如飞」

### v0.3.6
- 修复 `/ars` 权限：`default: False` 改为与弧光核心一致——全员 `common`，配置类 `config` 默认 `op`

### v0.3.5
- 兼容 Endstone 0.11.x：药水效果优先用文档中的 `endstone.potion.Effect`，不可用时回退 `/effect` 命令，修复插件无法加载

### v0.3.4
- 创造/旁观模式：口渴、营养、感染统一设为正常值并停止变动；仅在 `PlayerGameModeChangeEvent` 时切换状态
- 切回生存/冒险时从快照恢复真实数值（退出创造不会把「正常值」写入数据库）

### v0.3.3
- 口渴移速联动：≥70 加速 20%，≤30 减速 20%（Endstone `Attribute.MOVEMENT_SPEED`）
- 新增丧尸服食物播种脚本 `scripts/seed_zombie_server_food.py`（sgs_farm 全量 + 原版食物口渴/营养）
- 修复退出时未清理营养修饰符的问题

### v0.3.2
- 死亡重置口渴值与感染值；营养值不重置，重生后恢复缺素症状

### v0.3.1
- 丧尸化支持 `infection_zombie_entities` 多实体随机生成
- 新增丧尸服感染源播种脚本 `scripts/seed_zombie_server_infection.py`

### v0.3.0
- 新增丧尸病毒感染系统（可配置感染源、临界恶化、满值丧尸化）
- `/ars infection` 感染面板与 `/ars infectset` 调试命令
- OP 配置面板增加感染相关参数

### v0.2.0
- 新增营养学系统（维生素 A/C、铁、蛋白质）
- 四种缺素病：夜盲症、坏血病、贫血、肌无力
- `/ars nutrition` 营养面板与 `/ars nutriset` 调试命令
- 修复 thirst_items 药水效果未生效的问题（改用原生 Effect API）
- 首次启动自动播种 30 种原版食物营养配置

### v0.1.0
- 初始版本发布
- 实现口渴值系统
- 实现物品效果系统
- 支持游戏内配置
- 支持热重载

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

本项目采用 [LICENSE](LICENSE) 中规定的许可证。

## 👤 作者

**DEVILENMO**
- Email: DEVILENMO@gmail.com
- GitHub: [@DEVILENMO](https://github.com/DEVILENMO)

## 🔗 相关链接

- [Endstone 官方文档](https://endstone.dev)
- [项目主页](https://github.com/DEVILENMO/EndstoneMC-ARC-Button-Shop-Plugin)

---

如有问题或建议，请通过 GitHub Issues 联系我们！
