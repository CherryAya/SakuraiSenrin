# 词库模块

## 概览
低资源优先的群聊词库系统，覆盖词条添加、搜索、删除恢复、运行时自动回复，以及图片指纹和审核前待生效流程。

## 权限与触发
- 触发方式: 指令触发 / 被动触发
- 权限: 普通用户

## 用法
- Help: `wordbank`
- 常用入口: `#wordbank add`、`#wordbank search`、`#wordbank delete`、`#wordbank restore`、`#苦瓜榜`

## 说明
- 日常最常用的是 `add`、`search`、`delete`、`restore` 和被动触发。
- 添加后默认进入 `pending`，审核通过前不会参与自动回复；审核流程单独见 `#help 词库审核`。
- 文本与图文触发统一按严格匹配处理；图片优先走 `MD5` 精确命中，未命中再做相似图匹配。

## 子功能目录
- `add`: 添加文本、图片或图文混排词条。
- `add-scope` / `add-prob` / `add-weight` / `add-role` / `add-call`: 添加时可选的高级规则。
- `search` / `group`: 搜索词条并展开某个 trigger group。
- `rank`: 查看已通过审核词条创建排行。
- `delete` / `restore`: 软删除与恢复词条。
- `trigger` / `response`: 修改触发词、概率、响应内容和权重。
- `passive`: 普通消息或事件命中后自动回复。
- `reply-shortcut`: 回复机器人词库消息后直接做详情、删除、恢复或快捷修改。

## 子功能详情
### `add` 基础添加
- 摘要: 添加文本回复、图片回复、图片触发或图文混排词条。
- Hero: true
- Priority: 10
- 别名: 添加, 学习, wordbank.add, 添加词条
- 指令: `wordbank add 触发词 => 响应词`
- 兼容指令: `wordbank.add 触发词 => 响应词`、`添加词条 触发词 => 响应词`
- Demo: wordbank-add.png
#### 说明
支持四类常见写法:
- `文本 => 文本`
- `文本 + 图片回复`
- `图片触发 => 文本`
- `文本 => 图文混排回复`

不带参数发送 `wordbank add` 会进入引导模式，顺序是 `触发词 -> 响应词 -> 生效范围 -> 高级选项`。图片会在后台下载处理；最终无法下载或识别时不会写入词库。提交成功后状态为 `pending`，审核通过前不参与被动匹配。
#### 常用示例命令
- `#wordbank add 晚安 => 做个好梦`
- `#wordbank add 晚安 [图片]`
- `#wordbank add [图片] => 这张图我记住了`
- `#wordbank add 晚安 => 做个好梦 [图片]`
- `#wordbank.add`
#### 前置条件
当前消息需要通过运行时检查。
#### 完整流程
```demo
USER: #wordbank.add
BOT: 请输入触发词，或直接发送图片作为图片触发：
USER: 晚安
BOT: 请输入响应词，或发送图片作为图片回复：
USER: 做个好梦
BOT: 请选择生效范围：
1. 当前群（默认）
2. 所有群
3. 仅自己
4. 仅私聊
USER: 1
BOT: 是否需要高级选项？发送 n 跳过；需要请直接输入参数，例如 --prob 0.5 --weight 3 --role admin --call 60:0:3
USER: n
BOT: 词条已提交审核
```
#### 失败情况
触发词或响应词为空、图片失败、规则冲突、参数越界，或引导中输入 `revoke` / `recall` 时会提示并终止。

### `add-scope` 生效范围选项
- 摘要: 选择词条在哪些对象和场景上生效。
- Advanced: true
- Priority: 110
- 别名: scope, 范围, 生效范围
- 指令: `wordbank add 触发词 => 响应词 --scope current_group|all_groups|self|private_only`
- Demo: wordbank-add-scope.png
#### 说明
`current_group` 当前群，`all_groups` 所有群，`self` 仅创建者，`private_only` 仅私聊。引导模式里会单独询问范围，因此通常不需要重复写 `--scope`。
#### 常用示例命令
- `#wordbank add 大家好 => 大家好呀 --scope current_group`
- `#wordbank add 早上好 => 早安 --scope all_groups`
- `#wordbank add 个人提醒 => 别忘记喝水 --scope self`
- `#wordbank add 私聊一下 => 收到 --scope private_only`
#### 前置条件
需要明确词条是群内通用、跨群、仅自己还是仅私聊。
#### 失败情况
scope 拼写错误或重复设置时会返回说明。

### `add-prob` 触发概率选项
- 摘要: 控制命中某个 trigger group 后是否真正回复。
- Advanced: true
- Priority: 120
- 别名: prob, 概率, 触发概率
- 指令: `wordbank add 触发词 => 响应词 --prob 0.0-1.0`
- Demo: wordbank-add-prob.png
#### 说明
`--prob` 控制整组 trigger 的触发概率，适合给高频短词降噪；通过后再按响应权重选具体回复。
#### 常用示例命令
- `#wordbank add 早 => 早安呀 --prob 0.5`
- `#wordbank add 哈哈 => 笑死 --prob 0.2`
#### 前置条件
建议只用于容易高频误触的词条。
#### 失败情况
概率不是数字或不在 `0-1` 范围内时会提示。

