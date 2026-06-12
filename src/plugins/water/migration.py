"""Legacy water migration helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
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


async def reset_current_water_runtime(*, preserve_seasons: bool = True) -> None:
    await water_repo.reset_runtime_data(preserve_seasons=preserve_seasons)


async def import_legacy_water_rows(
    rows: Sequence[LegacyWaterRow],
    *,
    chunk_size: int = 1000,
) -> int:
    inserted = 0
    payloads: list[WaterMessagePayload] = [
        {
            "group_id": row.group_id,
            "user_id": row.user_id,
            "record_date": int(
                arrow.get(row.created_at).to("Asia/Shanghai").format("YYYYMMDD")
            ),
            "hour": int(arrow.get(row.created_at).to("Asia/Shanghai").format("H")),
            "msg_count": 1,
        }
        for row in rows
    ]
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
        report.imported_messages = await import_legacy_water_rows(
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
    "LegacyWaterRow",
    "WaterMigrationReport",
    "build_legacy_water_rows",
    "fetch_legacy_water_rows",
    "import_legacy_water_rows",
    "migrate_legacy_water",
    "normalize_legacy_timestamp",
    "rebuild_water_runtime_from_messages",
    "reset_current_water_runtime",
    "write_report",
]
