"""Approval-message helpers for wordbank additions."""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent

from src.config import config
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.logger import logger
from src.plugins.wordbank.services.core import (
    WordbankAddResult,
    WordbankService,
    format_add_result,
    format_response_summary,
)
from src.plugins.wordbank.services.media import WordbankMediaService

from .rendering import render_shape_message

APPROVAL_APPROVE_ALIASES = {"y", "approve", "通过", "同意", "批准"}
APPROVAL_REJECT_ALIASES = {"n", "reject", "拒绝", "驳回", "反对"}
APPROVAL_REPLY_ALIASES = APPROVAL_APPROVE_ALIASES | APPROVAL_REJECT_ALIASES
_background_tasks: set[asyncio.Task[None]] = set()


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
        entry_id=result.response_item_id,
        trigger_text=result.trigger_text,
        response_text=format_response_summary(
            result.response_text,
            shape=result.response_shape,
        ),
        scope=result.scope,
        probability=f"{result.probability:g}",
        weight=result.weight,
        user_id=str(event.user_id),
        group_id=str(getattr(event, "group_id", "")) or "-",
    )


async def build_add_result_message(
    result: WordbankAddResult,
    *,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> Message:
    text = format_add_result(result, locale=locale)
    return await _append_response_image(result, text=text, media_service=media_service)


async def build_pending_approval_notice_message(
    result: WordbankAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> Message:
    text = format_pending_approval_notice(result, event=event, locale=locale)
    return await _append_response_image(result, text=text, media_service=media_service)


async def _append_response_image(
    result: WordbankAddResult,
    *,
    text: str,
    media_service: WordbankMediaService,
) -> Message:
    response_shape = result.response_shape
    if response_shape is None or response_shape.is_empty():
        return Message(text)
    if all(atom.kind == "text" for atom in response_shape.atoms):
        return Message(text)
    summary = format_response_summary(result.response_text, shape=response_shape)
    return _embed_response_shape(
        text=text,
        summary=summary,
        rendered_response=await render_shape_message(response_shape, media_service),
    )


def _embed_response_shape(
    *,
    text: str,
    summary: str,
    rendered_response: Message,
) -> Message:
    marker = f"响应: {summary}"
    if marker not in text:
        return Message(text) + rendered_response

    prefix, _, suffix = text.partition(marker)
    message = Message(prefix + "响应:\n")
    message += rendered_response
    message += Message(suffix)
    return message


async def send_pending_approval_notice(
    bot: Bot,
    service: WordbankService,
    *,
    event: MessageEvent,
    result: WordbankAddResult,
    locale: LocaleCode,
    media_service: WordbankMediaService | None = None,
) -> None:
    if result.status != "pending":
        return

    message = (
        await build_pending_approval_notice_message(
            result,
            event=event,
            locale=locale,
            media_service=media_service,
        )
        if media_service is not None
        else Message(format_pending_approval_notice(result, event=event, locale=locale))
    )
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
            await service.record_message_ref(
                ref_kind="approval",
                message_id=message_id,
                trigger_group_id=result.trigger_group_id,
                response_item_id=result.response_item_id,
                group_id=group_id,
                user_id=user_id,
                source_message_id=source_message_id,
                message_type="approval",
            )
        except Exception as exc:
            logger.warning(
                f"[Wordbank] approval notice skipped for {superuser_id}: {exc}"
            )


def schedule_pending_approval_notice(
    bot: Bot,
    service: WordbankService,
    *,
    event: MessageEvent,
    result: WordbankAddResult,
    locale: LocaleCode,
    media_service: WordbankMediaService | None = None,
) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(
        send_pending_approval_notice(
            bot,
            service,
            event=event,
            result=result,
            locale=locale,
            media_service=media_service,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


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
        await service.record_message_ref(
            ref_kind="approval",
            message_id=message_id,
            trigger_group_id=result.trigger_group_id,
            response_item_id=result.response_item_id,
            group_id=str(getattr(event, "group_id", "") or ""),
            user_id=str(event.user_id),
            source_message_id=str(getattr(event, "message_id", "") or ""),
            message_type="submission",
        )
    except Exception as exc:
        logger.warning(f"[Wordbank] submission approval record skipped: {exc}")
