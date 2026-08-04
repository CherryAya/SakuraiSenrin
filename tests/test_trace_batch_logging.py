from __future__ import annotations

import pytest

from src.lib.db.batch import BatchWriter


@pytest.mark.asyncio
async def test_batch_writer_emits_trace_events_on_retry_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import batch as batch_module

    events: list[dict[str, object]] = []
    attempts = {"count": 0}

    def _capture_trace(**kwargs: object) -> str:
        events.append(dict(kwargs))
        return str(kwargs.get("trace_id") or "trace-test")

    async def _flush(batch: list[int]) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("boom")
        assert batch == [1, 2]

    monkeypatch.setattr(batch_module, "log_trace_event", _capture_trace)
    monkeypatch.setattr(batch_module, "new_trace_id", lambda _component: "trace-test")

    writer = BatchWriter[int](
        flush_callback=_flush,
        batch_size=2,
        flush_interval=0.01,
        max_retries=2,
        retry_backoff=0.0,
    )

    await writer.add_all([1, 2])
    await writer.drain()

    assert [event["event_name"] for event in events] == [
        "worker_started",
        "flush_started",
        "flush_retry",
        "flush_finished",
    ]
    assert events[2]["status"] == "retry"
    assert events[3]["status"] == "success"
    await writer.close()


@pytest.mark.asyncio
async def test_batch_writer_emits_dead_letter_trace_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import batch as batch_module

    events: list[dict[str, object]] = []

    def _capture_trace(**kwargs: object) -> str:
        events.append(dict(kwargs))
        return str(kwargs.get("trace_id") or "trace-dead")

    async def _flush(_batch: list[int]) -> None:
        raise RuntimeError("always fail")

    monkeypatch.setattr(batch_module, "log_trace_event", _capture_trace)
    monkeypatch.setattr(batch_module, "new_trace_id", lambda _component: "trace-dead")

    writer = BatchWriter[int](
        flush_callback=_flush,
        batch_size=1,
        flush_interval=0.01,
        max_retries=2,
        retry_backoff=0.0,
    )

    await writer.add(1)
    with pytest.raises(RuntimeError, match="always fail"):
        await writer.drain()

    assert events[-1]["event_name"] == "flush_dead_letter"
    assert events[-1]["status"] == "dead_letter"
    await writer.close()
