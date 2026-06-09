# 词库模块

## 概览
词库模块提供低资源优先的群聊词库能力。运行时使用内存索引匹配文本和图片，普通消息路径不查询数据库；新增、删除、恢复后通过延迟重建刷新索引。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `add` 添加文本词条: 将触发词和响应词加入词库。
- `search` 搜索词条: 按触发词或响应词查询现有词条。
- `delete` 删除词条: 软删除指定词条并刷新索引。
- `restore` 恢复词条: 恢复已删除词条并刷新索引。
- `image` 添加图片词条: 将图片指纹归并为 canonical image 后绑定响应。

## 子功能详情
### `add` 添加文本词条
- 摘要: 添加一条文本触发词库。
- 别名: 添加, 学习
- 指令: `wordbank add 触发词 => 响应词`
- Demo: wordbank-placeholder.png
#### 说明
默认触发模式为 `contains`，短词默认转为 `fullmatch` 并使用较低概率。高级选项支持 `--mode`、`--scope`、`--prob`、`--weight`、`--role`、`--call`。
#### 前置条件
当前用户所在群聊需要通过运行时检查。
#### 完整流程
```demo
USER: #wordbank add 晚安 => 做个好梦
BOT: 词条已加入词库
```
#### 失败情况
触发词或响应词为空、规则字段冲突、概率和权重越界时会返回中文原因。

### `search` 搜索词条
- 摘要: 查询词库中的触发词和响应词。
- 别名: 查询, 搜索
- 指令: `wordbank search [关键词]`
- Demo: wordbank-placeholder.png
#### 说明
搜索按需查询数据库并返回最多 10 条结果，不参与被动消息热路径。
#### 前置条件
无。
#### 完整流程
```demo
USER: #wordbank search 晚安
BOT: 词库搜索结果:
```
#### 失败情况
没有命中时返回空结果说明。

### `delete` 删除词条
- 摘要: 软删除指定词条。
- 别名: 删除, remove
- 指令: `wordbank delete <id>`
- Demo: wordbank-placeholder.png
#### 说明
删除后词条不会参与匹配，数据仍保留以便恢复。
#### 前置条件
需要提供数字词条 ID。
#### 完整流程
```demo
USER: #wordbank delete 12
BOT: 词条 #12 已删除。
```
#### 失败情况
ID 非数字或词条不存在时返回明确说明。

### `restore` 恢复词条
- 摘要: 恢复已删除词条。
- 别名: 恢复
- 指令: `wordbank restore <id>`
- Demo: wordbank-placeholder.png
#### 说明
恢复后通过延迟重建重新加入运行时索引。
#### 前置条件
目标词条已被删除。
#### 完整流程
```demo
USER: #wordbank restore 12
BOT: 词条 #12 已恢复。
```
#### 失败情况
ID 非数字或词条不存在时返回明确说明。

### `image` 添加图片词条
- 摘要: 添加一条图片触发词库。
- 别名: 图片, img
- 指令: `wordbank image 响应词` 并附带图片
- Demo: wordbank-placeholder.png
#### 说明
入库时计算 MD5、dHash、pHash、尺寸和文件大小，相似图片会归并到同一个 canonical image。
#### 前置条件
命令消息必须包含一张图片。
#### 完整流程
```demo
USER: #wordbank image 是这张图喔 [图片]
BOT: 词条已加入词库
```
#### 失败情况
图片下载失败、图片无法识别或响应词为空时会返回中文原因。
