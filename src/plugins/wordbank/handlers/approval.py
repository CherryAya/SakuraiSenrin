"""Approval-message helpers for wordbank additions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent

from src.config import config
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_delivery import DeliveryTarget
from src.lib.message_plan import (
    DeliveryPlan,
    MessagePlanBlock,
    MessagePlanEntry,
    MessagePlanInput,
    TextBlock,
    build_text_plan_entry,
    deliver_message_plan,
)
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
from src.plugins.wordbank.services.presentation import WordbankBatchAddResult

from .rendering import build_pending_item_blocks, build_shape_plan_entry

APPROVAL_APPROVE_ALIASES = {"y", "approve", "通过", "同意", "批准"}
APPROVAL_REJECT_ALIASES = {"n", "reject", "拒绝", "驳回", "反对"}
APPROVAL_REPLY_ALIASES = APPROVAL_APPROVE_ALIASES | APPROVAL_REJECT_ALIASES
_background_tasks: set[asyncio.Task[None]] = set()


@dataclass(slots=True, frozen=True)
class _RenderedShapeField:
    label: str
    summary: str
    rendered_entry: MessagePlanEntry


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
    split_detail: bool = False,
) -> str:
    key: MessageKey = "wordbank.approval.notice"
    detail_hint = ""
    if split_detail:
        key = "wordbank.approval.notice_summary"
        detail_hint = tr(locale, "wordbank.approval.pending_detail_hint") + "\n"
    return tr(
        locale,
        key,
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
        detail_hint=detail_hint,
    )


def format_pending_batch_approval_notice(
    batch: WordbankBatchAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    split_detail: bool = False,
) -> str:
    pending_results = _pending_results(batch)
    lines = [
        tr(locale, "wordbank.approval.pending_title", page=1),
        tr(locale, "wordbank.approval.pending_batch_instruction"),
    ]
    if split_detail:
        lines.append(tr(locale, "wordbank.approval.pending_detail_hint"))
        return "\n".join(lines)
    for index, result in enumerate(pending_results, start=1):
        lines.append(
            tr(
                locale,
                "wordbank.approval.pending_item",
                entry_id=result.response_item_id,
                scope=result.scope,
                trigger_text=format_response_summary(
                    result.trigger_text,
                    shape=result.trigger_shape,
                ),
                response_text=format_response_summary(
                    result.response_text,
                    shape=result.response_shape,
                ),
                created_by=str(event.user_id),
            )
        )
    return "\n".join(lines)


async def build_add_result_plan_entry(
    result: WordbankAddResult,
    *,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> MessagePlanEntry:
    text = format_add_result(result, locale=locale)
    return await _build_rendered_result_entry(
        result,
        text=text,
        locale=locale,
        media_service=media_service,
    )


async def build_pending_approval_notice_plan_entry(
    result: WordbankAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> MessagePlanEntry:
    split_detail = _should_split_pending_approval_notice(result)
    text = format_pending_approval_notice(
        result,
        event=event,
        locale=locale,
        split_detail=split_detail,
    )
    if split_detail:
        return build_text_plan_entry(text)
    return await _build_rendered_result_entry(
        result,
        text=text,
        locale=locale,
        media_service=media_service,
    )


async def build_pending_batch_approval_notice_plan_entry(
    batch: WordbankBatchAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    media_service: WordbankMediaService | None = None,
) -> MessagePlanEntry:
    split_detail = media_service is not None and bool(_pending_results(batch))
    if media_service is None:
        return MessagePlanEntry(
            blocks=(
                TextBlock(
                    format_pending_batch_approval_notice(
                        batch,
                        event=event,
                        locale=locale,
                        split_detail=False,
                    )
                ),
            )
        )

    if split_detail:
        return build_text_plan_entry(
            format_pending_batch_approval_notice(
                batch,
                event=event,
                locale=locale,
                split_detail=True,
            )
        )

    pending_results = _pending_results(batch)
    if not pending_results:
        return MessagePlanEntry(
            blocks=(
                TextBlock(
                    format_pending_batch_approval_notice(
                        batch,
                        event=event,
                        locale=locale,
                        split_detail=False,
                    )
                ),
            )
        )

    blocks: list[MessagePlanBlock] = [
        TextBlock(tr(locale, "wordbank.approval.pending_title", page=1)),
        TextBlock("\n" + tr(locale, "wordbank.approval.pending_batch_instruction")),
    ]
    created_by = str(event.user_id)
    for result in pending_results:
        blocks.extend(
            await build_pending_item_blocks(
                entry_id=result.response_item_id,
                scope=result.scope,
                trigger_text=result.trigger_text,
                response_text=result.response_text,
                created_by=created_by,
                trigger_shape=result.trigger_shape,
                response_shape=result.response_shape,
                locale=locale,
                media_service=media_service,
            )
        )
    return MessagePlanEntry(blocks=tuple(blocks))


async def _build_pending_approval_notice_detail_plan_entry(
    result: WordbankAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> MessagePlanEntry | None:
    if not _should_split_pending_approval_notice(result):
        return None
    blocks = await build_pending_item_blocks(
        entry_id=result.response_item_id,
        scope=result.scope,
        trigger_text=result.trigger_text,
        response_text=result.response_text,
        created_by=str(event.user_id),
        trigger_shape=result.trigger_shape,
        response_shape=result.response_shape,
        locale=locale,
        media_service=media_service,
        prefix="",
    )
    return MessagePlanEntry(blocks=blocks)


async def _build_pending_batch_approval_detail_entries(
    batch: WordbankBatchAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> tuple[MessagePlanEntry, ...]:
    pending_results = _pending_results(batch)
    if not pending_results:
        return ()
    entries: list[MessagePlanEntry] = []
    created_by = str(event.user_id)
    for index, result in enumerate(pending_results, start=1):
        entries.append(
            MessagePlanEntry(
                blocks=await build_pending_item_blocks(
                    entry_id=result.response_item_id,
                    scope=result.scope,
                    trigger_text=result.trigger_text,
                    response_text=result.response_text,
                    created_by=created_by,
                    trigger_shape=result.trigger_shape,
                    response_shape=result.response_shape,
                    locale=locale,
                    media_service=media_service,
                    prefix=tr(locale, "wordbank.batch.index", index=index) + "\n",
                )
            )
        )
    return tuple(entries)


async def _build_rendered_result_entry(
    result: WordbankAddResult,
    *,
    text: str,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> MessagePlanEntry:
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
                rendered_entry=await build_shape_plan_entry(
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
) -> MessagePlanEntry:
    if not rendered_fields:
        return build_text_plan_entry(text)

    fields_by_marker = {
        f"{field.label} {field.summary}": field for field in rendered_fields
    }
    used_markers: set[str] = set()
    blocks: list[MessagePlanBlock] = []
    for line in text.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        line_ending = line[len(line_body) :]
        field = fields_by_marker.get(line_body)
        if field is None:
            blocks.append(TextBlock(line))
            continue
        used_markers.add(line_body)
        blocks.append(TextBlock(f"{field.label}\n"))
        blocks.extend(field.rendered_entry.blocks)
        if line_ending:
            blocks.append(TextBlock(line_ending))

    for marker, field in fields_by_marker.items():
        if marker in used_markers:
            continue
        blocks.extend(field.rendered_entry.blocks)
    return MessagePlanEntry(blocks=tuple(blocks))


def _shape_requires_split_detail(shape: MessageShape | None) -> bool:
    if shape is None or shape.is_empty():
        return False
    if any(atom.kind != "text" for atom in shape.atoms):
        return True
    if len(shape.atoms) > 1:
        return True
    return any("\n" in atom.text for atom in shape.atoms if atom.kind == "text")


def _should_split_pending_approval_notice(result: WordbankAddResult) -> bool:
    return _shape_requires_split_detail(
        result.trigger_shape
    ) or _shape_requires_split_detail(result.response_shape)


def _pending_results(batch: WordbankBatchAddResult) -> tuple[WordbankAddResult, ...]:
    return tuple(
        item.result
        for item in batch.items
        if item.ok and item.result is not None and item.result.status == "pending"
    )


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

    detail_message = (
        await _build_pending_approval_notice_detail_plan_entry(
            result,
            event=event,
            locale=locale,
            media_service=media_service,
        )
        if media_service is not None
        else None
    )
    message = format_pending_approval_notice(
        result,
        event=event,
        locale=locale,
        split_detail=detail_message is not None,
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
                detail_message=detail_message,
                locale=locale,
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
    message: MessagePlanInput,
    detail_message: MessagePlanEntry | None,
    locale: LocaleCode,
    result: WordbankAddResult,
    group_id: str,
    user_id: str,
    source_message_id: str,
) -> None:
    try:
        plan_result = await deliver_message_plan(
            bot,
            plan=DeliveryPlan(
                messages=(message,),
                source_kind="wordbank_pending_approval_notice",
            ),
            target=DeliveryTarget(kind="private", target_id=str(superuser_id)),
        )
        send_result = plan_result.results[0]
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
        if detail_message is not None:
            await deliver_message_plan(
                bot,
                plan=DeliveryPlan(
                    messages=(detail_message,),
                    source_kind="wordbank_pending_approval_notice",
                    fallback_nickname=tr(
                        locale,
                        "wordbank.approval.pending_forward_nickname",
                    ),
                    force_forward=True,
                ),
                target=DeliveryTarget(kind="private", target_id=str(superuser_id)),
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


async def send_pending_batch_approval_notice(
    bot: Bot,
    service: WordbankService,
    *,
    event: MessageEvent,
    batch: WordbankBatchAddResult,
    locale: LocaleCode,
    media_service: WordbankMediaService | None = None,
) -> None:
    pending_results = _pending_results(batch)
    if not pending_results:
        return

    detail_messages = (
        await _build_pending_batch_approval_detail_entries(
            batch,
            event=event,
            locale=locale,
            media_service=media_service,
        )
        if media_service is not None
        else ()
    )
    message = format_pending_batch_approval_notice(
        batch,
        event=event,
        locale=locale,
        split_detail=bool(detail_messages),
    )
    source_message_id = str(getattr(event, "message_id", "") or "")
    group_id = str(getattr(event, "group_id", "") or "")
    user_id = str(event.user_id)
    response_item_ids = tuple(result.response_item_id for result in pending_results)
    first_result = pending_results[0]

    await asyncio.gather(
        *(
            _send_single_pending_batch_approval_notice(
                bot,
                service,
                superuser_id=superuser_id,
                message=message,
                detail_messages=detail_messages,
                locale=locale,
                first_result=first_result,
                response_item_ids=response_item_ids,
                group_id=group_id,
                user_id=user_id,
                source_message_id=source_message_id,
            )
            for superuser_id in config.SUPERUSERS
        )
    )


async def _send_single_pending_batch_approval_notice(
    bot: Bot,
    service: WordbankService,
    *,
    superuser_id: str,
    message: MessagePlanInput,
    detail_messages: tuple[MessagePlanEntry, ...],
    locale: LocaleCode,
    first_result: WordbankAddResult,
    response_item_ids: tuple[int, ...],
    group_id: str,
    user_id: str,
    source_message_id: str,
) -> None:
    try:
        plan_result = await deliver_message_plan(
            bot,
            plan=DeliveryPlan(
                messages=(message,),
                source_kind="wordbank_pending_approval_notice",
            ),
            target=DeliveryTarget(kind="private", target_id=str(superuser_id)),
        )
        send_result = plan_result.results[0]
        message_id = extract_sent_message_id(send_result)
        if message_id is None:
            return
        await service.record_message_ref(
            ref_kind="approval",
            message_id=message_id,
            trigger_group_id=first_result.trigger_group_id,
            response_item_id=first_result.response_item_id,
            group_id=group_id,
            user_id=user_id,
            source_message_id=source_message_id,
            context_type="pending_batch",
            message_type="approval_batch",
            group_ids=response_item_ids,
        )
        if detail_messages:
            await deliver_message_plan(
                bot,
                plan=DeliveryPlan(
                    messages=detail_messages,
                    source_kind="wordbank_pending_approval_notice",
                    fallback_nickname=tr(
                        locale,
                        "wordbank.approval.pending_forward_nickname",
                    ),
                    force_forward=True,
                ),
                target=DeliveryTarget(kind="private", target_id=str(superuser_id)),
            )
    except Exception as exc:
        logger.warning(
            f"[Wordbank] batch approval notice skipped for {superuser_id}: {exc}"
        )


def schedule_submission_approval_notice(
    bot: Bot,
    service: WordbankService,
    *,
    event: MessageEvent,
    submission: WordbankAddResult | WordbankBatchAddResult,
    locale: LocaleCode,
    media_service: WordbankMediaService | None = None,
) -> None:
    if isinstance(submission, WordbankAddResult):
        schedule_pending_approval_notice(
            bot,
            service,
            event=event,
            result=submission,
            locale=locale,
            media_service=media_service,
        )
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(
        send_pending_batch_approval_notice(
            bot,
            service,
            event=event,
            batch=submission,
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


async def record_batch_submission_approval_message(
    service: WordbankService,
    *,
    event: MessageEvent,
    batch: WordbankBatchAddResult,
    send_result: Any,
) -> None:
    pending_results = _pending_results(batch)
    if not pending_results:
        return

    message_id = extract_sent_message_id(send_result)
    if message_id is None:
        return

    first_result = pending_results[0]
    try:
        await service.record_message_ref(
            ref_kind="approval",
            message_id=message_id,
            trigger_group_id=first_result.trigger_group_id,
            response_item_id=first_result.response_item_id,
            group_id=str(getattr(event, "group_id", "") or ""),
            user_id=str(event.user_id),
            source_message_id=str(getattr(event, "message_id", "") or ""),
            context_type="pending_batch",
            message_type="submission_batch",
            group_ids=tuple(result.response_item_id for result in pending_results),
        )
    except Exception as exc:
        logger.warning(f"[Wordbank] batch submission approval record skipped: {exc}")
