# 词库模块（传统版）

## 概览
牢玩家最喜欢的 #study a f

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 用法
- Help: `study`
- 别名: study, 学习
- 指令: `#study <a|m> <t|f> 触发词 响应词`
- Demo: study-main.webp

## 说明
> [!TIP]
> - `a`: 对所有人有效
> - `m`: 仅对自己有效
> - `t`: 只在当前群生效
> - `f`: 不限制当前群


如果触发词比较长，或者你想把触发词和响应词分得更清楚，也可以写成：`#study 触发词 => 响应词`

[!TIP]
也支持事件类触发词，例如 `[@]`、`[戳一戳]`、`[bot加群]`、`[新人加入]`。

如果要把这些内容当普通文本录进去，请在前面加 `\`，例如 `\[戳一戳]`、`\【bot加群】`。

[!TIP]
不带参数直接发送 `#study`，会进入一步一步的引导模式。

[!TIP]
引导中输入 `revoke` / `recall` / `exit` 可以取消。

[!TIP]
引导模式走到“请输入响应词”时，如果你回复的是一条合并转发，凛凛会继续问你：

- `1` 把整条合并转发当成一个整体响应
- `2` 把每个转发节点拆开，批量导入成多条响应

## 前置条件
词条需要通过审核后才会触发

## 完整流程
```demo
SECTION: 引导式
USER: #study
BOT: 请选择触发方式：
a. 对所有人有效
m. 仅对自己有效
输入 revoke / recall / exit 可取消本次操作。
USER: a
BOT: 是否开启群组隔离？
t. 开启，仅当前群聊有效
f. 关闭，按触发方式跨群或私聊生效
输入 revoke / recall / exit 可取消本次操作。
USER: t
BOT: 请输入触发词，支持文本或图片消息。
USER: 晚安
BOT: 请输入响应词，支持文本、图片或合并转发消息。
USER: 做个好梦
BOT: 请输入响应词权重，1-5 之间的数字；直接发送 3 可用默认权重。
USER: 3
BOT: 词条已提交审核
ID: 15
状态: pending
触发: 晚安
响应: 做个好梦
模式: contains
范围: current_group
概率: 1
权重: 3
管理员通过前不会触发。
SECTION: 传统模式
USER: #study a f 群公告 大家记得看
BOT: 词条已提交审核
ID: 16
状态: pending
触发: 群公告
响应: 大家记得看
模式: contains
范围: all_groups
概率: 1
权重: 3
管理员通过前不会触发。
SECTION: => 语法糖
USER: #study 晚安呀 宝宝 => 做个好梦
BOT: 词条已提交审核
ID: 17
状态: pending
触发: 晚安呀 宝宝
响应: 做个好梦
模式: contains
范围: current_group
概率: 1
权重: 3
管理员通过前不会触发。
SECTION: 合并转发批量导入
USER: #study
BOT: 请选择触发方式：
a. 对所有人有效
m. 仅对自己有效
输入 revoke / recall / exit 可取消本次操作。
USER: a
BOT: 是否开启群组隔离？
t. 开启，仅当前群聊有效
f. 关闭，按触发方式跨群或私聊生效
输入 revoke / recall / exit 可取消本次操作。
USER: f
BOT: 请输入触发词，支持文本或图片消息。
USER: jrlp
BOT: 请输入响应词，支持文本、图片或合并转发消息。
USER: [回复一条包含很多条 jrlp 文案的合并转发]
BOT: 检测到合并转发消息，请回复 1 作为整体响应，或回复 2 拆开成多条响应。
USER: 2
BOT: 请输入响应词权重，1-5 之间的数字；直接发送 3 可用默认权重。
USER: 3
BOT: 已完成合并转发响应导入，正在整理详情
总数: 18
成功: 18
失败: 0
SYSTEM: 随后会附带一条合并转发详情，逐条列出每个导入结果。
SECTION: 事件类例子
USER: #study [@] => 我在
BOT: 词条已提交审核
ID: 19
状态: pending
触发: [@]
响应: 我在
模式: fullmatch
范围: current_group
概率: 1
权重: 3
管理员通过前不会触发。
USER: #study [bot加群] => 凛凛来啦
BOT: 词条已提交审核
ID: 20
状态: pending
触发: [bot加群]
响应: 凛凛来啦
模式: fullmatch
范围: current_group
概率: 1
权重: 3
管理员通过前不会触发。
SECTION: 高级选项
USER: #study
BOT: 请选择触发方式：
a. 对所有人有效
m. 仅对自己有效
输入 revoke / recall / exit 可取消本次操作。
USER: a
BOT: 是否开启群组隔离？
t. 开启，仅当前群聊有效
f. 关闭，按触发方式跨群或私聊生效
输入 revoke / recall / exit 可取消本次操作。
USER: t
BOT: 请输入触发词，支持文本或图片消息。
USER: 晚安
BOT: 请输入响应词，支持文本、图片或合并转发消息。
USER: 晚安晚安
BOT: 请输入响应词权重，1-5 之间的数字；直接发送 3 可用默认权重。
USER: 5
BOT: 词条已提交审核
ID: 21
状态: pending
触发: 晚安
响应: 晚安晚安
模式: contains
范围: current_group
概率: 1
权重: 5
管理员通过前不会触发。
```

## 失败情况
连续输错 3 次会结束本次对话；图片下载失败或无法识别时不会写入词库。
