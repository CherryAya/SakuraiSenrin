# 日志排查

先看 `data/logs/` 下的当前进程日志，再用 [query_trace_logs.py](../../scripts/query_trace_logs.py) 按 `trace_id`、`job_id`、日期或组件查询结构化日志。

跨月问题重点对照 `record_date`、`shard_key` 和 `status`，完整筛选参数看脚本 `--help`。

```bash
uv run python scripts/query_trace_logs.py --record-date 20261018
```
