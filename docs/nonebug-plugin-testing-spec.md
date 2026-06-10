# NoneBug 插件单测规范

版本: v1.0  
适用范围: `src/plugins/**`、`src/hooks/**`、`tests/plugins/**`  
最后更新: 2026-06-11

## 1. 定位

NoneBug 是 NoneBot2 的 pytest 测试插件。它不替代 pytest runner，而是在 pytest 中提供 `app` fixture 和 NoneBot 运行时测试能力。

本项目中的“使用 NoneBug 替代传统 pytest”指:

1. 插件入口、matcher、会话流程、权限、rule、OneBot API 调用必须优先使用 NoneBug。
2. 不再为 matcher 行为新增手写 `DummyMatcher`、直接调用入口函数、手动断言 `send/finish` 的测试。
3. 纯函数、service 业务规则、repo 边界、文档渲染等不依赖 NoneBot 运行时的逻辑，仍可使用普通 pytest。

资料依据:

1. NoneBot 测试文档: <https://nonebot.dev/docs/best-practice/testing/>
2. NoneBot 行为测试文档: <https://nonebot.dev/docs/best-practice/testing/behavior>
3. NoneBug 仓库: <https://github.com/nonebot/nonebug>
4. NoneBug PyPI: <https://pypi.org/project/nonebug/>

当前项目版本事实:

1. `nonebug 0.4.4`
2. `nonebot2 2.5.0`
3. `pytest 8.4.2`
4. `pytest-asyncio 1.3.0`
5. `tests/conftest.py` 已通过 `NONEBOT_INIT_KWARGS` 注入测试配置。

## 2. 测试分层

### 2.1 必须使用 NoneBug 的测试

以下测试必须优先使用 NoneBug:

1. `on_command`、`on_fullmatch`、`on_message`、`on_notice`、`on_request` 等 matcher 是否被正确触发。
2. matcher 的 `rule`、`permission`、`priority`、`block` 行为。
3. `matcher.send()`、`matcher.finish()`、`matcher.reject()`、`matcher.pause()`、`got()` 多轮会话。
4. command 参数注入，如 `CommandArg()`、`Arg()`、`Depends()`、`T_State`。
5. 插件直接调用 OneBot API，如 `send_group_msg`、`set_group_leave`、`delete_msg`。
6. 多 matcher 同时存在时，优先级、拦截、未命中、权限拒绝等整体行为。
7. 运行时 hook 对插件的影响，包括黑名单、群授权、全员禁言、`no_check`。

推荐工具:

1. `app.test_matcher()` 测试事件流和 matcher 行为。
2. `ctx.receive_event(bot, event)` 注入 OneBot V11 事件。
3. `ctx.should_call_send(...)` 断言发送消息。
4. `ctx.should_call_api(...)` 断言 OneBot API 调用。
5. `ctx.should_pass_rule()`、`ctx.should_not_pass_rule()` 断言 rule。
6. `ctx.should_pass_permission()`、`ctx.should_not_pass_permission()` 断言权限。
7. `ctx.should_finished()`、`ctx.should_rejected()`、`ctx.should_paused()` 断言会话控制。

### 2.2 可使用普通 pytest 的测试

以下测试可以继续使用普通 pytest:

1. 纯解析函数，如参数拆分、别名归一、分页参数校验。
2. service 层纯业务规则，如幂等判断、状态迁移、文案格式化。
3. repo/ops 边界测试，如唯一约束、缓存回填、writer policy。
4. 图片、文档、help docs 渲染等不依赖 NoneBot matcher 的函数。
5. 外部 API client 的纯封装测试，前提是没有 NoneBot bot/matcher 参与。

普通 pytest 测试必须能说明它不需要 NoneBot 运行时。若测试对象接收 `Matcher`、`Bot`、`Event` 或依赖 `CommandArg/Arg/Depends` 注入，应改用 NoneBug。

### 2.3 Service 中调用 Bot API 的测试

service/job 若直接接收 `Bot` 并调用 OneBot API，使用 `app.test_api()`:

```python
async def test_service_sends_group_message(app: App) -> None:
    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.should_call_api(
            "send_group_msg",
            {"group_id": 20001, "message": "READY"},
            result={"message_id": 1},
        )

        await service.notify(bot, group_id="20001")
```

API 失败路径必须通过 `exception=` 覆盖，并断言业务代码降级而不是阻塞主流程。

## 3. 插件测试矩阵

每个插件按类型选择测试项。

### 3.1 命令插件

必须覆盖:

1. 主命令和别名均能触发正确 matcher。
2. 参数为空、参数非法、参数边界值。
3. 普通用户、群管理员、群主、超级用户的权限差异。
4. 成功路径的返回消息和关键副作用。
5. `finish/reject/pause/got` 会话控制。
6. 外部依赖失败时的降级消息。
7. `extra.docs.provider` 返回 `Message`，help 可自动发现。

### 3.2 被动消息插件

必须覆盖:

1. 命中和未命中 rule 的消息。
2. 机器人自触发防护，如 `event.user_id == bot.self_id`。
3. `block=False` 时不误拦截后续 matcher。
4. 高并发热路径不会执行强 I/O。
5. 外部依赖失败时不影响主事件处理。

### 3.3 Notice/Request 插件

必须覆盖:

1. 事件类型过滤，如入群、退群、戳一戳、好友请求、群邀请。
2. OneBot API 调用参数。
3. API 调用失败后的日志或降级。
4. 数据同步、缓存更新、snapshot 写入。
5. `no_check` 是否确有必要，默认不设置。

