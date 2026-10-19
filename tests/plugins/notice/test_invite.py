from __future__ import annotations

from dataclasses import dataclass
import importlib
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import ActionFailed, Bot, NetworkError
from nonebot.matcher import Matcher
from nonebot.matcher import matchers as matcher_manager
from nonebug import App
import pytest

nonebot.init(
    superusers={"1"},
    IGNORED_USERS=set(),
    MAIN_GROUP_ID="10001",
    GITHUB_TOKEN="test-token",
    GITHUB_REPO="owner/repo",
    GITHUB_BRANCH="main",
    WORDBANK_MEDIA_PROVIDER="local",
    command_start={"#", "/"},
    command_sep={"."},
)
if nonebot.get_plugin("notice") is None:
    sys.modules.pop("src.plugins.notice.invite", None)
    sys.modules.pop("src.plugins.notice.group", None)
    sys.modules.pop("src.plugins.notice.user", None)
    sys.modules.pop("src.plugins.notice", None)
    nonebot.load_plugin("src.plugins.notice")

notice_invite_plugin = importlib.import_module("src.plugins.notice.invite")

from src.database.consts import WritePolicy
from src.database.core.consts import GroupStatus, InvitationStage, InvitationStatus
from src.lib.message_plan import render_delivery_plan_messages
from tests.plugins.notice.helpers import (
    MISSING_INVITED_ID,
    build_bot_join_event,
    build_group_request_event,
)


def _get_invite_matcher(event_type: str) -> type[Matcher]:
    for matchers in matcher_manager.values():
        for matcher in matchers:
            plugin = getattr(matcher, "plugin", None)
            if (
                getattr(plugin, "module_name", None) == "src.plugins.notice.invite"
                and matcher.type == event_type
            ):
                return matcher
    raise AssertionError(f"notice invite {event_type} matcher not found")


def _decision(
    *,
    joined: bool = False,
    should_notify: bool = True,
    replayed: bool = False,
    status: InvitationStatus = InvitationStatus.PENDING,
) -> SimpleNamespace:
    return SimpleNamespace(
        invitation_id=12,
        group_id="20001",
        inviter_id="10001",
        flag=None if joined else "flag-1",
        sub_type=InvitationStage.JOINED if joined else InvitationStage.INVITED,
        status=status,
        should_notify=should_notify,
        replayed=replayed,
    )


@dataclass
class NoticeIO:
    group: SimpleNamespace
    group_get: AsyncMock
    user_get: AsyncMock
    save_group: AsyncMock
    save_user: AsyncMock
    ensure_group: AsyncMock
    ensure_user: AsyncMock
    reconcile: AsyncMock
    mark_joined: AsyncMock
    update_status: AsyncMock
    private_notice: AsyncMock
    admin_notice: AsyncMock
    add_message_record: AsyncMock
    record_reply_context: AsyncMock


