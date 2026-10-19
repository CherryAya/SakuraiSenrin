from __future__ import annotations

import asyncio
from dataclasses import dataclass
import sys
from typing import Literal
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import (
    ActionFailed,
    Bot,
    Message,
    MessageSegment,
    NetworkError,
)
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebug import App
from nonebug.mixin.process import MatcherContext
import pytest

if nonebot.get_plugin("admin") is None:
    sys.modules.pop("src.plugins.admin", None)
    nonebot.load_plugin("src.plugins.admin")

from src.database.core.consts import GroupStatus, InvitationStage, InvitationStatus
from src.database.core.tables import Group, Invitation, User
from src.lib.i18n.catalog import tr
from src.lib.message_assets import message_asset_repo
from src.lib.messages import text_message
from src.lib.reply_router import reply_context_repo
from src.plugins.admin import invite as invite_plugin
from tests.plugins.water.helpers import (
    attach_reply_message,
    build_private_message_event,
)


@dataclass
class _InvitationState:
    invitation: Invitation
    save_user: AsyncMock
    update_status: AsyncMock
    update_group: AsyncMock
    mark_joined: AsyncMock


@pytest.fixture
def invitation_state(monkeypatch: pytest.MonkeyPatch) -> _InvitationState:
    invitation = Invitation(
        id=7,
        group_id="20001",
        inviter_id="10001",
        flag="flag-7",
        sub_type=InvitationStage.INVITED,
        status=InvitationStatus.PENDING,
        group=Group(
            group_id="20001",
            group_name="测试群",
            status=GroupStatus.UNAUTHORIZED,
        ),
        inviter=User(user_id="10001", user_name="邀请者"),
    )

    async def update_invitation(
        invitation_id: int,
        status: InvitationStatus,
        operator_id: str | None = None,
    ) -> Invitation:
        assert invitation_id == invitation.id
        invitation.status = status
        invitation.operator_id = operator_id
        if operator_id is not None:
            invitation.operator = User(user_id=operator_id, user_name="超管")
        return invitation

    state = _InvitationState(
        invitation=invitation,
        save_user=AsyncMock(),
        update_status=AsyncMock(side_effect=update_invitation),
        update_group=AsyncMock(),
        mark_joined=AsyncMock(),
    )
    monkeypatch.setattr(
        invite_plugin.invite_repo,
        "get_by_flag",
        AsyncMock(return_value=invitation),
    )
    monkeypatch.setattr(
        invite_plugin.invite_repo,
        "get_by_id",
        AsyncMock(return_value=invitation),
    )
    monkeypatch.setattr(
        invite_plugin.invite_repo,
        "get_by_group_id",
        AsyncMock(return_value=invitation),
    )
    monkeypatch.setattr(invite_plugin.invite_repo, "update_status", state.update_status)
    monkeypatch.setattr(invite_plugin.invite_repo, "mark_joined", state.mark_joined)
    monkeypatch.setattr(invite_plugin.group_repo, "update_status", state.update_group)
    monkeypatch.setattr(invite_plugin.user_repo, "save_user", state.save_user)
    monkeypatch.setattr(
        invite_plugin, "resolve_user_name", AsyncMock(return_value="超管")
    )
    monkeypatch.setattr(message_asset_repo, "get_asset", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "src.hooks.processor._runtime_sync", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "src.hooks.processor._runtime_check", AsyncMock(return_value=None)
    )
    return state


def _success_message(
    *,
    approve: bool,
    kind: Literal["platform", "local"],
    message_id: int = 1,
) -> Message:
    if kind == "local":
        message = tr(
            "zh-CN",
            "admin.invite.authorization_done",
            action=tr(
                "zh-CN",
                "admin.invite.action.authorize"
                if approve
                else "admin.invite.action.leave",
            ),
            invitation_id=7,
            group_id="20001",
            group_name="测试群",
            inviter_name="邀请者",
        )
    else:
        message = tr(
            "zh-CN",
            "admin.invite.action_done",
            action=tr(
                "zh-CN",
                "admin.invite.action.approve"
                if approve
                else "admin.invite.action.reject",
            ),
            invitation_id=7,
            group_id="20001",
            group_name="测试群",
            inviter_name="邀请者",
            flag="flag-7",
        )
    return MessageSegment.reply(message_id) + text_message(message)


