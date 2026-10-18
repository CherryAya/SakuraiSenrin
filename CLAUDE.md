@AGENTS.md

# Claude Code 入口

共同规则仅维护在上面的导入文件中。先遵守其 Plan → Confirm → Action、全局门禁、i18n 和提交要求。

专项技能由项目内 `.claude/skills/` 提供发现入口，正文统一读取 `.agents/skills/`。入口只做路由，不复制细则；按当前任务读取对应参考材料。

现有插件和新插件组成要求见[插件总表](docs/development/plugin-catalog.md)。本文件使用标准大小写 `CLAUDE.md`，兼容大小写敏感的工作区。
