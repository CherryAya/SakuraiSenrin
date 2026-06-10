# 吹水记录

## 概览
吹水记录插件负责记录群聊活跃度，并提供个人画像、周期榜单、成就系统、活动赛季、矩阵合并建议以及一组仅超管可用的维护命令。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `profile` 查看个人画像: 查询默认个人画像。
- `profile-full` 查看完整画像: 查询完整版个人画像。
- `ranking` 查看周期榜单: 查询周榜、月榜、季榜、年榜和总榜。
- `achievement` 查看个人成就: 查询当前已解锁与下一步成就。
- `season-query` 查看赛季数据: 查询赛季概览、积分、排名与成就视图。
- `merge-confirm` 处理矩阵合并建议: 群管理员对智能建议发送 `#water.merge yes/no`。
- `admin-maintenance` 超管维护命令: 超管执行结算、回档、忽略、状态与赛季管理。

## 子功能详情
### `profile` 查看个人画像
- 摘要: 查询默认个人画像。
- 别名: 水王, 画像
- 指令: `#水王`
- Demo: water-profile.png
#### 说明
直接发送 `#水王` 会查询当前用户在当前群的个人画像简版。
#### 前置条件
需要在群聊中使用，且当前用户已有足够统计数据。
#### 完整流程
默认不带参数时走个人画像查询。
```demo
USER: #水王
BOT: [发送个人画像图片]
```
#### 失败情况
如果当前不在群聊里，会提示“这个命令要在群里用”；如果数据不足，会提示先多聊几天。

### `profile-full` 查看完整画像
- 摘要: 查询完整版个人画像。
- 别名: 完整画像, full
- 指令: `#水王 完整`
- Demo: water-profile-full.png
#### 说明
在 `#水王` 后补上 `完整`，会返回信息量更高的完整版画像。
#### 前置条件
需要在群聊中使用，并且用户已有可用画像数据。
#### 完整流程
输入 `完整` 关键字即可切换模式。
```demo
USER: #水王 完整
BOT: [发送完整版个人画像图片]
```
#### 失败情况
如果画像生成失败，帮助文档需要与实际渲染逻辑一起排查。

### `ranking` 查看周期榜单
- 摘要: 查询周榜、月榜、季榜、年榜和总榜。
- 别名: 榜单, 日榜, 月榜, 季榜, 年榜, 总榜
- 指令: `#水王 日榜|月榜|季榜|年榜|总榜`
- Demo: water-ranking.png
#### 说明
用户可直接通过快捷命令查询不同周期的榜单。
#### 前置条件
需要在群聊中使用，并且对应周期已有结算数据。
#### 完整流程
发送目标榜单名称即可。
```demo
USER: #水王 月榜
BOT: [发送月榜图片]
```
#### 失败情况
如果当前周期没有可用数据，会提示“这个周期还没有可用结算数据”。

### `achievement` 查看个人成就
- 摘要: 查询当前已解锁与下一步成就。
- 别名: 成就
- 指令: `#水王 成就`
- Demo: water-achievement.png
#### 说明
成就页面会汇总已解锁成就，并提示下一步最接近的目标。
#### 前置条件
需要在群聊中使用，并且用户至少有基础活跃记录。
#### 完整流程
发送成就子命令即可。
```demo
USER: #水王 成就
BOT: ===== 成就总览 =====
[成就进度文本或图片]
```
#### 失败情况
如果当前没有足够数据，可能会只返回基础提示，不会展示完整进度。

### `season-query` 查看赛季数据
- 摘要: 查询赛季概览、积分、排名与成就视图。
- 别名: 赛季
- 指令: `#水王 赛季 ...`
- Demo: water-season-query.png
#### 说明
赛季查询支持默认概览、当前赛季、赛季列表，以及指定 `season_id|名称` 后查询个人、群聊或矩阵视图。
#### 前置条件
需要当前系统已创建并发布至少一个赛季。
#### 完整流程
可先看列表，再查指定赛季。
```demo
USER: #水王 赛季 列表
BOT: [发送已发布赛季列表]
USER: #水王 赛季 spring2026 个人 积分
BOT: [发送赛季积分图]
```
#### 失败情况
如果赛季不存在、为空或命名歧义，会返回明确提示并建议先看赛季列表。

### `merge-confirm` 处理矩阵合并建议
- 摘要: 对智能建议发送 `#water.merge yes/no`。
- 别名: merge, 合并建议
- 指令: `#water.merge yes` / `#water.merge no`
- 权限: 群管理员
- Demo: water-merge-confirm.png
#### 说明
当系统判断某些群可能属于同一矩阵时，会提示管理员确认。管理员可通过 `yes` 或 `no` 完成一次性选择。
#### 前置条件
当前群存在待确认的合并建议，且执行者是群主、群管理员或超级管理员。
#### 完整流程
收到提示后执行一次选择即可。
```demo
BOT: 检测到矩阵合并建议...
USER: #water.merge yes
BOT: 已记录为“同意合并”啦
```
#### 失败情况
如果当前群没有待确认建议，或这个群已经做过选择，会返回锁定/无需操作提示。

### `admin-maintenance` 超管维护命令
- 摘要: 结算、回档、忽略、状态与赛季管理。
- 别名: water 管理, 超管命令
- 指令: `#water help` / `#water settle [YYYYMMDD] [-f|--force]` / `#water pardon <penalty_id>` / `#water ignore <group_id>` / `#water ignored` / `#water state` / `#water season create <season_id> <YYYYMMDD> <YYYYMMDD> <name>` / `#water season publish <season_id>` / `#water season archive <season_id>` / `#water season show <season_id>` / `#water season list [current|published|archived|draft]` / `#water season delete <season_id>`
- 权限: 超级管理员
- Demo: water-admin-maintenance.png
#### 说明
这组命令仅对超级管理员开放，覆盖 `help`、`settle`、`pardon`、`ignore`、`ignored`、`state`、`season create/publish/archive/show/list/delete`。
#### 前置条件
执行者必须是超级管理员。
#### 完整流程
先看帮助，再执行具体动作。
```demo
USER: #water help
BOT: ===== Water Admin =====
1. settle [YYYYMMDD] [-f|--force]
2. pardon <penalty_id>
USER: #water settle 20260608 -f
BOT: Water 结算任务执行中，请稍候...
```
#### 失败情况
参数错误、日期格式错误、事务已结算或对象不存在时，都会返回明确失败文案。
