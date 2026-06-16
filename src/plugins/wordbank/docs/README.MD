# 词库模块

## 概览
词库模块提供低资源优先的群聊词库能力。`wordbank` 负责完整词库管理、运行时匹配、图片指纹和日志；`study` 只是快捷添加入口，内部仍写入同一套词库服务。运行时使用内存索引匹配文本、图片和事件触发词，普通文本消息路径不查询数据库；新增词条先进入 `pending` 待审状态，审核通过后通过延迟重建加入索引，删除、恢复后也会刷新索引。检测到 `revoke` / `recall` 相关信号时会直接跳过匹配，不回复也不写日志。普通用户常用的添加、搜索、删除、恢复和被动匹配说明都放在这里；管理员审批、待审核列表和回复审批通知快捷操作已拆到单独文档 `#help 词库审核`。

## 权限与触发
- 触发方式: 指令触发 / 被动触发
- 权限: 普通用户

## 子功能目录
- `add` 基础添加: 最常见的文本、图片和图文混排添加方式。
- `add-scope` 生效范围选项: `current_group` / `all_groups` / `self` / `private_only`。
- `add-prob` 触发概率选项: `--prob` 的作用和常见配置。
- `add-weight` 权重选项: `--weight` 如何影响候选词条和响应选择。
- `add-role` 角色限制选项: `--role owner|admin|member`。
- `add-call` 调用次数窗口选项: `--call window:min:max`。
- `search` 搜索词条: 按触发词或响应词查询现有词条，并可从结果卡片展开某个 trigger group。
- `group` 组详情: 直接查看某个 trigger group 的完整图文详情。
- `rank` 苦瓜榜: 查看全局已通过审核词条创建排行海报，支持周榜、月榜、季榜、总榜。
- `delete` 删除词条: 软删除指定词条并刷新索引。
- `restore` 恢复词条: 恢复已删除词条并刷新索引。
- `passive` 被动匹配: 文本、图片、@、戳一戳或入退群事件命中词条后自动回复。
- `reply-shortcut` 回复管理: 回复机器人自动回复后查看详情、摘要、删除或恢复对应词条。

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
文本和图文触发统一使用严格匹配。`wordbank add 触发词 [图片]` 表示文本触发、图片回复；`wordbank add [图片] => 响应词` 表示图片触发、文本回复；`wordbank add 触发词 => 响应词 [图片]` 表示文本触发、图文混排回复。引导模式收到图片后会在后台下载并自动重试，用户可继续配置范围和高级选项；最终仍无法下载或识别时，会提示词条设置失败且不写入词库。图片入库时会先用原始字节的 `MD5` 做精确去重；运行时图片触发会先尝试文件名、URL 或原始字节中的 `MD5` 精确命中，未命中时再计算 `dHash`、`pHash`、尺寸和文件大小；静态图按原图取特征，GIF 等动图按代表帧取特征做近似归并。动图会优先保存为 animated WebP，转码失败时回退为原始格式，因此响应图片会尽量保留动画效果。添加完成后词条状态为 `pending`，审核通过前不会参与被动匹配；具体审核流程请查看 `#help 词库审核`。引导任一步收到 `revoke` / `recall` 会发送取消提示并中止，连续输错 3 次会强制终止对话。
#### 常用示例命令
- `#wordbank add 晚安 => 做个好梦`
- `#wordbank add 晚安 [图片]`
- `#wordbank add [图片] => 这张图我记住了`
- `#wordbank add 晚安 => 做个好梦 [图片]`
- `#wordbank.add`
#### 前置条件
当前用户所在群聊需要通过运行时检查。
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
ID: 12
状态: pending
触发: 晚安
响应: 做个好梦
范围: current_group
概率: 1
权重: 3
管理员通过前不会触发。
SYSTEM: 审核完成前，这条词条不会参与被动匹配；审核文档请查看 #help 词库审核。
USER: #wordbank add 是这张图喔 [图片]
BOT: 词条已提交审核
ID: 13
状态: pending
触发: 是这张图喔
响应: [图片:7]
范围: current_group
概率: 1
权重: 3
管理员通过前不会触发。
USER: #wordbank add [图片] => 是这张图喔
BOT: 词条已提交审核
ID: 14
状态: pending
触发: [图片:7]
响应: 是这张图喔
范围: current_group
概率: 1
权重: 3
管理员通过前不会触发。
```
#### 失败情况
触发词或响应词为空、图片下载失败、图片无法识别、规则字段冲突、概率和权重越界时会返回当前群语言的原因；引导中若检测到 `revoke` / `recall` 会发送取消提示并中止，连续输错 3 次会强制终止对话。

### 严格匹配说明
- 摘要: 当前词库的文本与图文触发统一使用严格匹配。
#### 说明
触发消息会先被标准化为固定 `MessageShape`，再按精确指纹命中同一 trigger group。也就是说，文本内容、`@`、图片位置和图文顺序都属于匹配条件的一部分；图片部分会先尝试 `MD5` 精确命中，未命中时再回退到相似图匹配。
#### 常用示例
- `#wordbank add 晚安 => 做个好梦`
- `#wordbank add [图片] => 是这张图`
- `#wordbank add [CQ:at,qq=10001]早安 => 收到`

