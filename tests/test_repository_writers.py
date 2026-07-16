from types import TracebackType

import pytest

from src.database.consts import WritePolicy
from src.database.core.consts import GroupStatus, Permission
from src.lib.cache.impl import GroupCache, MemberCache, UserCache
from src.repositories.group import GroupRepository
from src.repositories.member import MemberRepository
from src.repositories.user import UserRepository


class _CaptureWriter:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    async def add(self, item: dict[str, object]) -> None:
        self.items.append(item)


class _FakeSessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


async def test_save_user_name_buffered_includes_created_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.repositories import user as user_module

    writer = _CaptureWriter()
    monkeypatch.setattr(user_module, "get_current_time", lambda: 1_780_901_962)
    monkeypatch.setattr(user_module, "user_update_name_writer", writer)

    cache = UserCache()
    cache.upsert_user("10001", "Old Name", Permission.NORMAL)
    repo = UserRepository(cache)
    await repo.save_user(
        user_id="10001",
        user_name="Alice",
        policy=WritePolicy.BUFFERED,
    )

    assert writer.items == [
        {
            "created_at": 1_780_901_962,
            "updated_at": 1_780_901_962,
            "user_id": "10001",
            "user_name": "Alice",
        }
    ]


async def test_save_group_name_buffered_includes_created_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.repositories import group as group_module

    writer = _CaptureWriter()
    monkeypatch.setattr(group_module, "get_current_time", lambda: 1_780_901_962)
    monkeypatch.setattr(group_module, "group_update_name_writer", writer)

    cache = GroupCache()
    cache.upsert_group("20001", "Old Group", GroupStatus.UNAUTHORIZED, False)
    repo = GroupRepository(cache)
    await repo.save_group(
        group_id="20001",
        group_name="Test Group",
        policy=WritePolicy.BUFFERED,
    )

    assert writer.items == [
        {
            "created_at": 1_780_901_962,
            "updated_at": 1_780_901_962,
            "group_id": "20001",
            "group_name": "Test Group",
        }
    ]


async def test_save_member_card_buffered_includes_insert_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.repositories import member as member_module

    writer = _CaptureWriter()
    monkeypatch.setattr(member_module, "get_current_time", lambda: 1_780_901_962)
    monkeypatch.setattr(member_module, "member_update_card_writer", writer)

    cache = MemberCache()
    cache.upsert_member("10001", "20001", Permission.SUPERUSER, "Old Card")
    repo = MemberRepository(cache)
    await repo.save_member(
        user_id="10001",
        group_id="20001",
        group_card="New Card",
        policy=WritePolicy.BUFFERED,
    )

    assert writer.items == [
        {
            "created_at": 1_780_901_962,
            "updated_at": 1_780_901_962,
            "group_id": "20001",
            "user_id": "10001",
            "group_card": "New Card",
            "permission": Permission.SUPERUSER,
        }
    ]


async def test_save_user_name_immediate_includes_snapshot_created_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.repositories import user as user_module

    captured: dict[str, object] = {}

    class _FakeUserOps:
        def __init__(self, _session: object) -> None:
            pass

        async def update_name(self, user_id: str, user_name: str) -> None:
            captured["user_update"] = (user_id, user_name)

    class _FakeSnapshotOps:
        def __init__(self, _session: object) -> None:
            pass

        async def create_user_snapshot(
            self,
            *,
            user_id: str,
            content: str,
            created_at: int,
        ) -> None:
            captured["snapshot"] = (user_id, content, created_at)

    monkeypatch.setattr(user_module, "get_current_time", lambda: 1_780_901_962)
    monkeypatch.setattr(user_module, "UserOps", _FakeUserOps)
    monkeypatch.setattr(user_module, "UserSnapshotOps", _FakeSnapshotOps)
    monkeypatch.setattr(user_module.core_db, "session", _FakeSessionContext)
    monkeypatch.setattr(user_module.log_db, "session", _FakeSessionContext)
    monkeypatch.setattr(user_module.snapshot_db, "session", _FakeSessionContext)

    cache = UserCache()
    cache.upsert_user("10001", "Old Name", Permission.NORMAL)
    repo = UserRepository(cache)
    await repo.save_user(
        user_id="10001",
        user_name="Alice",
        policy=WritePolicy.IMMEDIATE,
    )

    assert captured["user_update"] == ("10001", "Alice")
    assert captured["snapshot"] == ("10001", "Alice", 1_780_901_962)


