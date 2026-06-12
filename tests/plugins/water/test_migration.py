from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import arrow
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from src.database.system_migration import LegacyPgConfig
from src.plugins.water.database import water_repo
from src.plugins.water.database.instances import water_core_db, water_message
from src.plugins.water.database.tables import (
    WaterActivitySeason,
    WaterDailySummary,
    WaterHourlyCounter,
    WaterUserAchievement,
)
from src.plugins.water.migration import (
    LegacyWaterRow,
    build_legacy_water_rows,
    import_legacy_water_rows,
    migrate_legacy_water,
    migrate_legacy_water_from_pg,
    normalize_legacy_timestamp,
    rebuild_water_runtime_from_messages,
    reset_current_water_runtime,
)


@pytest_asyncio.fixture(autouse=True)
async def _reset_water_runtime() -> AsyncIterator[None]:
    await water_repo.init_all_tables()
    await water_repo.reset_runtime_data(preserve_seasons=False)
    yield
    await water_repo.init_all_tables()
    await water_repo.reset_runtime_data(preserve_seasons=False)


def test_normalize_legacy_timestamp_uses_shanghai_for_naive_values() -> None:
    timestamp = normalize_legacy_timestamp(datetime(2026, 6, 11, 12, 0, 0))
    assert timestamp == 1781150400


def test_build_legacy_water_rows_normalizes_fields() -> None:
    rows = build_legacy_water_rows(
        [
            {
                "id": 1,
                "user_id": 10001,
                "group_id": 20001,
                "created_at": "2026-06-11T12:00:00",
            }
        ]
    )

    assert rows == [
        LegacyWaterRow(
            id=1,
            user_id="10001",
            group_id="20001",
            created_at=1781150400,
        )
    ]