def _expect_private_message(
    ctx: MatcherContext,
    event: MessageEvent,
    message: Message | str,
    *,
    bot: Bot,
) -> None:
    # 统一消息计划使用协议 API，matcher.send 的断言无法覆盖这条正式投递链。
    ctx.should_call_api(
        "send_private_msg",
        {
            "user_id": event.user_id,
            "message": text_message(message) if isinstance(message, str) else message,
        },
        result={"message_id": 99000},
        adapter=bot.adapter,
    )


def _assert_processed(state: _InvitationState, *, approve: bool) -> None:
    state.update_status.assert_awaited_once_with(
        invitation_id=7,
        status=InvitationStatus.APPROVED if approve else InvitationStatus.REJECTED,
        operator_id="1",
    )
    state.update_group.assert_awaited_once_with(
        group_id="20001",
        status=GroupStatus.AUTHORIZED if approve else GroupStatus.UNAUTHORIZED,
    )
    state.save_user.assert_awaited_once()


def _assert_pending(state: _InvitationState) -> None:
    assert state.invitation.status == InvitationStatus.PENDING
    state.update_status.assert_not_awaited()
    state.update_group.assert_not_awaited()
    state.save_user.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
async def test_verified_invitation_calls_platform_api(
    app: App,
    invitation_state: _InvitationState,
    approve: bool,
) -> None:
    action = "approve" if approve else "reject"
    event = build_private_message_event(f"#admin.invite {action} -f flag-7", user_id=1)

    async with app.test_matcher(invite_plugin.admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-7", "sub_type": "invite", "approve": approve},
            result=None,
        )
        _expect_private_message(
            ctx, event, _success_message(approve=approve, kind="platform"), bot=bot
        )
        ctx.should_finished(invite_plugin.admin_invite)

    _assert_processed(invitation_state, approve=approve)
    invitation_state.mark_joined.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
