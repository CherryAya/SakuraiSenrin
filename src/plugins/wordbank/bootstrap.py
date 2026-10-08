"""wordbank 定时任务与启动钩子（依赖 nonebot driver / apscheduler）。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from nonebot import get_driver, require

from src.config import config
from src.lib.long_task import LoggerProgressSink, LongTaskRunner, LongTaskSpec
from src.logger import logger
from src.services.startup_sync import ensure_restore_not_in_progress

from .lifecycle import initialize_wordbank_plugin
from .services import wordbank_media_service, wordbank_service

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler as _raw_scheduler

driver = get_driver()


class _Scheduler(Protocol):
    """本模块真正用到的 APScheduler 子集。

    ``nonebot_plugin_apscheduler.scheduler`` 的类型经 apscheduler 3.x 传播后参数
    退化成 ``Unknown``（apscheduler 未带 ``py.typed``）。这里只声明调用到的
    ``scheduled_job``，把第三方边界收口成一个明确的协议。
    """

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


scheduler = cast("_Scheduler", _raw_scheduler)


@driver.on_startup
async def _initialize_wordbank_plugin_hook() -> None:
    await initialize_wordbank_plugin()


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=30,
    id="wordbank_event_archive",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _wordbank_event_archive_job() -> None:
    try:
        async with LongTaskRunner(
            LongTaskSpec(
                task_name="wordbank.event_archive",
                source_kind="wordbank_event_archive",
                threshold_ms=0,
            ),
            sink=LoggerProgressSink(),
        ) as long_task:
            ensure_restore_not_in_progress(source="wordbank_event_archive")
            await long_task.advance("archiving")
            await wordbank_service.repository.archive_event_shards()
        logger.success("[Wordbank] cron archive done")
    except Exception as exc:
        logger.exception(f"[Wordbank] cron archive failed: {exc}")


@scheduler.scheduled_job(
    "cron",
    hour=1,
    minute=15,
    id="wordbank_media_maintenance",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _wordbank_media_maintenance_job() -> None:
    try:
        async with LongTaskRunner(
            LongTaskSpec(
                task_name="wordbank.media_maintenance",
                source_kind="wordbank_media_maintenance",
                threshold_ms=0,
            ),
            sink=LoggerProgressSink(),
        ) as long_task:
            ensure_restore_not_in_progress(source="wordbank_media_maintenance")
            await long_task.advance("processing_items")
            report = await wordbank_media_service.run_scheduled_maintenance(
                batch_size=config.WORDBANK_MEDIA_MIGRATION_BATCH_SIZE
            )
        logger.success(f"[Wordbank] media maintenance done: {report}")
    except Exception as exc:
        logger.exception(f"[Wordbank] media maintenance failed: {exc}")