@pytest.mark.asyncio
async def test_reset_current_water_runtime_preserves_activity_seasons() -> None:
    now_ts = 1_781_150_400
    await water_repo.create_activity_season(
        {
            "season_id": "s1",
            "name": "Legacy Season",
            "normalized_name": "legacy season",
            "description": "",
            "start_date": 20260101,
            "end_date": 20261231,
            "status": "published",
            "published_at": now_ts,
            "created_by": "1",
            "created_at": now_ts,
            "updated_at": now_ts,
        }
    )
    await water_repo.save_summary_batch(
        [
            {
                "group_id": "20001",
                "user_id": "10001",
                "record_date": 20260611,
                "msg_count": 3,
                "active_hours": 1,
                "hourly_counts": [0] * 24,
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        ]
    )
    await water_repo.unlock_achievements(
        [
            {
                "user_id": "10001",
                "achievement_id": "FIRST_BLOOD",
                "track_type": "permanent",
                "season_id": "",
                "unlocked_at": now_ts,
                "context": "test",
            }
        ]
    )
    await water_repo.import_message_batch(
        [
            {
                "group_id": "20001",
                "user_id": "10001",
                "record_date": 20260611,
                "hour": 12,
                "msg_count": 1,
            }
        ]
    )

    await reset_current_water_runtime(preserve_seasons=True)

    async with water_core_db.session(commit=False) as session:
        season_count = await session.scalar(
            select(func.count()).select_from(WaterActivitySeason)
        )
        summary_count = await session.scalar(
            select(func.count()).select_from(WaterDailySummary)
        )
        achievement_count = await session.scalar(
            select(func.count()).select_from(WaterUserAchievement)
        )

    assert int(season_count or 0) == 1
    assert int(summary_count or 0) == 0
    assert int(achievement_count or 0) == 0
    assert not list(water_message.base_dir.glob("logs_*.db"))


@pytest.mark.asyncio
async def test_import_legacy_water_rows_routes_multiple_months() -> None:
    rows = build_legacy_water_rows(
        [
            {
                "id": 1,
                "user_id": "10001",
                "group_id": "20001",
                "created_at": datetime(2026, 1, 15, 8, 0, 0),
            },
            {
                "id": 2,
                "user_id": "10002",
                "group_id": "20001",
                "created_at": datetime(2026, 2, 1, 8, 0, 0),
            },
        ]
    )

    inserted = await import_legacy_water_rows(rows, chunk_size=1)

    assert inserted == 2
    assert (water_message.base_dir / "logs_2026_01.db").exists()
    assert (water_message.base_dir / "logs_2026_02.db").exists()

    async with water_message.read_session(
        time_ctx=arrow.get("2026-01-15", "YYYY-MM-DD").datetime
    ) as session:
        jan_count = await session.scalar(
            select(func.sum(WaterHourlyCounter.msg_count)).select_from(
                WaterHourlyCounter
            )
        )
    async with water_message.read_session(
        time_ctx=arrow.get("2026-02-01", "YYYY-MM-DD").datetime
    ) as session:
        feb_count = await session.scalar(
            select(func.sum(WaterHourlyCounter.msg_count)).select_from(
                WaterHourlyCounter
            )
        )

    assert int(jan_count or 0) == 1
    assert int(feb_count or 0) == 1


@pytest.mark.asyncio
async def test_import_legacy_water_rows_aggregates_same_hour_records() -> None:
    rows = build_legacy_water_rows(
        [
            {
                "id": 1,
                "user_id": "10001",
                "group_id": "20001",
                "created_at": datetime(2026, 1, 15, 8, 1, 0),
            },
            {
                "id": 2,
                "user_id": "10001",
                "group_id": "20001",
                "created_at": datetime(2026, 1, 15, 8, 30, 0),
            },
            {
                "id": 3,
                "user_id": "10001",
                "group_id": "20001",
                "created_at": datetime(2026, 1, 15, 9, 0, 0),
            },
        ]
    )

    inserted = await import_legacy_water_rows(rows, chunk_size=10)

    assert inserted == 2
    async with water_message.read_session(
        time_ctx=arrow.get("2026-01-15", "YYYY-MM-DD").datetime
    ) as session:
        counts = (
            await session.execute(
                select(WaterHourlyCounter.hour, WaterHourlyCounter.msg_count).order_by(
                    WaterHourlyCounter.hour.asc()
                )
            )
        ).all()

    assert counts == [(8, 2), (9, 1)]


@pytest.mark.asyncio
async def test_rebuild_water_runtime_from_messages_collects_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water import migration as migration_module

    run_mock = AsyncMock(
        side_effect=[
            SimpleNamespace(aggregate_rows=2, unlocked_achievements=1),
            RuntimeError("boom"),
            SimpleNamespace(aggregate_rows=1, unlocked_achievements=0),
        ]
    )
    monkeypatch.setattr(
        migration_module.water_settlement_service,
        "run_daily_settlement",
        run_mock,
    )

    (
        settled_days,
        summaries,
        achievements,
        failed_days,
    ) = await rebuild_water_runtime_from_messages(
        start_date=20260610,
        end_date=20260612,
    )

    assert settled_days == 2
    assert summaries == 3
    assert achievements == 1
    assert failed_days == [{"record_date": 20260611, "reason": "boom"}]


@pytest.mark.asyncio
async def test_migrate_legacy_water_imports_and_rebuilds_runtime() -> None:
    now_ts = 1_781_150_400
    await water_repo.create_activity_season(
        {
            "season_id": "keep-season",
            "name": "Keep Season",
            "normalized_name": "keep season",
            "description": "",
            "start_date": 20270101,
            "end_date": 20271231,
            "status": "published",
            "published_at": now_ts,
            "created_by": "1",
            "created_at": now_ts,
            "updated_at": now_ts,
        }
    )
    rows = build_legacy_water_rows(
        [
            {
                "id": 1,
                "user_id": "10001",
                "group_id": "20001",
                "created_at": datetime(2026, 6, 11, 8, 0, 0),
            },
            {
                "id": 2,
                "user_id": "10001",
                "group_id": "20001",
                "created_at": datetime(2026, 6, 11, 9, 0, 0),
            },
            {
                "id": 3,
                "user_id": "10002",
                "group_id": "20001",
                "created_at": datetime(2026, 6, 11, 10, 0, 0),
            },
        ]
    )

    report = await migrate_legacy_water(rows, reset_target=True, chunk_size=2)

    async with water_core_db.session(commit=False) as session:
        summary_count = await session.scalar(
            select(func.count()).select_from(WaterDailySummary)
        )
        achievement_count = await session.scalar(
            select(func.count()).select_from(WaterUserAchievement)
        )

    assert report.source_rows == 3
    assert report.imported_messages == 3
    assert report.imported_counter_rows == 3
    assert report.imported_date_range == {
        "start_date": 20260611,
        "end_date": 20260611,
    }
    assert report.settled_days == 1
    assert report.generated_summaries == 2
    assert report.generated_achievements >= 2
    assert report.preserved_seasons == 1
    assert report.failed_days == []
    assert int(summary_count or 0) == 2
    assert int(achievement_count or 0) >= 2


@pytest.mark.asyncio
async def test_migrate_legacy_water_reports_aggregated_counter_rows() -> None:
    rows = build_legacy_water_rows(
        [
            {
                "id": 1,
                "user_id": "10001",
                "group_id": "20001",
                "created_at": datetime(2026, 6, 11, 8, 0, 0),
            },
            {
                "id": 2,
                "user_id": "10001",
                "group_id": "20001",
                "created_at": datetime(2026, 6, 11, 8, 10, 0),
            },
        ]
    )

    report = await migrate_legacy_water(rows, reset_target=True, chunk_size=10)

    assert report.source_rows == 2
    assert report.imported_messages == 2
    assert report.imported_counter_rows == 1


@pytest.mark.asyncio
async def test_migrate_legacy_water_from_pg_streams_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows_batch_1 = build_legacy_water_rows(
        [
            {
                "id": 1,
                "user_id": "10001",
                "group_id": "20001",
                "created_at": datetime(2026, 6, 10, 8, 0, 0),
            },
            {
                "id": 2,
                "user_id": "10001",
                "group_id": "20001",
                "created_at": datetime(2026, 6, 10, 8, 5, 0),
            },
        ]
    )
    rows_batch_2 = build_legacy_water_rows(
        [
            {
                "id": 3,
                "user_id": "10002",
                "group_id": "20002",
                "created_at": datetime(2026, 6, 11, 9, 0, 0),
            }
        ]
    )

    from src.plugins.water import migration as migration_module

    monkeypatch.setattr(
        migration_module,
        "iter_legacy_water_row_batches",
        lambda *args, **kwargs: iter([rows_batch_1, rows_batch_2]),
    )

    imported_batches: list[list[LegacyWaterRow]] = []

    async def _fake_import(
        rows: list[LegacyWaterRow],
        *,
        chunk_size: int = 1000,
    ) -> int:
        imported_batches.append(rows)
        _ = chunk_size
        return 1

    async def _fake_rebuild(
        *,
        start_date: int | None,
        end_date: int | None,
    ) -> tuple[int, int, int, list[dict[str, object]]]:
        assert start_date == 20260610
        assert end_date == 20260611
        return (2, 3, 4, [])

    monkeypatch.setattr(migration_module, "import_legacy_water_rows", _fake_import)
    monkeypatch.setattr(
        migration_module,
        "rebuild_water_runtime_from_messages",
        _fake_rebuild,
    )

    report = await migrate_legacy_water_from_pg(
        LegacyPgConfig(
            host="127.0.0.1",
            port=5432,
            user="legacy",
            password="secret",
            database="senrin_water",
        ),
        reset_target=True,
        chunk_size=500,
        fetch_size=10_000,
    )

    assert len(imported_batches) == 2
    assert report.source_rows == 3
    assert report.imported_messages == 3
    assert report.imported_counter_rows == 2
    assert report.imported_date_range == {
        "start_date": 20260610,
        "end_date": 20260611,
    }
    assert report.settled_days == 2
    assert report.generated_summaries == 3
    assert report.generated_achievements == 4
