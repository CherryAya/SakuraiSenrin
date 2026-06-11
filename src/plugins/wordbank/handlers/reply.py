"""Reply-style wordbank management handlers."""

from __future__ import annotations

from dataclasses import dataclass

from nonebot.adapters.onebot.v11.event import MessageEvent

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.database.types import (
    WordbankApprovalMessageRecord,
    WordbankGroupDetail,
    WordbankResponseMessageRecord,
)
from src.plugins.wordbank.services.core import WordbankService

from .approval import APPROVAL_APPROVE_ALIASES, APPROVAL_REJECT_ALIASES
from .commands import (
    actor_can_review,
    build_mutation_actor,
    handle_delete,
    handle_restore,
)

INFO_ALIASES = {"info", "详情"}
HISTORY_ALIASES = {"history", "历史", "历史记录", "审批记录", "审批历史"}
DELETE_ALIASES = {
    "del",
    "delete",
    "删除",
    "del response",
    "delete response",
    "删除响应词",
    "del trigger",
    "delete trigger",
    "删除触发词",
}
RESTORE_ALIASES = {
    "rst",
    "restore",
    "恢复",
    "rst response",
    "restore response",
    "恢复响应词",
    "rst trigger",
    "restore trigger",
    "恢复触发词",
}
REPLY_COMMAND_ALIASES = (
    INFO_ALIASES | HISTORY_ALIASES | DELETE_ALIASES | RESTORE_ALIASES
)


@dataclass(slots=True, frozen=True)
class ApprovalReplyOutcome:
    message: str
    approval_message: WordbankApprovalMessageRecord | None = None
    completed: bool = False
    action: str = ""


async def is_reply(event: MessageEvent) -> bool:
    return event.reply is not None


def get_reply_message_id(event: MessageEvent) -> str | None:
    reply = event.reply
    if reply is None:
        return None
    message_id = getattr(reply, "message_id", None)
    if message_id is None:
        return None
    return str(message_id)


