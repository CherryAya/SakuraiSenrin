# Docs Index

`docs/` 目录按三类维护：

- `docs/user/`
  - 面向最终用户
  - 允许进入 Git
- `docs/operations/`
  - 面向部署、巡检、恢复、迁移、维护
  - 默认只做本地维护，不作为对外用户文档发布
- `docs/development/`
  - 面向项目开发、测试、渲染、内部实现
  - 默认只做本地维护，不作为对外用户文档发布

## 当前目录

- `user/`
  - `README.zh_CN.md`
- `operations/`
  - `backup.md`
  - `operations-scripts.md`
- `development/`
  - `database-usage.md`
  - `build-docs.md`
  - `nonebug-plugin-testing-checklist.md`
  - `nonebug-plugin-testing-spec.md`
  - `pil-rendering-style-guide.md`
  - `plugin-demo-visual-checklist.md`
  - `permission-system-guide.md`
  - `demo-theme-migration.md`
  - `IMPLEMENTATION_COMPLETE.md`

## 维护规则

1. 新增文档前先判断目标读者是谁，再放到对应分类。
2. 用户文档只保留稳定、可公开、适合放仓库的内容。
3. 运维文档和开发文档默认本地维护；如果需要纳入 Git，必须明确说明原因。
4. 跨分类引用时使用相对路径，避免目录调整后失效。
