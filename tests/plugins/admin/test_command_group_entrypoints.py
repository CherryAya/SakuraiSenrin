from __future__ import annotations

import sys
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
                "message": text_message(
                    tr("zh-CN", "admin.invite.log.unavailable")
                ),
            },
            result={"message_id": 1},
        )
        ctx.should_finished(admin_invite)


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
