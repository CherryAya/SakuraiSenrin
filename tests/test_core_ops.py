from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from src.database.core.consts import (
    GroupStatus,
    InvitationStage,
    InvitationStatus,
    Permission,
)
from src.database.core.ops import GroupOps, InvitationOps, UserOps
from src.database.core.tables import CoreBase, Group, Invitation, User
from src.database.instances import core_db
from src.lib.db.connectors import StateStore
from src.repositories import invite as invite_repository_module
from src.repositories.invite import InviteRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, "", " ", "\t\r\n", "\u3000"])
async def test_platform_invitation_without_valid_flag_is_never_persisted(
    invitation_event_store: tuple[InviteRepository, StateStore], flag: str | None
) -> None:
    repo, store = invitation_event_store
    with pytest.raises(ValueError, match="flag"):
        await repo.reconcile_event(
            group_id="event-group",
            inviter_id="event-inviter",
            flag=flag,
            joined=False,
            event_time=1000,
        )
    async with store.read_session() as session:
        assert await session.scalar(select(func.count()).select_from(Invitation)) == 0


@pytest.mark.asyncio
async def test_valid_request_flag_is_persisted_verbatim(
    invitation_event_store: tuple[InviteRepository, StateStore],
) -> None:
    repo, _ = invitation_event_store
    invitation = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag=" opaque token ",
        joined=False,
        event_time=1000,
    )
    assert invitation.flag == " opaque token "
    saved = await repo.get_by_flag(" opaque token ")
    assert saved is not None
    assert saved.id == invitation.invitation_id


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["", " \t"])
async def test_valid_request_repairs_blank_legacy_flag_without_blank_alias(
    invitation_event_store: tuple[InviteRepository, StateStore], flag: str
) -> None:
    repo, store = invitation_event_store
    original = await repo.create_invitation(
        group_id="event-group", inviter_id="event-inviter", flag=flag
    )
    await repo.add_message_record(original.id, "legacy-report")
    current = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="other-inviter",
        flag="new-valid",
        joined=False,
        event_time=1001,
    )
    assert current.invitation_id == original.id
    assert current.inviter_id == "event-inviter"
    assert current.flag == "new-valid"
    assert current.should_notify is False
    saved = await repo.get_by_message_id("legacy-report")
    assert saved is not None
    assert saved.id == original.id
    async with store.read_session() as session:
        assert await session.scalar(select(func.count()).select_from(Invitation)) == 1


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
                "pre_ban_status": None,
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

        first = await invitation_ops.create_invitation(
            "g-1",
            "u-inviter",
            "flag-1",
            "invite",
        )
        await invitation_ops.create_invitation(
            "g-1",
            "u-inviter",
            "flag-2",
            "invite",
        )
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

        processed = await invitation_ops.create_invitation(
            "g-2",
            "u-inviter-2",
            "f-1",
            "invite",
        )
        await session.flush()
        await invitation_ops.update_status(processed.id, InvitationStatus.REJECTED)
        pending = await invitation_ops.create_invitation(
            "g-2",
            "u-inviter-2",
            "f-1",
            "invite",
        )
        await session.flush()

        by_group = await invitation_ops.get_by_group_id("g-2")
        by_flag = await invitation_ops.get_by_flag("f-1")

        assert by_group is not None
        assert by_group.id == pending.id
        assert by_flag is not None
        assert by_flag.id == pending.id


@pytest.mark.asyncio
async def test_invite_repository_get_by_id_eager_loads_relationships() -> None:
    async with core_db.session() as session:
        await session.execute(delete(Invitation))
        await session.execute(delete(Group))
        await session.execute(delete(User))

        user_ops = UserOps(session)
        group_ops = GroupOps(session)
        invitation_ops = InvitationOps(session)

        await user_ops.add_user("u-inviter-3", "Inviter3", Permission.NORMAL)
        await group_ops.add_group("g-3", "测试群3", GroupStatus.AUTHORIZED)
        invitation = await invitation_ops.create_invitation(
            "g-3",
            "u-inviter-3",
            "f-3",
            "invite",
        )
        await session.flush()

        invitation_id = invitation.id

    invitation = await InviteRepository().get_by_id(invitation_id)

    assert invitation is not None
    assert invitation.group.group_name == "测试群3"
    assert invitation.group.status is GroupStatus.AUTHORIZED
    assert invitation.inviter.user_name == "Inviter3"
    assert invitation.sub_type == "invite"