### `add-scope` 生效范围选项
- 摘要: 选择词条在哪些场景和对象上生效。
- Advanced: true
- Priority: 110
- 别名: scope, 范围, 生效范围
- 指令: `wordbank add 触发词 => 响应词 --scope current_group|all_groups|self|private_only`
- Demo: wordbank-add-scope.png
#### 说明
`current_group` 仅当前群有效，`all_groups` 所有群有效，`self` 仅词条创建者可触发，`private_only` 仅私聊可触发。引导模式里 scope 已单独询问，因此高级选项里不能重复设置 `--scope`。
#### 常用示例命令
- `#wordbank add 大家好 => 大家好呀 --scope current_group`
- `#wordbank add 早上好 => 早安 --scope all_groups`
- `#wordbank add 个人提醒 => 别忘记喝水 --scope self`
- `#wordbank add 私聊一下 => 收到 --scope private_only`
#### 前置条件
需要明确自己想限制的是群范围、私聊范围还是个人范围。
#### 完整流程
```demo
USER: #wordbank add 私聊一下 => 收到 --scope private_only
SYSTEM: 这条词条只会在私聊里生效，群聊消息不会命中。
USER: [私聊机器人] 私聊一下
BOT: 收到
USER: [群聊里发送] 私聊一下
SYSTEM: 因为 scope=private_only，所以群聊不触发。
```
#### 失败情况
scope 拼写错误或同时设置多个 scope 时会返回明确说明。

### `add-prob` 触发概率选项
- 摘要: 控制某个 trigger group 命中后是否真正触发回复。
- Advanced: true
- Priority: 120
- 别名: prob, 概率, 触发概率
- 指令: `wordbank add 触发词 => 响应词 --prob 0.0-1.0`
- Demo: wordbank-add-prob.png
#### 说明
`--prob 1` 表示必中，`--prob 0.5` 表示大约一半概率触发。这里的概率属于 trigger，而不是单条响应；只要 trigger 本次通过，后续具体回哪条响应仍由响应权重决定。这个设置适合给高频短词降噪。
#### 常用示例命令
- `#wordbank add 早 => 早安呀 --prob 0.5`
- `#wordbank add 哈哈 => 笑死 --prob 0.2`
- `#wordbank add event:poke => 别戳啦 --prob 1`
#### 前置条件
建议只在容易高频误触的词条上使用。
#### 完整流程
```demo
USER: #wordbank add 早 => 早安呀 --prob 0.5
SYSTEM: 命中后只有约一半概率回复，适合短词降噪。
USER: 早
BOT: 早安呀
USER: 早
SYSTEM: 这次概率未命中，因此不回复。
```
#### 失败情况
概率超出 0 到 1 范围，或不是数字时会返回明确说明。

### `add-weight` 权重选项
- 摘要: 控制同一 trigger 已触发后，多条响应之间被选中的倾向。
- Advanced: true
- Priority: 130
- 别名: weight, 权重
- 指令: `wordbank add 触发词 => 响应词 --weight 1-5`
- Demo: wordbank-add-weight.png
#### 说明
多个候选 trigger 同时满足条件时，会先比较 scope 优先级，再决定命中哪组 trigger；进入某个 trigger group 后，如果它有多条响应，响应会按各自 `weight` 抽样选择。`weight` 不再承担 trigger 是否触发的职责。
#### 常用示例命令
- `#wordbank add 晚安 => 做个好梦 --weight 5`
- `#wordbank add 晚安 => 晚安晚安 --weight 1`
- `#wordbank add 管理员集合 => 管理员请看这里 --weight 5`
#### 前置条件
适合多条同类词条共存时使用。
#### 完整流程
```demo
USER: #wordbank add 晚安 => 做个好梦 --weight 5
USER: #wordbank add 晚安 => 晚安晚安 --weight 1
SYSTEM: 两条词条都命中“晚安”时，权重 5 的那条更容易被选中。
```
#### 失败情况
权重不是 1 到 5 的整数时会返回明确说明。