async def handle_reply_command(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> str:
    message_id = get_reply_message_id(event)
    if message_id is None:
        return tr(locale, "wordbank.reply.target_missing")

    response_message = await service.get_response_message(message_id)
    if response_message is None:
        return tr(locale, "wordbank.reply.target_not_found", message_id=message_id)

    action = normalize_reply_command(text)
    if action in INFO_ALIASES:
        detail = await _load_group_detail(service, response_message)
        if detail is None:
            return tr(
                locale,
                "wordbank.reply.entry_not_found",
                entry_id=response_message.response_item_id,
            )
        return format_entry_detail(detail, response_message, locale=locale)

    if action in HISTORY_ALIASES:
        detail = await _load_group_detail(service, response_message)
        if detail is None:
            return tr(
                locale,
                "wordbank.reply.entry_not_found",
                entry_id=response_message.response_item_id,
            )
        return format_entry_history(detail, locale=locale)

    if action in DELETE_ALIASES:
        return await handle_delete(
            service,
            event=event,
            response_item_id_text=str(response_message.response_item_id),
            locale=locale,
        )

    if action in RESTORE_ALIASES:
        return await handle_restore(
            service,
            event=event,
            response_item_id_text=str(response_message.response_item_id),
            locale=locale,
        )

    return tr(locale, "wordbank.reply.unknown_command", action=action)


async def handle_approval_reply(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> str:
    outcome = await handle_approval_reply_result(
        service,
        event=event,
        text=text,
        locale=locale,
    )
    return outcome.message


async def handle_approval_reply_result(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> ApprovalReplyOutcome:
    message_id = get_reply_message_id(event)
    if message_id is None:
        return ApprovalReplyOutcome(tr(locale, "wordbank.reply.target_missing"))

    approval_message = await service.get_approval_message(message_id)
    if approval_message is None:
        return ApprovalReplyOutcome(
            tr(
                locale,
                "wordbank.approval.reply_target_not_found",
                message_id=message_id,
            )
        )

    actor = build_mutation_actor(event)
    if not actor_can_review(actor):
        return ApprovalReplyOutcome(
            tr(locale, "wordbank.approval.permission_denied"),
            approval_message=approval_message,
        )

    action = normalize_reply_command(text)
    if action in APPROVAL_APPROVE_ALIASES:
        ok = await service.approve_response_item(
            approval_message.response_item_id,
            actor_user_id=actor.user_id,
            actor_group_id=actor.group_id,
            can_moderate_group=actor.can_moderate_group,
            is_superuser=actor.is_superuser,
        )
        if ok:
            return ApprovalReplyOutcome(
                tr(
                    locale,
                    "wordbank.approval.reply_approved",
                    entry_id=approval_message.response_item_id,
                ),
                approval_message=approval_message,
                completed=True,
                action="approve",
            )
        return ApprovalReplyOutcome(
            tr(
                locale,
                "wordbank.approval.not_found",
                entry_id=approval_message.response_item_id,
            ),
            approval_message=approval_message,
            action="approve",
        )

    if action in APPROVAL_REJECT_ALIASES:
        ok = await service.reject_response_item(
            approval_message.response_item_id,
            actor_user_id=actor.user_id,
            actor_group_id=actor.group_id,
            can_moderate_group=actor.can_moderate_group,
            is_superuser=actor.is_superuser,
        )
        if ok:
            return ApprovalReplyOutcome(
                tr(
                    locale,
                    "wordbank.approval.reply_rejected",
                    entry_id=approval_message.response_item_id,
                ),
                approval_message=approval_message,
                completed=True,
                action="reject",
            )
        return ApprovalReplyOutcome(
            tr(
                locale,
                "wordbank.approval.not_found",
                entry_id=approval_message.response_item_id,
            ),
            approval_message=approval_message,
            action="reject",
        )

    return ApprovalReplyOutcome(
        tr(locale, "wordbank.reply.unknown_command", action=action),
        approval_message=approval_message,
    )


def normalize_reply_command(text: str) -> str:
    return " ".join(text.casefold().strip().split())


async def _load_group_detail(
    service: WordbankService,
    response_message: WordbankResponseMessageRecord,
) -> WordbankGroupDetail | None:
    return await service.get_group_detail(
        response_message.trigger_group_id,
        response_item_id=response_message.response_item_id,
    )


def format_entry_detail(
    detail: WordbankGroupDetail,
    response_message: WordbankResponseMessageRecord,
    *,
    locale: LocaleCode,
) -> str:
    selected = detail.selected_response
    assert selected is not None
    return tr(
        locale,
        "wordbank.reply.info",
        entry_id=selected.response_item_id,
        status=selected.status,
        enabled=_format_enabled(selected.enabled),
        deleted_at=_format_deleted_at(selected.deleted_at),
        scope=selected.scope,
        group_id=selected.group_id or "-",
        created_by=selected.created_by,
        trigger_text=detail.trigger_text,
        response_text=selected.response_text,
        probability=f"{selected.probability:g}",
        weight=selected.weight,
        message_id=response_message.message_id,
        message_type=response_message.message_type,
    )


def format_entry_history(
    detail: WordbankGroupDetail,
    *,
    locale: LocaleCode,
) -> str:
    selected = detail.selected_response
    assert selected is not None
    return tr(
        locale,
        "wordbank.reply.history",
        entry_id=selected.response_item_id,
        status=selected.status,
        enabled=_format_enabled(selected.enabled),
        deleted_at=_format_deleted_at(selected.deleted_at),
        scope=selected.scope,
        probability=f"{selected.probability:g}",
        weight=selected.weight,
    )


def _format_enabled(enabled: int) -> str:
    return "yes" if enabled else "no"


def _format_deleted_at(deleted_at: int) -> str:
    return str(deleted_at) if deleted_at else "0"
