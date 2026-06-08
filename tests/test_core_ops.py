from __future__ import annotations

from typing import Any

import pytest

from src.database.core.consts import GroupStatus
from src.database.core.ops import GroupOps


class _FakeConnection:
    def __init__(self) -> None:
        self.sql: str | None = None
        self.params: list[dict[str, Any]] | None = None

    async def execute(self, sql: Any, params: list[dict[str, Any]]) -> "_FakeResult":
        self.sql = str(sql)
        self.params = params
        return _FakeResult()


class _FakeResult:
    rowcount = 1


class _FakeSession:
    def __init__(self) -> None:
        self.connection_obj = _FakeConnection()

    async def connection(self) -> _FakeConnection:
        return self.connection_obj


@pytest.mark.asyncio
async def test_bulk_update_group_statuses_updates_group_table() -> None:
    session = _FakeSession()
    ops = GroupOps(session)  # type: ignore[arg-type]

    result = await ops.bulk_update_statuses(
        [
            {
                "group_id": "20001",
                "status": GroupStatus.AUTHORIZED,
                "updated_at": 1_780_901_962,
            }
        ]
    )

    assert result == 1
    assert session.connection_obj.sql is not None
    assert "UPDATE biz_group" in session.connection_obj.sql
    assert "SET status = :status" in session.connection_obj.sql
    assert "WHERE group_id = :group_id" in session.connection_obj.sql
