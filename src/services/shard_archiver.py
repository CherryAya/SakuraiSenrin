"""核心分片库（log / snapshot）的定期归档调度。

``log_db`` / ``snapshot_db`` 是 EventStore 分片库：trace 审计日志与用户/群/
成员快照按月落盘，``active_window_months=2``。二者此前没有任何归档调用点——
分片只增不减，长跑后目录里堆积全部历史月份，而 water/wordbank 的同类库都有
月度归档任务。这里把归档接入 cron，与 water（00:25/00:35/00:40）和 wordbank
（00:30）错峰到 00:45。

冷策略说明：两个库保持 ``ColdPolicy.DENY``（与 wordbank 的 HYDRATE 不同）。
运行时没有读取历史分片的路径；归档后若真有路径去读，DENY 会让它**显式报错**
而不是静默返回空库。运维脚本 ``scripts/query_trace_logs.py`` 自带 HYDRATE，
不受影响。若将来新增历史读取需求，应连同该库的策略一起评估。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from nonebot import require

from src.lib.long_task import LoggerProgressSink, LongTaskRunner, LongTaskSpec
from src.logger import logger


class _Scheduler(Protocol):
    """本模块真正用到的 APScheduler 子集（与 backup_scheduler 同款收口）。"""

    def get_job(self, job_id: str, jobstore: str | None = ...) -> object | None: ...

    def scheduled_job(
        self,
        trigger: str,
        *,
        hour: int = ...,
        minute: int = ...,
        id: str = ...,
        coalesce: bool = ...,
        misfire_grace_time: int = ...,
        max_instances: int = ...,
    ) -> Callable[[Callable[[], Awaitable[None]]], object]: ...


# 与 water/wordbank 的归档任务错峰：00:25/00:30/00:35/00:40 已有任务。
ARCHIVE_HOUR = 0
ARCHIVE_MINUTE = 45


def install_shard_archiver_scheduler() -> None:
    """注册核心分片库的每日归档任务（幂等）。"""
    require("nonebot_plugin_apscheduler")
    from nonebot_plugin_apscheduler import scheduler as _raw_scheduler

    scheduler = cast(_Scheduler, _raw_scheduler)

    if scheduler.get_job("core_shard_archive") is not None:
        return

    @scheduler.scheduled_job(
        "cron",
        hour=ARCHIVE_HOUR,
        minute=ARCHIVE_MINUTE,
        id="core_shard_archive",
        coalesce=True,
        misfire_grace_time=300,
        max_instances=1,
    )
    async def _core_shard_archive_job() -> None:
        from src.database.instances import log_db, snapshot_db
        from src.services.startup_sync import ensure_restore_not_in_progress

        try:
            async with LongTaskRunner(
                LongTaskSpec(
                    task_name="core.shard_archive",
                    source_kind="core_shard_archive",
                    threshold_ms=0,
                ),
                sink=LoggerProgressSink(),
            ) as task:
                ensure_restore_not_in_progress(source="core_shard_archive")
                await task.advance("archiving")
                log_report = await log_db.run_archiver_task()
                snapshot_report = await snapshot_db.run_archiver_task()
                failures = [*log_report.failures, *snapshot_report.failures]
                if failures:
                    details = "; ".join(
                        f"{failure.shard_key}({failure.outcome.value}): "
                        f"{failure.reason}"
                        for failure in failures
                    )
                    raise RuntimeError(f"core shard archive failures: {details}")
            logger.success(
                f"[ShardArchiver] cron archive done: log({log_report.summary()}) "
                f"snapshot({snapshot_report.summary()})"
            )
        except Exception as exc:
            logger.exception(f"[ShardArchiver] cron archive failed: {exc}")


__all__ = [
    "ARCHIVE_HOUR",
    "ARCHIVE_MINUTE",
    "install_shard_archiver_scheduler",
]