@pytest.fixture
def notice_io(monkeypatch: pytest.MonkeyPatch) -> NoticeIO:
    monkeypatch.setattr("src.hooks.processor._runtime_sync", AsyncMock())
    monkeypatch.setattr("src.hooks.processor._runtime_check", AsyncMock())
    group = SimpleNamespace(status=GroupStatus.UNAUTHORIZED, display_name="测试群")
    io = NoticeIO(
        group=group,
        group_get=AsyncMock(return_value=group),
        user_get=AsyncMock(return_value=SimpleNamespace(display_name="邀请者")),
        save_group=AsyncMock(),
        save_user=AsyncMock(),
        ensure_group=AsyncMock(),
        ensure_user=AsyncMock(),
        reconcile=AsyncMock(return_value=_decision()),
        mark_joined=AsyncMock(return_value=_decision(joined=True)),
        update_status=AsyncMock(),
        private_notice=AsyncMock(),
        admin_notice=AsyncMock(
            return_value=SimpleNamespace(results=[SimpleNamespace(message_id="99")])
        ),
        add_message_record=AsyncMock(),
        record_reply_context=AsyncMock(),
    )
    for repo, method, mock in (
        (notice_invite_plugin.group_repo, "get_group", io.group_get),
        (notice_invite_plugin.group_repo, "save_group", io.save_group),
        (notice_invite_plugin.group_repo, "ensure_persisted", io.ensure_group),
        (notice_invite_plugin.user_repo, "get_user", io.user_get),
        (notice_invite_plugin.user_repo, "save_user", io.save_user),
        (notice_invite_plugin.user_repo, "ensure_persisted", io.ensure_user),
        (notice_invite_plugin.invite_repo, "reconcile_event", io.reconcile),
        (notice_invite_plugin.invite_repo, "mark_joined", io.mark_joined),
        (notice_invite_plugin.invite_repo, "update_status", io.update_status),
        (notice_invite_plugin.invite_repo, "add_message_record", io.add_message_record),
    ):
        monkeypatch.setattr(repo, method, mock)
    monkeypatch.setattr(
        notice_invite_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(notice_invite_plugin, "send_private_i18n", io.private_notice)
    monkeypatch.setattr(
        notice_invite_plugin,
        "record_reply_context_from_send_result",
        io.record_reply_context,
    )

    async def _deliver(
        bot: Bot,
        *,
        plan: object,
        on_delivered: Any = None,
    ) -> tuple[object, ...]:
        target = SimpleNamespace(channel="private_superuser", target_id="1")
        result = await io.admin_notice(bot, plan=plan, target=target)
        if on_delivered is not None:
            await on_delivered(target, result)
        return (result,)

    monkeypatch.setattr(
        notice_invite_plugin, "deliver_admin_notification_plan", _deliver
    )
    return io


@pytest.mark.parametrize(
    "group_status",
    [GroupStatus.AUTHORIZED, GroupStatus.UNAUTHORIZED, GroupStatus.BANNED],
)
@pytest.mark.parametrize(
    ("invited_id", "sub_type"),
    [
        (10002, "invite"),
        (MISSING_INVITED_ID, "invite"),
        (None, "invite"),
        ("", "invite"),
        ("invalid-target", "invite"),
        (99999.0, "invite"),
        (True, "invite"),
        (0, "invite"),
        (99999, "add"),
    ],
    ids=[
        "other-user",
        "missing",
        "null",
        "empty",
        "invalid",
        "float",
        "bool",
        "zero",
        "add",
    ],
)
async def test_request_guard_rejects_unrelated_or_unverifiable_requests(
    app: App,
    notice_io: NoticeIO,
    group_status: GroupStatus,
    invited_id: object,
    sub_type: str,
) -> None:
    notice_io.group.status = group_status
    matcher = _get_invite_matcher("request")
    event = build_group_request_event(invited_id=invited_id, sub_type=sub_type)

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_not_pass_rule(matcher)

    notice_io.group_get.assert_not_awaited()
    notice_io.ensure_group.assert_not_awaited()
    notice_io.ensure_user.assert_not_awaited()
    notice_io.reconcile.assert_not_awaited()
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_not_awaited()


@pytest.mark.parametrize("invited_id", [99999, "99999"])
@pytest.mark.parametrize(
    "flag", ["flag-1", "slreq:real-bot-invitation", " opaque flag "]
)
async def test_real_bot_invitation_reports_once_and_binds_reply(
    app: App,
    notice_io: NoticeIO,
    invited_id: object,
    flag: str,
) -> None:
    notice_io.reconcile.return_value.flag = flag
    matcher = _get_invite_matcher("request")
    event = build_group_request_event(invited_id=invited_id, flag=flag)

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_pass_rule(matcher)
        ctx.should_call_api("get_group_list", {}, result=[])

    notice_io.reconcile.assert_awaited_once_with(
        group_id="20001",
        inviter_id="10001",
        flag=flag,
        joined=False,
        event_time=event.time,
    )
    notice_io.private_notice.assert_awaited_once()
    notice_io.admin_notice.assert_awaited_once()
    notice_io.add_message_record.assert_awaited_once_with(
        invitation_id=12, message_id="99"
    )
    reply_args = notice_io.record_reply_context.await_args
    assert reply_args is not None
    assert reply_args.kwargs["context_spec"].payload["invitation_id"] == 12
    assert reply_args.kwargs["context_spec"].payload["flag"] == flag
    notice_args = notice_io.admin_notice.await_args
    assert notice_args is not None
    assert notice_args.kwargs["plan"].allow_asset_reuse is False


async def test_pending_request_persists_inviter_and_group_before_reconcile(
    app: App,
    notice_io: NoticeIO,
) -> None:
    notice_io.group_get.return_value = None
    notice_io.user_get.return_value = None
    matcher = _get_invite_matcher("request")
    event = build_group_request_event()

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("get_group_list", {}, result=[])

    notice_io.save_user.assert_awaited_once_with(
        user_id="10001", user_name="10001", policy=WritePolicy.IMMEDIATE
    )
    notice_io.save_group.assert_awaited_once_with(
        group_id="20001", group_name="20001", policy=WritePolicy.IMMEDIATE
    )
    notice_io.ensure_user.assert_awaited_once_with("10001", "10001")
    notice_io.ensure_group.assert_awaited_once_with("20001", "20001")
    notice_io.reconcile.assert_awaited_once()
    notice_io.admin_notice.assert_awaited_once()


@pytest.mark.parametrize("group_status", [GroupStatus.AUTHORIZED, GroupStatus.DORMANT])
async def test_working_group_invitation_auto_approves_real_bot_only(
    app: App,
    notice_io: NoticeIO,
    group_status: GroupStatus,
) -> None:
    notice_io.group.status = group_status
    matcher = _get_invite_matcher("request")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-1", "sub_type": "invite", "approve": True},
        )
        ctx.should_finished(matcher)

    notice_io.update_status.assert_awaited_once_with(
        12, status=InvitationStatus.APPROVED, operator_id="99999"
    )
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_not_awaited()


