from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from src.database.instances import log_db
from src.database.log.tables import TraceEventLog
from src.lib.trace_log import configure_logging, flush_trace_logging, log_trace_event


@pytest.mark.asyncio
async def test_log_trace_event_persists_to_log_db() -> None:
    configure_logging(log_role="test-trace")

    trace_id = log_trace_event(
        event_name="unit_test_event",
        source_kind="unit_test",
        component="tests.trace_log",
        status="success",
        summary="trace log persistence test",
        record_date=20260803,
        batch_size=2,
        payload_json={"hello": "world"},
    )
    await asyncio.sleep(0)
    await flush_trace_logging()

    async with log_db.read_session() as session:
        result = await session.execute(
            select(TraceEventLog).where(TraceEventLog.trace_id == trace_id)
        )
        row = result.scalars().one()

    assert row.component == "tests.trace_log"
    assert row.source_kind == "unit_test"
    assert row.status == "success"
    assert row.record_date == 20260803
    assert row.batch_size == 2
    assert row.payload_json == {"hello": "world"}
