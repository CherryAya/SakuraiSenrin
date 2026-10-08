"""Runtime backup scheduling."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from nonebot import require

from src.lib.long_task import LoggerProgressSink, LongTaskRunner, LongTaskSpec
from src.logger import logger


class _Scheduler(Protocol):
    """本模块真正用到的 APScheduler 子集。

    ``nonebot_plugin_apscheduler.scheduler`` 的类型经 apscheduler 3.x 传播后
    参数退化成 ``Unknown``（apscheduler 未带 ``py.typed``）。这里只声明调用到的
    两个方法，把第三方边界收口成一个明确的协议，避免整条调用链被判为未知。
    """

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


def install_backup_scheduler() -> None:
    from src.config import config
    from src.services.backup import (
        build_backup_service_from_config,
        build_default_backup_plan,
    )
    from src.services.startup_sync import ensure_restore_not_in_progress

    if not config.BACKUP_ENABLED:
        return

    require("nonebot_plugin_apscheduler")
    from nonebot_plugin_apscheduler import scheduler as _raw_scheduler

    scheduler = cast(_Scheduler, _raw_scheduler)

    if scheduler.get_job("database_backup_default") is not None:
        return
    plan = build_default_backup_plan()

    @scheduler.scheduled_job(
        "cron",
        hour=plan.cron_hour,
        minute=plan.cron_minute,
        id="database_backup_default",
        coalesce=True,
        misfire_grace_time=600,
        max_instances=1,
    )
    async def _database_backup_default_job() -> None:
        service = build_backup_service_from_config()
        try:
            async with LongTaskRunner(
                LongTaskSpec(
                    task_name="backup.scheduler.default",
                    source_kind="backup_scheduler",
                    threshold_ms=0,
                ),
                sink=LoggerProgressSink(),
            ) as long_task:
                ensure_restore_not_in_progress(source="backup_scheduler")
                await long_task.advance("archiving")
                await service.run(plan)
        except Exception as exc:
            logger.exception(f"[Backup] scheduled run failed: {exc}")
