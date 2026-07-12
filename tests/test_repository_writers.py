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
