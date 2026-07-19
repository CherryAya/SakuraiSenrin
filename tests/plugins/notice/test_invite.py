from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
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
    monkeypatch.setattr(
        notice_invite_plugin,
        "deliver_message_plan",
        deliver_message_plan_mock,
    )
    monkeypatch.setattr(notice_invite_plugin, "resolve_locale", resolve_locale_mock)
    monkeypatch.setattr(notice_invite_plugin.asyncio, "sleep", AsyncMock())

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
    )
    send_private_i18n_mock.assert_awaited_once()
    add_message_record_mock.assert_awaited_once_with(
        invitation_id=12,
        message_id="99",
    )


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
    update_status_mock.assert_awaited_once_with(34, status=InvitationStatus.APPROVED)
