# 吹水记录

## 概览
大水怪！说的就是你！哈哈！

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 用法
- Help: `water`
- 常用入口: `#我有多水`、`#我有多水 完整`、`#水王`、`#水王 成就`、`#水王 赛季`

## 说明
> [!TIP]
> 日常使用主要分成三类：个人画像、榜单查询、成就/赛季。
>
> 如果你只是想快速上手，通常先看 `profile`、`ranking`、`achievement` 就够了。

> [!TIP]
> 管理能力只有两类：
>
> - 群管理可用的 `today-report`
> - 仅超管可用的 `merge-confirm`
> - 仅超管可用的 `admin-maintenance`

> [!TIP]
> 榜单支持引导式输入。
>
> 和 `study` 一样，引导过程中可用 `revoke` / `recall` 取消。

## 子功能目录
- `profile` / `profile-full`: 个人画像简版与完整版。
- `ranking`: 三维榜单查询，支持快捷入口和引导模式。
- `today-report`: 群管理主动拉取今日群报告。
- `achievement`: 查看个人成就与下一步目标。
- `season-query`: 查看赛季列表、概览、积分、排名与成就。
- `merge-confirm`: 超管对矩阵合并建议回复 `yes` / `no`。
- `admin-maintenance`: 结算、回档、忽略和赛季管理等超管命令。

## 子功能详情
### `profile` 查看个人画像
- 摘要: 查询当前用户在当前群的默认画像。
- Hero: true
- Priority: 10
- 别名: 我有多水, 画像
- 指令: `#我有多水`
- Demo: water-profile.webp
#### 说明
> [!TIP]
> `profile` 默认返回简版画像。
>
> 适合日常快速看一眼自己最近有多活跃。
#### 前置条件
需要在群聊中使用，且已有基础统计数据。
#### 完整流程
```demo
USER: #我有多水
BOT: [发送个人画像图片]
```
#### 失败情况
非群聊或数据不足时会直接提示。

### `profile-full` 查看完整画像
- 摘要: 查询信息更完整的画像版本。
- Priority: 20
- 别名: 完整画像, full
- 指令: `#我有多水 完整`
- Demo: water-profile-full.webp
#### 说明
> [!TIP]
> 在默认画像指令后追加 `完整`，就会切到完整版。
>
> 当你想看更多维度时，再用这个入口即可。
#### 前置条件
需要在群聊中使用，且已有可用画像数据。
#### 完整流程
```demo
USER: #我有多水 完整
BOT: [发送完整版个人画像图片]
```
#### 失败情况
非群聊、数据不足或渲染失败时会返回提示。

### `ranking` 查看周期榜单
- 摘要: 按 主体 + 范围 + 时间 查询榜单。
- Hero: true
- Priority: 30
- 别名: 榜单
- 指令: `#水王` / `#水王 <主体> <范围> <时间>` / `#今日水王` / `#本周群榜` / `#本季矩阵榜`
- Demo: water-ranking.webp
#### 说明
> [!TIP]
> 榜单使用三维模型：
>
> - 主体：`用户榜 / 群聊榜 / 矩阵榜`
> - 范围：`本群 / 本矩阵 / 全局`
> - 时间：`日榜 / 周榜 / 月榜 / 季榜`

> [!TIP]
> 直接发送 `#水王` 时，系统会一次把主体、范围、时间三项都列出来。
>
> 你可以一条消息直接补全，也可以缺什么补什么；引导过程中支持 revoke / recall 取消。

> [!TIP]
> 常用快捷入口包括：
>
> - `#今日水王` / `#本周水王` / `#本月水王` / `#本季水王`
> - `#今日矩阵水王` / `#今日全局水王`
> - `#今日矩阵群榜` / `#今日群榜` / `#本周群榜`
> - `#今日矩阵榜` / `#本季矩阵榜`
#### 前置条件
需要在群聊中使用，且目标组合已有可用数据。
#### 完整流程
```demo
USER: #水王
BOT: 请选择榜单主体：用户榜 / 群聊榜 / 矩阵榜
请选择范围：本群 / 本矩阵 / 全局
请选择时间：日榜 / 周榜 / 月榜 / 季榜
三者组合为有效参数，请使用空格分割，例如：用户榜 本群 月榜
USER: 用户榜 本群 月榜
BOT: [发送用户榜 · 本群月榜图片]
USER: #今日水王
BOT: [发送用户榜 · 本群日榜图片]
```
#### 失败情况
缺少维度会继续引导；非法组合、无数据或快捷入口附带多余参数时会提示。

