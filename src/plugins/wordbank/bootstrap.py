"""wordbank 插件生命周期与定时任务（初始化、事件归档、媒体维护）。"""

from __future__ import annotations

from nonebot import get_driver, require

from src.config import config
from src.lib.long_task import LoggerProgressSink, LongTaskRunner, LongTaskSpec
from src.logger import logger
from src.plugins.wordbank.debug import elapsed_ms, log_perf, perf_start
from src.services.startup_sync import ensure_restore_not_in_progress

from .services import wordbank_media_service, wordbank_service

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

_wordbank_initialized = False


async def initialize_wordbank_plugin() -> None:
    global _wordbank_initialized
    if _wordbank_initialized:
        log_perf("plugin.initialize.cached", initialized=True)
        return
    start = perf_start()
    service_start = perf_start()
    await wordbank_service.initialize()
    service_ms = elapsed_ms(service_start)
    media_start = perf_start()
    await wordbank_media_service.rebuild_cache()
    media_ms = elapsed_ms(media_start)
    _wordbank_initialized = True
    log_perf(
        "plugin.initialize.done",
        start=start,
        service_initialize_ms=f"{service_ms:.2f}",
        media_rebuild_ms=f"{media_ms:.2f}",
    )


def reset_wordbank_initialized() -> None:
    """复位初始化标记，供运行时重载流程强制重新初始化。"""
    global _wordbank_initialized
    _wordbank_initialized = False


async def _initialize_wordbank_plugin() -> None:
    await initialize_wordbank_plugin()


driver = get_driver()


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
