"""Compatibility wrapper for legacy water migration helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
import time
from typing import cast

import arrow

from scripts.migrations import water as _impl
from src.database.system_migration import LegacyPgConfig

LegacyWaterMigrationProgressCallback = _impl.LegacyWaterMigrationProgressCallback
LegacyWaterMigrationProgressPayload = _impl.LegacyWaterMigrationProgressPayload
LegacyWaterRow = _impl.LegacyWaterRow
WaterMigrationReport = _impl.WaterMigrationReport
build_legacy_water_rows = _impl.build_legacy_water_rows
fetch_legacy_water_rows = _impl.fetch_legacy_water_rows
import_legacy_water_rows = _impl.import_legacy_water_rows
normalize_legacy_timestamp = _impl.normalize_legacy_timestamp
rebuild_water_runtime_from_messages = _impl.rebuild_water_runtime_from_messages
reset_current_water_runtime = _impl.reset_current_water_runtime
water_settlement_service = _impl.water_settlement_service
write_report = _impl.write_report


def iter_legacy_water_row_batches(
    config: LegacyPgConfig,
    *,
    from_date: int | None = None,
    to_date: int | None = None,
    fetch_size: int = 50_000,
) -> Iterator[list[LegacyWaterRow]]:
    return _impl.iter_legacy_water_row_batches(
        config,
        from_date=from_date,
        to_date=to_date,
        fetch_size=fetch_size,
    )


async def iter_legacy_water_row_batches_prefetched(
    config: LegacyPgConfig,
    *,
    from_date: int | None = None,
    to_date: int | None = None,
    fetch_size: int = 50_000,
    prefetch_batches: int = 2,
) -> AsyncIterator[list[LegacyWaterRow]]:
    prefetch = max(1, prefetch_batches)
    stop_event = _impl.threading.Event()
    batch_queue: _impl.Queue[object] = _impl.Queue(maxsize=prefetch)

    def _put(item: object) -> None:
        while not stop_event.is_set():
            try:
                batch_queue.put(item, timeout=0.1)
            except _impl.Full:
                continue
            return

    def _producer() -> None:
        try:
            for rows in iter_legacy_water_row_batches(
                config,
                from_date=from_date,
                to_date=to_date,
                fetch_size=fetch_size,
            ):
                if stop_event.is_set():
                    break
                _put(rows)
        except BaseException as exc:
            _put(_impl._BatchStreamFailure(exc))
        finally:
            _put(_impl._BATCH_STREAM_END)

    producer = _impl.threading.Thread(
        target=_producer,
        name="water-migration-pg-reader",
        daemon=True,
    )
    producer.start()

    try:
        while True:
            item = await _impl.asyncio.to_thread(batch_queue.get)
            if item is _impl._BATCH_STREAM_END:
                break
            if isinstance(item, _impl._BatchStreamFailure):
                raise item.error
            yield cast(list[LegacyWaterRow], item)
    finally:
        stop_event.set()
        await _impl.asyncio.to_thread(producer.join, 1.0)


async def migrate_legacy_water(
    rows: list[LegacyWaterRow] | tuple[LegacyWaterRow, ...],
    *,
    reset_target: bool = True,
    preserve_seasons: bool = True,
    chunk_size: int = 1000,
) -> WaterMigrationReport:
    return await _impl.migrate_legacy_water(
        rows,
        reset_target=reset_target,
        preserve_seasons=preserve_seasons,
        chunk_size=chunk_size,
    )


async def migrate_legacy_water_from_pg(
    config: LegacyPgConfig,
    *,
    reset_target: bool = True,
    preserve_seasons: bool = True,
    chunk_size: int = 1000,
    from_date: int | None = None,
    to_date: int | None = None,
    fetch_size: int = 50_000,
    prefetch_batches: int = 2,
    progress: LegacyWaterMigrationProgressCallback | None = None,
) -> WaterMigrationReport:
    report = WaterMigrationReport(
        reset_target=reset_target,
        started_at=_impl.get_current_time(),
    )
    started_at = time.monotonic()

    await _impl.water_repo.init_all_tables()
    if reset_target:
        await reset_current_water_runtime(preserve_seasons=preserve_seasons)
        await _impl.water_repo.init_all_tables()

    report.preserved_seasons = len(await _impl.water_repo.list_activity_seasons())

    start_ts: int | None = None
    end_ts: int | None = None
    batch_count = 0

    async for batch in iter_legacy_water_row_batches_prefetched(
        config,
        from_date=from_date,
        to_date=to_date,
        fetch_size=fetch_size,
        prefetch_batches=prefetch_batches,
    ):
        rows = tuple(batch)
        if not rows:
            continue
        batch_count += 1
        report.source_rows += len(rows)
        report.imported_messages += len(rows)
        imported_counter_rows = await import_legacy_water_rows(
            rows,
            chunk_size=chunk_size,
        )
        report.imported_counter_rows += imported_counter_rows

        batch_min_ts = min(row.created_at for row in rows)
        batch_max_ts = max(row.created_at for row in rows)
        start_ts = batch_min_ts if start_ts is None else min(start_ts, batch_min_ts)
        end_ts = batch_max_ts if end_ts is None else max(end_ts, batch_max_ts)
        if progress is not None:
            elapsed_seconds = max(time.monotonic() - started_at, 0.001)
            progress(
                "import_batch",
                {
                    "batch_index": batch_count,
                    "batch_rows": len(rows),
                    "source_rows": report.source_rows,
                    "imported_counter_rows": report.imported_counter_rows,
                    "batch_start_date": int(
                        arrow.get(batch_min_ts).to("Asia/Shanghai").format("YYYYMMDD")
                    ),
                    "batch_end_date": int(
                        arrow.get(batch_max_ts).to("Asia/Shanghai").format("YYYYMMDD")
                    ),
                    "elapsed_seconds": round(elapsed_seconds, 2),
                    "source_rows_per_second": round(
                        report.source_rows / elapsed_seconds,
                        2,
                    ),
                    "counter_rows_per_second": round(
                        report.imported_counter_rows / elapsed_seconds,
                        2,
                    ),
                },
            )

    if report.source_rows > 0 and start_ts is not None and end_ts is not None:
        start_date = int(arrow.get(start_ts).to("Asia/Shanghai").format("YYYYMMDD"))
        end_date = int(arrow.get(end_ts).to("Asia/Shanghai").format("YYYYMMDD"))
        report.imported_date_range = {
            "start_date": start_date,
            "end_date": end_date,
        }
        (
            report.settled_days,
            report.generated_summaries,
            report.generated_achievements,
            report.failed_days,
        ) = await rebuild_water_runtime_from_messages(
            start_date=start_date,
            end_date=end_date,
        )
        if progress is not None:
            progress(
                "rebuild_complete",
                {
                    "settled_days": report.settled_days,
                    "generated_summaries": report.generated_summaries,
                    "generated_achievements": report.generated_achievements,
                    "failed_days": len(report.failed_days),
                },
            )

    report.finished_at = _impl.get_current_time()
    return report


__all__ = [
    "LegacyWaterMigrationProgressCallback",
    "LegacyWaterMigrationProgressPayload",
    "LegacyWaterRow",
    "WaterMigrationReport",
    "build_legacy_water_rows",
    "fetch_legacy_water_rows",
    "import_legacy_water_rows",
    "iter_legacy_water_row_batches",
    "iter_legacy_water_row_batches_prefetched",
    "migrate_legacy_water",
    "migrate_legacy_water_from_pg",
    "normalize_legacy_timestamp",
    "rebuild_water_runtime_from_messages",
    "reset_current_water_runtime",
    "water_settlement_service",
    "write_report",
]