async def test_banned_group_invitation_rejects_real_bot_request(
    app: App,
    notice_io: NoticeIO,
) -> None:
    notice_io.group.status = GroupStatus.BANNED
    matcher = _get_invite_matcher("request")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-1", "sub_type": "invite", "approve": False},
        )
        ctx.should_finished(matcher)

    notice_io.update_status.assert_awaited_once_with(
        12, status=InvitationStatus.REJECTED, operator_id="99999"
    )
    notice_io.private_notice.assert_awaited_once()
    private_args = notice_io.private_notice.await_args
    assert private_args is not None
    assert private_args.args[2] == "notice.invite.auto_reject"
    assert private_args.kwargs["main_group_id"] == "10001"
    notice_io.admin_notice.assert_awaited_once()


async def test_working_group_join_notice_marks_processed_without_platform_approval(
    app: App,
    notice_io: NoticeIO,
) -> None:
    notice_io.group.status = GroupStatus.AUTHORIZED
    notice_io.reconcile.return_value = _decision(joined=True)
    matcher = _get_invite_matcher("notice")
    event = build_bot_join_event()

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "get_group_list", {}, result=[{"group_id": 20001, "group_name": "测试群"}]
        )
        ctx.should_finished(matcher)

    notice_io.reconcile.assert_awaited_once_with(
        group_id="20001",
        inviter_id="10001",
        flag=None,
        joined=True,
        event_time=event.time,
    )
    notice_io.update_status.assert_awaited_once_with(
        12, status=InvitationStatus.APPROVED, operator_id="99999"
    )
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_not_awaited()


async def test_banned_group_join_notice_leaves_without_platform_approval(
    app: App,
    notice_io: NoticeIO,
) -> None:
    notice_io.group.status = GroupStatus.BANNED
    notice_io.reconcile.return_value = _decision(joined=True)
    matcher = _get_invite_matcher("notice")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_bot_join_event())
        ctx.should_call_api(
            "get_group_list", {}, result=[{"group_id": 20001, "group_name": "测试群"}]
        )
        ctx.should_call_api("set_group_leave", {"group_id": 20001})
        ctx.should_finished(matcher)

    notice_io.update_status.assert_awaited_once_with(
        12, status=InvitationStatus.REJECTED, operator_id="99999"
    )
    notice_io.private_notice.assert_awaited_once()
    notice_io.admin_notice.assert_awaited_once()


