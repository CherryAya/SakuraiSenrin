from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
import json
from types import SimpleNamespace

import pytest

from scripts import query_trace_logs as query_script


@pytest.mark.asyncio
async def test_query_trace_logs_main_renders_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = SimpleNamespace(
        trace_id="trace-1",
        component="water.worker",
        source_kind="water_worker",
        status="failed",
        record_date=20260801,
        job_id="job-1",
        shard_key="2026_08",
        start_month=None,
        end_month=None,
        limit=20,
    )

    async def _init(_base: object) -> None:
        return None

    async def _query(_session: object) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                trace_id="trace-1",
                source_kind="water_worker",
                component="water.worker",
                level="ERROR",
                event_name="route_batch",
                status="failed",
                summary="batch failed",
                record_date=20260801,
                job_id="job-1",
                shard_key="2026_08",
                batch_size=3,
                attempt=2,
                created_at=123,
                payload_json={"error": "boom"},
            )
        ]

    async def _map_reduce(
        start: datetime,
        end: datetime,
        query_func: Callable[[object], Awaitable[list[SimpleNamespace]]],
        **kwargs: object,
    ) -> list[list[SimpleNamespace]]:
        assert kwargs["cold_policy"] is query_script.ColdPolicy.HYDRATE
        assert start.strftime("%Y%m") == "202608"
        assert end.strftime("%Y%m") == "202608"
        result = await query_func(object())
        return [result]

    monkeypatch.setattr(query_script, "parse_args", lambda: args)
    monkeypatch.setattr(query_script.log_db, "init", _init)
    monkeypatch.setattr(query_script.log_db, "map_reduce", _map_reduce)
    monkeypatch.setattr(
        query_script.TraceEventLogOps,
        "query_trace_events",
        lambda self, **kwargs: _query(object()),
    )

    await query_script.main()
    rendered = json.loads(capsys.readouterr().out)

    assert rendered == [
        {
            "trace_id": "trace-1",
            "source_kind": "water_worker",
            "component": "water.worker",
            "level": "ERROR",
            "event_name": "route_batch",
            "status": "failed",
            "summary": "batch failed",
            "record_date": 20260801,
            "job_id": "job-1",
            "shard_key": "2026_08",
            "batch_size": 3,
            "attempt": 2,
            "created_at": 123,
            "payload_json": {"error": "boom"},
        }
    ]