@pytest_asyncio.fixture
async def invitation_event_store(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[InviteRepository, StateStore]:
    # GLOBAL_DB_ROOT 由 conftest 指向临时目录；各用例再使用独立的库文件。
    store = StateStore(
        namespace=f"invitation_events_{uuid4().hex}",
        filename="core.db",
    )
    await store.init(CoreBase)
    async with store.write_session() as session:
        await UserOps(session).add_user("event-inviter", "Inviter", Permission.NORMAL)
        await UserOps(session).add_user("other-inviter", "Other", Permission.NORMAL)
        await GroupOps(session).add_group(
            "event-group", "测试邀请群", GroupStatus.UNAUTHORIZED
        )
        await GroupOps(session).add_group(
            "other-group", "另一测试群", GroupStatus.UNAUTHORIZED
        )
    monkeypatch.setattr(invite_repository_module, "core_db", store)
    return InviteRepository(), store


@pytest.mark.asyncio
@pytest.mark.parametrize("request_first", [True, False])
async def test_invitation_events_merge_both_orders_and_keep_reply_and_inviter(
    invitation_event_store: tuple[InviteRepository, StateStore],
    request_first: bool,
) -> None:
    repo, store = invitation_event_store
    first = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="event-flag" if request_first else None,
        joined=not request_first,
        event_time=1000,
    )
    await repo.add_message_record(first.invitation_id, "event-report")

    second = await InviteRepository().reconcile_event(
        group_id="event-group",
        inviter_id="other-inviter",
        flag=None if request_first else "event-flag",
        joined=True,
        event_time=1001,
    )

    assert second.invitation_id == first.invitation_id
    assert second.inviter_id == "event-inviter"
    assert second.flag == "event-flag"
    assert second.sub_type == InvitationStage.JOINED
    assert second.should_notify is False
    assert second.replayed is False
    by_message = await repo.get_by_message_id("event-report")
    assert by_message is not None
    assert by_message.id == first.invitation_id
    async with store.read_session() as session:
        assert await session.scalar(select(func.count()).select_from(Invitation)) == 1


@pytest.mark.asyncio
async def test_invitation_event_retries_only_until_delivery_record_exists(
    invitation_event_store: tuple[InviteRepository, StateStore],
) -> None:
    repo, _ = invitation_event_store
    first = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="event-flag",
        joined=False,
        event_time=1000,
    )
    retry = await InviteRepository().reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="event-flag",
        joined=False,
        event_time=1000,
    )
    assert retry.invitation_id == first.invitation_id
    assert retry.should_notify is True
    assert retry.replayed is True
    with pytest.raises(FrozenInstanceError):
        setattr(retry, "flag", "mutated")

    await repo.add_message_record(first.invitation_id, "event-report")
    delivered = await InviteRepository().reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="event-flag",
        joined=False,
        event_time=1000,
    )
    assert delivered.invitation_id == first.invitation_id
    assert delivered.should_notify is False
    assert delivered.replayed is True


@pytest.mark.asyncio
async def test_invitation_processed_request_replays_and_new_flag_starts_new_round(
    invitation_event_store: tuple[InviteRepository, StateStore],
) -> None:
    repo, _ = invitation_event_store
    first = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="first-flag",
        joined=False,
        event_time=1000,
    )
    await repo.update_status(first.invitation_id, InvitationStatus.REJECTED)
    replay = await InviteRepository().reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="first-flag",
        joined=False,
        event_time=1000,
    )
    assert replay.invitation_id == first.invitation_id
    assert replay.status == InvitationStatus.REJECTED
    assert replay.should_notify is False
    assert replay.replayed is True

    new_round = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="new-flag",
        joined=False,
        event_time=2000,
    )
    assert new_round.invitation_id != first.invitation_id
    assert new_round.sub_type == InvitationStage.INVITED
    assert new_round.status == InvitationStatus.PENDING
    assert new_round.should_notify is True
    assert new_round.replayed is False


@pytest.mark.asyncio
async def test_invitation_updated_flag_keeps_durable_alias_and_original_reply(
    invitation_event_store: tuple[InviteRepository, StateStore],
) -> None:
    repo, store = invitation_event_store
    first = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="first-flag",
        joined=False,
        event_time=1000,
    )
    await repo.add_message_record(first.invitation_id, "event-report")
    updated = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="other-inviter",
        flag="new-flag",
        joined=False,
        event_time=1001,
    )
    assert updated.invitation_id == first.invitation_id
    assert updated.flag == "new-flag"
    assert updated.inviter_id == "event-inviter"
    assert updated.should_notify is False

    replay = await InviteRepository().reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="first-flag",
        joined=False,
        event_time=1000,
    )
    assert replay.status == InvitationStatus.IGNORED
    assert replay.replayed is True
    assert replay.should_notify is False
    canonical = await repo.get_by_message_id("event-report")
    assert canonical is not None
    assert canonical.id == first.invitation_id
    assert canonical.flag == "new-flag"
    assert canonical.sub_type == InvitationStage.INVITED
    async with store.read_session() as session:
        assert await session.scalar(select(func.count()).select_from(Invitation)) == 2


@pytest.mark.asyncio
async def test_invitation_same_flag_is_scoped_to_group(
    invitation_event_store: tuple[InviteRepository, StateStore],
) -> None:
    repo, _ = invitation_event_store
    first = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="same-flag",
        joined=False,
        event_time=1000,
    )
    other = await repo.reconcile_event(
        group_id="other-group",
        inviter_id="event-inviter",
        flag="same-flag",
        joined=False,
        event_time=1000,
    )
    assert other.invitation_id != first.invitation_id
    assert other.group_id == "other-group"
    assert other.should_notify is True


