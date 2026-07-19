from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import delete

from src.database.core.consts import GroupStatus, InvitationStatus, Permission
from src.database.core.ops import GroupOps, InvitationOps, UserOps
from src.database.core.tables import Group, Invitation, User
from src.database.instances import core_db


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


@pytest.mark.asyncio
async def test_invitation_ops_update_status_sets_operator_and_ignores_siblings() -> (
    None
):
    async with core_db.session() as session:
        await session.execute(delete(Invitation))
        await session.execute(delete(Group))
        await session.execute(delete(User))

        user_ops = UserOps(session)
        group_ops = GroupOps(session)
        invitation_ops = InvitationOps(session)

        await user_ops.add_user("u-inviter", "Inviter", Permission.NORMAL)
        await user_ops.add_user("u-operator", "Operator", Permission.SUPERUSER)
        await group_ops.add_group("g-1", "测试群", GroupStatus.UNAUTHORIZED)

        first = await invitation_ops.create_invitation("g-1", "u-inviter", "flag-1")
        await invitation_ops.create_invitation("g-1", "u-inviter", "flag-2")
        await session.flush()

        await invitation_ops.update_status(
            first.id,
            InvitationStatus.APPROVED,
            operator_id="u-operator",
        )
        await session.flush()

        approved = await invitation_ops.get_by_flag("flag-1")
        ignored = await invitation_ops.get_by_flag("flag-2")

        assert approved is not None
        assert approved.status is InvitationStatus.APPROVED
        assert approved.operator_id == "u-operator"
        assert ignored is not None
        assert ignored.status is InvitationStatus.IGNORED
        assert ignored.operator_id == "u-operator"


@pytest.mark.asyncio
async def test_invitation_ops_get_by_group_and_flag_prefer_pending_records() -> None:
    async with core_db.session() as session:
        await session.execute(delete(Invitation))
        await session.execute(delete(Group))
        await session.execute(delete(User))

        user_ops = UserOps(session)
        group_ops = GroupOps(session)
        invitation_ops = InvitationOps(session)

        await user_ops.add_user("u-inviter-2", "Inviter2", Permission.NORMAL)
        await group_ops.add_group("g-2", "测试群2", GroupStatus.UNAUTHORIZED)

        processed = await invitation_ops.create_invitation("g-2", "u-inviter-2", "f-1")
        await session.flush()
        await invitation_ops.update_status(processed.id, InvitationStatus.REJECTED)
        pending = await invitation_ops.create_invitation("g-2", "u-inviter-2", "f-1")
        await session.flush()

        by_group = await invitation_ops.get_by_group_id("g-2")
        by_flag = await invitation_ops.get_by_flag("f-1")

        assert by_group is not None
        assert by_group.id == pending.id
        assert by_flag is not None
        assert by_flag.id == pending.id
