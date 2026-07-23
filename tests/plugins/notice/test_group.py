from __future__ import annotations

import asyncio
import importlib
import sys
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import NotifyEvent
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
sys.modules.pop("src.plugins.notice.group", None)
sys.modules.pop("src.plugins.notice", None)
nonebot.load_plugin("src.plugins.notice")

notice_group_plugin = importlib.import_module("src.plugins.notice.group")
from tests.plugins.water.helpers import (
    build_group_decrease_event,
    build_group_increase_event,
)


@pytest.mark.asyncio
async def test_group_increase_notice_syncs_members_for_any_join(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_mock = AsyncMock()
    monkeypatch.setattr(notice_group_plugin, "sync_members_from_api", sync_mock)
    event = build_group_increase_event(
        user_id=10002, group_id=20001, sub_type="approve"
    )

    async with app.test_matcher(notice_group_plugin.group_increase_notice) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)

    sync_mock.assert_awaited_once_with(
        bot,
        "20001",
        trigger_source="notice_group_increase:approve",
    )


@pytest.mark.asyncio
async def test_group_decrease_notice_syncs_members_for_kick(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_mock = AsyncMock()
    monkeypatch.setattr(notice_group_plugin, "sync_members_from_api", sync_mock)
    event = build_group_decrease_event(user_id=10002, group_id=20001, sub_type="kick")

    async with app.test_matcher(notice_group_plugin.group_decrease_notice) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)

    sync_mock.assert_awaited_once_with(
        bot,
        "20001",
        trigger_source="notice_group_decrease:kick",
    )


@pytest.mark.asyncio
async def test_group_decrease_notice_syncs_members_for_leave(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_mock = AsyncMock()
    monkeypatch.setattr(notice_group_plugin, "sync_members_from_api", sync_mock)
    event = build_group_decrease_event(user_id=10002, group_id=20001, sub_type="leave")

    async with app.test_matcher(notice_group_plugin.group_decrease_notice) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)

    sync_mock.assert_awaited_once_with(
        bot,
        "20001",
        trigger_source="notice_group_decrease:leave",
    )


@pytest.mark.asyncio
async def test_group_decrease_notice_skips_member_sync_for_kick_me(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_mock = AsyncMock()
    notify_mock = AsyncMock(return_value=())
    monkeypatch.setattr(notice_group_plugin, "sync_members_from_api", sync_mock)
    monkeypatch.setattr(
        notice_group_plugin,
        "ban_user_and_cleanup_groups",
        AsyncMock(return_value=""),
    )
    monkeypatch.setattr(
        notice_group_plugin,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    monkeypatch.setattr(
        notice_group_plugin,
        "deliver_admin_notification_i18n",
        notify_mock,
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    event = build_group_decrease_event(
        user_id=99999,
        group_id=20001,
        operator_id=10001,
        sub_type="kick_me",
        self_id=99999,
    )

    async with app.test_matcher(notice_group_plugin.group_decrease_notice) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)

    sync_mock.assert_not_awaited()
    notify_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_rename_notice_updates_group_name_immediately(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update_name_mock = AsyncMock()
    monkeypatch.setattr(notice_group_plugin.group_repo, "update_name", update_name_mock)
    event = NotifyEvent.model_validate(
        {
            "time": 1_700_000_000,
            "self_id": "99999",
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "group_name",
            "user_id": 10001,
            "group_id": 20001,
            "name_new": "新群名",
            "name_old": "旧群名",
        }
    )

    async with app.test_matcher(notice_group_plugin.group_rename_notice) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)

    update_name_mock.assert_awaited_once_with("20001", "新群名")
