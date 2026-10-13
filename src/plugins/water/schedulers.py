"""water 定时任务：结算、归档、日报推送。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast

from nonebot import get_bots, require
from nonebot.adapters.onebot.v11.bot import Bot

from src.lib.db.connectors import SegmentArchiveReport
from src.lib.long_task import (
    LoggerProgressSink,
    LongTaskRunner,
    LongTaskSpec,
)
from src.logger import logger
from src.services.startup_sync import ensure_restore_not_in_progress

from .database import water_repo
from .services.cron_orchestrator import (
    WaterSubprocessResult,
    WaterWorkerJobName,
    WaterWorkerManifest,
    run_water_subprocess_job,
)
from .services.report import (
    build_daily_report_prepare_result_from_manifest,
    water_report_service,
)
from .services.settlement import build_settlement_result_from_manifest

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler


@asynccontextmanager
async def _worker_job(
    job_key: str,
    worker_job: WaterWorkerJobName,
    *,
    locale: str | None = None,
) -> AsyncGenerator[tuple[LongTaskRunner, WaterWorkerManifest, WaterSubprocessResult]]:
    """执行一次 worker 子进程：恢复期互斥 + 长任务进度 + 清单校验。"""
    async with LongTaskRunner(
        LongTaskSpec(
            task_name=f"water.{job_key}",
            source_kind=f"water_{job_key}",
            threshold_ms=0,
        ),
        sink=LoggerProgressSink(),
    ) as long_task:
        ensure_restore_not_in_progress(source=f"water_{job_key}")
        result = await run_water_subprocess_job(worker_job, locale=locale)
        manifest = result.manifest
        if manifest is None:
            raise RuntimeError(
                f"water {job_key} manifest missing, exit={result.exit_code}"
            )
        yield long_task, manifest, result


def _raise_on_archive_failures(job_key: str, report: SegmentArchiveReport) -> None:
    """归档失败/冲突时抛错，让 job 的 except 分支记 ERROR。"""
    if report.ok:
        return
    details = "; ".join(
        f"{failure.shard_key}({failure.outcome.value}): {failure.reason}"
        for failure in report.failures
    )
    raise RuntimeError(f"water {job_key} archive failures: {details}")


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=5,
    id="water_daily_settlement",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _water_daily_settlement_job() -> None:
    try:
        async with _worker_job("daily_settlement", "settlement") as (
            task,
            manifest,
            _worker,
        ):
            await task.advance("processing_items")
            result = build_settlement_result_from_manifest(manifest)
        if result.success:
            logger.success(
                "[Water] cron settlement done: "
                f"date={result.record_date} "
                f"rows={result.aggregate_rows} "
                f"achievements={result.unlocked_achievements}"
            )
        else:
            logger.warning(
                "[Water] cron settlement skipped: "
                f"date={result.record_date} reason={result.reason}"
            )
    except Exception as e:
        logger.exception(f"[Water] cron settlement failed: {e}")


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=25,
    id="water_message_archive",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _water_message_archive_job() -> None:
    try:
        async with LongTaskRunner(
            LongTaskSpec(
                task_name="water.message_archive",
                source_kind="water_message_archive",
                threshold_ms=0,
            ),
            sink=LoggerProgressSink(),
        ) as task:
            ensure_restore_not_in_progress(source="water_message_archive")
            await task.advance("archiving")
            report = await water_repo.archive_message_shards()
            _raise_on_archive_failures("message_archive", report)
        logger.success(f"[Water] cron archive done: {report.summary()}")
    except Exception as e:
        logger.exception(f"[Water] cron archive failed: {e}")


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=35,
    id="water_summary_archive",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _water_summary_archive_job() -> None:
    try:
        async with LongTaskRunner(
            LongTaskSpec(
                task_name="water.summary_archive",
                source_kind="water_summary_archive",
                threshold_ms=0,
            ),
            sink=LoggerProgressSink(),
        ) as task:
            ensure_restore_not_in_progress(source="water_summary_archive")
            await task.advance("archiving")
            # 顺序要求：先复制滑出热窗的行，再走 prune 清理
            backfill = await water_repo.backfill_expiring_summaries()
            report = await water_repo.archive_summary_shards()
            pruned = await water_repo.prune_hot_summaries()
            _raise_on_archive_failures("summary_archive", report)
        logger.success(
            f"[Water] cron summary archive done: backfilled={backfill['copied']} "
            f"pruned={pruned} {report.summary()}"
        )
    except Exception as e:
        logger.exception(f"[Water] cron summary archive failed: {e}")


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=40,
    id="water_daily_report_push",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _water_daily_report_push_job() -> None:
    try:
        bots = list(get_bots().values())
        if not bots:
            logger.warning("[Water][ReportPush] skipped: no bot connected")
            return
        async with _worker_job(
            "daily_report_push", "daily_report_prepare", locale="zh-CN"
        ) as (task, manifest, worker):
            await task.advance("preparing")
            prepared = build_daily_report_prepare_result_from_manifest(manifest)
            if prepared.candidate_groups <= 0:
                logger.warning(
                    "[Water][ReportPush] skipped: no report candidates "
                    f"date={prepared.record_date}"
                )
                return
            await task.advance("sending")
            result = await water_report_service.send_prepared_daily_group_report_push(
                bot=cast(Bot, bots[0]),
                prepared=prepared,
                output_dir=worker.output_dir,
                locale="zh-CN",
                task=task,
            )
        logger.success(
            "[Water][ReportPush] cron done: "
            f"date={result.record_date} candidates={result.candidate_groups} "
            f"sent={result.sent_groups} failed={result.failed_groups}"
        )
    except Exception as e:
        logger.exception(f"[Water][ReportPush] cron failed: {e}")
