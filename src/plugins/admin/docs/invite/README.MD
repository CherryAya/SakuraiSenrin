# 邀请管理模块

## 概览
邀请管理模块负责处理待审批群邀请，支持查看列表、同意、拒绝、忽略，以及查看处理日志。模块同时支持直接回复邀请通知消息做快捷审批。

## 权限与触发
- 触发方式: 指令触发
- 权限: 超级管理员

## 用法
- Help: `admin.invite`

## 子功能目录
- `list` 查看邀请列表: 以图片形式展示当前待处理邀请。
- `approve` 同意邀请: 按群号或 flag 同意单个邀请。
- `reject` 拒绝邀请: 按群号、flag 或全部拒绝邀请。
- `ignore` 忽略邀请: 按群号、flag 或全部忽略邀请。
- `reply-shortcut` 快捷回复审批: 直接回复邀请通知消息进行同意或拒绝。
- `log` 查看日志: 查询已处理邀请记录。

## 子功能详情
### `list` 查看邀请列表
- 摘要: 以图片形式展示当前待处理邀请。
- 别名: show, ls, 列表
- 指令: `#admin.invite list`
- Demo: admin-invite-list.webp
#### 说明
> [!TIP]
> `list` 会把当前待处理邀请渲染成列表图。
>
> 适合先在私聊里快速浏览，再决定后续是批准、拒绝还是忽略。
#### 前置条件
系统中已经记录了待处理邀请。
#### 完整流程
不带额外参数即可查看。
```demo
USER: #admin.invite list
BOT: [发送一张待处理邀请列表图片]
```
#### 失败情况
如果当前没有待处理邀请，会返回“当前没有待处理的邀请哦”。

### `approve` 同意邀请
- 摘要: 按群号或 flag 同意单个邀请。
- 别名: 同意
- 指令: `#admin.invite approve -g <gid>` / `#admin.invite approve -f <flag>`
- Demo: admin-invite-approve.webp
#### 说明
> [!TIP]
> `approve` 会先定位对应邀请，再调用 OneBot 审批接口。
>
> 审批成功后，邀请记录状态也会一起同步更新。
#### 前置条件
必须能通过群号或 flag 精确定位到一个待处理邀请。
#### 完整流程
通常先用 `list` 看列表，再执行批准。
```demo
USER: #admin.invite approve -g 114514
BOT: 已同意群聊邀请 17
群号：114514
```
#### 失败情况
若邀请已被处理或无法定位到记录，会直接返回失败原因。

### `reject` 拒绝邀请
- 摘要: 按群号、flag 或全部拒绝邀请。
- 别名: 拒绝
- 指令: `#admin.invite reject -g <gid>` / `#admin.invite reject -f <flag>` / `#admin.invite reject --all`
- Demo: admin-invite-reject.webp
#### 说明
> [!TIP]
> `reject` 既能处理单条邀请，也能用 `--all` 批量清理。
>
> 适合待处理邀请已经堆积很多、准备一次性清空的时候使用。
#### 前置条件
需要存在至少一条待处理邀请。
#### 完整流程
可先查看列表再决定单条或批量处理。
```demo
USER: #admin.invite reject --all
BOT: ========== 批量拒绝 ==========
统计: 共拒绝了 3 个待处理邀请。
```
#### 失败情况
如果当前没有待拒绝邀请，会提示“当前没有需要拒绝的待处理邀请哦”。

### `ignore` 忽略邀请
- 摘要: 按群号、flag 或全部忽略邀请。
- 别名: 忽略
- 指令: `#admin.invite ignore -g <gid>` / `#admin.invite ignore -f <flag>` / `#admin.invite ignore --all`
- Demo: admin-invite-ignore.webp
#### 说明
> [!TIP]
> `ignore` 只会把邀请标记为已忽略。
>
> 它不会真的同意或拒绝，只是把这条记录从待处理列表里移开。
#### 前置条件
需要存在至少一条待处理邀请。
#### 完整流程
适合临时不处理但又不想一直堆积的邀请。
```demo
USER: #admin.invite ignore -g 114514
BOT: ======= 操作成功: 已忽略 =======
群号：114514
```
#### 失败情况
如果当前没有待忽略邀请，会返回对应提示。

### `reply-shortcut` 快捷回复审批
- 摘要: 直接回复邀请通知消息进行同意或拒绝。
- 别名: y, n
- 指令: 回复通知消息发送 `y` / `n`
- Demo: admin-invite-reply-shortcut.webp
#### 说明
> [!TIP]
> 如果你正回复机器人推送的邀请通知消息，就可以直接用短指令处理。
>
> 常见写法是 `y` / `approve` 代表同意，`n` / `reject` 代表拒绝。
#### 前置条件
必须是“回复机器人推送的邀请通知消息”，且发送者具备超级管理员权限。
#### 完整流程
收到通知后直接在原消息上回复。
```demo
BOT: 📩 新的邀请事件通知
USER: [回复该消息] y
BOT: 已同意群聊邀请 18
```
#### 失败情况
如果回复的不是有效邀请通知消息，会提示找不到对应邀请记录。

### `log` 查看日志
- 摘要: 查询已处理邀请记录。
- 别名: 日志
- 指令: `#admin.invite log [-g <gid>]`
- Demo: admin-invite-log.webp
#### 说明
> [!TIP]
> `log` 用于查看最近处理过的邀请记录。
>
> 需要排查某个群近期被怎么处理过时，可以补 `-g <gid>` 精确过滤。
#### 前置条件
系统中需要已经存在处理日志。
#### 完整流程
按需传入群号即可。
```demo
USER: #admin.invite log -g 114514
BOT: 已处理邀请日志...
```
#### 失败情况
如果没有任何处理记录，会返回空结果说明。
