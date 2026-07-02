import asyncio

import pytest

from src.lib.long_task import LongTaskEvent, LongTaskRunner, LongTaskSpec


class _CollectSink:
    def __init__(self) -> None:
        self.events: list[LongTaskEvent] = []

    async def handle_event(self, event: LongTaskEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_long_task_skips_prompt_before_threshold() -> None:
    sink = _CollectSink()
    async with LongTaskRunner(
        LongTaskSpec(
            task_name="test.short",
            source_kind="test",
            prompt="请稍候",
            threshold_ms=50,
        ),
        sink=sink,
    ):
        await asyncio.sleep(0.01)

    assert [event.kind for event in sink.events] == ["start", "finish"]


@pytest.mark.asyncio
async def test_long_task_emits_prompt_after_threshold() -> None:
    sink = _CollectSink()
    async with LongTaskRunner(
        LongTaskSpec(
            task_name="test.slow",
            source_kind="test",
            prompt="请稍候",
            threshold_ms=10,
        ),
        sink=sink,
    ) as long_task:
        await asyncio.sleep(0.03)
        await long_task.advance("processing_items")

    assert [event.kind for event in sink.events] == [
        "start",
        "prompt",
        "progress",
        "finish",
    ]
