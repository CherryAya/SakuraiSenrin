from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Bot, MessageSegment
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
if nonebot.get_plugin("admin") is None:
    sys.modules.pop("src.plugins.admin", None)
    nonebot.load_plugin("src.plugins.admin")

SUPERUSER_ID = int(next(iter(nonebot.get_driver().config.superusers)))

from src.database.core.consts import GroupStatus
from src.database.core.consts import Permission as CorePermission
from src.lib.i18n.runtime import tr
from src.lib.message_assets import message_asset_repo
from src.lib.messages import empty_message, text_message
from src.plugins.admin.backup import admin_backup
from src.plugins.admin.group import admin_group
from src.plugins.admin.i18n import admin_i18n
from src.plugins.admin.invite import admin_invite
from src.plugins.admin.user import admin_user
from tests.plugins.water.helpers import (
    attach_reply_message,
    build_group_message_event,
    build_private_message_event,
)


class _Status:
    def __str__(self) -> str:
        return "AUTHORIZED"


class _Group:
    group_id = "20001"
    status = _Status()


class _User:
    user_id = "12345"
    permission = CorePermission.NORMAL


class _MatcherFinished(Exception):
    pass


class _DummyMatcher:
    async def finish(self) -> None:
        raise _MatcherFinished


async def _run_reply_handler(handler: object, *, bot: object, event: object) -> None:
    try:
        await handler(bot=bot, event=event, matcher=_DummyMatcher())  # type: ignore[misc]
    except _MatcherFinished:
        return


