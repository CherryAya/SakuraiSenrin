"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-21 01:51:01
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-03 12:24:46
Description: 邀请通知处理
"""

from pathlib import Path
from typing import NoReturn, Protocol

from nonebot import on_notice, on_request
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupIncreaseNoticeEvent,
    GroupRequestEvent,
)
from nonebot.exception import ActionFailed, NetworkError
from nonebot.matcher import Matcher
from nonebot.rule import is_type, to_me

from src.config import config
from src.database.consts import WritePolicy
from src.database.core.consts import InvitationStage, InvitationStatus, Permission
from src.lib.admin_notifications import (
    AdminNotificationTarget,
    deliver_admin_notification_plan,
)
from src.lib.consts import TriggerType
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import resolve_locale, send_private_i18n, tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import DeliveryPlan, DeliveryPlanResult
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.lib.reply_router import ReplyContextSpec, record_reply_context_from_send_result
from src.lib.request_flags import is_valid_request_flag
from src.logger import logger
from src.repositories import group_repo, invite_repo, user_repo
from src.repositories.invite import InvitationEventResult
from src.services.invitation import (
    InvitationGroupState,
    InvitationStateError,
    invitation_display_name,
    invitation_event_names,
    invitation_lock,
    is_invite_target,
    is_request_unavailable,
    read_invitation_group_state,
)

name = tr("zh-CN", "plugin.notice_invite.name")
description = tr("zh-CN", "plugin.notice_invite.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "invite" / "README.MD"


class _MatcherFinisher(Protocol):
    """零参 ``finish`` 的最小协议，收窄 strict 模式类型。"""

    async def finish(self) -> NoReturn: ...


async def _finish_event(matcher: _MatcherFinisher) -> NoReturn:
    await matcher.finish()


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "no_check": True,
        "i18n": {
            "name_key": "plugin.notice_invite.name",
            "description_key": "plugin.notice_invite.description",
        },
        "docs": create_docs_meta(
            visible=False,
            category="system",
            order=130,
            source=DOCS_SOURCE,
            slug="notice.invite",
            parent_slug="notice",
            aliases=("群组邀请处理", "notice.invite"),
        ),
    },
)


async def is_invite_request(bot: Bot, event: GroupRequestEvent) -> bool:
    accepted = is_invite_target(bot, event)
    if not accepted:
        reason = (
            "unverified_target" if is_valid_request_flag(event.flag) else "invalid_flag"
        )
        logger.debug(
            f"[NoticeInvite] request ignored reason={reason} "
            f"group_id={event.group_id} sub_type={event.sub_type}"
        )
    return accepted


def _inviter_label(locale: LocaleCode, inviter_id: str, inviter_name: str) -> str:
    if inviter_name == inviter_id:
        return inviter_id
    return tr(
        locale,
        "notice.invite.inviter_label",
        inviter_name=inviter_name,
        inviter_id=inviter_id,
    )


async def _ensure_invitation_dependencies(
    *,
    inviter_id: str,
    group_id: str,
    group_name: str | None,
    inviter_name: str | None = None,
) -> tuple[str, str]:
    # biz_invitation 外键校验数据库行
    inviter = await user_repo.get_user(inviter_id)
    stored_user_name = invitation_display_name(
        inviter.display_name if inviter is not None else None
    )
    resolved_user_name = inviter_name or stored_user_name or inviter_id
    if inviter is None:
        await user_repo.save_user(
            user_id=inviter_id,
            user_name=resolved_user_name,
            policy=WritePolicy.IMMEDIATE,
        )
    elif inviter_name is not None and inviter_name != stored_user_name:
        await user_repo.save_user(user_id=inviter_id, user_name=inviter_name)
    await user_repo.ensure_persisted(inviter_id, resolved_user_name)

    group = await group_repo.get_group(group_id)
    stored_group_name = invitation_display_name(
        group.display_name if group is not None else None
    )
    resolved_group_name = group_name or stored_group_name or group_id
    if group is None:
        await group_repo.save_group(
            group_id=group_id,
            group_name=resolved_group_name,
            policy=WritePolicy.IMMEDIATE,
        )
    elif group_name is not None and group_name != stored_group_name:
        await group_repo.save_group(group_id=group_id, group_name=group_name)
    await group_repo.ensure_persisted(group_id, resolved_group_name)
    return resolved_group_name, resolved_user_name


def _build_invite_reply_context_spec(
    *,
    invitation_id: int,
    group_id: str,
    inviter_id: str,
    flag: str | None,
) -> ReplyContextSpec:
    return ReplyContextSpec(
        context_kind="admin.invite.approval",
        payload={
            "invitation_id": invitation_id,
            "group_id": group_id,
            "inviter_id": inviter_id,
            "flag": flag or "",
        },
    )


def _notification_origin_from_target(
    target: AdminNotificationTarget,
) -> tuple[str, str]:
    if target.channel == "private_superuser":
        return "private", target.target_id
    return "group", target.target_id


async def _record_invitation_notice_delivery(
    bot: Bot,
    *,
    invitation_id: int,
    group_id: str,
    inviter_id: str,
    flag: str | None,
    report_message: str,
    target: AdminNotificationTarget,
    plan_result: DeliveryPlanResult,
) -> None:
    send_result = plan_result.results[0]
    message_id = getattr(send_result, "message_id", None)
    if not message_id:
        return
    await invite_repo.add_message_record(
        invitation_id=invitation_id,
        message_id=str(message_id),
    )
    origin_message_type, origin_target_id = _notification_origin_from_target(target)
    try:
        await record_reply_context_from_send_result(
            bot,
            send_result=send_result,
            context_spec=_build_invite_reply_context_spec(
                invitation_id=invitation_id,
                group_id=group_id,
                inviter_id=inviter_id,
                flag=flag,
            ),
            source_kind="notice_invite",
            origin_message_type=origin_message_type,
            origin_target_id=origin_target_id,
            fallback_message=report_message,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "[NoticeInvite] reply context record skipped "
            f"invitation_id={invitation_id} channel={target.channel} "
            f"target_id={target.target_id} error={type(exc).__name__}: {exc}"
        )


# [notice.group_increase.invite]


@on_notice(
    priority=5,
    rule=is_type(GroupIncreaseNoticeEvent) & to_me(),
    block=False,
).handle()
@on_request(
    priority=5,
    rule=is_type(GroupRequestEvent) & is_invite_request,
    block=False,
).handle()
async def _(
    bot: Bot,
    event: GroupRequestEvent | GroupIncreaseNoticeEvent,
    matcher: Matcher,
) -> None:
    if isinstance(event, GroupRequestEvent):
        if not is_invite_target(bot, event):
            return
    elif str(event.user_id) != str(bot.self_id):
        return

    group_id = str(event.group_id)
    async with invitation_lock(str(bot.self_id), group_id):
        await _handle_invitation_event(bot, event)
    await _finish_event(matcher)


async def _notify_failure(bot: Bot, group_id: str, key: MessageKey) -> None:
    locale = await resolve_locale(group_id)
    await deliver_admin_notification_plan(
        bot,
        plan=DeliveryPlan(
            messages=(
                tr(
                    locale,
                    "notice.invite.processing_failed",
                    group_id=group_id,
                    reason=tr(locale, key),
                ),
            ),
            source_kind="notice_invite_failure",
            allow_asset_reuse=False,
        ),
    )


async def _handle_invitation_event(
    bot: Bot,
    event: GroupRequestEvent | GroupIncreaseNoticeEvent,
) -> None:
    group_id = str(event.group_id)
    group_state = await _read_current_group_state(bot, group_id)
    if group_state is None:
        return
    joined = group_state.joined
    if isinstance(event, GroupIncreaseNoticeEvent) and not joined:
        logger.debug(f"[NoticeInvite] stale join notice ignored group_id={group_id}")
        return

    inviter_id = str(
        event.user_id if isinstance(event, GroupRequestEvent) else event.operator_id
    )
    event_group_name, event_inviter_name = invitation_event_names(event, inviter_id)
    group_name, inviter_name = await _ensure_invitation_dependencies(
        inviter_id=inviter_id,
        group_id=group_id,
        group_name=event_group_name or group_state.group_name,
        inviter_name=event_inviter_name,
    )
    invitation = await invite_repo.reconcile_event(
        group_id=group_id,
        inviter_id=inviter_id,
        flag=event.flag if isinstance(event, GroupRequestEvent) else None,
        joined=joined,
        event_time=event.time,
    )
    if invitation.status.is_processed:
        logger.debug(
            "[NoticeInvite] processed event ignored "
            f"group_id={group_id} invitation_id={invitation.invitation_id}"
        )
        return
    if not joined and invitation.sub_type == InvitationStage.JOINED:
        # 已入群阶段的原 flag 只是重放键；离群后的旧事件不能再次审批。
        logger.debug(
            "[NoticeInvite] joined request replay ignored "
            f"group_id={group_id} invitation_id={invitation.invitation_id}"
        )
        return
    if invitation.inviter_id != inviter_id:
        original_inviter = await user_repo.get_user(invitation.inviter_id)
        inviter_name = (
            invitation_display_name(
                original_inviter.display_name if original_inviter is not None else None
            )
            or invitation.inviter_id
        )
    group = await group_repo.get_group(group_id)
    if group and group.status.is_working:
        if not joined and isinstance(event, GroupRequestEvent):
            try:
                await bot.set_group_add_request(
                    flag=event.flag, sub_type="invite", approve=True
                )
            except (ActionFailed, NetworkError) as exc:
                logger.warning(
                    "[NoticeInvite] auto approve failed "
                    f"group_id={group_id} error={exc}"
                )
                joined = await _read_current_membership(bot, group_id)
                if joined is None:
                    return
                if not joined:
                    await _notify_failure(
                        bot,
                        group_id,
                        "admin.invite.request_unavailable"
                        if is_request_unavailable(exc)
                        else "admin.invite.action_failed",
                    )
                    return
                await invite_repo.mark_joined(invitation.invitation_id)
        await _ensure_invitation_dependencies(
            inviter_id=str(bot.self_id), group_id=group_id, group_name=group_name
        )
        await invite_repo.update_status(
            invitation.invitation_id,
            status=InvitationStatus.APPROVED,
            operator_id=str(bot.self_id),
        )
        return

    if group and group.status.is_banned:
        rejected = await _reject_banned_invitation(
            bot,
            invitation_id=invitation.invitation_id,
            group_id=group_id,
            flag=event.flag if isinstance(event, GroupRequestEvent) else None,
            joined=joined,
        )
        if not rejected:
            return
        await _ensure_invitation_dependencies(
            inviter_id=str(bot.self_id), group_id=group_id, group_name=group_name
        )
        await invite_repo.update_status(
            invitation.invitation_id,
            status=InvitationStatus.REJECTED,
            operator_id=str(bot.self_id),
        )
        await _notify_inviter(
            bot,
            invitation,
            group_name=group_name,
            inviter_name=inviter_name,
            key="notice.invite.auto_reject",
        )
        locale = await resolve_locale(group_id)
        await deliver_admin_notification_plan(
            bot,
            plan=DeliveryPlan(
                messages=(
                    tr(
                        locale,
                        "notice.invite.auto_reject.details",
                        group_id=group_id,
                        group_name=group_name,
                        inviter_label=_inviter_label(
                            locale, invitation.inviter_id, inviter_name
                        ),
                    ),
                ),
                source_kind="notice_invite_auto_reject",
                allow_asset_reuse=False,
            ),
        )
        return

    if invitation.should_notify:
        await _report_pending(
            bot, invitation, group_name=group_name, inviter_name=inviter_name
        )


async def _read_current_group_state(
    bot: Bot, group_id: str
) -> InvitationGroupState | None:
    try:
        return await read_invitation_group_state(bot, group_id)
    except InvitationStateError as exc:
        logger.warning(
            f"[NoticeInvite] state unavailable group_id={group_id} error={exc}"
        )
        await _notify_failure(bot, group_id, "admin.invite.state_unavailable")
        return None


async def _read_current_membership(bot: Bot, group_id: str) -> bool | None:
    state = await _read_current_group_state(bot, group_id)
    return state.joined if state is not None else None


async def _reject_banned_invitation(
    bot: Bot, *, invitation_id: int, group_id: str, flag: str | None, joined: bool
) -> bool:
    if not joined:
        if not is_valid_request_flag(flag):
            await _notify_failure(bot, group_id, "admin.invite.request_unavailable")
            return False
        try:
            await bot.set_group_add_request(flag=flag, sub_type="invite", approve=False)
            return True
        except (ActionFailed, NetworkError) as exc:
            logger.warning(
                f"[NoticeInvite] auto reject failed group_id={group_id} error={exc}"
            )
            current_membership = await _read_current_membership(bot, group_id)
            if current_membership is None:
                return False
            if not current_membership:
                await _notify_failure(
                    bot,
                    group_id,
                    "admin.invite.request_unavailable"
                    if is_request_unavailable(exc)
                    else "admin.invite.action_failed",
                )
                return False
            await invite_repo.mark_joined(invitation_id)
    try:
        await bot.set_group_leave(group_id=int(group_id))
    except (ActionFailed, NetworkError) as exc:
        logger.warning(
            f"[NoticeInvite] auto leave failed group_id={group_id} error={exc}"
        )
        still_joined = await _read_current_membership(bot, group_id)
        if still_joined is None:
            return False
        if still_joined:
            await _notify_failure(bot, group_id, "admin.invite.action_failed")
            return False
    return True


async def _report_pending(
    bot: Bot, invitation: InvitationEventResult, *, group_name: str, inviter_name: str
) -> None:
    group_id = invitation.group_id
    inviter_id = invitation.inviter_id
    await _notify_inviter(
        bot,
        invitation,
        group_name=group_name,
        inviter_name=inviter_name,
        key="notice.invite.received",
    )
    locale = await resolve_locale(group_id)
    report_message = tr(
        locale,
        "notice.invite.authorization_report"
        if invitation.sub_type == InvitationStage.JOINED
        else "notice.invite.report",
        group_id=group_id,
        group_name=group_name,
        inviter_label=_inviter_label(locale, inviter_id, inviter_name),
        flag=invitation.flag,
    )

    async def _on_delivered(
        target: AdminNotificationTarget,
        plan_result: DeliveryPlanResult,
    ) -> None:
        await _record_invitation_notice_delivery(
            bot,
            invitation_id=invitation.invitation_id,
            group_id=group_id,
            inviter_id=inviter_id,
            flag=invitation.flag,
            report_message=report_message,
            target=target,
            plan_result=plan_result,
        )

    await deliver_admin_notification_plan(
        bot,
        plan=DeliveryPlan(
            messages=(report_message,),
            source_kind="notice_invite",
            allow_asset_reuse=False,
        ),
        on_delivered=_on_delivered,
    )


async def _notify_inviter(
    bot: Bot,
    invitation: InvitationEventResult,
    *,
    group_name: str,
    inviter_name: str,
    key: MessageKey,
) -> None:
    inviter_id = invitation.inviter_id
    if not inviter_id.isascii() or not inviter_id.isdecimal() or int(inviter_id) < 1:
        return
    try:
        locale = await resolve_locale(invitation.group_id)
        await send_private_i18n(
            bot,
            int(inviter_id),
            key,
            locale_group_id=invitation.group_id,
            group_id=invitation.group_id,
            group_name=group_name,
            inviter_label=_inviter_label(locale, inviter_id, inviter_name),
            main_group_id=config.MAIN_GROUP_ID,
        )
    except Exception as exc:
        # 可选的邀请者私聊失败，不能阻止管理员获得有效待办。
        logger.warning(
            "[NoticeInvite] inviter notice failed "
            f"invitation_id={invitation.invitation_id} group_id={invitation.group_id} "
            f"error={type(exc).__name__}: {exc}"
        )