### `add-role` 角色限制选项
- 摘要: 限制只有特定群角色才能触发词条。
- Advanced: true
- Priority: 140
- 别名: role, roles, 角色限制
- 指令: `wordbank add 触发词 => 响应词 --role owner|admin|member`
- Demo: wordbank-add-role.png
#### 说明
常见用法是把某些提醒限定为管理员或群主专用。`member` 表示只允许普通成员触发；不设置则默认不限角色。
#### 常用示例命令
- `#wordbank add 管理员集合 => 管理员请看这里 --role admin`
- `#wordbank add 群主确认 => 已收到 --role owner`
- `#wordbank add 萌新报道 => 欢迎加入 --role member`
#### 前置条件
只在群聊里有实际区分意义。
#### 完整流程
```demo
USER: #wordbank add 管理员集合 => 管理员请看这里 --role admin
SYSTEM: 只有管理员身份发送这句话时才会命中。
USER: [普通成员] 管理员集合
SYSTEM: 因为 role 不满足，所以这次不会触发。
USER: [群管理员] 管理员集合
BOT: 管理员请看这里
```
#### 失败情况
角色值不是 `owner`、`admin`、`member` 时会返回明确说明。

### `add-call` 调用次数窗口选项
- 摘要: 按时间窗口限制触发次数区间。
- Advanced: true
- Priority: 150
- 别名: call, 调用次数窗口
- 指令: `wordbank add 触发词 => 响应词 --call window:min:max`
- Demo: wordbank-add-call.png
#### 说明
`window` 是统计窗口秒数，`min` 是最少调用次数，`max` 是最多调用次数。当前窗口上限为 3 个月（90 天）。示例 `--call 3600:3:10` 表示 1 小时窗口内至少第 3 次才触发，超过第 10 次不再触发。
#### 常用示例命令
- `#wordbank add 连续打卡 => 你今天很活跃 --call 3600:3:10`
- `#wordbank add 又来了 => 欢迎回来 --call 60:2:5`
- `#wordbank add 高频词 => 稍微歇一会儿 --call 300:5:0`
#### 前置条件
适合防止一条高频词条每次都回复。
#### 完整流程
```demo
USER: #wordbank add 连续打卡 => 你今天很活跃 --call 3600:3:10
SYSTEM: 1 小时内前两次不会触发，从第 3 次开始可能触发，超过第 10 次后又会停止。
USER: 连续打卡
SYSTEM: 第 1 次，未达到最小调用次数。
USER: 连续打卡
SYSTEM: 第 2 次，未达到最小调用次数。
USER: 连续打卡
BOT: 你今天很活跃
```
#### 失败情况
`window:min:max` 结构错误、字段不是整数、窗口小于等于 0、窗口超过 3 个月，或 `min > max` 时会返回明确说明。

