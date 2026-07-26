from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupIncreaseNoticeEvent
from nonebot.matcher import matchers as matcher_manager
from nonebug import App
import pytest

nonebot.init(
    SUPERUSERS={"1"},
    IGNORED_USERS=set(),
    MAIN_GROUP_ID="10001",
    GITHUB_TOKEN="test-token",
    GITHUB_REPO="owner/repo",
    GITHUB_BRANCH="main",
    WORDBANK_MEDIA_PROVIDER="local",
    command_start={"#", "/"},
    command_sep={"."},
)
sys.modules.pop("src.plugins.notice.invite", None)
sys.modules.pop("src.plugins.notice.group", None)
sys.modules.pop("src.plugins.notice.user", None)
sys.modules.pop("src.plugins.notice", None)
nonebot.load_plugin("src.plugins.notice")

notice_invite_plugin = importlib.import_module("src.plugins.notice.invite")

from src.database.consts import WritePolicy
from src.database.core.consts import GroupStatus, InvitationStatus
from tests.plugins.water.helpers import build_group_request_event


class _MatcherFinished(Exception):
    pass


class _DummyMatcher:
    async def finish(self) -> None:
        raise _MatcherFinished


async def _run_handler(
    *,
    bot: object,
    event: object,
    matcher: _DummyMatcher | None = None,
) -> None:
    try:
        await notice_invite_plugin._(
            bot=bot,
            event=event,
            matcher=matcher or _DummyMatcher(),
        )
    except _MatcherFinished:
        return


def _get_notice_invite_request_matcher() -> type:
    for matchers in matcher_manager.values():
        for matcher in matchers:
            plugin = getattr(matcher, "plugin", None)
            if (
                getattr(plugin, "module_name", None) == "src.plugins.notice.invite"
                and matcher.type == "request"
            ):
                return matcher
    raise AssertionError("notice invite request matcher not found")


def _install_admin_notification_delivery(
    monkeypatch: pytest.MonkeyPatch,
    *,
    deliver_mock: AsyncMock,
) -> None:
    async def _deliver(
        bot: object,
        *,
        plan: object,
        on_delivered: Any = None,
    ) -> tuple[object, ...]:
        target = SimpleNamespace(channel="private_superuser", target_id="1")
        plan_result = await deliver_mock(bot, plan=plan, target=target)
        if on_delivered is not None:
            maybe_awaitable = on_delivered(target, plan_result)
            if maybe_awaitable is not None:
                await maybe_awaitable
        return (plan_result,)

    monkeypatch.setattr(
        notice_invite_plugin,
        "deliver_admin_notification_plan",
        _deliver,
    )


@pytest.mark.asyncio
async def test_notice_invite_request_persists_dependencies_and_reports_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_get_mock = AsyncMock(side_effect=[None, None, None])
    user_get_mock = AsyncMock(return_value=None)
    save_user_mock = AsyncMock()
    save_group_mock = AsyncMock()
    resolve_user_name_mock = AsyncMock(return_value="邀请者")
    resolve_group_name_mock = AsyncMock(return_value="测试群")
    create_invitation_mock = AsyncMock(return_value=SimpleNamespace(id=12))
    send_private_i18n_mock = AsyncMock()
    add_message_record_mock = AsyncMock()
    deliver_message_plan_mock = AsyncMock(
        return_value=SimpleNamespace(results=[SimpleNamespace(message_id="99")])
    )
    resolve_locale_mock = AsyncMock(return_value="zh-CN")

    monkeypatch.setattr(notice_invite_plugin.group_repo, "get_group", group_get_mock)
    monkeypatch.setattr(notice_invite_plugin.user_repo, "get_user", user_get_mock)
    monkeypatch.setattr(notice_invite_plugin.user_repo, "save_user", save_user_mock)
    monkeypatch.setattr(notice_invite_plugin.group_repo, "save_group", save_group_mock)
    monkeypatch.setattr(
        notice_invite_plugin,
        "resolve_user_name",
        resolve_user_name_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "resolve_group_name",
        resolve_group_name_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin.invite_repo,
        "create_invitation",
        create_invitation_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin.invite_repo,
        "add_message_record",
        add_message_record_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "send_private_i18n",
        send_private_i18n_mock,
    )
    _install_admin_notification_delivery(
        monkeypatch,
        deliver_mock=deliver_message_plan_mock,
    )
    monkeypatch.setattr(notice_invite_plugin, "resolve_locale", resolve_locale_mock)

    event = build_group_request_event(user_id=10001, group_id=20001, flag="flag-1")
    bot = SimpleNamespace(self_id="99999")

    await _run_handler(bot=bot, event=event)

    resolve_group_name_mock.assert_awaited_once_with(bot, "20001")
    resolve_user_name_mock.assert_awaited_once_with(bot, "10001")
    save_user_mock.assert_awaited_once_with(
        user_id="10001",
        user_name="邀请者",
        policy=WritePolicy.IMMEDIATE,
    )
    save_group_mock.assert_awaited_once_with(
        group_id="20001",
        group_name="测试群",
        policy=WritePolicy.IMMEDIATE,
    )
    create_invitation_mock.assert_awaited_once_with(
        group_id="20001",
        inviter_id="10001",
        flag="flag-1",
        sub_type="invite",
    )
    send_private_i18n_mock.assert_awaited_once()
    add_message_record_mock.assert_awaited_once_with(
        invitation_id=12,
        message_id="99",
    )
    await_args = deliver_message_plan_mock.await_args
    assert await_args is not None
    assert await_args.kwargs["plan"].allow_asset_reuse is False


