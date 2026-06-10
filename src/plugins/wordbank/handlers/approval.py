"""Approval-message helpers for wordbank additions."""

from __future__ import annotations

from typing import Any

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent

from src.config import config
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.logger import logger
from src.plugins.wordbank.services.core import (
    WordbankAddResult,
    WordbankService,
    format_response_summary,
)

APPROVAL_APPROVE_ALIASES = {"y", "approve", "通过", "同意", "批准"}
APPROVAL_REJECT_ALIASES = {"n", "reject", "拒绝", "驳回", "反对"}
APPROVAL_REPLY_ALIASES = APPROVAL_APPROVE_ALIASES | APPROVAL_REJECT_ALIASES


def extract_sent_message_id(result: Any) -> str | None:
    if isinstance(result, dict):
        value = result.get("message_id")
    else:
        value = getattr(result, "message_id", None)
    if value is None:
        return None
    return str(value)


def format_pending_approval_notice(
    result: WordbankAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
) -> str:
    return tr(
        locale,
        "wordbank.approval.notice",
        entry_id=result.entry_id,
        trigger_text=result.trigger_text,
        response_text=format_response_summary(
            result.response_text,
            kind=result.response_kind,
            canonical_image_id=result.response_canonical_image_id,
        ),
        trigger_mode=result.trigger_mode,
        scope=result.scope,
        probability=f"{result.probability:g}",
        weight=result.weight,
        user_id=str(event.user_id),
        group_id=str(getattr(event, "group_id", "")) or "-",
    )


async def send_pending_approval_notice(
    bot: Bot,
    service: WordbankService,
    *,
    event: MessageEvent,
    result: WordbankAddResult,
    locale: LocaleCode,
) -> None:
    if result.status != "pending":
        return

    message = format_pending_approval_notice(result, event=event, locale=locale)
    source_message_id = str(getattr(event, "message_id", "") or "")
    group_id = str(getattr(event, "group_id", "") or "")
    user_id = str(event.user_id)
    for superuser_id in config.SUPERUSERS:
        try:
            send_result = await bot.send_private_msg(
                user_id=int(superuser_id),
                message=message,
            )
            message_id = extract_sent_message_id(send_result)
            if message_id is None:
                continue
            await service.record_approval_message(
                message_id=message_id,
                entry_id=result.entry_id,
                group_id=group_id,
                user_id=user_id,
                source_message_id=source_message_id,
                message_type="approval",
            )
        except Exception as exc:
            logger.warning(
                f"[Wordbank] approval notice skipped for {superuser_id}: {exc}"
            )


async def record_submission_approval_message(
    service: WordbankService,
    *,
    event: MessageEvent,
    result: WordbankAddResult,
    send_result: Any,
) -> None:
    if result.status != "pending":
        return

    message_id = extract_sent_message_id(send_result)
    if message_id is None:
        return

    try:
        await service.record_approval_message(
            message_id=message_id,
            entry_id=result.entry_id,
            group_id=str(getattr(event, "group_id", "") or ""),
            user_id=str(event.user_id),
            source_message_id=str(getattr(event, "message_id", "") or ""),
            message_type="submission",
        )
    except Exception as exc:
        logger.warning(f"[Wordbank] submission approval record skipped: {exc}")
