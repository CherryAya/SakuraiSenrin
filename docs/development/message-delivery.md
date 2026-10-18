# 消息投递规范入口

消息与文案接入的唯一细则已整理为项目 [sakuraisenrin-message-delivery skill](../../.agents/skills/sakuraisenrin-message-delivery/SKILL.md)。

- 用户文案、语言解析、键生成与校验：[i18n.md](../../.agents/skills/sakuraisenrin-message-delivery/references/i18n.md)。
- 安全消息计划、目标/会话、转发复用和 fallback：[delivery.md](../../.agents/skills/sakuraisenrin-message-delivery/references/delivery.md)。
- 相关插件入口、文档与测试：[插件总表](plugin-catalog.md)。

实施前遵守根 AGENTS.md 的计划审查流程。插件表达逻辑内容，统一层负责投递与缓存，不复制本页或 skill 为另一套规范。

重写前原文保存在[原文备份](../../.agents/backups/2026-10-18-before-rewrite/message-delivery.md.bak)，历史材料不作为现行规则。