### `add-weight` 权重选项
- 摘要: 控制同组多条响应之间的抽样倾向。
- Advanced: true
- Priority: 130
- 别名: weight, 权重
- 指令: `wordbank add 触发词 => 响应词 --weight 1-5`
- Demo: wordbank-add-weight.png
#### 说明
权重越高，被选中的概率越大；不影响 trigger 本身是否触发。
#### 常用示例命令
- `#wordbank add 晚安 => 做个好梦 --weight 5`
- `#wordbank add 晚安 => 晚安晚安 --weight 1`
#### 前置条件
适合同一触发词下存在多条候选响应的场景。
#### 失败情况
权重不是 `1-5` 的整数时会提示。

### `add-role` 角色限制选项
- 摘要: 限制只有特定群角色才能触发词条。
- Advanced: true
- Priority: 140
- 别名: role, roles, 角色限制
- 指令: `wordbank add 触发词 => 响应词 --role owner|admin|member`
- Demo: wordbank-add-role.png
#### 说明
常用于把提醒或彩蛋限定给群主、管理员或普通成员。
#### 常用示例命令
- `#wordbank add 管理员集合 => 管理员请看这里 --role admin`
- `#wordbank add 群主确认 => 已收到 --role owner`
- `#wordbank add 萌新报道 => 欢迎加入 --role member`
#### 前置条件
只在群聊里有实际区分意义。
#### 失败情况
角色值非法时会提示。

### `add-call` 调用次数窗口选项
- 摘要: 按时间窗口限制触发次数区间。
- Advanced: true
- Priority: 150
- 别名: call, 调用次数窗口
- 指令: `wordbank add 触发词 => 响应词 --call window:min:max`
- Demo: wordbank-add-call.png
#### 说明
`window:min:max` 表示在指定秒数窗口内，只在第 `min` 到第 `max` 次之间允许触发；适合做“第几次才回复”或“过多后静默”的规则。
#### 常用示例命令
- `#wordbank add 连续打卡 => 你今天很活跃 --call 3600:3:10`
- `#wordbank add 又来了 => 欢迎回来 --call 60:2:5`
#### 前置条件
适合限制高频触发词的回复节奏。
#### 失败情况
结构错误、数字非法、窗口超限或 `min > max` 时会提示。

### `search` 搜索词条
- 摘要: 统一搜索文本、图片或图文混排词条。
- Hero: true
- Priority: 20
- 别名: 查询, 搜索, wordbank.search, 搜索词条
- 指令: `wordbank search [关键词] [图片] [--field all|trigger|response] [--creator 账号] [--page 页码] [--limit 每页数量]`
- 兼容指令: `wordbank.search [关键词]`、`搜索词条 [关键词]`
- Demo: wordbank-search.png
#### 说明
默认同时搜触发词和响应词，也支持按图片相似度搜索。结果会以卡片展示，可继续发送 `page <页码>`、`详情 <group_id>`、`del <序号...>` 或 `exit` 管理当前搜索会话。
#### 前置条件
无。
#### 完整流程
```demo
USER: #wordbank search 晚安
BOT: [词库搜索结果卡片]
BOT: 可发送 `page 页码`、`详情 组号`、`del 序号...` 或 `exit`
USER: 详情 271
BOT: [展开 trigger group 271]
```
#### 失败情况
没有命中、筛选参数非法或会话指令无效时会返回明确提示。

### `rank` 苦瓜榜
- 摘要: 查看全局已通过审核词条创建排行海报。
- Priority: 40
- 别名: 苦瓜榜, rank, 排行, 榜
- 指令: `wordbank rank [周榜|月榜|季榜|总榜]`
- 兼容指令: `苦瓜榜`
- Demo: wordbank-rank.png
#### 说明
默认返回月榜，也支持周榜、季榜和总榜；海报展示 Top 10、冠军占比、覆盖群数等信息。
#### 常用示例命令
- `#苦瓜榜`
- `#苦瓜榜 周榜`
- `#wordbank rank 总榜`
#### 前置条件
无。
#### 完整流程
```demo
USER: #苦瓜榜
BOT: [发送苦瓜榜海报]
```
#### 失败情况
空榜时返回空榜结果；渲染失败会降级为文本榜单。

### `group` 组详情
- 摘要: 直接查看某个 trigger group 的完整详情。
- Advanced: true
- Priority: 160
- 别名: group, 展开
- 指令: `wordbank group <group_id> [--page 页码]`
- Demo: wordbank-search.png
#### 说明
适合从搜索结果继续展开，查看 trigger、本组状态和各条响应详情；详情页内可继续翻页。
#### 常用示例命令
- `#wordbank group 271`
- `#wordbank group 271 --page 3`
#### 前置条件
需要知道 `group_id`。
#### 完整流程
```demo
USER: #wordbank group 271
BOT: Trigger Group #271
...
```
#### 失败情况
group 不存在、页码越界或参数非法时会提示。