@pytest.mark.asyncio
async def test_invitation_concurrent_events_create_one_persistent_pending_record(
    invitation_event_store: tuple[InviteRepository, StateStore],
) -> None:
    repo, store = invitation_event_store
    request, notice = await asyncio.gather(
        repo.reconcile_event(
            group_id="event-group",
            inviter_id="event-inviter",
            flag="event-flag",
            joined=False,
            event_time=1000,
        ),
        InviteRepository().reconcile_event(
            group_id="event-group",
            inviter_id="event-inviter",
            flag=None,
            joined=True,
            event_time=1000,
        ),
    )
    assert request.invitation_id == notice.invitation_id
    canonical = await repo.get_by_id(request.invitation_id)
    assert canonical is not None
    assert canonical.sub_type == InvitationStage.JOINED
    assert canonical.flag == "event-flag"
    async with store.read_session() as session:
        assert await session.scalar(select(func.count()).select_from(Invitation)) == 1


@pytest.mark.asyncio
async def test_invitation_processed_notice_replay_and_later_join_are_distinct(
    invitation_event_store: tuple[InviteRepository, StateStore],
) -> None:
    repo, _ = invitation_event_store
    first = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag=None,
        joined=True,
        event_time=1000,
    )
    await repo.update_status(first.invitation_id, InvitationStatus.REJECTED)
    processed = await repo.get_by_id(first.invitation_id)
    assert processed is not None
    replay = await InviteRepository().reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag=None,
        joined=True,
        event_time=processed.updated_at,
    )
    assert replay.invitation_id == first.invitation_id
    assert replay.should_notify is False
    assert replay.replayed is True

    later = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag=None,
        joined=True,
        event_time=processed.updated_at + 1,
    )
    assert later.invitation_id != first.invitation_id
    assert later.should_notify is True
    assert later.replayed is False


@pytest.mark.asyncio
async def test_invitation_mark_joined_keeps_flag_inviter_status_and_reply(
    invitation_event_store: tuple[InviteRepository, StateStore],
) -> None:
    repo, _ = invitation_event_store
    first = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="event-flag",
        joined=False,
        event_time=1000,
    )
    await repo.add_message_record(first.invitation_id, "event-report")
    await repo.mark_joined(first.invitation_id)
    await repo.mark_joined(first.invitation_id)
    invitation = await repo.get_by_message_id("event-report")
    assert invitation is not None
    assert invitation.id == first.invitation_id
    assert invitation.sub_type == InvitationStage.JOINED
    assert invitation.flag == "event-flag"
    assert invitation.inviter_id == "event-inviter"
    assert invitation.status == InvitationStatus.PENDING


@pytest.mark.asyncio
async def test_invitation_new_request_after_leaving_resumes_platform_stage(
    invitation_event_store: tuple[InviteRepository, StateStore],
) -> None:
    repo, _ = invitation_event_store
    joined = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="joined-flag",
        joined=True,
        event_time=1000,
    )
    await repo.add_message_record(joined.invitation_id, "event-report")
    stale = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="joined-flag",
        joined=False,
        event_time=1000,
    )
    assert stale.sub_type == InvitationStage.JOINED
    assert stale.replayed is True

    reinvited = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="other-inviter",
        flag="reinvited-flag",
        joined=False,
        event_time=1001,
    )
    assert reinvited.invitation_id == joined.invitation_id
    assert reinvited.sub_type == InvitationStage.INVITED
    assert reinvited.inviter_id == "event-inviter"
    assert reinvited.flag == "reinvited-flag"
    assert reinvited.should_notify is False
    canonical = await repo.get_by_message_id("event-report")
    assert canonical is not None
    assert canonical.id == joined.invitation_id
    assert canonical.sub_type == InvitationStage.INVITED


@pytest.mark.asyncio
async def test_invitation_failed_reconciliation_rolls_back_flag_and_alias(
    invitation_event_store: tuple[InviteRepository, StateStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, store = invitation_event_store
    first = await repo.reconcile_event(
        group_id="event-group",
        inviter_id="event-inviter",
        flag="first-flag",
        joined=False,
        event_time=1000,
    )

    async def _fail_notification_read(
        self: InvitationOps,
        invitation: Invitation,
    ) -> bool:
        _ = (self, invitation)
        raise RuntimeError("notification read failed")

    with monkeypatch.context() as patch:
        patch.setattr(InvitationOps, "_needs_notification", _fail_notification_read)
        with pytest.raises(RuntimeError, match="notification read failed"):
            await repo.reconcile_event(
                group_id="event-group",
                inviter_id="event-inviter",
                flag="new-flag",
                joined=False,
                event_time=1001,
            )

    canonical = await repo.get_by_id(first.invitation_id)
    assert canonical is not None
    assert canonical.flag == "first-flag"
    async with store.read_session() as session:
        assert await session.scalar(select(func.count()).select_from(Invitation)) == 1