@pytest.mark.parametrize(
    "stage", [InvitationStage.INVITED, InvitationStage.JOINED, "invite:manual-review"]
)
@pytest.mark.parametrize("flag", [None, "", " \t", "flag-7"])
async def test_already_joined_invitation_only_authorizes_or_leaves(
    app: App,
    invitation_state: _InvitationState,
    approve: bool,
    stage: str,
    flag: str | None,
) -> None:
    invitation_state.invitation.sub_type = stage
    invitation_state.invitation.flag = flag
    action = "approve" if approve else "reject"
    event = build_private_message_event(f"#admin.invite {action} -g 20001", user_id=1)

    async with app.test_matcher(invite_plugin.admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("get_group_list", {}, result=[{"group_id": 20001}])
        if not approve:
            ctx.should_call_api("set_group_leave", {"group_id": 20001}, result=None)
        _expect_private_message(
            ctx, event, _success_message(approve=approve, kind="local"), bot=bot
        )
        ctx.should_finished(invite_plugin.admin_invite)

    _assert_processed(invitation_state, approve=approve)
    invitation_state.mark_joined.assert_awaited_once_with(7)


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
@pytest.mark.parametrize(
    ("stage", "flag"),
    [
        ("invite", "flag-7"),
        ("invite:manual-review", "flag-7"),
        ("add", "flag-7"),
        (InvitationStage.JOINED, "flag-7"),
        (InvitationStage.INVITED, None),
        (InvitationStage.INVITED, ""),
        (InvitationStage.INVITED, " \t"),
        (InvitationStage.INVITED, "\u3000"),
    ],
)
async def test_absent_bot_never_uses_legacy_or_joined_request_flags(
    app: App,
    invitation_state: _InvitationState,
    approve: bool,
    stage: str,
    flag: str | None,
) -> None:
    invitation_state.invitation.sub_type = stage
    invitation_state.invitation.flag = flag
    action = "approve" if approve else "reject"
    event = build_private_message_event(f"#admin.invite {action} -f flag-7", user_id=1)

    async with app.test_matcher(invite_plugin.admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("get_group_list", {}, result=[])
        _expect_private_message(
            ctx, event, tr("zh-CN", "admin.invite.request_unavailable"), bot=bot
        )
        ctx.should_finished(invite_plugin.admin_invite)

    _assert_pending(invitation_state)
    invitation_state.mark_joined.assert_not_awaited()


@pytest.mark.asyncio
async def test_membership_failure_keeps_invitation_pending(
    app: App,
    invitation_state: _InvitationState,
) -> None:
    event = build_private_message_event("#admin.invite approve -f flag-7", user_id=1)

    async with app.test_matcher(invite_plugin.admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "get_group_list", {}, exception=ActionFailed(retcode=100, wording="offline")
        )
        _expect_private_message(
            ctx, event, tr("zh-CN", "admin.invite.state_unavailable"), bot=bot
        )
        ctx.should_finished(invite_plugin.admin_invite)

    _assert_pending(invitation_state)


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
@pytest.mark.parametrize("network", [False, True])
async def test_platform_failure_rechecks_and_handles_joined_bot_locally(
    app: App,
    invitation_state: _InvitationState,
    approve: bool,
    network: bool,
) -> None:
    action = "approve" if approve else "reject"
    event = build_private_message_event(f"#admin.invite {action} -f flag-7", user_id=1)

    async with app.test_matcher(invite_plugin.admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-7", "sub_type": "invite", "approve": approve},
            exception=NetworkError("timeout")
            if network
            else ActionFailed(retcode=100, wording="matching group request not found"),
        )
        ctx.should_call_api("get_group_list", {}, result=[{"group_id": 20001}])
        if not approve:
            ctx.should_call_api("set_group_leave", {"group_id": 20001}, result=None)
        _expect_private_message(
            ctx, event, _success_message(approve=approve, kind="local"), bot=bot
        )
        ctx.should_finished(invite_plugin.admin_invite)

    _assert_processed(invitation_state, approve=approve)
    invitation_state.mark_joined.assert_awaited_once_with(7)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wording", "unavailable_request"),
    [
        ("matching group request not found", True),
        ("already agree msg", True),
        ("permission denied", False),
    ],
)
async def test_platform_failure_without_membership_keeps_invitation_pending(
    app: App,
    invitation_state: _InvitationState,
    wording: str,
    unavailable_request: bool,
) -> None:
    event = build_private_message_event("#admin.invite approve -f flag-7", user_id=1)

    async with app.test_matcher(invite_plugin.admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-7", "sub_type": "invite", "approve": True},
            exception=ActionFailed(retcode=100, wording=wording),
        )
        ctx.should_call_api("get_group_list", {}, result=[])
        _expect_private_message(
            ctx,
            event,
            tr(
                "zh-CN",
                "admin.invite.request_unavailable"
                if unavailable_request
                else "admin.invite.action_failed",
            ),
            bot=bot,
        )
        ctx.should_finished(invite_plugin.admin_invite)

    _assert_pending(invitation_state)
    invitation_state.mark_joined.assert_not_awaited()


@pytest.mark.asyncio
async def test_membership_recheck_failure_keeps_invitation_pending(
    app: App,
    invitation_state: _InvitationState,
) -> None:
    event = build_private_message_event("#admin.invite approve -f flag-7", user_id=1)

    async with app.test_matcher(invite_plugin.admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-7", "sub_type": "invite", "approve": True},
            exception=ActionFailed(
                retcode=100, wording="matching group request not found"
            ),
        )
        ctx.should_call_api(
            "get_group_list", {}, exception=ActionFailed(retcode=100, wording="offline")
        )
        _expect_private_message(
            ctx, event, tr("zh-CN", "admin.invite.state_unavailable"), bot=bot
        )
        ctx.should_finished(invite_plugin.admin_invite)

    _assert_pending(invitation_state)


@pytest.mark.asyncio
@pytest.mark.parametrize("network", [False, True])
async def test_leave_failure_keeps_invitation_pending(
    app: App,
    invitation_state: _InvitationState,
    network: bool,
) -> None:
    event = build_private_message_event("#admin.invite reject -f flag-7", user_id=1)

    async with app.test_matcher(invite_plugin.admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("get_group_list", {}, result=[{"group_id": 20001}])
        ctx.should_call_api(
            "set_group_leave",
            {"group_id": 20001},
            exception=NetworkError("timeout")
            if network
            else ActionFailed(retcode=100, wording="permission denied"),
        )
        ctx.should_call_api("get_group_list", {}, result=[{"group_id": 20001}])
        _expect_private_message(
            ctx, event, tr("zh-CN", "admin.invite.action_failed"), bot=bot
        )
        ctx.should_finished(invite_plugin.admin_invite)

    _assert_pending(invitation_state)
    invitation_state.mark_joined.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_banned_group_cannot_be_authorized(
    app: App,
    invitation_state: _InvitationState,
) -> None:
    invitation_state.invitation.group.status = GroupStatus.BANNED
    event = build_private_message_event("#admin.invite approve -f flag-7", user_id=1)

    async with app.test_matcher(invite_plugin.admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        _expect_private_message(
            ctx, event, tr("zh-CN", "admin.invite.need_unban_first"), bot=bot
        )
        ctx.should_finished(invite_plugin.admin_invite)

    _assert_pending(invitation_state)


async def _seed_invitation_reply(message_id: int) -> None:
    await reply_context_repo.upsert_context(
        context_kind="admin.invite.approval",
        message_id=str(message_id),
        message_hash=f"invite-test-{message_id}",
        sender_bot_id="99999",
        origin_message_type="private",
        origin_target_id="1",
        source_kind="test_admin_invite",
        payload={"invitation_id": 7},
    )


@pytest.mark.asyncio
async def test_old_and_new_reply_contexts_resolve_one_invitation_once(
    app: App,
    invitation_state: _InvitationState,
) -> None:
    await _seed_invitation_reply(90001)
    await _seed_invitation_reply(90002)
    first = attach_reply_message(
        build_private_message_event("y", user_id=1), message_id=90001
    )
    second = attach_reply_message(
        build_private_message_event("y", user_id=1, message_id=2), message_id=90002
    )
    first.to_me = True
    second.to_me = True

    async with app.test_matcher(invite_plugin.approve_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, first)
        ctx.should_call_api("get_group_list", {}, result=[{"group_id": 20001}])
        _expect_private_message(
            ctx, first, _success_message(approve=True, kind="local"), bot=bot
        )
        ctx.receive_event(bot, second)
        _expect_private_message(
            ctx,
            second,
            tr(
                "zh-CN",
                "admin.invite.processed",
                operator_name="超管",
                operator_id="1",
                status=InvitationStatus.APPROVED,
                group_id="20001",
                group_name="测试群",
                inviter_name="邀请者",
                flag="flag-7",
            ),
            bot=bot,
        )

    _assert_processed(invitation_state, approve=True)
    invitation_state.mark_joined.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_concurrent_approvals_recheck_pending_state_under_lock(
    app: App,
    invitation_state: _InvitationState,
) -> None:
    first = build_private_message_event("y", user_id=1)
    second = build_private_message_event("y", user_id=1, message_id=2)

    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-7", "sub_type": "invite", "approve": True},
            result=None,
        )
        results = await asyncio.gather(
            *(
                invite_plugin.handle_invitation(
                    invite_plugin.InviteContext(
                        bot=bot,
                        event=event,
                        matcher=invite_plugin.approve_matcher(),
                        approve=True,
                        locale="zh-CN",
                        invitation_id=7,
                        operator_id="1",
                        silent=True,
                    )
                )
                for event in (first, second)
            )
        )

    assert sorted(results) == [False, True]
    _assert_processed(invitation_state, approve=True)


@pytest.mark.asyncio
async def test_unexpected_platform_error_propagates_without_marking_success(
    app: App,
    invitation_state: _InvitationState,
) -> None:
    event = build_private_message_event("y", user_id=1)

    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-7", "sub_type": "invite", "approve": True},
            exception=RuntimeError("unexpected adapter failure"),
        )
        with pytest.raises(RuntimeError, match="unexpected adapter failure"):
            await invite_plugin.handle_invitation(
                invite_plugin.InviteContext(
                    bot=bot,
                    event=event,
                    matcher=invite_plugin.approve_matcher(),
                    approve=True,
                    locale="zh-CN",
                    invitation_id=7,
                    operator_id="1",
                    silent=True,
                )
            )

    _assert_pending(invitation_state)
    invitation_state.mark_joined.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [True, False])
