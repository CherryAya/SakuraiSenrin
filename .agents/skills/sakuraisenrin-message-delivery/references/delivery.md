# 统一消息投递与复用

以下路径均相对于仓库根目录。内容先按 i18n.md 本地化，再进入消息计划。

## 正式入口

插件/hook 使用 `src.lib.message_plan` 的 `DeliveryPlan` 和 `deliver_message_plan(...)`；便捷的单条/结束/暂停/重试交互复用该模块提供的 `finish_with_message`、`send_with_message`、`pause_with_message`、`reject_with_message` 等函数。

业务层定义逻辑内容和上下文，不复制底层发送、缓存、转发节点与 fallback。基础设施内部可以调用单条/forward API；这不是插件层的通行证。

```python
from src.lib.i18n.catalog import tr
from src.lib.i18n.delivery import get_group_locale, resolve_locale
from src.lib.message_plan import DeliveryPlan, deliver_message_plan

locale = await resolve_locale(get_group_locale(event))
plan = DeliveryPlan(
    messages=(tr(locale, "admin.i18n.show.effective", locale=locale),),
    source_kind="admin_i18n",
)
await deliver_message_plan(bot, plan=plan, event=event)
await matcher.finish()
```

示例使用已有键。DeliveryPlan 接受字符串输入并按文字块安全渲染，这不允许传入硬编码产品文案。仅构造计划不会自动结束 matcher；需要结束时使用统一结束 helper 或投递后空调用 finish。

## 计划与安全分段

| 需要表达 | 接口/块 |
| --- | --- |
| 文本 | TextBlock / build_text_plan_entry / 已本地化的字符串 |
| 图片字节 | ImageBytesBlock / build_image_plan_entry |
| 艾特 | AtRefBlock |
| 回复 | ReplyRefBlock |
| 表情 | FaceBlock |
| 已安全构造的 OneBot Message | RawMessageBlock / MessagePlanEntry.from_message |

构造复合节点时使用 `MessagePlanEntry(blocks=(...))`。若在计划外组装 Message，用 `MessageSegment.text/image/at/reply` 或项目安全 helper 显式分段。

禁止 `Message(raw_text)`、手工拼 CQ 码再解析。空 `Message()` 只用于随后逐段 append；类型注解、遍历、isinstance 等保留 Message 导入不构成违规。RawMessageBlock 不是不可信 CQ 文本的逃生口。

## 目标、交互和等待

- 消息事件用 `event=` 交给统一层解析目标。定时任务、notice、私聊通知等没有可用 MessageEvent 时，明确提供 `DeliveryTarget`；notice 可复用现有 `resolve_notice_delivery_target`。
- 不在插件调用底层 `resolve_delivery_target` 或直接 send_* API 绕过计划。
- pause/reject 保持 NoneBot 会话语义，使用统一 wrapper 处理正文；不要先发一遍再让 matcher 携带同一正文重复发送。
- `wait_message` 会在投递本计划主体前发送；它不会自动并发执行长任务。计算很慢且结果尚未生成时，先通过统一层发送等待计划，完成后发送结果计划。
- `source_kind` 是稳定机器标识。可显示给用户的 fallback_nickname/摘要按 i18n 处理。
- 默认由计划按逻辑节点决定发送形式。确有产品要求强制某种形式时，在审查计划中说明并使用统一层参数，不另写分支发送链路。

## 消息资产与 forward 缓存

1. 资产描述/hash 复用 `src/lib/message_assets.py`，插件不自行维护消息缓存表、hash 或全局消息 ID。
2. 普通纯文本/图文是否可复用由统一层判断；空文本、reply、at 等上下文绑定内容不能为命中缓存强行全局复用。
3. i18n 先渲染再进入资产描述；相同文案键、不同语言不等于同一输出。
4. 合并转发同时匹配内容、顺序和上下文。只复用连续安全前缀，遇到冲突从该点重建；不得跳过中间节点稀疏复用后缀。
5. help/guide/demo/列表调整节点顺序、摘要或首屏结构时，检查原缓存身份是否仍成立。
6. 失效资产、转发失败或能力不足时由统一层重建/fallback。自定义节点与 staging 保留在 `onebot_forward` 等底层，不暴露“先发给自己”业务路径。
7. 新增复用/渲染/转发判断分支要记录短 hash、命中/失效、节点位置和停止/fallback 原因；不刷长正文或敏感参数。

### 当前身份与停止条件

统一层的 `build_forward_batch_descriptor(...)` 负责生成：

- `context_key`：整批消息按顺序的资产 hash。
- `node_context_keys[index]`：从首条到当前节点的前缀 hash。
- `node_asset_keys[index]`：前缀身份与当前内容身份的组合 hash。

默认 `ForwardReusePolicy(preserve_node_time_order=True)`。前缀扫描遇到缓存缺失、空 message_id、节点的 forward_context_key 不匹配，或 `(created_at, updated_at, forward_sort_key)` 不再单调时立即停止，后续全部重建。业务插件不得关闭顺序保护来扩大命中。

平台转发验证失败时，由投递基础设施通过 message_asset_repo.mark_stale 标脏并降级；不能静默保留失效消息 ID。已有缓存 node 不重复执行 staging。

help 的 summary/guide/demo 图片优先使用静态构建产物，避免运行时时间戳引起 hash 抖动。实际内容改变形成新资产；只有内容与上下文可复用条件均满足时，才允许跨目标复用。

## 静态约束与回归

现有 Ruff banned-api 和 `scripts/check_message_delivery_guardrails.py` 检查插件/hook 的直接发送路径。改变输出接入方式时可运行：

```bash
uv run python scripts/check_message_delivery_guardrails.py
```

这项检查补充根规范门禁，不代替它。相关回归入口：

- `tests/test_message_plan_guardrails.py`
- `tests/test_message_delivery_forward.py`
- `tests/test_message_api_hooks.py`
- `tests/test_message_asset_upsert.py`
- 本次受影响插件的 matcher/交互测试

修改统一计划/投递语义时验证：单条、等待、批量 forward、缓存命中、失效重建、连续前缀复用、上下文冲突和 fallback。增加 reply/at 或语言分支时补相应“不错误复用”覆盖。

只消费已有接口的插件，验证自身内容、目标、节点顺序、失败与会话行为即可，不复制基础设施测试。无副作用的语义验证优先 mock Bot/API，真实发消息需要计划明确授权。

## 代码定位

推荐按需阅读：`src/lib/message_plan.py` → `message_api_hooks.py` → `message_delivery.py` → `message_assets.py` → `onebot_forward.py`。兼容 i18n helper 的内部直发实现属于基础设施；新增业务输出仍遵循本 skill 的计划入口。