@pytest.mark.asyncio
async def test_notice_invite_request_auto_approves_working_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = SimpleNamespace(status=GroupStatus.AUTHORIZED)
    create_invitation_mock = AsyncMock(return_value=SimpleNamespace(id=34))
    update_status_mock = AsyncMock()
    bot = SimpleNamespace(
        self_id="99999",
        set_group_add_request=AsyncMock(),
    )

    monkeypatch.setattr(
        notice_invite_plugin.group_repo,
        "get_group",
        AsyncMock(return_value=group),
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "resolve_group_name",
        AsyncMock(return_value="已授权群"),
    )
    monkeypatch.setattr(
        notice_invite_plugin.invite_repo,
        "create_invitation",
        create_invitation_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin.invite_repo,
        "update_status",
        update_status_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin.user_repo,
        "get_user",
        AsyncMock(return_value=SimpleNamespace(user_id="10001")),
    )

    event = build_group_request_event(user_id=10001, group_id=20001, flag="flag-1")

    await _run_handler(bot=bot, event=event)

    bot.set_group_add_request.assert_awaited_once_with(
        flag="flag-1",
        sub_type="invite",
        approve=True,
    )
    update_status_mock.assert_awaited_once_with(
        34,
        status=InvitationStatus.APPROVED,
        operator_id="99999",
    )


@pytest.mark.asyncio
async def test_notice_invite_request_auto_rejects_banned_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = SimpleNamespace(status=GroupStatus.BANNED)
    create_invitation_mock = AsyncMock(return_value=SimpleNamespace(id=78))
    update_status_mock = AsyncMock()
    send_private_i18n_mock = AsyncMock()
    bot = SimpleNamespace(
        self_id="99999",
        set_group_add_request=AsyncMock(),
    )

    monkeypatch.setattr(
        notice_invite_plugin.group_repo,
        "get_group",
        AsyncMock(return_value=group),
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "resolve_group_name",
        AsyncMock(return_value="封禁群"),
    )
    monkeypatch.setattr(
        notice_invite_plugin.invite_repo,
        "create_invitation",
        create_invitation_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin.invite_repo,
        "update_status",
        update_status_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin.user_repo,
        "get_user",
        AsyncMock(return_value=SimpleNamespace(user_id="10001")),
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "send_private_i18n",
        send_private_i18n_mock,
    )
    _install_admin_notification_delivery(
        monkeypatch,
        deliver_mock=AsyncMock(
            return_value=SimpleNamespace(results=[SimpleNamespace(message_id="100")])
        ),
    )

    event = build_group_request_event(user_id=10001, group_id=20001, flag="flag-2")

    await _run_handler(bot=bot, event=event)

    bot.set_group_add_request.assert_awaited_once_with(
        flag="flag-2",
        sub_type="invite",
        approve=False,
    )
    update_status_mock.assert_awaited_once_with(
        78,
        status=InvitationStatus.REJECTED,
        operator_id="99999",
    )
    send_private_i18n_mock.assert_awaited_once()
    await_args = send_private_i18n_mock.await_args
    assert await_args is not None
    assert await_args.args[2] == "notice.invite.auto_reject"
    assert await_args.kwargs["main_group_id"] == "10001"


