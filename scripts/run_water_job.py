"""Run one water cron job in a standalone subprocess."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
import traceback
from typing import TYPE_CHECKING, cast

import arrow
import nonebot

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.logger import logger

if TYPE_CHECKING:
    from src.plugins.water.services.worker_jobs import WaterWorkerManifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a water cron job")
    parser.add_argument(
        "--job",
        required=True,
        choices=[
            "settlement",
            "message_archive",
            "summary_archive",
            "daily_report_prepare",
        ],
    )
    parser.add_argument("--job-id")
    parser.add_argument("--output-dir")
    parser.add_argument("--record-date", type=int)
    parser.add_argument("--locale", default="zh-CN")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    return parser.parse_args()


def _resolve_output_dir(job_id: str, output_dir: str | None) -> Path:
    if output_dir:
        return Path(output_dir)
    return Path("/tmp") / "sakurai-water-jobs" / job_id


async def _run_job(
    job_name: str,
    *,
    job_id: str,
    output_dir: Path,
    record_date: int | None,
    locale: str,
    force: bool,
) -> "WaterWorkerManifest":
    from src.plugins.water.database import water_repo
    from src.plugins.water.services.report import water_report_service
    from src.plugins.water.services.settlement import water_settlement_service
    from src.plugins.water.services.worker_jobs import (
        WaterWorkerJobName,
        WaterWorkerManifest,
    )

    started_at = get_current_time()
    typed_job_name = cast(WaterWorkerJobName, job_name)
    if job_name == "settlement":
        result = await water_settlement_service.run_daily_settlement(
            force=force,
            target_date=(
                None
                if record_date is None
                else arrow.get(str(record_date), "YYYYMMDD").to("Asia/Shanghai")
            ),
        )
        status = "skipped" if result.skipped else "success"
        if not result.success and not result.skipped:
            status = "failed"
        return WaterWorkerManifest(
            job_name=typed_job_name,
            job_id=job_id,
            started_at=started_at,
            finished_at=get_current_time(),
            status=status,
            record_date=result.record_date,
            metrics={
                "aggregate_rows": result.aggregate_rows,
                "unlocked_achievements": result.unlocked_achievements,
                "forced": result.forced,
                "reason": result.reason,
            },
            error=result.reason,
        )
    if job_name == "message_archive":
        await water_repo.archive_message_shards()
        return WaterWorkerManifest(
            job_name=typed_job_name,
            job_id=job_id,
            started_at=started_at,
            finished_at=get_current_time(),
            status="success",
        )
    if job_name == "summary_archive":
        await water_repo.archive_summary_shards()
        pruned = await water_repo.prune_hot_summaries()
        return WaterWorkerManifest(
            job_name=typed_job_name,
            job_id=job_id,
            started_at=started_at,
            finished_at=get_current_time(),
            status="success",
            metrics={"pruned": pruned},
        )
    prepared = await water_report_service.prepare_daily_group_report_push(
        locale=cast(LocaleCode, locale),
        record_date=record_date,
        output_dir=output_dir,
    )
    status = "success"
    if prepared.candidate_groups <= 0:
        status = "skipped"
    elif prepared.failed_groups > 0 or prepared.skipped_groups > 0:
        status = "partial"
    return WaterWorkerManifest(
        job_name=typed_job_name,
        job_id=job_id,
        started_at=started_at,
        finished_at=get_current_time(),
        status=status,
        record_date=prepared.record_date,
        metrics={
            "candidate_groups": prepared.candidate_groups,
            "rendered_groups": prepared.rendered_groups,
            "skipped_groups": prepared.skipped_groups,
            "failed_groups": prepared.failed_groups,
            "total_elapsed_ms": prepared.total_elapsed_ms,
        },
        artifacts={"output_dir": str(output_dir)},
        report_items=prepared.report_items,
    )


async def main() -> None:
    nonebot.init()
    from src.plugins.water.services.worker_jobs import (
        WaterWorkerManifest,
        build_water_job_id,
        write_water_worker_manifest,
    )

    args = parse_args()
    job_name = args.job
    job_id = args.job_id or build_water_job_id(job_name)
    output_dir = _resolve_output_dir(job_id, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"

    try:
        manifest = await _run_job(
            job_name,
            job_id=job_id,
            output_dir=output_dir,
            record_date=args.record_date,
            locale=args.locale,
            force=args.force,
        )
    except Exception:
        logger.exception("[Water][Worker] job failed")
        manifest = WaterWorkerManifest(
            job_name=job_name,
            job_id=job_id,
            started_at=get_current_time(),
            finished_at=get_current_time(),
            status="failed",
            record_date=args.record_date,
            error=traceback.format_exc(),
        )
    write_water_worker_manifest(manifest_path, manifest)
    logger.success(
        "[Water][Worker] manifest written job={} job_id={} status={} path={}",
        job_name,
        job_id,
        manifest.status,
        manifest_path,
    )


if __name__ == "__main__":
    asyncio.run(main())
