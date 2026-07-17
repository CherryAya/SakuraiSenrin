from __future__ import annotations

import pytest
from sqlalchemy import select

from src.database.instances import snapshot_db
from src.database.snapshot.ops import GroupSnapshotOps
from src.database.snapshot.tables import GroupSnapshot


@pytest.mark.asyncio
async def test_create_group_snapshot_persists_created_at() -> None:
    created_at = 1_780_901_962

    async with snapshot_db.session() as session:
        ops = GroupSnapshotOps(session)
        await ops.create_group_snapshot(
            group_id="20001",
            content="Test Group",
            created_at=created_at,
        )

        row = await session.scalar(
            select(GroupSnapshot)
            .where(
                GroupSnapshot.group_id == "20001",
                GroupSnapshot.created_at == created_at,
            )
            .order_by(GroupSnapshot.id.desc())
        )

    assert row is not None
    assert row.content == "Test Group"
    assert row.created_at == created_at