@pytest.mark.asyncio
async def test_notice_invite_group_increase_working_group_marks_invitation_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = SimpleNamespace(status=GroupStatus.AUTHORIZED)
    create_invitation_mock = AsyncMock(return_value=SimpleNamespace(id=56))
    update_status_mock = AsyncMock()
    send_private_i18n_mock = AsyncMock()
    bot = SimpleNamespace(self_id="99999", set_group_leave=AsyncMock())

    monkeypatch.setattr(
        notice_invite_plugin.group_repo,
        "get_group",
        AsyncMock(return_value=group),
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "resolve_group_name",
        AsyncMock(return_value="已授权群"),
    )
    monkeypatch.setattr(
        notice_invite_plugin.invite_repo,
        "create_invitation",
        create_invitation_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin.invite_repo,
        "update_status",
        update_status_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin.user_repo,
        "get_user",
        AsyncMock(return_value=SimpleNamespace(user_id="99999")),
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "send_private_i18n",
        send_private_i18n_mock,
    )

    event = GroupIncreaseNoticeEvent.model_validate(
        {
            "time": 1_700_000_000,
            "self_id": "99999",
            "post_type": "notice",
            "notice_type": "group_increase",
            "sub_type": "invite",
            "user_id": 99999,
            "group_id": 20001,
            "operator_id": 10001,
        }
    )

    await _run_handler(bot=bot, event=event)

    update_status_mock.assert_awaited_once_with(
        56,
        status=InvitationStatus.APPROVED,
        operator_id="99999",
    )
    send_private_i18n_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_notice_invite_group_increase_banned_group_leaves_and_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = SimpleNamespace(status=GroupStatus.BANNED)
    create_invitation_mock = AsyncMock(return_value=SimpleNamespace(id=79))
    update_status_mock = AsyncMock()
    send_private_i18n_mock = AsyncMock()
    bot = SimpleNamespace(
        self_id="99999",
        set_group_leave=AsyncMock(),
    )

    monkeypatch.setattr(
        notice_invite_plugin.group_repo,
        "get_group",
        AsyncMock(return_value=group),
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "resolve_group_name",
        AsyncMock(return_value="封禁群"),
    )
    monkeypatch.setattr(
        notice_invite_plugin.invite_repo,
        "create_invitation",
        create_invitation_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin.invite_repo,
        "update_status",
        update_status_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin.user_repo,
        "get_user",
        AsyncMock(return_value=SimpleNamespace(user_id="99999")),
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "send_private_i18n",
        send_private_i18n_mock,
    )
    _install_admin_notification_delivery(
        monkeypatch,
        deliver_mock=AsyncMock(
            return_value=SimpleNamespace(results=[SimpleNamespace(message_id="101")])
        ),
    )

    event = GroupIncreaseNoticeEvent.model_validate(
        {
            "time": 1_700_000_000,
            "self_id": "99999",
            "post_type": "notice",
            "notice_type": "group_increase",
            "sub_type": "invite",
            "user_id": 99999,
            "group_id": 20001,
            "operator_id": 10001,
        }
    )

    await _run_handler(bot=bot, event=event)

    bot.set_group_leave.assert_awaited_once_with(group_id=20001)
    update_status_mock.assert_awaited_once_with(
        79,
        status=InvitationStatus.REJECTED,
        operator_id="99999",
    )
    send_private_i18n_mock.assert_awaited()


@pytest.mark.asyncio
async def test_notice_invite_request_matcher_handles_group_request_event(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = _get_notice_invite_request_matcher()
    group_get_mock = AsyncMock(side_effect=[None, None, None])
    user_get_mock = AsyncMock(return_value=None)
    save_user_mock = AsyncMock()
    save_group_mock = AsyncMock()
    resolve_user_name_mock = AsyncMock(return_value="邀请者")
    resolve_group_name_mock = AsyncMock(return_value="测试群")
    create_invitation_mock = AsyncMock(return_value=SimpleNamespace(id=12))
    send_private_i18n_mock = AsyncMock()
    deliver_admin_notification_mock = AsyncMock(return_value=())

    monkeypatch.setattr(notice_invite_plugin.group_repo, "get_group", group_get_mock)
    monkeypatch.setattr(notice_invite_plugin.user_repo, "get_user", user_get_mock)
    monkeypatch.setattr(notice_invite_plugin.user_repo, "save_user", save_user_mock)
    monkeypatch.setattr(notice_invite_plugin.group_repo, "save_group", save_group_mock)
    monkeypatch.setattr(
        notice_invite_plugin,
        "resolve_user_name",
        resolve_user_name_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "resolve_group_name",
        resolve_group_name_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin.invite_repo,
        "create_invitation",
        create_invitation_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "send_private_i18n",
        send_private_i18n_mock,
    )
    monkeypatch.setattr(
        notice_invite_plugin,
        "deliver_admin_notification_plan",
        deliver_admin_notification_mock,
    )

    event = build_group_request_event(user_id=10001, group_id=20001, flag="flag-1")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)

    resolve_group_name_mock.assert_awaited_once_with(bot, "20001")
    resolve_user_name_mock.assert_awaited_once_with(bot, "10001")
    save_user_mock.assert_awaited_once()
    save_group_mock.assert_awaited_once()
    create_invitation_mock.assert_awaited_once_with(
        group_id="20001",
        inviter_id="10001",
        flag="flag-1",
        sub_type="invite",
    )
    send_private_i18n_mock.assert_awaited_once()
    deliver_admin_notification_mock.assert_awaited_once()