### `search` 搜索词条
- 摘要: 统一搜索文本、图片或图文混排查询。
- Hero: true
- Priority: 20
- 别名: 查询, 搜索, wordbank.search, 搜索词条
- 指令: `wordbank search [关键词] [图片] [--field all|trigger|response] [--creator 账号] [--page 页码] [--limit 每页数量]`
- 兼容指令: `wordbank.search [关键词]`、`搜索词条 [关键词]`
- Demo: wordbank-search.png
#### 说明
搜索按需查询数据库，默认每页 10 条，最多每页 20 条。默认 `--field all`，同时检索触发词和响应词；`--field trigger` 只看触发词，`--field response` 只看响应词。发送图片时会按视觉相似度检索图片触发词和图片回复，不依赖严格 MD5；文本和图片同时提供时会合并召回并按综合关联度排序。`--creator` 会严格匹配创建者账号，单独使用时可列出该账号创建的词条。不带参数发送 `wordbank search` 或 `搜索词条` 会进入引导式搜索，先选择搜索维度组合，再发送文本、图片或图文混合消息；如果选择了创建者维度，会额外要求输入账号。搜索结果默认返回旧版风格结果图，图里会直接展示代表图、范围、概率、权重和命中来源。若同一 trigger group 下有多条词条，结果图会同时预览前几条响应，并额外标记这是折叠展示，提示输入 `详情 <group_id>` 展开更多。结果发出后会进入搜索会话：可直接发送 `page <页码>` 翻页、`详情 <group_id>` 展开当前页的某个 trigger group、`del <序号...>` 删除当前页展示的代表响应，或 `exit` 结束搜索。`del` 只允许词条创建者或超级用户使用。回复结果图发送 `详情 <group_id>` 的老入口继续保留。分页只查询当前页，不参与被动消息热路径。
#### 前置条件
无。
#### 完整流程
```demo
USER: #wordbank search 晚安 --page 1 --limit 10
BOT: [词库搜索结果卡片]
BOT: 可发送 `page 页码` 查看其他页、`详情 组号` 展开词条、`del 序号...` 删除当前页展示词条，或发送 `exit` 结束搜索。
USER: page 2
BOT: [词库搜索结果卡片]
USER: del 1
BOT: 词条 #12 已删除。
BOT: [刷新后的词库搜索结果卡片]
USER: #搜索词条
BOT: 请选择搜索维度，可组合输入：
1. 触发词
2. 响应词
3. 创建者
例如：12 表示触发词+响应词，123 表示再加创建者筛选
USER: 123
BOT: 请输入搜索内容，可直接发送文本、图片或图文混合消息：
USER: 晚安 [图片]
BOT: 请输入需要匹配的创建者账号：
USER: 10001
BOT: [词库搜索结果卡片]
BOT: 可发送 `page 页码` 查看其他页、`详情 组号` 展开词条、`del 序号...` 删除当前页展示词条，或发送 `exit` 结束搜索。
USER: #搜索词条 [图片]
BOT: 词库搜索结果 (第 1 页):
#18 [approved/current_group] [图片:7] => 识图命中
```
#### 失败情况
没有命中时返回空结果说明；页码、每页数量或 `--field` 非法时返回当前群语言的原因；搜索会话里输入无效命令、展开不在当前页的组号，或 `del` 的序号越界时也会返回明确提示。

### `rank` 苦瓜榜
- 摘要: 查看全局已通过审核词条创建数排行海报，支持周榜、月榜、季榜、总榜。
- Priority: 40
- 别名: 苦瓜榜, rank, 排行, 榜
- 指令: `wordbank rank [周榜|月榜|季榜|总榜]`
- 兼容指令: `苦瓜榜`
- Demo: wordbank-rank.png
#### 说明
当前版本按 `response_item` 维度统计已通过审核词条的创建数量，不区分当前是否已删除。默认返回“月榜”，也支持切换“周榜 / 季榜 / 总榜”。海报会展示冠军占比、上榜创建者数量、通过词条总数和前 10 名创建者；每一行会补充真实 QQ 头像、覆盖群数、最近创建时间以及主要 scope 胶囊标签。
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
SYSTEM: 海报展示对应周期的全局已通过审核词条创建 Top 10、冠军占比、真实头像与覆盖群数。
```
#### 失败情况
若所选周期暂无已通过审核的词条，会返回空榜海报；如果图片渲染失败，则降级为文本榜单；输入了不支持的周期名时，会返回明确的可选周期提示。

### `group` 组详情
- 摘要: 直接查看某个 trigger group 的完整详情，默认每页 10 条响应。
- Advanced: true
- Priority: 160
- 别名: group, 展开
- 指令: `wordbank group <group_id> [--page 页码]`
- Demo: wordbank-search.png
#### 说明
组详情页会展示 trigger、本组状态、有效响应数量，以及当前页内每条 response 的状态、范围、概率、权重、规则和完整图文内容。详情页本身也支持继续回复：`下一页`、`上一页`、`第N页`。
#### 常用示例命令
- `#wordbank group 271`
- `#wordbank group 271 --page 3`
#### 前置条件
需要知道 trigger group id，可直接从搜索结果卡片上读取。
#### 完整流程
```demo
USER: #wordbank search jrlp
BOT: [返回搜索结果卡片，卡片内显示 #组271]
USER: [回复搜索结果卡片] @机器人 详情 271
BOT: Trigger Group #271
...
USER: [回复上条组详情] @机器人 下一页
BOT: Trigger Group #271
...
```
#### 失败情况
group id 非法、group 不存在、页码超出范围时会返回当前群语言的原因。