### `delete` 删除词条
- 摘要: 软删除指定词条。
- Priority: 50
- 别名: 删除, remove, wordbank.delete, wordbank.del, 删除词条
- 指令: `wordbank delete <id>`
- 兼容指令: `wordbank.delete <id>`、`wordbank.del <id>`、`删除词条 <id>`
- Demo: wordbank-delete.png
#### 说明
删除后不再参与匹配，但数据仍保留，可后续恢复。
#### 前置条件
需要提供数字词条 ID，且当前用户有对应操作权限。
#### 完整流程
```demo
USER: #wordbank delete 12
BOT: 词条 #12 已删除。
```
#### 失败情况
ID 非法、词条不存在或权限不足时会提示。

### `restore` 恢复词条
- 摘要: 恢复已删除词条。
- Priority: 60
- 别名: 恢复, wordbank.restore, 恢复词条
- 指令: `wordbank restore <id>`
- 兼容指令: `wordbank.restore <id>`、`恢复词条 <id>`
- Demo: wordbank-restore.png
#### 说明
恢复后会重新加入运行时索引。
#### 前置条件
目标词条已被删除，且当前用户符合恢复权限。
#### 完整流程
```demo
USER: #wordbank restore 12
BOT: 词条 #12 已恢复。
```
#### 失败情况
ID 非法、词条不存在或权限不足时会提示。

### `trigger` 修改触发词与触发概率
- 摘要: 修改 trigger group 的整体概率或触发内容。
- 指令:
  - `wordbank trigger prob <group_id> <0.0-1.0>`
  - `wordbank trigger set <group_id> <新触发内容>`
#### 说明
`trigger prob` 改整组命中概率；`trigger set` 改触发内容。修改触发内容后，该组会重新进入 `pending`，需要重新审核。
#### 常用示例命令
- `#wordbank trigger prob 271 0.3`
- `#wordbank trigger set 271 晚安啦`
#### 前置条件
仅超级用户可执行。
#### 失败情况
group 不存在、内容冲突、参数非法或权限不足时会提示。

### `response` 修改响应词与响应权重
- 摘要: 修改某条响应内容或权重。
- 指令:
  - `wordbank response weight <id> <1-5>`
  - `wordbank response set <id> <新响应内容>`
#### 说明
`response weight` 只影响同组内抽样；`response set` 修改内容后会重新进入 `pending`。
#### 常用示例命令
- `#wordbank response weight 12 5`
- `#wordbank response set 12 做个好梦`
#### 前置条件
超级用户可修改任意响应；普通用户仅可改自己创建的响应。
#### 失败情况
ID 非法、对象不存在、参数越界或权限不足时会提示。

### `passive` 被动匹配
- 摘要: 普通消息或事件命中词条后自动回复。
- Hero: true
- Priority: 30
- 别名: 被动, 自动回复
- 指令: 无
- Demo: wordbank-passive.png
#### 说明
文本优先走内存索引，图片只在消息含图时下载并匹配。除普通文本外，也支持事件触发词，例如:
- `event:at`
- `event:poke`
- `event:join` / `event:group_increase`
- `event:leave` / `event:group_decrease`

检测到撤回类信号或规则不通过时会直接跳过，不回复。
#### 常用示例命令
- `#wordbank add event:at => 我在`
- `#wordbank add event:poke => 别戳啦`
- `#wordbank add event:join => 欢迎加入`
#### 前置条件
词条已启用，且当前消息通过运行时检查。
#### 完整流程
```demo
USER: [戳一戳机器人]
BOT: 别戳啦
```
#### 失败情况
未命中、规则不通过、概率未命中、图片失败或命中撤回信号时不回复。

### `reply-shortcut` 回复管理
- 摘要: 回复机器人词库消息后，用短命令直接管理对应词条。
- Advanced: true
- Priority: 170
- 别名: info, 详情, history, 摘要, del, delete, 删除, rst, restore, 恢复, trigger prob, trigger set, response weight, response set
- 指令: 回复机器人词库自动回复并发送 `info` / `history` / `del` / `rst` / `trigger prob` / `trigger set` / `response weight` / `response set`
- 兼容指令: `详情`、`history`、`删除`、`恢复`
- Demo: wordbank-reply-shortcut.png
#### 说明
回复词库自动回复后，可以直接查看详情、摘要、删除、恢复，或做 `trigger` / `response` 快捷修改。搜索结果卡片和 group 详情页的上下文展开也复用这套记录。审批通知里的 `y / n` 不在这里，见 `#help 词库审核`。
#### 前置条件
必须回复一条由词库系统记录过上下文的机器人消息，且当前用户满足对应权限。
#### 完整流程
```demo
USER: [回复机器人词库消息] @机器人 详情
BOT: 词条详情 #12
USER: [回复机器人词库消息] @机器人 删除
BOT: 词条 #12 已删除。
```
#### 失败情况
未回复有效上下文、对象已清理或权限不足时会提示。
