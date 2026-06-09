# 词库模块

## 概览
词库模块提供低资源优先的群聊词库能力。`wordbank` 负责完整词库管理、运行时匹配、图片指纹和日志；`study` 只是快捷添加入口。运行时使用内存索引匹配文本、图片和事件触发词，普通文本消息路径不查询数据库；新增、删除、恢复后通过延迟重建刷新索引。检测到 `revoke` / `recall` 相关信号时会直接跳过匹配，不回复也不写日志。为兼容旧入口，`wordbank.add`、`wordbank.search`、`wordbank.delete`、`wordbank.restore`、`wordbank.image` 和常用中文直达命令会自动转入对应新版子命令。

## 权限与触发
- 触发方式: 指令触发 / 被动触发
- 权限: 普通用户

## 子功能目录
- `add` 添加文本词条: 将触发词和响应词加入词库。
- `search` 搜索词条: 按触发词或响应词查询现有词条。
- `delete` 删除词条: 软删除指定词条并刷新索引。
- `restore` 恢复词条: 恢复已删除词条并刷新索引。
- `image` 添加图片词条: 将图片指纹归并为 canonical image 后绑定响应。
- `passive` 被动匹配: 文本、图片、@、戳一戳或入退群事件命中词条后自动回复，撤回信号直接跳过。

## 子功能详情
### `add` 添加文本词条
- 摘要: 添加一条文本触发词库。
- 别名: 添加, 学习, wordbank.add, 添加词条
- 指令: `wordbank add 触发词 => 响应词`
- 兼容指令: `wordbank.add 触发词 => 响应词`、`添加词条 触发词 => 响应词`
#### 说明
默认触发模式为 `contains`，短词默认转为 `fullmatch` 并使用较低概率。高级选项支持 `--mode`、`--scope`、`--prob`、`--weight`、`--role`、`--call`。`study 触发词 => 响应词` 会走同一套添加逻辑；旧版 `study <a|m> <t|f> 触发词 响应词` 也会转换为新版固定 scope。
#### 前置条件
当前用户所在群聊需要通过运行时检查。
#### 完整流程
```demo
USER: #wordbank add 晚安 => 做个好梦 --mode contains --scope current_group --prob 1 --weight 3
BOT: 词条已加入词库
ID: 12
触发: 晚安
响应: 做个好梦
模式: contains
范围: current_group
概率: 1
权重: 3
USER: 大家晚安啦
BOT: 做个好梦
```
#### 失败情况
触发词或响应词为空、规则字段冲突、概率和权重越界时会返回当前群语言的原因。

### `search` 搜索词条
- 摘要: 查询词库中的触发词和响应词。
- 别名: 查询, 搜索, wordbank.search, 搜索词条
- 指令: `wordbank search [关键词] [--page 页码] [--limit 每页数量]`
- 兼容指令: `wordbank.search [关键词]`、`搜索词条 [关键词]`
#### 说明
搜索按需查询数据库，默认每页 10 条，最多每页 20 条。分页只读取当前页和一条额外记录判断是否还有下一页，不参与被动消息热路径。
#### 前置条件
无。
#### 完整流程
```demo
USER: #wordbank search 晚安 --page 1 --limit 10
BOT: 词库搜索结果 (第 1 页):
#12 [contains/current_group] 晚安 => 做个好梦
还有更多结果，可使用 --page 2 --limit 10 查看下一页。
```
#### 失败情况
没有命中时返回空结果说明；页码或每页数量非法时返回当前群语言的原因。

### `delete` 删除词条
- 摘要: 软删除指定词条。
- 别名: 删除, remove, wordbank.delete, wordbank.del, 删除词条
- 指令: `wordbank delete <id>`
- 兼容指令: `wordbank.delete <id>`、`wordbank.del <id>`、`删除词条 <id>`
#### 说明
删除后词条不会参与匹配，数据仍保留以便恢复。兼容入口仍使用新版按 ID 删除模型，不恢复旧版回复消息审批流。
#### 前置条件
需要提供数字词条 ID。创建者可以删除自己的词条；当前群管理员或群主可以删除本群词条；超级用户可以删除任意词条。
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
- 别名: 恢复, wordbank.restore, 恢复词条
- 指令: `wordbank restore <id>`
- 兼容指令: `wordbank.restore <id>`、`恢复词条 <id>`
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

### `image` 添加图片词条
- 摘要: 添加一条图片触发词库。
- 别名: 图片, img, wordbank.image, wordbank.img, 图片词条
- 指令: `wordbank image 响应词` 并附带图片
- 兼容指令: `wordbank.image 响应词`、`wordbank.img 响应词`、`图片词条 响应词` 并附带图片
#### 说明
入库时计算 MD5、dHash、pHash、尺寸和文件大小，相似图片会归并到同一个 canonical image。
#### 前置条件
命令消息必须包含一张图片。
#### 完整流程
```demo
USER: #wordbank image 是这张图喔 [图片]
BOT: 词条已加入词库
ID: 21
触发: image:7
响应: 是这张图喔
模式: fullmatch
范围: current_group
概率: 1
权重: 3
USER: [相似截图]
BOT: 是这张图喔
```
#### 失败情况
图片下载失败、图片无法识别或响应词为空时会返回当前群语言的原因。

### `passive` 被动匹配
- 摘要: 普通消息命中词条后自动回复。
- 别名: 被动, 自动回复
- 指令: 无
#### 说明
文本先走内存索引匹配，图片只在消息包含图片时下载并比对本地热缓存。@、戳一戳、入群和退群会被转换成固定事件触发词后复用同一套内存索引与规则判断。检测到事件名、`notice_type`、`sub_type`、消息段类型或原始消息里包含 `revoke` / `recall` 时直接跳过。

可添加的常用事件触发词:
- `event:at`: 有人 @ 机器人。
- `event:poke`: 有人戳一戳机器人。
- `event:join` / `event:group_increase`: 群成员加入。
- `event:leave` / `event:group_decrease`: 群成员退出或被移出。
#### 前置条件
词条已启用，且运行时检查允许当前消息继续处理。
#### 完整流程
```demo
USER: 大家晚安啦
BOT: 做个好梦
USER: #wordbank add event:poke => 别戳啦
BOT: 词条已加入词库
USER: [戳一戳机器人]
BOT: 别戳啦
SYSTEM: 检测到 group_recall / message_revoke / recall 片段时直接跳过，不回复也不写匹配日志。
```
#### 失败情况
没有命中、规则不通过、概率未命中、图片下载失败或检测到撤回信号时不回复。
