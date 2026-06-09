# 学习模块

## 概览
学习模块保留传统 `study` / `学习` 快捷入口，用于把简单文本词条加入 `wordbank`。它不维护独立数据库、规则或运行时索引；查询、删除、投票删除、恢复、图片词条和被动匹配都由 `wordbank` 负责。完整管理命令请使用 `wordbank add/search/delete/support/vote/restore/image`，也兼容旧式 `wordbank.add`、`wordbank.search`、`wordbank.delete`、`wordbank.support`、`wordbank.vote`、`wordbank.restore`、`wordbank.image` 入口。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `shortcut` 快捷学习: 兼容旧学习格式并转换为 wordbank 固定规则。

## 子功能详情
### `shortcut` 快捷学习
- 摘要: 使用旧式简短命令添加词库。
- 别名: study, 学习
- 指令: `study 触发词 => 响应词`
- 兼容指令: `study <a|m> <t|f> 触发词 响应词`
#### 说明
支持 `=>`、`->`、`回答`、`回复` 分隔触发词和响应词。也兼容旧版一键格式：`a` 表示对所有人有效，`m` 表示仅对自己有效；`t` 表示开启群组隔离，`f` 表示关闭群组隔离。内部会调用 `wordbank add` 使用的固定规则模型，默认群聊范围为当前群，私聊范围为本人。需要搜索、删除、投票删除、恢复或添加图片词条时请使用 `wordbank` 命令；中文直达入口 `添加词条`、`搜索词条`、`删除词条`、`支持删除`、`查看投票状态`、`恢复词条`、`图片词条` 也会转入对应新版子命令。
#### 前置条件
当前用户所在群聊需要通过运行时检查。
#### 完整流程
```demo
USER: #study 晚安 => 做个好梦
BOT: 词条已加入词库
ID: 12
触发: 晚安
响应: 做个好梦
模式: contains
范围: current_group
概率: 1
权重: 3
USER: #wordbank search 晚安
BOT: 词库搜索结果 (第 1 页):
#12 [contains/current_group] 晚安 => 做个好梦
USER: 大家晚安啦
BOT: 做个好梦
USER: #study m t 私人暗号 只回应我
BOT: 词条已加入词库
ID: 13
触发: 私人暗号
响应: 只回应我
模式: contains
范围: self_in_current_group
概率: 1
权重: 3
```
#### 失败情况
缺少触发词或响应词、旧版 `a/m t/f` 参数不完整时返回格式说明；撤回类 `revoke` / `recall` 信号由 `wordbank` 被动匹配入口直接跳过。