### 3.4 Cron/Job 插件

必须覆盖:

1. job 函数幂等。
2. 锁冲突、重复执行、无待处理数据。
3. 成功路径和失败路径日志。
4. 对 bot API 的调用使用 `app.test_api()`。
5. scheduler 注册唯一 `id`，不在测试 collection 时触发真实外部任务。

### 3.5 Hook 插件

必须覆盖:

1. `_runtime_sync` 对用户、群组、成员缓存的回填调用。
2. `_runtime_check` 对全局忽略、超级用户、全局黑名单、群黑名单、未授权群、全员禁言的处理。
3. `PluginMetadata.extra.no_check` 绕过检查链。
4. 未命中群缓存时默认阻止。
5. 需要 API 同步群成员时只调用必要接口。

## 4. 测试结构

推荐目录:

```text
tests/plugins/<plugin_name>/
  helpers.py
  test_matchers.py
  test_handlers.py
  test_services.py
  test_repository_edges.py
  test_plugin_docs.py
```

文件职责:

1. `helpers.py`: 只放事件构造器、fake records、公共断言，不放业务逻辑。
2. `test_matchers.py`: NoneBug matcher 行为测试，是命令/被动插件的主测试入口。
3. `test_handlers.py`: 仅测试无入口副作用的薄 handler helper；若涉及 matcher 行为，放入 `test_matchers.py`。
4. `test_services.py`: service 业务规则和降级路径。
5. `test_repository_edges.py`: repo/ops 的边界与约束。
6. `test_plugin_docs.py`: docs provider 和 README 解析。

新测试不要继续扩大 `DummyMatcher` 用法。历史测试可保留，但新 matcher 行为应以 NoneBug 为准。

## 5. NoneBug 用例模板

### 5.1 Matcher 成功路径

```python
async def test_command_returns_expected_message(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module.service, "run", AsyncMock(return_value="OK"))

    async with app.test_matcher(module.command_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("#command arg")

        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "OK", bot=bot)
        ctx.should_finished(module.command_matcher)
```

### 5.2 权限拒绝路径

```python
async def test_admin_command_rejects_member(app: App) -> None:
    async with app.test_matcher(module.admin_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("#admin", role="member")

        ctx.receive_event(bot, event)
        ctx.should_not_pass_permission(module.admin_matcher)
```

若权限逻辑在 handler 内手写，应断言返回的拒绝文案和 `ctx.should_finished()`。

### 5.3 got 多轮会话

```python
async def test_remove_confirm_flow(app: App) -> None:
    async with app.test_matcher(module.remove_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("#remove", role="admin")

        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "请输入确认文本", bot=bot)
        ctx.should_rejected(module.remove_matcher)

        ctx.receive_event(bot, build_group_message_event("确认退群", role="admin"))
        ctx.should_call_send(event, "请输入退群原因", bot=bot)
        ctx.should_rejected(module.remove_matcher)
```

按实际插件文案和事件上下文调整断言，不允许只断言“不抛异常”。

### 5.4 Bot API 成功和失败

```python
async def test_api_failure_degrades(app: App) -> None:
    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.should_call_api(
            "send_group_msg",
            {"group_id": 20001, "message": "NOTICE"},
            exception=RuntimeError("network down"),
        )

        await service.notify(bot, "20001")
```

失败路径应断言日志、返回值、状态或无副作用，不能只吞异常。

## 6. Fixture 与隔离

1. 事件构造统一放在 `tests/plugins/<plugin_name>/helpers.py` 或复用现有 helper。
2. OneBot V11 事件使用 `model_validate()` 构造，字段必须贴近真实上报。
3. 通过 `monkeypatch` 和 `AsyncMock` 隔离数据库、外部 HTTP、对象存储、图片搜索、GitHub 等 I/O。
4. 不在测试 collection 阶段加载无关插件。入口模块存在启动副作用时，应先隔离配置、scheduler、数据库初始化和外部服务。
5. 测试全局配置使用 `NONEBOT_INIT_KWARGS`，不要读取本地生产配置。
6. 数据库测试必须使用测试库或临时路径，不得写入生产数据目录。

## 7. 断言要求

每个行为测试至少断言一类可观察结果:

1. 消息发送内容。
2. API 名称和参数。
3. matcher 状态，如 passed、ignored、finished、rejected、paused。
4. service/repo 调用参数。
5. 数据库状态或缓存状态。
6. 明确的降级返回值或日志。

禁止只断言:

1. 用例没有异常。
2. mock 被调用但不校验参数。
3. 字符串只包含过宽泛关键词。
4. matcher 行为通过手写 fake matcher 间接模拟。

## 8. 质量门禁

涉及 Python 测试或功能代码改动时执行:

```bash
uv run ruff check --fix
uv run ruff format
uv run pyright
uv run pytest
```

只改文档时可跳过 Python 质量命令，但仍应执行:

```bash
git diff --check
```

## 9. 迁移策略

1. 新插件和新 matcher 行为测试直接使用 NoneBug。
2. 修改旧插件入口或 handler 时，同步补一组 `app.test_matcher()` 覆盖核心路径。
3. 旧 `DummyMatcher` 测试不强制一次性删除，但同一行为被 NoneBug 覆盖后应停止扩展旧模式。
4. service/repo 纯逻辑测试不迁移到 NoneBug，避免慢速和过度耦合。
5. 每次迁移以一个插件或一个独立行为为单位提交，避免大范围测试重写。
