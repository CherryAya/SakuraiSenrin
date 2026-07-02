# 消息发送与转发复用规范

本规范描述项目内 `src/lib/message_plan.py`、`src/lib/message_delivery.py`、`src/lib/message_assets.py`、`src/lib/message_api_hooks.py` 与 `src/lib/onebot_forward.py` 的协作约束。

适用范围：

- 需要主动发消息的插件
- 需要发送合并转发的帮助、列表、批量结果输出
- 需要做消息缓存、消息复用、消息转发优化的运行时能力

## 1. 目标

当前消息发送链路的目标不是“把消息发出去”这么简单，而是同时满足：

1. 统一消息注入安全约束。
2. 尽可能复用历史消息，减少重复发送。
3. 对合并转发保持顺序敏感，不允许因缓存命中打乱用户观感。
4. 在缓存失效、转发失败或平台能力不足时，自动降级。
5. 对复用判定、hash 计算和失败原因保持可审计、可调试。

## 2. 统一入口

### 2.0 消息计划层

插件或 hook 在实现“主动发消息”“发送批量结果”“带等待提示的异步回复”时，优先产出：

```python
from src.lib.message_plan import DeliveryPlan, deliver_message_plan
```

当前语义：

- `DeliveryPlan.messages`：业务层要发出的逻辑消息节点。
- `DeliveryPlan.wait_message`：在主体消息前额外发送的一条等待提示。
- `DeliveryPlan.force_forward`：强制将多节点输出按合并转发处理。
- `DeliveryPlan.source_kind` / `fallback_nickname`：发送链路的基础设施元数据。

约束：

- 插件层负责“表达要发什么内容”，不负责“最终如何发出去”。
- 插件层不应自己决定 should_forward、前缀复用、fallback 或消息 staging。
- 对正式输出路径，`DeliveryPlan` 是插件 / hook 层唯一推荐接口。
- 插件 / hook 层不应直接导入或调用 `deliver_single_message(...)`、`resolve_delivery_target(...)`。
- `deliver_single_message(...)` 仅保留给基础设施层、兼容层或 `message_plan` 内部编排使用。

### 2.1 单条消息

底层基础设施代码在确实需要单条直发时，优先使用：

```python
from src.lib.message_delivery import DeliveryTarget, deliver_single_message
```

约束：

- 不要在业务代码里手写“先查缓存、再转发、再发送、再回写”的重复逻辑。
- 不要为不同插件各自维护一套消息复用策略。
- 需要明确目标时，显式传 `DeliveryTarget`。
- 该接口不应再作为插件正式输出入口暴露给业务层日常使用。

### 2.2 事件回复

基于 OneBot `send_handler` 或 `send_group_msg` / `send_private_msg` 的常规回复，会由：

- `src/lib/message_api_hooks.py::delivery_send_handler`
- `src/lib/message_api_hooks.py::intercept_message_send_api`

自动接入 `deliver_single_message(...)`。

约束：

- 默认回复路径允许依赖 hook 自动接管。
- 但新功能若本身就在做复杂发送编排，仍应直接调用 `message_delivery`，不要把核心逻辑隐藏在 hook 副作用里。

### 2.3 合并转发

需要输出多条 demo、多段帮助、多条批量结果时，正式业务代码应优先构造 `DeliveryPlan`，再交给：

```python
from src.lib.message_plan import DeliveryPlan, deliver_message_plan
```

约束：

- 插件层不要直接调用 `deliver_forward_messages(...)`。
- 不要在插件里自行拼 `send_group_forward_msg` / `send_private_forward_msg`。
- 不要再保留“先发给自己，再重新组整包转发”的业务层残留实现。
- 自定义合并转发节点构造只允许留在 `src/lib/onebot_forward.py` 作为底层 fallback。

## 3. 单条消息资产规范

### 3.1 资产描述

消息资产描述统一由 `describe_message_asset(...)` 生成，核心字段包括：

- `asset_key`
- `content_hash`
- `message_shape_kind`
- `reusable_globally`
- `disqualify_reason`

当前 `asset_key` 与 `content_hash` 都基于消息序列化结果的 `sha256`。

### 3.2 可复用判定

当前规则：

- 纯文本消息可全局复用。
- 普通图文等 `rich` 消息可全局复用。
- 含 `reply` 段消息不可全局复用。
- 含 `at` 段消息不可全局复用。
- 空字符串消息不可复用。

约束：

- 新增消息段类型时，必须先明确它是否能跨上下文复用。
- 只要消息里带有上下文绑定语义，就不能为了“命中缓存”强行标为全局可复用。

### 3.3 资产库职责

`message_asset` 表保存的是“已存在消息 ID 与其可复用语义”的映射，而不是业务消息历史。

约束：

- 业务插件不得把 `message_asset` 当业务表直接查询或写入。
- 一切读写统一走 `message_asset_repo`。
- 若消息转发验证失败，必须通过 `mark_stale(...)` 标脏，而不是静默忽略。

## 4. 合并转发复用规范

### 4.1 基本原则

合并转发复用不是“节点内容相同就复用”，而是：

1. 内容相同。
2. 顺序相同。
3. 当前批次上下文相同。
4. 复用后客户端展示顺序仍然单调稳定。

只满足内容相同但不满足顺序语义时，视为不能安全复用。

### 4.2 Batch 上下文

`build_forward_batch_descriptor(...)` 会为一批消息生成：

