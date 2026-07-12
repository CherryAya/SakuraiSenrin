from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from scripts import maintain_water_storage as maintain_script


def _install_fake_components(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        maintain_script,
        "water_repo",
        SimpleNamespace(
            init_all_tables=None,
            _hot_summary_start_date=None,
            prune_hot_summaries=None,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        maintain_script,
        "water_core_db",
        SimpleNamespace(session=None),
        raising=False,
    )
    monkeypatch.setattr(
        maintain_script,
        "water_message",
        SimpleNamespace(base_dir=Path("."), prefix="logs", write_session=None),
        raising=False,
    )
    monkeypatch.setattr(
        maintain_script,
        "water_summary",
        SimpleNamespace(write_session=None),
        raising=False,
    )
    monkeypatch.setattr(
        maintain_script,
        "WaterArchivedSummaryOps",
        object,
        raising=False,
    )
    monkeypatch.setattr(
        maintain_script,
        "WaterDailySummary",
        text("water_daily_summary"),
        raising=False,
    )
    monkeypatch.setattr(
        maintain_script,
        "_load_water_components",
        lambda: None,
    )


def test_maintain_water_storage_parse_args_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["maintain_water_storage.py"])

    args = maintain_script.parse_args()

    assert args.report == "./data/db/water-storage-maintenance-report.json"
    assert args.summary_batch_size == 5_000
    assert args.skip_summary_backfill is False
    assert args.skip_log_index_drop is False
    assert args.skip_summary_prune is False
    assert args.run_audit is False


@pytest.mark.asyncio
async def test_flush_summary_payloads_routes_by_month(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_components(monkeypatch)
    written_payloads: dict[str, list[dict[str, object]]] = {}

    class _FakeSessionCtx:
        def __init__(self, shard_key: str) -> None:
            self._session = object()
            self.shard_key = shard_key

        async def __aenter__(self) -> object:
            return self._session

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool:
            _ = (exc_type, exc, tb)
            return False

    def _fake_write_session(*, time_ctx: datetime) -> _FakeSessionCtx:
        shard_key = time_ctx.strftime("%Y_%m")
        return _FakeSessionCtx(shard_key)

    class FakeArchivedSummaryOps:
        def __init__(self, session: object) -> None:
            self._session = session

        async def bulk_upsert_summary(self, payloads: list[dict[str, object]]) -> int:
            for payload in payloads:
                record_date = str(payload["record_date"])
                shard_key = record_date[:4] + "_" + record_date[4:6]
                written_payloads.setdefault(shard_key, []).append(payload)
            return len(payloads)

    monkeypatch.setattr(
        maintain_script.water_summary,
        "write_session",
        _fake_write_session,
    )
    monkeypatch.setattr(
        maintain_script,
        "WaterArchivedSummaryOps",
        FakeArchivedSummaryOps,
    )

    shard_stats: dict[str, int] = {}
    written = await maintain_script._flush_summary_payloads(
        [
            {
                "group_id": "20001",
                "user_id": "10001",
                "record_date": 20260131,
                "msg_count": 1,
                "active_hours": 1,
                "hourly_counts": [0] * 24,
                "created_at": 1,
                "updated_at": 1,
            },
            {
                "group_id": "20001",
                "user_id": "10002",
                "record_date": 20260201,
                "msg_count": 2,
                "active_hours": 2,
                "hourly_counts": [0] * 24,
                "created_at": 1,
                "updated_at": 1,
            },
        ],
        shard_stats,
    )

    assert written == 2
    assert shard_stats == {"2026_01": 1, "2026_02": 1}
    assert len(written_payloads["2026_01"]) == 1
    assert len(written_payloads["2026_02"]) == 1


@pytest.mark.asyncio
async def test_prune_core_summaries_reports_before_and_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_components(monkeypatch)
    scalar_values = iter([10, 3])

    class FakeSession:
        async def scalar(self, stmt: object) -> int:
            _ = stmt
            return next(scalar_values)

    class _FakeSessionCtx:
        async def __aenter__(self) -> FakeSession:
            return FakeSession()

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool:
            _ = (exc_type, exc, tb)
            return False

    monkeypatch.setattr(
        maintain_script.water_core_db,
        "session",
        lambda **kwargs: _FakeSessionCtx(),
    )
    monkeypatch.setattr(
        maintain_script.water_repo,
        "_hot_summary_start_date",
        lambda: 20260301,
    )
    monkeypatch.setattr(
        maintain_script.water_repo,
        "prune_hot_summaries",
        AsyncMock(return_value=7),
    )
    monkeypatch.setattr(maintain_script.asyncio, "to_thread", AsyncMock())

    result = await maintain_script.prune_core_summaries()

    assert result == {
        "hot_start_date": 20260301,
        "before_rows": 10,
        "pruned_rows": 7,
        "after_rows": 3,
    }


@pytest.mark.asyncio
async def test_main_runs_selected_steps_and_writes_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    _install_fake_components(monkeypatch)
    report_path = tmp_path / "report.json"
    args = SimpleNamespace(
        report=str(report_path),
        summary_batch_size=100,
        skip_summary_backfill=False,
        skip_log_index_drop=False,
        skip_summary_prune=False,
        run_audit=True,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(maintain_script.nonebot, "init", lambda: None)
    monkeypatch.setattr(maintain_script, "parse_args", lambda: args)
    monkeypatch.setattr(maintain_script.water_repo, "init_all_tables", AsyncMock())
    monkeypatch.setattr(
        maintain_script,
        "backfill_archived_summaries",
        AsyncMock(return_value={"moved_rows": 1}),
    )
    monkeypatch.setattr(
        maintain_script,
        "drop_redundant_log_indexes",
        AsyncMock(return_value={"shards_touched": 2}),
    )
    monkeypatch.setattr(
        maintain_script,
        "prune_core_summaries",
        AsyncMock(return_value={"pruned_rows": 3}),
    )
    monkeypatch.setattr(
        maintain_script,
        "build_water_storage_audit_report",
        lambda path: {"db_root": str(path)},
    )

    async def _fake_to_thread(
        func: Any,
        *func_args: Any,
        **func_kwargs: Any,
    ) -> Any:
        captured["report"] = (func_args[0], func_args[1])
        return func(*func_args, **func_kwargs)

    monkeypatch.setattr(maintain_script.asyncio, "to_thread", _fake_to_thread)

    await maintain_script.main()

    report_record = captured["report"]
    assert isinstance(report_record, tuple)
    report_payload = report_record[1]
    assert report_payload["summary_backfill"] == {"moved_rows": 1}
    assert report_payload["log_index_cleanup"] == {"shards_touched": 2}
    assert report_payload["summary_prune"] == {"pruned_rows": 3}
    assert report_payload["post_audit"] == {"db_root": "data/db"}
