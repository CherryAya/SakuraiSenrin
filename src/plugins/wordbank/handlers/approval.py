"""Approval-message helpers for wordbank additions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent

from src.config import config
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_delivery import DeliveryTarget, deliver_single_message
from src.lib.messages import empty_message, text_message
from src.logger import logger
from src.plugins.wordbank.message_model import MessageShape
from src.plugins.wordbank.services import (
    format_add_result,
    format_response_summary,
)
from src.plugins.wordbank.services.core import (
    WordbankAddResult,
    WordbankService,
)
from src.plugins.wordbank.services.media import WordbankMediaService

from .rendering import render_shape_message

APPROVAL_APPROVE_ALIASES = {"y", "approve", "通过", "同意", "批准"}
APPROVAL_REJECT_ALIASES = {"n", "reject", "拒绝", "驳回", "反对"}
APPROVAL_REPLY_ALIASES = APPROVAL_APPROVE_ALIASES | APPROVAL_REJECT_ALIASES
_background_tasks: set[asyncio.Task[None]] = set()


@dataclass(slots=True, frozen=True)
class _RenderedShapeField:
    label: str
    summary: str
    rendered_message: Message


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
    return await _append_response_image(
        result,
        text=text,
        locale=locale,
        media_service=media_service,
    )


async def build_pending_approval_notice_message(
    result: WordbankAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> Message:
    text = format_pending_approval_notice(result, event=event, locale=locale)
    return await _append_response_image(
        result,
        text=text,
        locale=locale,
        media_service=media_service,
    )


async def _append_response_image(
    result: WordbankAddResult,
    *,
    text: str,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> Message:
    rendered_fields = await _collect_rendered_shape_fields(
        result,
        locale=locale,
        media_service=media_service,
    )
    return _embed_rendered_shapes(
        text=text,
        rendered_fields=rendered_fields,
    )


async def _collect_rendered_shape_fields(
    result: WordbankAddResult,
    *,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> tuple[_RenderedShapeField, ...]:
    fields: list[_RenderedShapeField] = []
    entries: tuple[tuple[MessageKey, str, MessageShape | None], ...] = (
        ("wordbank.approval.trigger_label", result.trigger_text, result.trigger_shape),
        (
            "wordbank.approval.response_label",
            result.response_text,
            result.response_shape,
        ),
    )
    for label_key, summary_text, shape in entries:
        if not _should_render_shape(shape):
            continue
        assert shape is not None
        fields.append(
            _RenderedShapeField(
                label=tr(locale, label_key),
                summary=format_response_summary(summary_text, shape=shape),
                rendered_message=await render_shape_message(
                    shape,
                    media_service,
                    locale=locale,
                ),
            )
        )
    return tuple(fields)


def _should_render_shape(shape: MessageShape | None) -> bool:
    return (
        shape is not None
        and not shape.is_empty()
        and not all(atom.kind == "text" for atom in shape.atoms)
    )


def _embed_rendered_shapes(
    *,
    text: str,
    rendered_fields: tuple[_RenderedShapeField, ...],
) -> Message:
    if not rendered_fields:
        return text_message(text)

    fields_by_marker = {
        f"{field.label} {field.summary}": field for field in rendered_fields
    }
    used_markers: set[str] = set()
    message = empty_message()
    for line in text.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        line_ending = line[len(line_body) :]
        field = fields_by_marker.get(line_body)
        if field is None:
            message += MessageSegment.text(line)
            continue
        used_markers.add(line_body)
        message += MessageSegment.text(f"{field.label}\n")
        message += field.rendered_message
        if line_ending:
            message += MessageSegment.text(line_ending)

    for marker, field in fields_by_marker.items():
        if marker in used_markers:
            continue
        message += field.rendered_message
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
        else text_message(
            format_pending_approval_notice(result, event=event, locale=locale)
        )
    )
    source_message_id = str(getattr(event, "message_id", "") or "")
    group_id = str(getattr(event, "group_id", "") or "")
    user_id = str(event.user_id)
    await asyncio.gather(
        *(
            _send_single_pending_approval_notice(
                bot,
                service,
                superuser_id=superuser_id,
                message=message,
                result=result,
                group_id=group_id,
                user_id=user_id,
                source_message_id=source_message_id,
            )
            for superuser_id in config.SUPERUSERS
        )
    )


async def _send_single_pending_approval_notice(
    bot: Bot,
    service: WordbankService,
    *,
    superuser_id: str,
    message: Message,
    result: WordbankAddResult,
    group_id: str,
    user_id: str,
    source_message_id: str,
) -> None:
    try:
        send_result = await deliver_single_message(
            bot,
            target=DeliveryTarget(kind="private", target_id=str(superuser_id)),
            message=message,
            source_kind="wordbank_pending_approval_notice",
        )
        message_id = extract_sent_message_id(send_result)
        if message_id is None:
            return
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
        logger.warning(f"[Wordbank] approval notice skipped for {superuser_id}: {exc}")


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
