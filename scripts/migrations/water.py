"""Legacy water migration helpers."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
from queue import Full, Queue
import threading
import time
from typing import Any, cast
from zoneinfo import ZoneInfo

import arrow

from src.database.system_migration import LegacyPgConfig
from src.lib.utils.common import get_current_time, split_list
from src.plugins.water.database import water_repo
from src.plugins.water.database.types import WaterMessagePayload
from src.plugins.water.services.settlement import water_settlement_service

_LEGACY_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(slots=True, frozen=True)
class LegacyWaterRow:
    id: int
    user_id: str
    group_id: str
    created_at: int


@dataclass(slots=True)
class WaterMigrationReport:
    source_rows: int = 0
    imported_messages: int = 0
    imported_counter_rows: int = 0
    imported_date_range: dict[str, int | None] = field(
        default_factory=lambda: {"start_date": None, "end_date": None}
    )
    settled_days: int = 0
    generated_summaries: int = 0
    generated_achievements: int = 0
    preserved_seasons: int = 0
    reset_target: bool = True
    failed_days: list[dict[str, object]] = field(default_factory=list)
    started_at: int = 0
    finished_at: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


LegacyWaterMigrationProgressPayload = dict[str, int | float]
LegacyWaterMigrationProgressCallback = Callable[
    [str, LegacyWaterMigrationProgressPayload], None
]

_BATCH_STREAM_END = object()


@dataclass(slots=True, frozen=True)
class _BatchStreamFailure:
    error: BaseException


def normalize_legacy_timestamp(value: object) -> int:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        raise TypeError(f"unsupported timestamp value: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_LEGACY_TZ)
    return int(dt.timestamp())


def build_legacy_water_rows(
    rows: Sequence[Mapping[str, object]],
) -> list[LegacyWaterRow]:
    return [
        LegacyWaterRow(
            id=_coerce_int(row["id"], field="water_info.id"),
            user_id=str(row["user_id"]),
            group_id=str(row["group_id"]),
            created_at=normalize_legacy_timestamp(row["created_at"]),
        )
        for row in rows
    ]


def _connect_legacy_postgres(config: LegacyPgConfig) -> Any:
    import psycopg2

    return psycopg2.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        dbname=config.database,
    )


def _fetch_legacy_rows_sync(
    config: LegacyPgConfig,
    *,
    from_date: int | None = None,
    to_date: int | None = None,
) -> list[dict[str, object]]:
    from psycopg2.extras import RealDictCursor

    sql = """
        SELECT
            id,
            user_id,
            group_id,
            created_at
        FROM water_info
    """
    conditions: list[str] = []
    params: list[object] = []
    if from_date is not None:
        conditions.append("created_at >= %s")
        params.append(datetime.strptime(str(from_date), "%Y%m%d"))
    if to_date is not None:
        conditions.append("created_at < %s")
        params.append(datetime.strptime(str(to_date), "%Y%m%d") + timedelta(days=1))
    if conditions:
        sql += "\nWHERE " + " AND ".join(conditions)
    sql += "\nORDER BY id ASC"

    with closing(_connect_legacy_postgres(config)) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
    return [dict(cast(Any, row)) for row in rows]


def _iter_legacy_row_batches_sync(
    config: LegacyPgConfig,
    *,
    from_date: int | None = None,
    to_date: int | None = None,
    fetch_size: int = 50_000,
) -> Iterator[list[LegacyWaterRow]]:
    from psycopg2.extras import RealDictCursor

    sql = """
        SELECT
            id,
            user_id,
            group_id,
            created_at
        FROM water_info
    """
    conditions: list[str] = []
    params: list[object] = []
    if from_date is not None:
        conditions.append("created_at >= %s")
        params.append(datetime.strptime(str(from_date), "%Y%m%d"))
    if to_date is not None:
        conditions.append("created_at < %s")
        params.append(datetime.strptime(str(to_date), "%Y%m%d") + timedelta(days=1))
    if conditions:
        sql += "\nWHERE " + " AND ".join(conditions)
    sql += "\nORDER BY id ASC"

    with closing(_connect_legacy_postgres(config)) as conn:
        with conn.cursor(
            name="water_migration_cursor",
            cursor_factory=RealDictCursor,
        ) as cursor:
            cursor.itersize = max(1, fetch_size)
            cursor.execute(sql, params)
            while True:
                rows = cursor.fetchmany(fetch_size)
                if not rows:
                    break
                yield build_legacy_water_rows([dict(cast(Any, row)) for row in rows])


async def fetch_legacy_water_rows(
    config: LegacyPgConfig,
    *,
    from_date: int | None = None,
    to_date: int | None = None,
) -> list[dict[str, object]]:
    return await asyncio.to_thread(
        _fetch_legacy_rows_sync,
        config,
        from_date=from_date,
        to_date=to_date,
    )


def iter_legacy_water_row_batches(
    config: LegacyPgConfig,
    *,
    from_date: int | None = None,
    to_date: int | None = None,
    fetch_size: int = 50_000,
) -> Iterator[list[LegacyWaterRow]]:
    return _iter_legacy_row_batches_sync(
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
    stop_event = threading.Event()
    batch_queue: Queue[object] = Queue(maxsize=prefetch)

    def _put(item: object) -> None:
        while not stop_event.is_set():
            try:
                batch_queue.put(item, timeout=0.1)
            except Full:
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
            _put(_BatchStreamFailure(exc))
        finally:
            _put(_BATCH_STREAM_END)

    producer = threading.Thread(
        target=_producer,
        name="water-migration-pg-reader",
        daemon=True,
    )
    producer.start()

    try:
        while True:
            item = await asyncio.to_thread(batch_queue.get)
            if item is _BATCH_STREAM_END:
                break
            if isinstance(item, _BatchStreamFailure):
                raise item.error
            yield cast(list[LegacyWaterRow], item)
    finally:
        stop_event.set()
        await asyncio.to_thread(producer.join, 1.0)


async def reset_current_water_runtime(*, preserve_seasons: bool = True) -> None:
    await water_repo.reset_runtime_data(preserve_seasons=preserve_seasons)


async def import_legacy_water_rows(
    rows: Sequence[LegacyWaterRow],
    *,
    chunk_size: int = 1000,
) -> int:
    aggregated: dict[tuple[int, int, str, str], WaterMessagePayload] = {}
    counts: dict[tuple[int, int, str, str], int] = defaultdict(int)
    for row in rows:
        dt = arrow.get(row.created_at).to("Asia/Shanghai")
        key = (
            int(dt.format("YYYYMMDD")),
            int(dt.format("H")),
            row.group_id,
            row.user_id,
        )
        counts[key] += 1

    payloads: list[WaterMessagePayload] = []
    for (record_date, hour, group_id, user_id), msg_count in counts.items():
        payload = aggregated.get((record_date, hour, group_id, user_id))
        if payload is None:
            payload = WaterMessagePayload(
                group_id=group_id,
                user_id=user_id,
                record_date=record_date,
                hour=hour,
                msg_count=msg_count,
            )
            aggregated[(record_date, hour, group_id, user_id)] = payload
            payloads.append(payload)

    inserted = 0
    for chunk in split_list(payloads, chunk_size):
        inserted += await water_repo.import_message_batch(chunk)
    return inserted


async def rebuild_water_runtime_from_messages(
    *,
    start_date: int | None,
    end_date: int | None,
) -> tuple[int, int, int, list[dict[str, object]]]:
    if start_date is None or end_date is None:
        return 0, 0, 0, []

    settled_days = 0
    generated_summaries = 0
    generated_achievements = 0
    failed_days: list[dict[str, object]] = []

    cursor = arrow.get(str(start_date), "YYYYMMDD").floor("day")
    end = arrow.get(str(end_date), "YYYYMMDD").floor("day")
    while cursor <= end:
        record_date = int(cursor.format("YYYYMMDD"))
        try:
            result = await water_settlement_service.run_daily_settlement(
                target_date=cursor,
                force=True,
                chunk_pause_seconds=0,
                prune_after_settlement=False,
            )
        except Exception as exc:
            failed_days.append(
                {
                    "record_date": record_date,
                    "reason": str(exc),
                }
            )
        else:
            settled_days += 1
            generated_summaries += result.aggregate_rows
            generated_achievements += result.unlocked_achievements
        cursor = cursor.shift(days=1)

    return (
        settled_days,
        generated_summaries,
        generated_achievements,
        failed_days,
    )


async def migrate_legacy_water(
    rows: Sequence[LegacyWaterRow],
    *,
    reset_target: bool = True,
    preserve_seasons: bool = True,
    chunk_size: int = 1000,
) -> WaterMigrationReport:
    report = WaterMigrationReport(
        source_rows=len(rows),
        reset_target=reset_target,
        started_at=get_current_time(),
    )

    await water_repo.init_all_tables()
    if reset_target:
        await reset_current_water_runtime(preserve_seasons=preserve_seasons)
        await water_repo.init_all_tables()

    report.preserved_seasons = len(await water_repo.list_activity_seasons())

    if rows:
        report.imported_messages = len(rows)
        report.imported_counter_rows = await import_legacy_water_rows(
            rows,
            chunk_size=chunk_size,
        )
        start_ts = min(row.created_at for row in rows)
        end_ts = max(row.created_at for row in rows)
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

    report.finished_at = get_current_time()
    return report


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
        started_at=get_current_time(),
    )
    started_at = time.monotonic()

    await water_repo.init_all_tables()
    if reset_target:
        await reset_current_water_runtime(preserve_seasons=preserve_seasons)
        await water_repo.init_all_tables()

    report.preserved_seasons = len(await water_repo.list_activity_seasons())

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
        if not batch:
            continue
        batch_count += 1
        report.source_rows += len(batch)
        report.imported_messages += len(batch)
        imported_counter_rows = await import_legacy_water_rows(
            batch,
            chunk_size=chunk_size,
        )
        report.imported_counter_rows += imported_counter_rows

        batch_min_ts = min(row.created_at for row in batch)
        batch_max_ts = max(row.created_at for row in batch)
        start_ts = batch_min_ts if start_ts is None else min(start_ts, batch_min_ts)
        end_ts = batch_max_ts if end_ts is None else max(end_ts, batch_max_ts)
        if progress is not None:
            elapsed_seconds = max(time.monotonic() - started_at, 0.001)
            progress(
                "import_batch",
                {
                    "batch_index": batch_count,
                    "batch_rows": len(batch),
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
                    "start_date": start_date,
                    "end_date": end_date,
                    "settled_days": report.settled_days,
                    "generated_summaries": report.generated_summaries,
                    "generated_achievements": report.generated_achievements,
                    "failed_days": len(report.failed_days),
                },
            )

    report.finished_at = get_current_time()
    return report


def write_report(path: Path, report: WaterMigrationReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _coerce_int(value: object, *, field: str) -> int:
    try:
        if isinstance(value, bool):
            raise TypeError
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value)
        if hasattr(value, "__int__") or hasattr(value, "__index__"):
            return int(cast(Any, value))
        raise TypeError
    except (TypeError, ValueError) as exc:
        raise TypeError(f"invalid int for {field}: {value!r}") from exc


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
    "write_report",
]
