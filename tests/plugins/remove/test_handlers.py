from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from nonebot.exception import ActionFailed
import pytest

from src.plugins.remove import handlers as remove_handlers
from src.plugins.remove.handlers import (
    has_remove_permission,
    is_group_admin,
    is_group_inviter,
    is_remove_confirmed,
    notify_superusers,
    perform_remove,
)
from tests.plugins.water.helpers import (
    DummyMatcher,
    MatcherFinished,
    build_group_message_event,
)


class FakeBot:
    def __init__(self) -> None:
        self.left_groups: list[int] = []
        self.private_messages: list[tuple[int, object]] = []

    async def set_group_leave(self, *, group_id: int) -> None:
        self.left_groups.append(group_id)

    async def send_private_msg(
        self,
        *,
        user_id: int,
        message: object,
    ) -> dict[str, int]:
        self.private_messages.append((user_id, message))
        return {"message_id": 1}


def test_is_group_admin_by_role() -> None:
    assert is_group_admin(build_group_message_event("x", role="owner")) is True
    assert is_group_admin(build_group_message_event("x", role="admin")) is True
    assert is_group_admin(build_group_message_event("x", role="member")) is False


def test_is_remove_confirmed() -> None:
    assert is_remove_confirmed("y") is True
    assert is_remove_confirmed(" YES ") is True
    assert is_remove_confirmed("no") is False


@pytest.mark.asyncio
async def test_is_group_inviter_matches_latest_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_group_message_event("x", user_id=10001)
    monkeypatch.setattr(
        remove_handlers.invite_repo,
        "get_by_group_id",
        AsyncMock(return_value=SimpleNamespace(inviter_id="10001")),
    )

    assert await is_group_inviter(event) is True


@pytest.mark.asyncio
async def test_has_remove_permission_accepts_inviter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_group_message_event("x", role="member", user_id=10001)
    monkeypatch.setattr(
        remove_handlers.invite_repo,
        "get_by_group_id",
        AsyncMock(return_value=SimpleNamespace(inviter_id="10001")),
    )

    assert await has_remove_permission(event) is True


@pytest.mark.asyncio
async def test_has_remove_permission_rejects_plain_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_group_message_event("x", role="member", user_id=10001)
    monkeypatch.setattr(
        remove_handlers.invite_repo,
        "get_by_group_id",
        AsyncMock(return_value=None),
    )

    assert await has_remove_permission(event) is False


@pytest.mark.asyncio
async def test_notify_superusers_sends_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = FakeBot()
    monkeypatch.setattr(remove_handlers.config, "SUPERUSERS", {"2", "3"})

    await notify_superusers(
        cast(Any, bot),
        locale="zh-CN",
        group_id="20001",
        group_name="测试群",
        operator_id="10001",
        reason="例行维护",
    )

    assert {user_id for user_id, _ in bot.private_messages} == {2, 3}
    assert all("测试群" in str(message) for _, message in bot.private_messages)


@pytest.mark.asyncio
async def test_perform_remove_rejects_empty_reason() -> None:
    matcher = DummyMatcher()
    event = build_group_message_event("#remove", role="admin")

    with pytest.raises(MatcherFinished):
        await perform_remove(
            cast(Any, FakeBot()),
            cast(Any, matcher),
            event,
            locale="zh-CN",
            reason="   ",
        )

    assert matcher.finished == "退群原因不能为空。"


@pytest.mark.asyncio
async def test_perform_remove_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = DummyMatcher()
    bot = FakeBot()
    event = build_group_message_event("#remove", role="admin")

    monkeypatch.setattr(
        remove_handlers,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    monkeypatch.setattr(
        remove_handlers.group_repo,
        "update_status",
        AsyncMock(),
    )
    notify_mock = AsyncMock()
    monkeypatch.setattr(remove_handlers, "notify_superusers", notify_mock)

    with pytest.raises(MatcherFinished):
        await perform_remove(
            cast(Any, bot),
            cast(Any, matcher),
            event,
            locale="zh-CN",
            reason="例行维护",
        )

    assert matcher.sent == ["走了走了，再见啦！原因：例行维护"]
    assert matcher.finished == "已从当前群聊退出: 测试群"
    assert bot.left_groups == [20001]
    notify_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_perform_remove_handles_leave_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = DummyMatcher()
    event = build_group_message_event("#remove", role="admin")

    class FailingBot(FakeBot):
        async def set_group_leave(self, *, group_id: int) -> None:
            raise ActionFailed("OneBot V11", "leave failed")

    monkeypatch.setattr(
        remove_handlers,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    update_status = AsyncMock()
    monkeypatch.setattr(remove_handlers.group_repo, "update_status", update_status)

    with pytest.raises(MatcherFinished):
        await perform_remove(
            cast(Any, FailingBot()),
            cast(Any, matcher),
            event,
            locale="zh-CN",
            reason="例行维护",
        )

    assert matcher.sent == ["走了走了，再见啦！原因：例行维护"]
    assert matcher.finished == "退群失败，请稍后重试或联系超管处理。"
    update_status.assert_not_awaited()