async def test_join_notice_for_other_user_does_not_enter_invitation_chain(
    app: App,
    notice_io: NoticeIO,
) -> None:
    matcher = _get_invite_matcher("notice")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_bot_join_event(user_id=10002))
        ctx.should_not_pass_rule(matcher)

    notice_io.reconcile.assert_not_awaited()
    notice_io.admin_notice.assert_not_awaited()


@pytest.mark.parametrize("joined", [False, True])
async def test_replayed_event_keeps_existing_notification_and_reply_binding(
    app: App,
    notice_io: NoticeIO,
    joined: bool,
) -> None:
    notice_io.reconcile.return_value = _decision(
        joined=joined, should_notify=False, replayed=True
    )
    matcher = _get_invite_matcher("notice" if joined else "request")
    event = build_bot_join_event() if joined else build_group_request_event()

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "get_group_list",
            {},
            result=[{"group_id": 20001, "group_name": "测试群"}] if joined else [],
        )

    notice_io.reconcile.assert_awaited_once()
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_not_awaited()
    notice_io.add_message_record.assert_not_awaited()
    notice_io.record_reply_context.assert_not_awaited()


async def test_late_request_uses_live_membership_without_platform_approval(
    app: App,
    notice_io: NoticeIO,
) -> None:
    notice_io.group.status = GroupStatus.AUTHORIZED
    notice_io.reconcile.return_value = _decision(joined=True)
    matcher = _get_invite_matcher("request")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api(
            "get_group_list",
            {},
            result=[{"group_id": 20001, "group_name": "测试群"}],
        )
        ctx.should_finished(matcher)

    notice_io.update_status.assert_awaited_once_with(
        12, status=InvitationStatus.APPROVED, operator_id="99999"
    )
    notice_io.admin_notice.assert_not_awaited()


async def test_stale_join_notice_cannot_create_local_authorization_pending(
    app: App,
    notice_io: NoticeIO,
) -> None:
    matcher = _get_invite_matcher("notice")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_bot_join_event())
        ctx.should_call_api("get_group_list", {}, result=[])

    notice_io.reconcile.assert_not_awaited()
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_not_awaited()


@pytest.mark.parametrize("request_first", [True, False])
async def test_request_and_join_notice_send_one_notification_in_either_order(
    app: App,
    notice_io: NoticeIO,
    request_first: bool,
) -> None:
    request_matcher = _get_invite_matcher("request")
    notice_matcher = _get_invite_matcher("notice")
    request = build_group_request_event()
    notice = build_bot_join_event()
    events = [request, notice] if request_first else [notice, request]
    notice_io.reconcile.side_effect = [
        _decision(joined=not request_first),
        _decision(joined=True, should_notify=False, replayed=True),
    ]

    async with app.test_matcher([request_matcher, notice_matcher]) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        for index, event in enumerate(events):
            ctx.receive_event(bot, event)
            ctx.should_call_api(
                "get_group_list",
                {},
                result=[] if request_first and index == 0 else [{"group_id": 20001}],
            )

    assert notice_io.reconcile.await_count == 2
    notice_io.private_notice.assert_awaited_once()
    notice_io.admin_notice.assert_awaited_once()
    notice_io.add_message_record.assert_awaited_once_with(
        invitation_id=12, message_id="99"
    )
    notice_io.record_reply_context.assert_awaited_once()


async def test_processed_request_replay_cannot_call_auto_approval_again(
    app: App,
    notice_io: NoticeIO,
) -> None:
    notice_io.group.status = GroupStatus.AUTHORIZED
    notice_io.reconcile.return_value = _decision(
        status=InvitationStatus.APPROVED, should_notify=False, replayed=True
    )
    matcher = _get_invite_matcher("request")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])

    notice_io.update_status.assert_not_awaited()
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_not_awaited()


