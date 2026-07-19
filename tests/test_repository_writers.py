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


class _FakeScalarResult:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def scalars(self) -> "_FakeScalarResult":
        return self

    def all(self) -> list[object]:
        return list(self._items)


class _FakeExecuteSession:
    def __init__(self, items: list[object]) -> None:
        self._items = items
        self.execute_calls = 0

    async def execute(self, _stmt: object) -> _FakeScalarResult:
        self.execute_calls += 1
        return _FakeScalarResult(self._items)


class _FakeExecuteSessionContext:
    def __init__(self, session: _FakeExecuteSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeExecuteSession:
        return self._session

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


async def test_get_group_returns_item_after_db_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.repositories import group as group_module

    class _FakeGroupOps:
        def __init__(self, _session: object) -> None:
            pass

        async def get_by_group_id(self, group_id: str) -> object:
            return type(
                "_DbGroup",
                (),
                {
                    "group_id": group_id,
                    "group_name": "测试群",
                    "status": GroupStatus.AUTHORIZED,
                },
            )()

    monkeypatch.setattr(group_module, "GroupOps", _FakeGroupOps)
    monkeypatch.setattr(group_module.core_db, "session", _FakeSessionContext)

    cache = GroupCache()
    repo = GroupRepository(cache)

    group = await repo.get_group("20001")

    assert group is not None
    assert group.display_name == "测试群"
    assert group.status == GroupStatus.AUTHORIZED


async def test_get_name_by_uid_backfills_cache_and_reuses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.repositories import user as user_module

    calls = 0

    class _FakeUserOps:
        def __init__(self, _session: object) -> None:
            pass

        async def get_by_user_id(self, user_id: str) -> object:
            nonlocal calls
            calls += 1
            return type(
                "_DbUser",
                (),
                {
                    "user_id": user_id,
                    "user_name": "Alice",
                    "permission": Permission.SUPERUSER,
                },
            )()

    monkeypatch.setattr(user_module, "UserOps", _FakeUserOps)
    monkeypatch.setattr(user_module.core_db, "session", _FakeSessionContext)

    cache = UserCache()
    repo = UserRepository(cache)

    assert await repo.get_name_by_uid("10001") == "Alice"
    assert await repo.get_name_by_uid("10001") == "Alice"
    assert calls == 1
    cached = cache.get("10001")
    assert cached is not None
    assert cached.display_name == "Alice"
    assert cached.permission == Permission.SUPERUSER


async def test_get_names_by_uids_only_queries_cache_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.repositories import user as user_module

    db_session = _FakeExecuteSession(
        [
            type(
                "_DbUser",
                (),
                {
                    "user_id": "10002",
                    "user_name": "Bob",
                    "permission": Permission.SUPERUSER,
                },
            )()
        ]
    )
    monkeypatch.setattr(
        user_module.core_db,
        "session",
        lambda: _FakeExecuteSessionContext(db_session),
    )

    cache = UserCache()
    cache.upsert_user("10001", "Alice", Permission.NORMAL)
    repo = UserRepository(cache)

    resolved = await repo.get_names_by_uids(["10001", "10002", "10001"])

    assert resolved == {"10001": "Alice", "10002": "Bob"}
    assert db_session.execute_calls == 1
    cached = cache.get("10002")
    assert cached is not None
    assert cached.display_name == "Bob"
    assert cached.permission == Permission.SUPERUSER


async def test_get_names_by_gids_only_queries_cache_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.repositories import group as group_module

    db_session = _FakeExecuteSession(
        [
            type(
                "_DbGroup",
                (),
                {
                    "group_id": "20002",
                    "group_name": "第二群",
                    "status": GroupStatus.AUTHORIZED,
                },
            )()
        ]
    )
    monkeypatch.setattr(
        group_module.core_db,
        "session",
        lambda: _FakeExecuteSessionContext(db_session),
    )

    cache = GroupCache()
    cache.upsert_group("20001", "第一群", GroupStatus.DORMANT, False)
    repo = GroupRepository(cache)

    resolved = await repo.get_names_by_gids(["20001", "20002", "20001"])

    assert resolved == {"20001": "第一群", "20002": "第二群"}
    assert db_session.execute_calls == 1
    cached = cache.get("20002")
    assert cached is not None
    assert cached.display_name == "第二群"
    assert cached.status == GroupStatus.AUTHORIZED


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


async def test_get_card_by_uid_gid_backfills_cache_and_reuses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.repositories import member as member_module

    calls = 0

    class _FakeMemberOps:
        def __init__(self, _session: object) -> None:
            pass

        async def get_by_uid_gid(self, user_id: str, group_id: str) -> object:
            nonlocal calls
            calls += 1
            return type(
                "_DbMember",
                (),
                {
                    "user_id": user_id,
                    "group_id": group_id,
                    "group_card": "阿明",
                    "permission": Permission.NORMAL,
                },
            )()

    monkeypatch.setattr(member_module, "MemberOps", _FakeMemberOps)
    monkeypatch.setattr(member_module.core_db, "session", _FakeSessionContext)

    cache = MemberCache()
    repo = MemberRepository(cache)

    assert await repo.get_card_by_uid_gid("10001", "20001") == "阿明"
    assert await repo.get_card_by_uid_gid("10001", "20001") == "阿明"
    assert calls == 1
    cached = cache.get_member("10001", "20001")
    assert cached is not None
    assert cached.group_card == "阿明"
