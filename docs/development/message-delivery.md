# 文案与消息投递

用户文案通过 i18n 生成，再用安全分段与 `DeliveryPlan` 交给统一投递层；接入流程见 [消息 skill](../../.agents/skills/sakuraisenrin-message-delivery/SKILL.md)。

文案键、语言与占位符看 [i18n.md](../../.agents/skills/sakuraisenrin-message-delivery/references/i18n.md)，消息上下文、合并转发、缓存与降级看 [delivery.md](../../.agents/skills/sakuraisenrin-message-delivery/references/delivery.md)。