### `delete` 删除词条
- 摘要: 软删除指定词条。
- Priority: 50
- 别名: 删除, remove, wordbank.delete, wordbank.del, 删除词条
- 指令: `wordbank delete <id>`
- 兼容指令: `wordbank.delete <id>`、`wordbank.del <id>`、`删除词条 <id>`
- Demo: wordbank-delete.png
#### 说明
删除后词条不会参与匹配，数据仍保留以便恢复。只有创建者或超级用户可直接删除，删除投票功能当前未开放。
#### 前置条件
需要提供数字词条 ID。
#### 完整流程
```demo
USER: #wordbank delete 12
BOT: 词条 #12 已删除。
USER: 大家晚安啦
SYSTEM: 词条已软删除，普通消息不会触发回复。
```
#### 失败情况
ID 非数字、词条不存在或当前用户没有操作权限时返回明确说明。

### `restore` 恢复词条
- 摘要: 恢复已删除词条。
- Priority: 60
- 别名: 恢复, wordbank.restore, 恢复词条
- 指令: `wordbank restore <id>`
- 兼容指令: `wordbank.restore <id>`、`恢复词条 <id>`
- Demo: wordbank-restore.png
#### 说明
恢复后通过延迟重建重新加入运行时索引。
#### 前置条件
目标词条已被删除。创建者可以恢复自己的词条；当前群管理员或群主可以恢复本群词条；超级用户可以恢复任意词条。
#### 完整流程
```demo
USER: #wordbank restore 12
BOT: 词条 #12 已恢复。
USER: 大家晚安啦
BOT: 做个好梦
```
#### 失败情况
ID 非数字、词条不存在或当前用户没有操作权限时返回明确说明。

### `trigger` 修改触发词与触发概率
- 摘要: 仅超级用户可修改 trigger group 的整体触发概率或触发内容。
- 指令:
  - `wordbank trigger prob <group_id> <0.0-1.0>`
  - `wordbank trigger set <group_id> <新触发内容>`
#### 说明
`trigger prob` 调整的是整组 trigger 的命中概率；`trigger set` 支持文本、图片、图文混排。修改触发内容后，该组下未删除的响应会重新进入 `pending`，需要重新审核后才会继续参与被动匹配。
#### 常用示例命令
- `#wordbank trigger prob 271 0.3`
- `#wordbank trigger set 271 晚安啦`
- `#wordbank trigger set 271 [图片]`
- `#wordbank trigger set 271 晚安啦 [图片]`
#### 前置条件
仅超级用户可执行。
#### 完整流程
```demo
USER: #wordbank trigger prob 271 0.3
BOT: Trigger Group #271 的触发概率已更新为 0.3。
SYSTEM: 后续命中该 trigger group 时，会先按 0.3 的概率决定是否继续响应。
USER: #wordbank trigger set 271 晚安啦
BOT: Trigger Group #271 已更新触发内容，组内响应已重新进入 pending。
SYSTEM: 重新审核通过前，这组 trigger 不会参与被动匹配；审核文档请查看 #help 词库审核。
```
#### 失败情况
group id 非法、group 不存在、权限不足，或新触发内容与其他 trigger group 完全重复时会返回明确说明。

### `response` 修改响应词与响应权重
- 摘要: 仅超级用户或该词条创建者可修改响应内容或响应权重。
- 指令:
  - `wordbank response weight <id> <1-5>`
  - `wordbank response set <id> <新响应内容>`
#### 说明
`response weight` 只影响同组多条响应之间的抽样权重；`response set` 支持文本、图片、图文混排。修改响应内容后，该响应会重新进入 `pending`，审核通过前不会参与被动匹配。
#### 常用示例命令
- `#wordbank response weight 12 5`
- `#wordbank response set 12 做个好梦`
- `#wordbank response set 12 [图片]`
- `#wordbank response set 12 做个好梦 [图片]`
#### 前置条件
超级用户可修改任意响应；非超管仅允许修改自己创建的响应。
#### 完整流程
```demo
USER: #wordbank response weight 12 5
BOT: 词条 #12 的响应权重已更新为 5。
SYSTEM: 同组 trigger 命中后，这条响应会更容易被抽中，但 trigger 概率不受影响。
USER: #wordbank response set 12 做个好梦
BOT: 词条 #12 的响应内容已更新，并重新进入 pending。
SYSTEM: 审核通过前，这条响应不会参与被动匹配；审核文档请查看 #help 词库审核。
```
#### 失败情况
词条 ID 非法、词条不存在、权限不足，或权重超出范围时会返回明确说明。