@pytest.mark.asyncio
async def test_admin_group_dot_form_hits_matcher(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_group_message_event("#admin.group status 20001", user_id=SUPERUSER_ID)

    from src.plugins.admin import group as group_plugin

    monkeypatch.setattr(
        group_plugin,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    monkeypatch.setattr(
        group_plugin.group_repo,
        "get_group",
        AsyncMock(return_value=_Group()),
    )

    async with app.test_matcher(admin_group) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "[20001|测试群] 当前状态: AUTHORIZED",
            bot=bot,
        )
        ctx.should_finished(admin_group)


@pytest.mark.asyncio
async def test_admin_group_chinese_alias_hits_matcher(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_group_message_event("#群组管理 状态 20001", user_id=SUPERUSER_ID)

    from src.plugins.admin import group as group_plugin

    monkeypatch.setattr(
        group_plugin,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    monkeypatch.setattr(
        group_plugin.group_repo,
        "get_group",
        AsyncMock(return_value=_Group()),
    )

    async with app.test_matcher(admin_group) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "[20001|测试群] 当前状态: AUTHORIZED",
            bot=bot,
        )
        ctx.should_finished(admin_group)


@pytest.mark.asyncio
async def test_admin_group_space_alias_hits_matcher(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_group_message_event("#admin group status 20001", user_id=SUPERUSER_ID)

    from src.plugins.admin import group as group_plugin

    monkeypatch.setattr(
        group_plugin,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    monkeypatch.setattr(
        group_plugin.group_repo,
        "get_group",
        AsyncMock(return_value=_Group()),
    )

    async with app.test_matcher(admin_group) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "[20001|测试群] 当前状态: AUTHORIZED",
            bot=bot,
        )
        ctx.should_finished(admin_group)


@pytest.mark.asyncio
async def test_admin_group_sync_members_all_invokes_runner(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event(
        "#admin.group sync-members-all",
        user_id=SUPERUSER_ID,
    )

    from src.plugins.admin import group as group_plugin

    monkeypatch.setattr(group_plugin, "get_active_sync_members_all_state", lambda: None)
    monkeypatch.setattr(
        group_plugin,
        "deliver_message_plan",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        group_plugin,
        "deliver_admin_notification_plan",
        AsyncMock(return_value=()),
    )
    monkeypatch.setattr(
        group_plugin,
        "run_sync_members_for_all_groups",
        AsyncMock(
            return_value=SimpleNamespace(
                total_groups=2,
                succeeded=2,
                failed=0,
                skipped=0,
            )
        ),
    )

    async with app.test_matcher(admin_group) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "群成员全量同步已结束。\n总群数：2\n成功：2\n失败：0\n跳过：0",
            bot=bot,
        )
        ctx.should_finished(admin_group)


@pytest.mark.asyncio
async def test_admin_group_sync_members_all_returns_running_summary(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event(
        "#admin.group sync-members-all",
        user_id=SUPERUSER_ID,
    )

    from src.plugins.admin import group as group_plugin

    monkeypatch.setattr(
        group_plugin,
        "get_active_sync_members_all_state",
        lambda: object(),
    )
    monkeypatch.setattr(
        group_plugin,
        "build_sync_members_all_running_summary",
        lambda state: "已有群成员全量同步任务正在执行。",
    )
    monkeypatch.setattr(
        group_plugin,
        "run_sync_members_for_all_groups",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(admin_group) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "已有群成员全量同步任务正在执行。",
            bot=bot,
        )
        ctx.should_finished(admin_group)


@pytest.mark.asyncio
async def test_admin_i18n_chinese_alias_hits_matcher(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event("#语言管理 list", user_id=SUPERUSER_ID)

    from src.plugins.admin import i18n as i18n_plugin

    monkeypatch.setattr(
        i18n_plugin,
        "i18n_repo",
        type(
            "_Repo",
            (),
            {"list_group_locales": AsyncMock(return_value=[("20001", "zh-CN")])},
        )(),
    )

    async with app.test_matcher(admin_i18n) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "===== 群级语言覆盖 =====\n- 20001: zh-CN",
            bot=bot,
        )
        ctx.should_finished(admin_i18n)


@pytest.mark.asyncio
async def test_admin_user_dot_form_hits_matcher(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event(
        "#admin.user status 12345",
        user_id=SUPERUSER_ID,
    )

    from src.plugins.admin import user as user_plugin

    monkeypatch.setattr(
        user_plugin,
        "resolve_user_name",
        AsyncMock(return_value="测试用户"),
    )
    monkeypatch.setattr(
        user_plugin.user_repo,
        "get_user",
        AsyncMock(return_value=_User()),
    )
    monkeypatch.setattr(
        user_plugin.blacklist_repo,
        "get_blacklist",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(admin_user) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "[12345|测试用户] 状态: 正常",
            bot=bot,
        )
        ctx.should_finished(admin_user)


@pytest.mark.asyncio
async def test_admin_invite_dot_form_hits_matcher(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event("#admin.invite list", user_id=SUPERUSER_ID)

    from src.plugins.admin import invite as invite_plugin

    monkeypatch.setattr(
        invite_plugin,
        "handle_list",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_finished(admin_invite)


@pytest.mark.asyncio
async def test_admin_invite_list_sends_image_via_delivery(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event("#admin.invite list", user_id=SUPERUSER_ID)

    from src.plugins.admin import invite as invite_plugin

    monkeypatch.setattr(
        invite_plugin.invite_repo,
        "get_by_status",
        AsyncMock(
            return_value=[
                type(
                    "_Invite",
                    (),
                    {
                        "id": 1,
                        "created_at": 1700000000,
                        "flag": "flag-1",
                        "group": type(
                            "_Group",
                            (),
                            {"group_name": "测试群", "group_id": "20001"},
                        )(),
                        "inviter": type(
                            "_Inviter",
                            (),
                            {"user_name": "测试用户", "user_id": "12345"},
                        )(),
                    },
                )()
            ]
        ),
    )
    monkeypatch.setattr(
        invite_plugin,
        "generate_invitation_image_bytes",
        AsyncMock(return_value=b"fake-image"),
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_asset",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_private_msg",
            {
                "user_id": SUPERUSER_ID,
                "message": empty_message() + MessageSegment.image(b"fake-image"),
            },
            result={"message_id": 1},
        )
        ctx.should_finished(admin_invite)


@pytest.mark.asyncio
async def test_admin_invite_log_returns_i18n_notice(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event("#admin.invite log", user_id=SUPERUSER_ID)
    monkeypatch.setattr(
        message_asset_repo,
        "get_asset",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_private_msg",
            {
                "user_id": SUPERUSER_ID,
                "message": text_message(tr("zh-CN", "admin.invite.log.unavailable")),
            },
            result={"message_id": 1},
        )
        ctx.should_finished(admin_invite)


@pytest.mark.asyncio
async def test_admin_invite_approve_reply_without_reply_finishes_safely() -> None:
    from src.plugins.admin import invite as invite_plugin

    event = build_private_message_event("y", user_id=SUPERUSER_ID)
    bot = SimpleNamespace(self_id="99999")

    await _run_reply_handler(
        invite_plugin.approve_matcher.handlers[0].call,
        bot=bot,
        event=event,
    )


@pytest.mark.asyncio
async def test_admin_invite_reject_reply_without_reply_finishes_safely() -> None:
    from src.plugins.admin import invite as invite_plugin

    event = build_private_message_event("n", user_id=SUPERUSER_ID)
    bot = SimpleNamespace(self_id="99999")

    await _run_reply_handler(
        invite_plugin.reject_matcher.handlers[0].call,
        bot=bot,
        event=event,
    )


@pytest.mark.asyncio
async def test_admin_invite_reply_persists_operator_when_approving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.admin import invite as invite_plugin

    event = attach_reply_message(
        build_private_message_event("y", user_id=SUPERUSER_ID),
        MessageSegment.text("邀请通知"),
    )
    bot = SimpleNamespace(
        self_id="99999",
        set_group_add_request=AsyncMock(),
    )
    invitation = SimpleNamespace(
        id=7,
        flag="flag-7",
        status=SimpleNamespace(is_processed=False),
        group=SimpleNamespace(
            status=GroupStatus.UNAUTHORIZED,
            group_name="测试群",
        ),
        group_id="20001",
        inviter=SimpleNamespace(user_name="邀请者"),
    )
    matcher = SimpleNamespace()

    monkeypatch.setattr(
        invite_plugin.invite_repo,
        "get_by_message_id",
        AsyncMock(return_value=invitation),
    )
    monkeypatch.setattr(
        invite_plugin,
        "resolve_user_name",
        AsyncMock(return_value="超管"),
    )
    save_user_mock = AsyncMock()
    monkeypatch.setattr(invite_plugin.user_repo, "save_user", save_user_mock)
    update_status_mock = AsyncMock()
    monkeypatch.setattr(invite_plugin.invite_repo, "update_status", update_status_mock)
    monkeypatch.setattr(
        invite_plugin.group_repo,
        "update_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        invite_plugin,
        "deliver_message_plan",
        AsyncMock(return_value=None),
    )

    await invite_plugin.handle_invitation(
        invite_plugin.InviteContext(
            bot=cast(Bot, bot),
            event=event,
            matcher=matcher,  # type: ignore[arg-type]
            approve=True,
            msg_id="90001",
            operator_id=str(SUPERUSER_ID),
            locale="zh-CN",
        )
    )

    save_user_mock.assert_awaited_once()
    update_status_mock.assert_awaited_once_with(
        invitation_id=7,
        status=invite_plugin.InvitationStatus.APPROVED,
        operator_id=str(SUPERUSER_ID),
    )


@pytest.mark.asyncio
async def test_admin_invite_processed_message_handles_missing_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.admin import invite as invite_plugin

    event = build_private_message_event(
        "#admin.invite ignore -f flag-9",
        user_id=SUPERUSER_ID,
    )
    matcher = SimpleNamespace()
    invitation = SimpleNamespace(
        id=9,
        flag="flag-9",
        status=SimpleNamespace(is_processed=True),
        operator=None,
        operator_id="42",
        group=SimpleNamespace(group_name="测试群"),
        group_id="20001",
        inviter=SimpleNamespace(user_name="邀请者"),
    )
    send_text_mock = AsyncMock()

    monkeypatch.setattr(
        invite_plugin.invite_repo,
        "get_by_flag",
        AsyncMock(return_value=invitation),
    )
    monkeypatch.setattr(invite_plugin, "_send_reusable_text", send_text_mock)

    result = await invite_plugin.handle_invitation(
        invite_plugin.InviteContext(
            bot=cast(Bot, SimpleNamespace(self_id="99999")),
            event=event,
            matcher=matcher,  # type: ignore[arg-type]
            approve=False,
            flag="flag-9",
            locale="zh-CN",
        )
    )

    assert result is False
    send_text_mock.assert_awaited_once()
    await_args = send_text_mock.await_args
    assert await_args is not None
    assert "42" in await_args.kwargs["message"]


@pytest.mark.asyncio
async def test_admin_backup_chinese_alias_hits_matcher(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event("#备份管理 check", user_id=SUPERUSER_ID)

    class _Service:
        async def list_snapshots(self) -> list[object]:
            return []

    from src.plugins.admin import backup as backup_plugin

    monkeypatch.setattr(
        backup_plugin,
        "build_backup_service_from_config",
        lambda: _Service(),
    )

    async with app.test_matcher(admin_backup) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            tr("zh-CN", "admin.backup.check.empty"),
            bot=bot,
        )
        ctx.should_finished(admin_backup)