async def test_expired_auto_approval_request_already_joined_finishes_locally(
    app: App,
    notice_io: NoticeIO,
) -> None:
    notice_io.group.status = GroupStatus.AUTHORIZED
    matcher = _get_invite_matcher("request")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-1", "sub_type": "invite", "approve": True},
            exception=ActionFailed(
                retcode=100, wording="matching group request not found"
            ),
        )
        ctx.should_call_api(
            "get_group_list", {}, result=[{"group_id": 20001, "group_name": "测试群"}]
        )
        ctx.should_finished(matcher)

    notice_io.mark_joined.assert_awaited_once()
    notice_io.update_status.assert_awaited_once_with(
        12, status=InvitationStatus.APPROVED, operator_id="99999"
    )
    notice_io.admin_notice.assert_not_awaited()


@pytest.mark.parametrize(
    "wording", ["matching group request not found", "permission denied"]
)
async def test_failed_auto_approval_does_not_mark_pending_invitation_approved(
    app: App,
    notice_io: NoticeIO,
    wording: str,
) -> None:
    notice_io.group.status = GroupStatus.AUTHORIZED
    matcher = _get_invite_matcher("request")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-1", "sub_type": "invite", "approve": True},
            exception=ActionFailed(retcode=100, wording=wording),
        )
        ctx.should_call_api("get_group_list", {}, result=[])

    notice_io.update_status.assert_not_awaited()
    notice_io.mark_joined.assert_not_awaited()
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_awaited_once()
    notice_io.add_message_record.assert_not_awaited()


async def test_membership_query_failure_never_creates_or_approves_invitation(
    app: App,
    notice_io: NoticeIO,
) -> None:
    notice_io.group.status = GroupStatus.AUTHORIZED
    matcher = _get_invite_matcher("request")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api(
            "get_group_list",
            {},
            exception=ActionFailed(retcode=100, wording="query failed"),
        )

    notice_io.reconcile.assert_not_awaited()
    notice_io.update_status.assert_not_awaited()
    notice_io.private_notice.assert_not_awaited()
    notice_io.add_message_record.assert_not_awaited()
    notice_io.admin_notice.assert_awaited_once()


@pytest.mark.parametrize(
    "response",
    [None, {}, ["invalid-group"], [{"group_id": "invalid"}]],
)
async def test_invalid_membership_response_fails_closed(
    app: App,
    notice_io: NoticeIO,
    response: object,
) -> None:
    matcher = _get_invite_matcher("request")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=response)

    notice_io.reconcile.assert_not_awaited()
    notice_io.update_status.assert_not_awaited()
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_awaited_once()
    notice_io.add_message_record.assert_not_awaited()


async def test_failed_auto_rejection_does_not_mark_pending_invitation_rejected(
    app: App,
    notice_io: NoticeIO,
) -> None:
    notice_io.group.status = GroupStatus.BANNED
    matcher = _get_invite_matcher("request")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-1", "sub_type": "invite", "approve": False},
            exception=ActionFailed(retcode=100, wording="permission denied"),
        )
        ctx.should_call_api("get_group_list", {}, result=[])

    notice_io.update_status.assert_not_awaited()
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_awaited_once()
    notice_io.add_message_record.assert_not_awaited()


async def test_failed_auto_approval_and_membership_recheck_do_not_claim_success(
    app: App,
    notice_io: NoticeIO,
) -> None:
    notice_io.group.status = GroupStatus.AUTHORIZED
    matcher = _get_invite_matcher("request")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-1", "sub_type": "invite", "approve": True},
            exception=ActionFailed(
                retcode=100, wording="matching group request not found"
            ),
        )
        ctx.should_call_api(
            "get_group_list",
            {},
            exception=ActionFailed(retcode=100, wording="query failed"),
        )

    notice_io.mark_joined.assert_not_awaited()
    notice_io.update_status.assert_not_awaited()
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_awaited_once()
    notice_io.add_message_record.assert_not_awaited()


@pytest.mark.parametrize("banned", [False, True])
async def test_inviter_private_failure_does_not_block_admin_notification(
    app: App,
    notice_io: NoticeIO,
    banned: bool,
) -> None:
    notice_io.private_notice.side_effect = ActionFailed(
        retcode=100, wording="private messages unavailable"
    )
    if banned:
        notice_io.group.status = GroupStatus.BANNED
    matcher = _get_invite_matcher("request")
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
        if banned:
            ctx.should_call_api(
                "set_group_add_request",
                {"flag": "flag-1", "sub_type": "invite", "approve": False},
                result=None,
            )
        ctx.should_finished(matcher)
    notice_io.private_notice.assert_awaited_once()
    notice_io.admin_notice.assert_awaited_once()
    if banned:
        notice_io.update_status.assert_awaited_once_with(
            12, status=InvitationStatus.REJECTED, operator_id="99999"
        )
    else:
        notice_io.add_message_record.assert_awaited_once_with(
            invitation_id=12, message_id="99"
        )