### `passive` 被动匹配
- 摘要: 普通消息命中词条后自动回复。
- Hero: true
- Priority: 30
- 别名: 被动, 自动回复
- 指令: 无
- Demo: wordbank-passive.png
#### 说明
文本先走内存索引匹配，图片只在消息包含图片时下载并比对本地热缓存。响应可以是纯文本，也可以是 `wordbank add` 保存的图片或图文混排。@、戳一戳、入群和退群会被转换成固定事件触发词后复用同一套内存索引与规则判断。检测到事件名、`notice_type`、`sub_type`、消息段类型或原始消息里包含 `revoke` / `recall` 时直接跳过。

可添加的常用事件触发词:
- `event:at`: 有人 @ 机器人。
- `event:poke`: 有人戳一戳机器人。
- `event:join` / `event:group_increase`: 群成员加入。
- `event:leave` / `event:group_decrease`: 群成员退出或被移出。
#### 常用示例命令
- `#wordbank add event:at => 我在`
- `#wordbank add event:poke => 别戳啦`
- `#wordbank add event:join => 欢迎加入`
- `#wordbank add event:leave => 下次再见`
- `#wordbank add event:at => 管理员请处理 --role admin`
#### 前置条件
词条已启用，且运行时检查允许当前消息继续处理。
#### 完整流程
```demo
USER: 大家晚安啦
BOT: 做个好梦
USER: #wordbank add event:poke => 别戳啦
BOT: 词条已提交审核
SYSTEM: 审核完成后，这条事件词条才会加入运行时索引。
USER: [戳一戳机器人]
BOT: 别戳啦
SYSTEM: 检测到 group_recall / message_revoke / recall 片段时直接跳过，不回复也不写匹配日志。
```
#### 失败情况
没有命中、规则不通过、概率未命中、图片下载失败或检测到撤回信号时不回复。

### `reply-shortcut` 回复管理
- 摘要: 回复机器人词库自动回复后，用短命令管理对应词条。
- Advanced: true
- Priority: 170
- 别名: info, 详情, history, 摘要, del, delete, 删除, rst, restore, 恢复, trigger prob, trigger set, response weight, response set
- 指令: 回复机器人词库自动回复并发送 `info` / `history` / `del` / `rst` / `trigger prob` / `trigger set` / `response weight` / `response set`
- 兼容指令: `详情`、`history`、`删除`、`恢复`
- Demo: wordbank-reply-shortcut.png
#### 说明
词库自动回复发送成功后会记录该回复消息 ID 与命中的词条、触发词、响应词的关系。之后回复这条机器人消息并 @ 机器人发送短命令即可管理词条。`info` / `详情` 会查看词条详情；`history` 会查看当前状态摘要；`del` / `delete` / `删除` 复用新版删除权限；`rst` / `restore` / `恢复` 复用新版恢复权限；`trigger prob <0.0-1.0>` 会修改当前 trigger group 的整体触发概率；`trigger set <新触发内容>` 会修改当前 trigger；`response weight <1-5>` 会修改当前响应权重；`response set <新响应内容>` 会修改当前响应内容。搜索结果卡片和 group 详情页也会记录自己的视图消息 ID，因此回复搜索结果发送 `详情 <group_id>`、回复 group 详情发送 `下一页` / `上一页` / `第N页` 也会走同一套上下文展开逻辑。审批通知 / 提交回执的 `y` / `n` 快捷处理不在这里，请查看 `#help 词库审核`。
#### 前置条件
必须回复一条由词库被动匹配发出的机器人消息；删除只允许创建者或超级用户直接执行；恢复则保持原规则，创建者可恢复自己的词条，群管理员/群主可恢复当前群词条，超级用户可恢复任意词条；`trigger` 相关快捷命令仅超级用户可执行；`response` 相关快捷命令仅超级用户或当前响应创建者可执行。
#### 完整流程
```demo
USER: 大家晚安啦
BOT: 做个好梦
USER: [回复上条机器人消息] @机器人 详情
BOT: 词条详情 #12
状态: approved
触发: 晚安
响应: 做个好梦
USER: [回复上条机器人消息] @机器人 删除
BOT: 词条 #12 已删除。
USER: [回复上条机器人消息] @机器人 trigger prob 0.3
BOT: trigger group #271 的触发概率已更新为 0.3。
USER: [回复上条机器人消息] @机器人 response weight 5
BOT: 词条 #12 的响应权重已更新为 5。
```
#### 失败情况
未回复词库自动回复、消息记录不存在、词条已被清理、当前用户没有删除或恢复权限时会返回明确说明。被回复消息本身如为撤回类 `revoke` / `recall` 信号，不会被被动词库记录。