- `context_key`
- 每个节点的 `node_context_key`
- 每个节点的 `node_asset_key`

当前语义：

- `context_key`：整批消息按顺序的内容 hash。
- `node_context_key`：从第 `0` 条到当前节点的前缀 hash。
- `node_asset_key`：`node_context_key + 当前节点内容 hash` 的组合 hash。

这意味着：

- 同一条内容放在不同顺序位置，不是同一个 forward node 资产。
- 同一批里前缀相同、后缀不同，只允许复用相同前缀。
- 不允许跨顺序做稀疏命中，也不允许跳过中间节点复用后面的节点。

### 4.3 复用策略

默认策略是 `ForwardReusePolicy(preserve_node_time_order=True)`。

当前规则：

- 按节点顺序检查可复用前缀。
- 只要遇到首个不可安全复用节点，后续全部重建。
- 判定停止条件包括：
  - 缓存不存在
  - `message_id` 为空
  - `forward_context_key` 不匹配
  - 缓存节点的 `(created_at, updated_at, forward_sort_key)` 不再保持单调顺序

约束：

- 业务层不得实现“内容相同就整包命中”的宽松策略。
- 业务层不得自行做“跳着复用中间节点”的优化。
- 如需修改复用语义，必须同步更新测试和文档。

### 4.4 构造与 fallback

合并转发发送流程应当是：

1. 先解析 batch descriptor。
2. 计算最长可复用前缀。
3. 对前缀节点直接复用已缓存 `message_id`。
4. 对剩余节点重新构造 forward node。
5. 再调用 `send_group_forward_msg` / `send_private_forward_msg` 发送整包。

仅当上述流程失败时，才允许降级到 `send_custom_forward(...)`。

约束：

- fallback 是保底路径，不是常规路径。
- 常规路径应尽量复用 OB11 / NapCat 的单条消息转发能力。
- 若平台转发单条消息失败，应标记资产 stale，并回退到主动发送或自定义 forward。

## 5. “先发给自己” 的边界

当前实现里，“先发给自己”只允许存在于一个场景：

- 需要首次构造新的 forward node，而平台没有直接“构造 node asset 但不发送”的能力时。

约束：

- 该路径只能存在于 `ensure_forward_node(...)` 的底层实现里。
- 业务代码不得主动执行“私聊机器人自己，再拼转发”的流程。
- 对已经缓存的 node，后续应优先直接转发 `message_id`，不能再次走私聊 staging。

## 6. 静态资源与帮助系统

帮助系统中的 summary / guide / demo 图应优先走静态构建产物，而不是运行时临时重绘。

原因：

- 避免动态时间戳导致 hash 抖动。
- 保证 help 文档的缓存可稳定命中。
- 让“最近构建时间”来自静态产物而不是运行时消息时间。

约束：

- 影响 help 图输出顺序或内容的改动，必须考虑 forward cache 命中语义。
- 只要 summary 内容变了，就应视为新的资产；只要内容没变，就应允许跨群、跨私聊、跨域复用。

## 7. 调试与日志

`message_delivery` 与 `message_assets` 的 debug 日志应覆盖：

- 消息描述与 hash 计算结果
- 缓存 hit / miss
- 单条消息复用命中、跳过、失效原因
- forward batch 的 `context_key`
- 每个 node 的 `node_context_key` / `node_asset_key`
- 前缀复用从哪里停止、为什么停止
- 是否触发 staging
- 是否触发 merged forward fallback

约束：

- 新增复用分支时，必须补可解释的 debug 日志。
- 日志应以短 hash、节点下标、reason 为主，不要直接刷整段长消息正文。

## 8. 测试要求

涉及 `message_plan` / `message_delivery` 语义变更时，至少应覆盖：

1. `DeliveryPlan` 单条输出、等待提示、强制 forward 输出。
2. 单条消息缓存命中与 fallback。
3. `reply` / `at` 消息不复用。
4. 合并转发顺序敏感。
5. `forward_context_key` 不匹配时从冲突点重建。
6. 时间顺序冲突时从首个不安全节点重建。
7. 前缀复用成功、后缀重建成功。
8. fallback 到 `send_custom_forward(...)` 的路径。

推荐测试文件：

- `tests/test_message_delivery_forward.py`
- `tests/test_message_api_hooks.py`
- 具体业务插件自己的 help / 输出路径测试

## 9. 开发禁令

以下做法视为不合规：

1. 插件自行调用 `send_group_forward_msg` / `send_private_forward_msg` 拼业务转发。
2. 插件正式输出路径直接调用 `send_custom_forward(...)` 或 `deliver_forward_messages(...)`。
3. 插件自行维护消息 hash、缓存表或消息复用数据库。
4. 为了命中缓存而忽略 `reply`、`at`、顺序或上下文语义。
5. 把“先发给自己”暴露成业务层策略。
6. 修改 help/demo 输出顺序后，不同步考虑 forward cache 的顺序语义。

## 10. 代码定位

当前关键实现位置：

- `src/lib/message_plan.py`
- `src/lib/message_delivery.py`
- `src/lib/message_assets.py`
- `src/lib/message_api_hooks.py`
- `src/lib/onebot_forward.py`

阅读顺序建议：

1. `message_plan.py`
2. `message_api_hooks.py`
3. `message_delivery.py`
4. `message_assets.py`
5. `onebot_forward.py`
