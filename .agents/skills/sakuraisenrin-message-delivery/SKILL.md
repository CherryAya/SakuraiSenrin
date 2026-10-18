---
name: sakuraisenrin-message-delivery
description: 在 SakuraiSenrin 修改用户文案、插件或 hook 回复、通知、图片文字、消息计划、合并转发或消息资产缓存时使用，统一处理 i18n、安全分段与投递。
---

# SakuraiSenrin 消息与文案

先遵守仓库 [AGENTS.md](../../../AGENTS.md) 的计划审查流程。此技能用于实现和审查输出路径，不自动授权向真实用户发送消息。

## 按任务读取

- 新增或修改用户可见文字、名称/描述、图片标签、帮助包装：读 [i18n.md](references/i18n.md)。
- 新增或修改回复、通知、图片/回复/艾特段、等待提示、批量节点、缓存与 fallback：读 [delivery.md](references/delivery.md)。
- 一般输出改动同时涉及两者。只有纯缓存算法变更且不改文案时，可只读投递参考。

## 输出流程

1. 核对触发事件、接收目标、用户/群组语言、权限和交互状态；确认哪个模块拥有文案与业务数据。
2. 在计划里列出受影响的文案键、消息节点/顺序、上下文语义、缓存与降级行为，以及相应验证。
3. 用户确认后，先通过项目 i18n 得到文案，再表达为安全的 `MessagePlanEntry`/block 和 `DeliveryPlan`。
4. 使用 `deliver_message_plan(...)` 或 `message_plan` 的统一便捷函数投递；让基础设施决定发送形式与复用。
5. 核对文案键/占位符、CQ 注入安全、reply/at 上下文，以及本次涉及的等待、失败和会话结束路径。

## 核心边界

- 插件不得直接导入/调用 `deliver_single_message`、`resolve_delivery_target`、`deliver_forward_messages`、`send_custom_forward` 充当正式输出入口。
- 不直接调用 matcher/bot 的发送接口，也不手写 OneBot forward 节点、消息资产 SQL 或“发给自己再转发”的业务路径。
- 不用 `Message(raw_text)` 解析用户输入、数据库内容或译文；文字用 TextBlock、安全 helper 或 MessageSegment.text，图片/艾特/回复显式分段。
- `reply`、`at` 等上下文绑定内容不能被当作全局资产复用；合并转发保持顺序与上下文匹配，只允许连续安全前缀复用。
- 用户可见文字不直接硬编码，包括 fallback、等待提示、图片标题和转发摘要。机器标识和原始业务数据不伪装成翻译键。
- 调整统一层复用/渲染判定时补可解释 debug 日志和对应回归；不把长消息正文写进调试日志。