@pytest.mark.parametrize(
    "group_status",
    [GroupStatus.AUTHORIZED, GroupStatus.UNAUTHORIZED, GroupStatus.BANNED],
)
async def test_joined_request_replay_after_leaving_never_reuses_platform_flag(
    app: App,
    notice_io: NoticeIO,
    group_status: GroupStatus,
) -> None:
    notice_io.group.status = group_status
    notice_io.reconcile.return_value = _decision(
        joined=True, should_notify=False, replayed=True
    )
    notice_io.reconcile.return_value.flag = "flag-1"
    matcher = _get_invite_matcher("request")
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_finished(matcher)
    notice_io.update_status.assert_not_awaited()
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_not_awaited()


@pytest.mark.parametrize("flag", ["", " ", "\t\r\n", "\u3000"])
@pytest.mark.parametrize(
    "status", [GroupStatus.AUTHORIZED, GroupStatus.UNAUTHORIZED, GroupStatus.BANNED]
)
async def test_blank_group_request_never_queries_writes_notifies_or_approves(
    app: App, notice_io: NoticeIO, flag: str, status: GroupStatus
) -> None:
    notice_io.group.status = status
    matcher = _get_invite_matcher("request")
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event(flag=flag))
        ctx.should_not_pass_rule(matcher)
    notice_io.ensure_group.assert_not_awaited()
    notice_io.ensure_user.assert_not_awaited()
    notice_io.reconcile.assert_not_awaited()
    notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_not_awaited()


@pytest.mark.parametrize("joined", [False, True])
async def test_auto_approval_timeout_only_completes_if_membership_confirms_join(
    app: App, notice_io: NoticeIO, joined: bool
) -> None:
    notice_io.group.status = GroupStatus.AUTHORIZED
    matcher = _get_invite_matcher("request")
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-1", "sub_type": "invite", "approve": True},
            exception=NetworkError("timeout"),
        )
        ctx.should_call_api(
            "get_group_list", {}, result=[{"group_id": 20001}] if joined else []
        )
    if joined:
        notice_io.mark_joined.assert_awaited_once_with(12)
        notice_io.update_status.assert_awaited_once_with(
            12, status=InvitationStatus.APPROVED, operator_id="99999"
        )
        notice_io.admin_notice.assert_not_awaited()
    else:
        notice_io.update_status.assert_not_awaited()
        notice_io.admin_notice.assert_awaited_once()


async def test_banned_invitation_rejection_race_leaves_the_joined_group(
    app: App, notice_io: NoticeIO
) -> None:
    notice_io.group.status = GroupStatus.BANNED
    matcher = _get_invite_matcher("request")
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-1", "sub_type": "invite", "approve": False},
            exception=ActionFailed(retcode=100, wording="already agree msg"),
        )
        ctx.should_call_api("get_group_list", {}, result=[{"group_id": 20001}])
        ctx.should_call_api("set_group_leave", {"group_id": 20001})
    notice_io.update_status.assert_awaited_once_with(
        12, status=InvitationStatus.REJECTED, operator_id="99999"
    )
    notice_io.private_notice.assert_awaited_once()
    notice_io.admin_notice.assert_awaited_once()


