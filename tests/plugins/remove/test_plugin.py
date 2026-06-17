from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import ActionFailed
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
)
if nonebot.get_plugin("remove") is None:
    sys.modules.pop("src.plugins.remove", None)
    nonebot.load_plugin("src.plugins.remove")

import src.plugins.remove as remove_plugin
from src.plugins.remove import remove_matcher
from tests.plugins.water.helpers import (
    build_group_message_event,
    build_private_message_event,
)


@pytest.fixture(autouse=True)
def _disable_runtime_processor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.hooks.processor._runtime_sync", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "src.hooks.processor._runtime_check",
        AsyncMock(return_value=None),
    )


@pytest.mark.asyncio
async def test_remove_rejects_private_message(app: App) -> None:
    event = build_private_message_event("#remove")

    async with app.test_matcher(remove_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "请到群聊中发起退群请求。", bot=bot)
        ctx.should_finished(remove_matcher)


@pytest.mark.asyncio
async def test_remove_rejects_member_without_permission(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_group_message_event("#remove", role="member", user_id=10001)
    monkeypatch.setattr(
        remove_plugin.invite_repo,
        "get_by_group_id",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(remove_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "您没有权限发起退群请求，仅群主、管理员、邀请者可以发起。",
            bot=bot,
        )
        ctx.should_finished(remove_matcher)


@pytest.mark.asyncio
async def test_remove_cancels_when_confirm_denied(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = build_group_message_event("#remove", role="admin", message_id=1)
    second = build_group_message_event("n", role="admin", message_id=2)
    monkeypatch.setattr(
        remove_plugin.invite_repo,
        "get_by_group_id",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(remove_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "是否确认退群？输入 y 或 yes 确认，其他内容取消：",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(second, "已取消退群。", bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_remove_succeeds_for_admin(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = build_group_message_event("#remove", role="admin", message_id=1)
    second = build_group_message_event("y", role="admin", message_id=2)
    third = build_group_message_event("例行维护", role="admin", message_id=3)

    monkeypatch.setattr(
        remove_plugin.invite_repo,
        "get_by_group_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        remove_plugin,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    update_status = AsyncMock()
    monkeypatch.setattr(remove_plugin.group_repo, "update_status", update_status)
    monkeypatch.setattr(remove_plugin.config, "SUPERUSERS", {"2"})

    async with app.test_matcher(remove_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "是否确认退群？输入 y 或 yes 确认，其他内容取消：",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(second, "请输入退群原因：", bot=bot)
        ctx.should_rejected()

        ctx.receive_event(bot, third)
        ctx.should_call_send(third, "走了走了，再见啦！原因：例行维护", bot=bot)
        ctx.should_call_api("set_group_leave", {"group_id": 20001}, result=None)
        ctx.should_call_api(
            "send_private_msg",
            {
                "user_id": 2,
                "message": (
                    "👋 退群提醒\n"
                    "群组 ID: 20001\n"
                    "群组名称: 测试群\n"
                    "退群者 ID: 10001\n"
                    "退群原因: 例行维护"
                ),
            },
            result={"message_id": 1},
        )
        ctx.should_call_send(third, "已从当前群聊退出: 测试群", bot=bot)
        ctx.should_finished()

    update_status.assert_awaited_once_with("20001", remove_plugin.GroupStatus.LEFT)


@pytest.mark.asyncio
async def test_remove_handles_leave_failure(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = build_group_message_event("#remove", role="admin", message_id=1)
    second = build_group_message_event("y", role="admin", message_id=2)
    third = build_group_message_event("例行维护", role="admin", message_id=3)

    monkeypatch.setattr(
        remove_plugin.invite_repo,
        "get_by_group_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        remove_plugin,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    update_status = AsyncMock()
    monkeypatch.setattr(remove_plugin.group_repo, "update_status", update_status)

    async with app.test_matcher(remove_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "是否确认退群？输入 y 或 yes 确认，其他内容取消：",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(second, "请输入退群原因：", bot=bot)
        ctx.should_rejected()

        ctx.receive_event(bot, third)
        ctx.should_call_send(third, "走了走了，再见啦！原因：例行维护", bot=bot)
        ctx.should_call_api(
            "set_group_leave",
            {"group_id": 20001},
            exception=ActionFailed("OneBot V11", "leave failed"),
        )
        ctx.should_call_send(third, "退群失败，请稍后重试或联系超管处理。", bot=bot)
        ctx.should_finished()

    update_status.assert_not_awaited()