async def test_save_group_name_immediate_includes_snapshot_created_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.repositories import group as group_module

    captured: dict[str, object] = {}

    class _FakeGroupOps:
        def __init__(self, _session: object) -> None:
            pass

        async def update_name(self, group_id: str, group_name: str) -> None:
            captured["group_update"] = (group_id, group_name)

    class _FakeSnapshotOps:
        def __init__(self, _session: object) -> None:
            pass

        async def create_group_snapshot(
            self,
            *,
            group_id: str,
            content: str,
            created_at: int,
        ) -> None:
            captured["snapshot"] = (group_id, content, created_at)

    monkeypatch.setattr(group_module, "get_current_time", lambda: 1_780_901_962)
    monkeypatch.setattr(group_module, "GroupOps", _FakeGroupOps)
    monkeypatch.setattr(group_module, "GroupSnapshotOps", _FakeSnapshotOps)
    monkeypatch.setattr(group_module.core_db, "session", _FakeSessionContext)
    monkeypatch.setattr(group_module.log_db, "session", _FakeSessionContext)
    monkeypatch.setattr(group_module.snapshot_db, "session", _FakeSessionContext)

    cache = GroupCache()
    cache.upsert_group("20001", "Old Group", GroupStatus.UNAUTHORIZED, False)
    repo = GroupRepository(cache)
    await repo.save_group(
        group_id="20001",
        group_name="Test Group",
        policy=WritePolicy.IMMEDIATE,
    )

    assert captured["group_update"] == ("20001", "Test Group")
    assert captured["snapshot"] == ("20001", "Test Group", 1_780_901_962)


async def test_save_member_card_immediate_includes_snapshot_created_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.repositories import member as member_module

    captured: dict[str, object] = {}

    class _FakeMemberOps:
        def __init__(self, _session: object) -> None:
            pass

        async def update_card(
            self,
            user_id: str,
            group_id: str,
            group_card: str,
        ) -> None:
            captured["member_update"] = (user_id, group_id, group_card)

    class _FakeSnapshotOps:
        def __init__(self, _session: object) -> None:
            pass

        async def create_member_snapshot(
            self,
            *,
            user_id: str,
            group_id: str,
            content: str,
            created_at: int,
        ) -> None:
            captured["snapshot"] = (user_id, group_id, content, created_at)

    monkeypatch.setattr(member_module, "get_current_time", lambda: 1_780_901_962)
    monkeypatch.setattr(member_module, "MemberOps", _FakeMemberOps)
    monkeypatch.setattr(member_module, "MemberSnapshotOps", _FakeSnapshotOps)
    monkeypatch.setattr(member_module.core_db, "session", _FakeSessionContext)
    monkeypatch.setattr(member_module.log_db, "session", _FakeSessionContext)
    monkeypatch.setattr(member_module.snapshot_db, "session", _FakeSessionContext)

    cache = MemberCache()
    cache.upsert_member("10001", "20001", Permission.SUPERUSER, "Old Card")
    repo = MemberRepository(cache)
    await repo.save_member(
        user_id="10001",
        group_id="20001",
        group_card="New Card",
        policy=WritePolicy.IMMEDIATE,
    )

    assert captured["member_update"] == ("10001", "20001", "New Card")
    assert captured["snapshot"] == ("10001", "20001", "New Card", 1_780_901_962)