@pytest.mark.parametrize("state", ["left", "joined", "unknown"])
async def test_banned_leave_timeout_requires_confirmed_absence(
    app: App, notice_io: NoticeIO, state: str
) -> None:
    notice_io.group.status = GroupStatus.BANNED
    notice_io.reconcile.return_value = _decision(joined=True)
    matcher = _get_invite_matcher("notice")
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_bot_join_event())
        ctx.should_call_api("get_group_list", {}, result=[{"group_id": 20001}])
        ctx.should_call_api(
            "set_group_leave", {"group_id": 20001}, exception=NetworkError("timeout")
        )
        if state == "unknown":
            ctx.should_call_api("get_group_list", {}, exception=NetworkError("offline"))
        else:
            ctx.should_call_api(
                "get_group_list",
                {},
                result=[] if state == "left" else [{"group_id": 20001}],
            )
    if state == "left":
        notice_io.update_status.assert_awaited_once_with(
            12, status=InvitationStatus.REJECTED, operator_id="99999"
        )
        notice_io.private_notice.assert_awaited_once()
    else:
        notice_io.update_status.assert_not_awaited()
        notice_io.private_notice.assert_not_awaited()
    notice_io.admin_notice.assert_awaited_once()


async def test_auto_rejection_timeout_without_join_does_not_mark_rejected(
    app: App, notice_io: NoticeIO
) -> None:
    notice_io.group.status = GroupStatus.BANNED
    matcher = _get_invite_matcher("request")
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-1", "sub_type": "invite", "approve": False},
            exception=NetworkError("timeout"),
        )
        ctx.should_call_api("get_group_list", {}, result=[])
    notice_io.update_status.assert_not_awaited()
    notice_io.admin_notice.assert_awaited_once()


@pytest.mark.parametrize("locale", ["zh-CN", "lzh", "x-meme"])
async def test_event_names_override_local_and_group_list_without_extra_name_api(
    app: App, notice_io: NoticeIO, monkeypatch: pytest.MonkeyPatch, locale: str
) -> None:
    monkeypatch.setattr(
        notice_invite_plugin, "resolve_locale", AsyncMock(return_value=locale)
    )
    group_name = "事件中的群名[CQ:at,qq=1]"
    nickname = "事件中的昵称[CQ:image,file=x]"
    event = build_group_request_event(
        extra={"group_name": group_name, "nickname": nickname}
    )
    notice_io.reconcile.return_value = _decision(joined=True)
    async with app.test_matcher(_get_invite_matcher("request")) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "get_group_list",
            {},
            result=[{"group_id": 20001, "group_name": "群列表名称"}],
        )
    args = notice_io.admin_notice.await_args
    assert args is not None
    messages = render_delivery_plan_messages(args.kwargs["plan"])
    assert len(messages) == 1
    assert all(segment.type == "text" for segment in messages[0])
    text = messages[0].extract_plain_text()
    assert "事件中的群名[CQ:at,qq=1]" in text
    assert "事件中的昵称[CQ:image,file=x]（10001）" in text
    assert "群列表名称" not in text
    notice_io.save_user.assert_awaited_once_with(user_id="10001", user_name=nickname)
    notice_io.save_group.assert_awaited_once_with(
        group_id="20001", group_name=group_name
    )
    reply_args = notice_io.record_reply_context.await_args
    assert reply_args is not None
    assert reply_args.kwargs["context_spec"].payload["inviter_id"] == "10001"


@pytest.mark.parametrize(
    ("extra", "list_name", "expected_group"),
    [
        ({}, "群列表新名称", "群列表新名称"),
        ({"group_name": " \t", "nickname": None}, "群列表新名称", "群列表新名称"),
        ({"group_name": 42, "nickname": []}, "", "测试群"),
        ({}, None, "测试群"),
    ],
)
async def test_join_report_reuses_group_list_then_local_names(
    app: App,
    notice_io: NoticeIO,
    extra: dict[str, object],
    list_name: object,
    expected_group: str,
) -> None:
    notice_io.reconcile.return_value = _decision(joined=True)
    async with app.test_matcher(_get_invite_matcher("notice")) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_bot_join_event(extra=extra))
        ctx.should_call_api(
            "get_group_list", {}, result=[{"group_id": 20001, "group_name": list_name}]
        )
    args = notice_io.admin_notice.await_args
    assert args is not None
    message = render_delivery_plan_messages(args.kwargs["plan"])[0].extract_plain_text()
    assert f"群名：{expected_group}" in message
    assert "邀请者：邀请者（10001）" in message
    notice_io.save_user.assert_not_awaited()


