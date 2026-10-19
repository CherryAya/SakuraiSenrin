from __future__ import annotations

from dataclasses import dataclass
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import ActionFailed, Bot, NetworkError
from nonebug import App
import pytest

if nonebot.get_plugin("notice") is None:
    sys.modules.pop("src.plugins.notice", None)
    nonebot.load_plugin("src.plugins.notice")

user_plugin = importlib.import_module("src.plugins.notice.user")

from tests.plugins.notice.helpers import (
    build_friend_request_event,
    build_group_request_event,
)


@dataclass
class FriendIO:
    wait: AsyncMock
    get_user: AsyncMock
    save_user: AsyncMock
    persist: AsyncMock
    notify: AsyncMock


@pytest.fixture
def friend_io(monkeypatch: pytest.MonkeyPatch) -> FriendIO:
    io = FriendIO(
        AsyncMock(), AsyncMock(return_value=None), AsyncMock(), AsyncMock(), AsyncMock()
    )
    monkeypatch.setattr(user_plugin, "_wait_before_accept", io.wait)
    monkeypatch.setattr(user_plugin.user_repo, "get_user", io.get_user)
    monkeypatch.setattr(user_plugin.user_repo, "save_user", io.save_user)
    monkeypatch.setattr(user_plugin.user_repo, "ensure_persisted", io.persist)
    monkeypatch.setattr(user_plugin, "deliver_admin_notification_i18n", io.notify)
    monkeypatch.setattr(user_plugin, "resolve_locale", AsyncMock(return_value="zh-CN"))
    monkeypatch.setattr("src.hooks.processor._runtime_sync", AsyncMock())
    monkeypatch.setattr("src.hooks.processor._runtime_check", AsyncMock())
    return io


@pytest.mark.parametrize("flag", ["friend-flag", " opaque flag "])
@pytest.mark.parametrize("existing_user", [False, True])
async def test_valid_friend_request_accepts_then_records_and_notifies(
    app: App, friend_io: FriendIO, flag: str, existing_user: bool
) -> None:
    if existing_user:
        friend_io.get_user.return_value = SimpleNamespace(user_id="10001")
    event = build_friend_request_event(flag=flag)
    async with app.test_matcher(user_plugin.friend_request) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("set_friend_add_request", {"flag": flag, "approve": True})
        ctx.should_call_api(
            "get_stranger_info", {"user_id": 10001}, result={"nickname": "申请者"}
        )
    friend_io.wait.assert_awaited_once()
    if existing_user:
        friend_io.save_user.assert_not_awaited()
    else:
        friend_io.save_user.assert_awaited_once()
    friend_io.persist.assert_awaited_once_with("10001", "申请者")
    assert friend_io.notify.await_args.kwargs["key"] == "notice.user.friend_request"


@pytest.mark.parametrize("flag", ["", " ", "\t\r\n", "\u3000"])
async def test_blank_friend_flag_has_no_delay_api_write_or_notification(
    app: App, friend_io: FriendIO, flag: str
) -> None:
    async with app.test_matcher(user_plugin.friend_request) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_friend_request_event(flag=flag))
        ctx.should_not_pass_rule(user_plugin.friend_request)
    friend_io.wait.assert_not_awaited()
    friend_io.get_user.assert_not_awaited()
    friend_io.save_user.assert_not_awaited()
    friend_io.persist.assert_not_awaited()
    friend_io.notify.assert_not_awaited()


@pytest.mark.parametrize("user_id", [0, -1])
async def test_invalid_friend_user_has_no_side_effects(
    app: App, friend_io: FriendIO, user_id: int
) -> None:
    async with app.test_matcher(user_plugin.friend_request) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_friend_request_event(user_id=user_id))
        ctx.should_not_pass_rule(user_plugin.friend_request)
    friend_io.wait.assert_not_awaited()
    friend_io.persist.assert_not_awaited()
    friend_io.notify.assert_not_awaited()


async def test_group_request_never_calls_friend_approval(
    app: App, friend_io: FriendIO
) -> None:
    async with app.test_matcher(user_plugin.friend_request) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_not_pass_rule(user_plugin.friend_request)
    friend_io.wait.assert_not_awaited()
    friend_io.persist.assert_not_awaited()


@pytest.mark.parametrize("network", [False, True])
async def test_friend_approval_failure_does_not_report_success(
    app: App, friend_io: FriendIO, network: bool
) -> None:
    error = NetworkError("timeout") if network else ActionFailed(retcode=100)
    async with app.test_matcher(user_plugin.friend_request) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_friend_request_event())
        ctx.should_call_api(
            "set_friend_add_request",
            {"flag": "friend-flag", "approve": True},
            exception=error,
        )
    friend_io.get_user.assert_not_awaited()
    friend_io.save_user.assert_not_awaited()
    friend_io.persist.assert_not_awaited()
    friend_io.notify.assert_awaited_once()
    assert (
        friend_io.notify.await_args.kwargs["key"] == "notice.user.friend_request_failed"
    )


@pytest.mark.parametrize("network", [False, True])
async def test_profile_failure_after_acceptance_still_records_success(
    app: App, friend_io: FriendIO, network: bool
) -> None:
    error = NetworkError("timeout") if network else ActionFailed(retcode=100)
    async with app.test_matcher(user_plugin.friend_request) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_friend_request_event())
        ctx.should_call_api(
            "set_friend_add_request", {"flag": "friend-flag", "approve": True}
        )
        ctx.should_call_api("get_stranger_info", {"user_id": 10001}, exception=error)
    friend_io.persist.assert_awaited_once_with("10001", "")
    assert friend_io.notify.await_args.kwargs["key"] == "notice.user.friend_request"


@pytest.mark.parametrize("profile", [None, [], {"nickname": None}])
async def test_invalid_profile_does_not_undo_friend_acceptance(
    app: App, friend_io: FriendIO, profile: object
) -> None:
    async with app.test_matcher(user_plugin.friend_request) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_friend_request_event())
        ctx.should_call_api(
            "set_friend_add_request", {"flag": "friend-flag", "approve": True}
        )
        ctx.should_call_api("get_stranger_info", {"user_id": 10001}, result=profile)
    friend_io.persist.assert_awaited_once_with("10001", "")
    assert friend_io.notify.await_args.kwargs["key"] == "notice.user.friend_request"


async def test_friend_delay_remains_between_ten_and_twenty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(user_plugin.random, "randint", lambda a, b: (a + b) // 2)
    monkeypatch.setattr(user_plugin.asyncio, "sleep", sleep)
    await user_plugin._wait_before_accept()
    sleep.assert_awaited_once_with(15)
