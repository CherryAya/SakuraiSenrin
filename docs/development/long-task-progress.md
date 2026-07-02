# LongTask Progress Endpoint

`LongTask` 现在不只是一个运行时辅助类，也有一份开发期可消费的进度出口，用来持续追踪“全仓通用 LongTask/Progress 接口重构”当前做到哪一步。

当前交付包含两部分：

- 运行时通用接口：`src/lib/long_task.py`
- 开发期进度 endpoint：`docs/development/long-task-progress.json`

这份 JSON 不是手写维护，而是由脚本扫描代码生成，避免“文档说完成，代码其实没跟上”。

## 为什么要有这个 Endpoint

LongTask 重构跨越多个插件和后台任务，单靠 README 或 issue checklist 很容易失真。这个 endpoint 的目标是提供一份随代码一起更新的审计视图：

- 哪些目标模块已经接入 `LongTaskRunner`
- 哪些入口已经接入 `LoggerProgressSink`
- 哪些用户态流程已经接入 `MessageEventProgressSink` / `MatcherProgressSink`
- 仓库里是否还残留“先发一条等待消息再阻塞”的旧味道

## 生成方式

```bash
uv run python scripts/long_task_progress.py
```

默认会刷新：

```text
docs/development/long-task-progress.json
```

如果想直接查看完整 JSON：

```bash
uv run python scripts/long_task_progress.py --stdout
```

如果想把“仍有遗留旧等待提示”作为失败条件：

```bash
uv run python scripts/long_task_progress.py --fail-on-candidates
```

## JSON 结构

当前 payload 结构如下：

```json
{
  "version": 1,
  "generated_at": "2026-07-03T00:00:00+00:00",
  "root": "/path/to/repo",
  "summary": {
    "total_targets": 15,
    "complete_targets": 15,
    "partial_targets": 0,
    "missing_file_targets": 0,
    "legacy_wait_candidates": 0
  },
  "targets": [
    {
      "slug": "wordbank.entry_add",
      "status": "complete",
      "has_runner": true,
      "has_logger_sink": true,
      "has_message_event_sink": true,
      "has_matcher_sink": false,
      "missing": []
    }
  ],
  "legacy_wait_candidates": []
}
```

其中：

- `targets` 是显式维护的迁移目标矩阵
- `legacy_wait_candidates` 是对 `src/plugins`、`src/services`、`src/hooks` 的启发式扫描结果
- 如果某个文件还保留“请稍候 / 执行中 / 处理中”这类旧等待提示，且文件本身没有 `LongTaskRunner`，就会被列出来

## 当前约束

这份 endpoint 目前是“开发进度审计”而不是“运行时 HTTP API”。仓库里没有统一 Web 服务层，因此先落为本地 JSON 出口最稳妥：

- 可以直接被 CI、脚本、人工 review 消费
- 不引入新的运行时服务依赖
- 后续如果仓库引入统一管理面板，再把这份 JSON 挂成 HTTP endpoint 即可

## 接入规则

新增耗时任务时，默认遵循：

1. 插件或服务只声明任务语义：`task_name`、`prompt`、阶段推进。
2. 用户可见提示统一走 `LongTaskRunner`。
3. 群聊/私聊事件优先用 `MessageEventProgressSink`。
4. `got` / 引导态 matcher 优先用 `MatcherProgressSink`。
5. 后台任务至少接 `LoggerProgressSink`。
6. 新接入点需要同步刷新 `long-task-progress.json`，保证文档与代码一致。

## 相关文件

- `src/lib/long_task.py`
- `src/lib/long_task_progress.py`
- `scripts/long_task_progress.py`
- `docs/development/long-task-progress.json`