### `today-report` 查看今日群报告
- 摘要: 群管理主动拉取当前群今日实时报告。
- Advanced: true
- Priority: 130
- 别名: 水王日报, 今日报告
- 指令: `#水王 今日报告` / `#水王日报`
- Demo: water-ranking.webp
#### 说明
> [!TIP]
> `today-report` 是从当天的 `00:00` 到当前时刻。
>
> 同一个群共享 1 分钟冷却时间。
#### 前置条件
需要在群聊中使用，且触发者是群管理员、群主或超管。
#### 完整流程
```demo
USER: #水王日报
BOT: 凛凛统计中，请稍后喔……
BOT: [发送今日群报告图片]
```
#### 失败情况
非群聊、权限不足、无可用数据或冷却中时会提示。

### `achievement` 查看个人成就
- 摘要: 查看已解锁成就和下一步目标。
- Hero: true
- Priority: 40
- 别名: 成就
- 指令: `#水王 成就`
- Demo: water-achievement.webp
#### 说明
> [!TIP]
> `achievement` 适合快速查看当前成就进度。
>
> 想知道自己离下一个成就还差多少时，用这个入口最直接。
#### 前置条件
需要在群聊中使用，且至少有基础活跃记录。
#### 完整流程
```demo
USER: #水王 成就
BOT: [发送成就概览]
```
#### 失败情况
数据不足时只返回基础提示。

### `season-query` 查看赛季数据
- 摘要: 查看赛季列表、概览、积分、排名与成就。
- Advanced: true
- Priority: 140
- 别名: 赛季
- 指令: `#水王 赛季` / `#水王 赛季 列表` / `#水王 赛季 <season_id|名称> [个人|群聊|矩阵] [概览|积分|排名|成就]`
- Demo: water-season-query.webp
#### 说明
> [!TIP]
> 如果你还不确定赛季编号，先看赛季列表。
>
> 确认 `season_id` 后，再查询指定赛季的个人、群聊或矩阵视图会更稳。
#### 前置条件
系统中至少已有一个已发布赛季。
#### 完整流程
```demo
USER: #水王 赛季 列表
BOT: [发送已发布赛季列表]
USER: #水王 赛季 spring2026 个人 积分
BOT: [发送赛季积分图]
```
#### 失败情况
赛季不存在、为空或命名歧义时会提示先看列表。

### `merge-confirm` 处理矩阵合并建议
- 摘要: 超管对智能建议发送 `#water.merge yes/no`。
- Advanced: true
- Priority: 150
- 别名: merge, 合并建议
- 指令: `#water.merge yes` / `#water.merge no`
- 权限: 超级管理员
- Demo: water-merge-confirm.webp
#### 说明
> [!TIP]
> 系统给出矩阵合并建议后，会按管理员通知配置投递，再由超管决定是否合并。
>
> 常见写法就是 `#water.merge yes` 或 `#water.merge no`。
#### 前置条件
当前群存在待确认建议，且执行者是超管。
#### 完整流程
```demo
BOT: [管理员通知] 检测到矩阵合并建议...
USER: #water.merge yes
BOT: 已记录为“同意合并”啦
```
#### 失败情况
无待确认建议、重复处理或非超管操作时会提示。

### `admin-maintenance` 超管维护命令
- 摘要: 结算、回档、忽略、状态、日报预演、日报重推与赛季管理。
- Advanced: true
- Priority: 160
- 别名: water 管理, 超管命令
- 指令: `#water help` / `#water settle [YYYYMMDD] [-f|--force]` / `#water pardon <penalty_id>` / `#water ignore <group_id>` / `#water ignored` / `#water state` / `#water report-dryrun` / `#water report-push <YYYYMMDD>` / `#water season create <season_id> <YYYYMMDD> <YYYYMMDD> <name>` / `#water season publish <season_id>` / `#water season archive <season_id>` / `#water season show <season_id>` / `#water season list [current|published|archived|draft]` / `#water season delete <season_id>`
- 权限: 超级管理员
- Demo: water-admin-maintenance.webp
#### 说明
> [!TIP]
> 这里是超管维护入口的总说明页。
>
> 具体参数和可用子命令，以运行时输出的 `#water help` 为准。
#### 前置条件
执行者必须是超级管理员。
`report-dryrun` 与 `report-push` 仅允许超级管理员私聊触发，群聊里不会响应。
#### 完整流程
```demo
USER: #water help
BOT: ===== Water Admin =====
1. settle [YYYYMMDD] [-f|--force]
2. pardon <penalty_id>
USER: #water report-push 20260725
BOT: Water 日报重推任务执行中，请稍候...
```
#### 失败情况
参数错误、对象不存在、权限不足或状态不允许时会返回明确失败文案。