@pytest.mark.parametrize("missing", [True, False])
async def test_missing_names_use_ids_without_extra_api_or_duplicate_id_labels(
    app: App, notice_io: NoticeIO, missing: bool
) -> None:
    notice_io.group_get.return_value = (
        None
        if missing
        else SimpleNamespace(status=GroupStatus.UNAUTHORIZED, display_name=" \t")
    )
    notice_io.user_get.return_value = (
        None if missing else SimpleNamespace(display_name=" \t")
    )
    async with app.test_matcher(_get_invite_matcher("request")) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, build_group_request_event())
        ctx.should_call_api("get_group_list", {}, result=[])
    args = notice_io.admin_notice.await_args
    assert args is not None
    message = render_delivery_plan_messages(args.kwargs["plan"])[0].extract_plain_text()
    assert "群名：20001" in message
    assert "邀请者：10001\n" in message
    assert "10001（10001）" not in message


@pytest.mark.parametrize(
    ("missing_group", "missing_user"), [(True, True), (False, True), (True, False)]
)
async def test_event_names_create_missing_dependencies_without_name_api(
    app: App, notice_io: NoticeIO, missing_group: bool, missing_user: bool
) -> None:
    if missing_group:
        notice_io.group_get.return_value = None
    if missing_user:
        notice_io.user_get.return_value = None
    async with app.test_matcher(_get_invite_matcher("request")) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(
            bot,
            build_group_request_event(
                extra={"group_name": "上报群名", "nickname": "上报昵称"}
            ),
        )
        ctx.should_call_api("get_group_list", {}, result=[])
    notice_io.ensure_group.assert_awaited_once_with("20001", "上报群名")
    notice_io.ensure_user.assert_awaited_once_with("10001", "上报昵称")
    if missing_group:
        notice_io.save_group.assert_awaited_once_with(
            group_id="20001", group_name="上报群名", policy=WritePolicy.IMMEDIATE
        )
    if missing_user:
        notice_io.save_user.assert_awaited_once_with(
            user_id="10001", user_name="上报昵称", policy=WritePolicy.IMMEDIATE
        )
    notice_io.admin_notice.assert_awaited_once()


async def test_merged_invitation_displays_original_inviter_not_later_operator(
    app: App, notice_io: NoticeIO
) -> None:
    notice_io.user_get.side_effect = lambda uid: SimpleNamespace(
        display_name="原邀请者" if uid == "10001" else "后来的操作者"
    )
    notice_io.reconcile.return_value = _decision(joined=True)
    async with app.test_matcher(_get_invite_matcher("notice")) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(
            bot,
            build_bot_join_event(
                operator_id=10002,
                extra={"operator_name": "上报的新操作者", "nickname": "机器人"},
            ),
        )
        ctx.should_call_api("get_group_list", {}, result=[{"group_id": 20001}])
    args = notice_io.admin_notice.await_args
    assert args is not None
    message = render_delivery_plan_messages(args.kwargs["plan"])[0].extract_plain_text()
    assert "邀请者：原邀请者（10001）" in message
    assert "上报的新操作者" not in message
    assert "机器人（10001）" not in message
    private_args = notice_io.private_notice.await_args
    assert private_args is not None
    assert private_args.args[1] == 10001


async def test_banned_invitation_reports_the_event_names(
    app: App, notice_io: NoticeIO
) -> None:
    notice_io.group.status = GroupStatus.BANNED
    async with app.test_matcher(_get_invite_matcher("request")) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(
            bot,
            build_group_request_event(
                extra={"group_name": "封禁群新名称", "nickname": "邀请人的新昵称"}
            ),
        )
        ctx.should_call_api("get_group_list", {}, result=[])
        ctx.should_call_api(
            "set_group_add_request",
            {"flag": "flag-1", "sub_type": "invite", "approve": False},
        )
    args = notice_io.admin_notice.await_args
    assert args is not None
    message = render_delivery_plan_messages(args.kwargs["plan"])[0].extract_plain_text()
    assert "群名：封禁群新名称" in message
    assert "邀请者：邀请人的新昵称（10001）" in message
