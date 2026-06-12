from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import pytest

from src.plugins.water.database import writers as writers_module
from src.plugins.water.database.writers import _flush_water_logs


@pytest.mark.asyncio
async def test_flush_water_logs_groups_by_month_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[list[dict[str, object]]] = []

    class FakeOps:
        async def bulk_insert_water_message(
            self,
            data: list[dict[str, object]],
        ) -> int:
            writes.append(data)
            return len(data)

    async def _fake_execute_batch_write(**kwargs: object) -> None:
        method = kwargs["method"]
        assert callable(method)
        typed_method = cast(
            Callable[[FakeOps, list[dict[str, object]]], Awaitable[None]],
            method,
        )
        assert isinstance(kwargs["batch"], list)
        batch = kwargs["batch"]
        ops = FakeOps()
        await typed_method(ops, batch)

    monkeypatch.setattr(
        writers_module,
        "execute_batch_write",
        _fake_execute_batch_write,
    )

    await _flush_water_logs(
        [
            {
                "group_id": "20001",
                "user_id": "10001",
                "record_date": 20260611,
                "hour": 8,
                "msg_count": 1,
                "created_at": 1_781_150_400,
            },
            {
                "group_id": "20001",
                "user_id": "10002",
                "record_date": 20260612,
                "hour": 9,
                "msg_count": 2,
                "created_at": 1_781_236_800,
            },
        ]
    )

    assert writes == [
        [
            {
                "group_id": "20001",
                "user_id": "10001",
                "record_date": 20260611,
                "hour": 8,
                "msg_count": 1,
            },
            {
                "group_id": "20001",
                "user_id": "10002",
                "record_date": 20260612,
                "hour": 9,
                "msg_count": 2,
            },
        ]
    ]
