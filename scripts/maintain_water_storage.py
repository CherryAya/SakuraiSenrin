"""Run one-shot maintenance for water storage slimming and log index baseline."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sqlite3
import sys
from typing import TYPE_CHECKING, Any

import arrow
import nonebot
from sqlalchemy import func, select, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_water_storage import build_water_storage_audit_report, write_report
from src.lib.utils.common import get_current_time, split_list
from src.logger import logger

if TYPE_CHECKING:
    from src.plugins.water.database.types import WaterSummaryPayload

water_repo: Any = None
water_core_db: Any = None
water_message: Any = None
water_summary: Any = None
WaterArchivedSummaryOps: Any = None
WaterDailySummary: Any = None


def _load_water_components() -> None:
    global water_repo
    global water_core_db
    global water_message
    global water_summary
    global WaterArchivedSummaryOps
    global WaterDailySummary
    if water_repo is not None:
        return

    from src.plugins.water.database import water_repo as loaded_water_repo
    from src.plugins.water.database.instances import (
        water_core_db as loaded_water_core_db,
    )
    from src.plugins.water.database.instances import (
        water_message as loaded_water_message,
    )
    from src.plugins.water.database.instances import (
        water_summary as loaded_water_summary,
    )
    from src.plugins.water.database.ops import (
        WaterArchivedSummaryOps as loaded_archived_summary_ops,
    )
    from src.plugins.water.database.tables import (
        WaterDailySummary as loaded_water_daily_summary,
    )

    water_repo = loaded_water_repo
    water_core_db = loaded_water_core_db
    water_message = loaded_water_message
    water_summary = loaded_water_summary
    WaterArchivedSummaryOps = loaded_archived_summary_ops
    WaterDailySummary = loaded_water_daily_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain water storage layout")
    parser.add_argument(
        "--report",
        default="./data/db/water-storage-maintenance-report.json",
        help="where to write the maintenance report JSON",
    )
    parser.add_argument(
        "--summary-batch-size",
        type=int,
        default=5_000,
        help="batch size for historical summary copy into summary shards",
    )
    parser.add_argument(
        "--skip-summary-backfill",
        action="store_true",
        help="skip backfilling historical summaries into summary shards",
    )
    parser.add_argument(
        "--skip-log-index-drop",
        action="store_true",
        help="skip dropping redundant indexes from existing log shards",
    )
    parser.add_argument(
        "--skip-summary-prune",
        action="store_true",
        help="skip pruning hot-window-external summaries from core.db",
    )
    parser.add_argument(
        "--run-audit",
        action="store_true",
        help="append a fresh water storage audit result into the report",
    )
    return parser.parse_args()


async def main() -> None:
    nonebot.init()
    _load_water_components()
    args = parse_args()
    await water_repo.init_all_tables()

    report: dict[str, Any] = {
        "generated_at": get_current_time(),
        "summary_batch_size": args.summary_batch_size,
        "summary_backfill": None,
        "log_index_cleanup": None,
        "summary_prune": None,
        "post_audit": None,
    }

    if not args.skip_summary_backfill:
        report["summary_backfill"] = await backfill_archived_summaries(
            batch_size=args.summary_batch_size
        )
    if not args.skip_log_index_drop:
        report["log_index_cleanup"] = await drop_redundant_log_indexes()
    if not args.skip_summary_prune:
        report["summary_prune"] = await prune_core_summaries()
    if args.run_audit:
        report["post_audit"] = build_water_storage_audit_report(Path("./data/db"))

    report_path = Path(args.report)
    await asyncio.to_thread(write_report, report_path, report)
    logger.success(f"water storage maintenance report written: {report_path}")


async def backfill_archived_summaries(*, batch_size: int) -> dict[str, Any]:
    _load_water_components()
    hot_start_date = water_repo._hot_summary_start_date()
    moved_rows = 0
    shard_stats: dict[str, int] = {}
    async with water_core_db.session(commit=False) as session:
        total_rows = int(
            await session.scalar(
                select(func.count()).select_from(WaterDailySummary).where(
                    WaterDailySummary.record_date < hot_start_date
                )
            )
            or 0
        )
        if total_rows <= 0:
            return {
                "hot_start_date": hot_start_date,
                "source_rows": 0,
                "moved_rows": 0,
                "shards": {},
            }

        rows = (
            await session.execute(
                select(WaterDailySummary)
                .where(WaterDailySummary.record_date < hot_start_date)
                .order_by(WaterDailySummary.record_date.asc())
            )
        ).scalars()
        batch: list[WaterSummaryPayload] = []
        for row in rows:
            batch.append(
                {
                    "group_id": str(row.group_id),
                    "user_id": str(row.user_id),
                    "record_date": int(row.record_date),
                    "msg_count": int(row.msg_count),
                    "active_hours": int(row.active_hours),
                    "hourly_counts": list(row.hourly_counts or [0] * 24)[:24],
                    "created_at": int(row.created_at),
                    "updated_at": int(row.updated_at),
                }
            )
            if len(batch) >= batch_size:
                moved_rows += await _flush_summary_payloads(batch, shard_stats)
                batch = []
        if batch:
            moved_rows += await _flush_summary_payloads(batch, shard_stats)

    return {
        "hot_start_date": hot_start_date,
        "source_rows": total_rows,
        "moved_rows": moved_rows,
        "shards": shard_stats,
    }


async def _flush_summary_payloads(
    payloads: list["WaterSummaryPayload"],
    shard_stats: dict[str, int],
) -> int:
    _load_water_components()
    routed: dict[str, list["WaterSummaryPayload"]] = {}
    for payload in payloads:
        shard_key = (
            arrow.get(str(payload["record_date"]), "YYYYMMDD")
            .to("Asia/Shanghai")
            .floor("month")
            .format("YYYY_MM")
        )
        routed.setdefault(shard_key, []).append(payload)

    written = 0
    for shard_key, shard_payloads in routed.items():
        route_ctx = arrow.get(shard_key, "YYYY_MM").datetime
        async with water_summary.write_session(time_ctx=route_ctx) as session:
            for chunk in split_list(shard_payloads, 100):
                written += await WaterArchivedSummaryOps(session).bulk_upsert_summary(
                    chunk
                )
        shard_stats[shard_key] = shard_stats.get(shard_key, 0) + len(shard_payloads)
    return written


async def drop_redundant_log_indexes() -> dict[str, Any]:
    _load_water_components()
    dropped_targets = [
        "idx_water_hourly_counter_date_group_user",
        "idx_water_hourly_counter_date_hour",
        "idx_water_hourly_counter_group_user_date",
    ]
    touched: dict[str, list[str]] = {}
    for db_path in sorted(water_message.base_dir.glob(f"{water_message.prefix}_*.db")):
        shard_key = db_path.stem.removeprefix(f"{water_message.prefix}_")
        time_ctx = arrow.get(shard_key, "YYYY_MM").datetime
        async with water_message.write_session(time_ctx=time_ctx) as session:
            existing = (
                await session.execute(text("PRAGMA index_list('water_hourly_counter')"))
            ).all()
            existing_names = {str(row[1]) for row in existing}
            dropped_now: list[str] = []
            for index_name in dropped_targets:
                if index_name not in existing_names:
                    continue
                await session.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
                dropped_now.append(index_name)
            if dropped_now:
                touched[shard_key] = dropped_now
    return {
        "shards_touched": len(touched),
        "dropped_indexes": touched,
    }


async def prune_core_summaries() -> dict[str, Any]:
    _load_water_components()
    hot_start_date = water_repo._hot_summary_start_date()
    before_rows = 0
    async with water_core_db.session(commit=False) as session:
        before_rows = int(
            await session.scalar(select(func.count()).select_from(WaterDailySummary))
            or 0
        )
    pruned = await water_repo.prune_hot_summaries()
    async with water_core_db.session(commit=False) as session:
        after_rows = int(
            await session.scalar(select(func.count()).select_from(WaterDailySummary))
            or 0
        )
    await asyncio.to_thread(_vacuum_core_db_file)
    return {
        "hot_start_date": hot_start_date,
        "before_rows": before_rows,
        "pruned_rows": pruned,
        "after_rows": after_rows,
    }


def _vacuum_core_db_file() -> None:
    _load_water_components()
    db_path = water_core_db.base_dir / water_core_db.filename
    if not db_path.exists():
        return
    with sqlite3.connect(db_path) as conn:
        conn.execute("VACUUM")


if __name__ == "__main__":
    asyncio.run(main())