async def test_non_superuser_cannot_process_invitation(
    app: App,
    invitation_state: _InvitationState,
    reply: bool,
) -> None:
    if reply:
        await _seed_invitation_reply(90003)
        event = attach_reply_message(
            build_private_message_event("y", user_id=10002), message_id=90003
        )
        event.to_me = True
        matcher = invite_plugin.approve_matcher
    else:
        event = build_private_message_event(
            "#admin.invite approve -f flag-7", user_id=10002
        )
        matcher = invite_plugin.admin_invite

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_not_pass_permission(matcher)

    _assert_pending(invitation_state)


@pytest.mark.parametrize("state", ["left", "unknown"])
async def test_leave_timeout_only_succeeds_after_confirmed_absence(
    app: App, invitation_state: _InvitationState, state: str
) -> None:
    event = build_private_message_event("#admin.invite reject -g 20001", user_id=1)
    async with app.test_matcher(invite_plugin.admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("get_group_list", {}, result=[{"group_id": 20001}])
        ctx.should_call_api(
            "set_group_leave", {"group_id": 20001}, exception=NetworkError("timeout")
        )
        if state == "left":
            ctx.should_call_api("get_group_list", {}, result=[])
            _expect_private_message(
                ctx, event, _success_message(approve=False, kind="local"), bot=bot
            )
        else:
            ctx.should_call_api("get_group_list", {}, exception=NetworkError("offline"))
            _expect_private_message(
                ctx, event, tr("zh-CN", "admin.invite.state_unavailable"), bot=bot
            )
        ctx.should_finished(invite_plugin.admin_invite)
    if state == "left":
        _assert_processed(invitation_state, approve=False)
    else:
        _assert_pending(invitation_state)


@pytest.mark.parametrize("approve", [True, False])
async def test_platform_timeout_without_join_keeps_invitation_pending(
    app: App, invitation_state: _InvitationState, approve: bool
) -> None:
    action = "approve" if approve else "reject"
    event = build_private_message_event(f"#admin.invite {action} -f flag-7", user_id=1)
    async with app.test_matcher(invite_plugin.admin_invite) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-7", "sub_type": "invite", "approve": approve},
            exception=NetworkError("timeout"),
        )
        ctx.should_call_api("get_group_list", {}, result=[])
        _expect_private_message(
            ctx, event, tr("zh-CN", "admin.invite.action_failed"), bot=bot
        )
        ctx.should_finished(invite_plugin.admin_invite)
    _assert_pending(invitation_state)


async def test_admin_approval_keeps_opaque_flag_verbatim(
    app: App, invitation_state: _InvitationState
) -> None:
    invitation_state.invitation.flag = " opaque token "
    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": " opaque token ", "sub_type": "invite", "approve": True},
        )
        result = await invite_plugin.handle_invitation(
            invite_plugin.InviteContext(
                bot=bot,
                event=build_private_message_event("y", user_id=1),
                matcher=invite_plugin.approve_matcher(),
                approve=True,
                locale="zh-CN",
                invitation_id=7,
                operator_id="1",
                silent=True,
            )
        )
    assert result is True
    _assert_processed(invitation_state, approve=True)
