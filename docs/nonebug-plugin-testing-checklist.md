# NoneBug 插件测试 Checklist

用于新增插件、修改插件入口、迁移传统 matcher mock 测试时逐项检查。

## 1. 基础合规

- [ ] 插件声明 `PluginMetadata`。
- [ ] `extra.author`、`extra.version`、`extra.trigger`、`extra.permission`、`extra.docs` 完整。
- [ ] `extra.docs.provider` 返回 `Message` 或 await 后返回 `Message`。
- [ ] 多文件命令插件存在 `docs/README.MD`。
- [ ] 管理能力使用明确权限或群管理角色检查。
- [ ] 默认不设置 `no_check`；如设置，已测试绕过运行时检查的必要性。

## 2. 测试分层

- [ ] matcher、command、notice、request、got 会话使用 NoneBug。
- [ ] service/repo 纯逻辑继续使用普通 pytest。
- [ ] 直接调用 OneBot API 的 service/job 使用 `app.test_api()`。
- [ ] 不为新 matcher 行为新增 `DummyMatcher` 测试。
- [ ] 每个普通 pytest 用例都能说明不需要 NoneBot 运行时。

## 3. 命令插件

- [ ] 主命令可触发。
- [ ] 每个别名可触发或已有集中别名测试。
- [ ] 空参数有明确提示。
- [ ] 非法参数有明确提示。
- [ ] 边界参数被覆盖，如分页、ID、范围、开关值。
- [ ] 普通用户权限路径被覆盖。
- [ ] 群管理员或群主路径被覆盖。
- [ ] 超级用户路径被覆盖。
- [ ] 权限拒绝路径被覆盖。
- [ ] 成功路径断言发送文案和关键副作用。
- [ ] `finish/reject/pause/got` 状态被断言。
- [ ] 外部依赖失败有降级测试。

## 4. 被动与事件插件

- [ ] 命中 rule 的事件被覆盖。
- [ ] 未命中 rule 的事件被覆盖。
- [ ] 机器人自触发防护被覆盖。
- [ ] `block=False` 不误拦截后续 matcher。
- [ ] Notice/Request 事件类型过滤被覆盖。
- [ ] API 调用参数被断言。
- [ ] API 失败路径被覆盖。
- [ ] 缓存、snapshot 或审计副作用被断言。

## 5. 运行时检查

- [ ] 全局忽略用户被拦截。
- [ ] 超级用户放行。
- [ ] 全局黑名单被拦截。
- [ ] 群黑名单被拦截。
- [ ] 未授权群被拦截。
- [ ] 全员禁言群被拦截。
- [ ] 群缓存未命中时默认阻止。
- [ ] `no_check` 插件绕过检查链。
- [ ] 运行时同步只调用必要 repo/service。

## 6. 数据与幂等

- [ ] 重复操作不会重复写入。
- [ ] 锁冲突或并发冲突有稳定结果。
- [ ] 无待处理状态有明确文案或返回值。
- [ ] 管理类强一致写入使用合适 writer policy。
- [ ] 高频路径避免不必要同步 I/O。
- [ ] 需要留痕的状态变更写入审计日志。
- [ ] 用户可感知文本变化写入 snapshot。
- [ ] 外部 I/O 全部用 mock 隔离。

## 7. 文档与 Help

- [ ] docs provider 返回 `Message`。
- [ ] help 列表可发现可见插件。
- [ ] 精确查询返回插件详情。
- [ ] 模糊查询返回合理命中。
- [ ] 歧义查询返回候选提示。
- [ ] provider 抛异常时 help 有明确降级说明。
- [ ] 命令文档覆盖命令、参数、示例、权限说明。

## 8. Cron/Job

- [ ] scheduler job `id` 唯一。
- [ ] job 函数可重复执行且幂等。
- [ ] 无待处理数据路径被覆盖。
- [ ] 成功日志或结果被覆盖。
- [ ] 失败日志或降级路径被覆盖。
- [ ] job 中的 bot API 使用 `app.test_api()`。
- [ ] 测试 collection 不触发真实定时任务。

## 9. 用例质量

- [ ] 每个行为用例至少断言消息、API、matcher 状态、service 调用或数据状态之一。
- [ ] 不只断言“不抛异常”。
- [ ] mock 调用断言包含关键参数。
- [ ] 字符串断言不过度宽泛。
- [ ] 事件 helper 字段贴近 OneBot V11 真实事件。
- [ ] 测试不读取生产配置。
- [ ] 测试不写入生产数据目录。

## 10. 提交前命令

Python 代码或测试改动:

- [ ] `uv run ruff check --fix`
- [ ] `uv run ruff format`
- [ ] `uv run pyright`
- [ ] `uv run pytest`

只改文档:

- [ ] `git diff --check`

PR 或提交说明:

- [ ] 说明变更点。
- [ ] 说明风险点。
- [ ] 说明验证命令与结果。
- [ ] 若 docs 接口变化，说明 `extra.docs` 输出示例。
