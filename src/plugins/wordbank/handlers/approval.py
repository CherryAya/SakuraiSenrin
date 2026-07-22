"""Approval-message helpers for wordbank additions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import MessageSegment

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
    normalize_message_plan_entry,
)
from src.logger import logger
from src.plugins.wordbank.message_model import MessageShape
from src.plugins.wordbank.services import format_add_result
from src.plugins.wordbank.services.core import (
    WordbankAddResult,
    WordbankService,
)
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.presentation import (
    WordbankBatchAddResult,
    format_notice_content_raw_text,
    format_notice_content_summary,
    format_rule_summary,
    format_scope_label,
    format_status_label,
    format_timestamp,
    response_mode_label,
)

from .rendering import build_pending_item_plan_entries, build_shape_plan_entry

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


def _event_submit_timestamp(event: MessageEvent, created_at: int) -> str:
    fallback = int(getattr(event, "time", 0) or 0)
    return format_timestamp(created_at or fallback)


def _trigger_summary(result: WordbankAddResult) -> str:
    return format_notice_content_raw_text(
        result.trigger_text,
        shape=result.trigger_shape,
    )


def _response_summary(result: WordbankAddResult) -> str:
    return format_notice_content_raw_text(
        result.response_text,
        shape=result.response_shape,
        response_mode=result.response_mode,
        forward_node_count=result.forward_node_count,
    )


def _rule_summary(result: WordbankAddResult) -> str:
    return format_rule_summary(
        probability=result.probability,
        rule=result.rule,
    )


def format_pending_approval_notice(
    result: WordbankAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    split_detail: bool = False,
) -> str:
    _ = split_detail, locale
    lines = [
        "新增词条待审核",
        "回复 y / 通过 可通过",
        "回复 n / 拒绝 可驳回",
        "",
        f"ID: {result.response_item_id}",
        f"状态: {format_status_label(result.status)}",
        f"触发词: {_trigger_summary(result)}",
        f"响应词: {_response_summary(result)}",
        f"创建者: {result.created_by or str(event.user_id)}",
        f"提交时间: {_event_submit_timestamp(event, result.created_at)}",
        f"范围: {format_scope_label(result.scope)}",
        f"权重: {result.weight}",
        f"规则: {_rule_summary(result)}",
        f"响应模式: {response_mode_label(result)}",
    ]
    return "\n".join(lines)


def format_pending_batch_approval_notice(
    batch: WordbankBatchAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    split_detail: bool = False,
) -> str:
    _ = split_detail, locale
    pending_results = _pending_results(batch)
    first_result = pending_results[0]
    lines = [
        tr(locale, "wordbank.approval.pending_title", page=1),
        tr(locale, "wordbank.approval.pending_batch_instruction"),
        "",
        f"触发词: {_trigger_summary(first_result)}",
        f"创建者: {first_result.created_by or str(event.user_id)}",
        f"提交时间: {_event_submit_timestamp(event, first_result.created_at)}",
        f"范围: {format_scope_label(first_result.scope)}",
        f"权重: {first_result.weight}",
        f"规则: {_rule_summary(first_result)}",
        f"响应模式: {response_mode_label(first_result)}",
        f"待审数量: {len(pending_results)}",
    ]
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
    _ = media_service
    return build_text_plan_entry(
        format_pending_approval_notice(
            result,
            event=event,
            locale=locale,
        )
    )


async def build_pending_batch_approval_notice_plan_entry(
    batch: WordbankBatchAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    media_service: WordbankMediaService | None = None,
) -> MessagePlanEntry:
    _ = media_service
    return build_text_plan_entry(
        format_pending_batch_approval_notice(
            batch,
            event=event,
            locale=locale,
        )
    )


async def _build_pending_approval_detail_plan_entry(
    result: WordbankAddResult,
    *,
    locale: LocaleCode,
    media_service: WordbankMediaService,
    index: int | None = None,
    prefix: str = "",
) -> tuple[MessagePlanInput, ...]:
    return await build_pending_item_plan_entries(
        entry_id=result.response_item_id,
        scope=result.scope,
        trigger_text=result.trigger_text,
        response_text=result.response_text,
        created_by=result.created_by or "-",
        created_at=result.created_at,
        probability=result.probability,
        weight=result.weight,
        rule=result.rule,
        trigger_shape=result.trigger_shape,
        response_shape=result.response_shape,
        locale=locale,
        media_service=media_service,
        prefix=prefix,
        index=index,
        response_mode=result.response_mode,
        forward_source_message_id=result.forward_source_message_id,
        forward_node_count=result.forward_node_count,
    )


def _build_forward_source_entry(source_message_id: str) -> MessagePlanEntry:
    return MessagePlanEntry.from_message(
        Message([MessageSegment.forward(source_message_id)])
    )


def _prepend_notice_intro(
    entries: tuple[MessagePlanInput, ...],
    *,
    intro_text: str,
) -> tuple[MessagePlanInput, ...]:
    if not entries:
        return (build_text_plan_entry(intro_text),)
    first_entry = normalize_message_plan_entry(entries[0])
    return (
        MessagePlanEntry(
            blocks=(TextBlock(intro_text), *first_entry.blocks),
        ),
        *entries[1:],
    )


def _append_response_mode_line(
    entries: tuple[MessagePlanInput, ...],
    *,
    result: WordbankAddResult,
) -> tuple[MessagePlanInput, ...]:
    if not entries:
        return entries
    first_entry = normalize_message_plan_entry(entries[0])
    return (
        MessagePlanEntry(
            blocks=(
                *first_entry.blocks,
                TextBlock(f"\n响应模式: {response_mode_label(result)}"),
            ),
        ),
        *entries[1:],
    )


async def _build_pending_approval_delivery_plan(
    result: WordbankAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    media_service: WordbankMediaService | None,
) -> DeliveryPlan:
    if media_service is not None:
        messages = list(
            _append_response_mode_line(
                _prepend_notice_intro(
                    await _build_pending_approval_detail_plan_entry(
                        result,
                        locale=locale,
                        media_service=media_service,
                        prefix="\n\n",
                    ),
                    intro_text="\n".join(
                        (
                            "新增词条待审核",
                            "回复 y / 通过 可通过",
                            "回复 n / 拒绝 可驳回",
                        )
                    ),
                ),
                result=result,
            )
        )
    else:
        messages = [
            build_text_plan_entry(
                "\n".join(
                    (
                        "新增词条待审核",
                        "回复 y / 通过 可通过",
                        "回复 n / 拒绝 可驳回",
                        "",
                        f"ID: {result.response_item_id}",
                        f"状态: {format_status_label(result.status)}",
                        f"触发词: {_trigger_summary(result)}",
                        f"响应词: {_response_summary(result)}",
                        f"创建者: {result.created_by or str(event.user_id)}",
                        (
                            "提交时间: "
                            f"{_event_submit_timestamp(event, result.created_at)}"
                        ),
                        f"范围: {format_scope_label(result.scope)}",
                        f"权重: {result.weight}",
                        f"规则: {_rule_summary(result)}",
                        f"响应模式: {response_mode_label(result)}",
                    )
                )
            )
        ]
        if result.response_mode == "forward_whole" and result.forward_source_message_id:
            messages.append(
                _build_forward_source_entry(result.forward_source_message_id)
            )
    return DeliveryPlan(
        messages=tuple(messages),
        source_kind="wordbank_pending_approval_notice",
    )


async def _build_pending_batch_approval_delivery_plan(
    batch: WordbankBatchAddResult,
    *,
    event: MessageEvent,
    locale: LocaleCode,
    media_service: WordbankMediaService | None,
) -> DeliveryPlan:
    pending_results = _pending_results(batch)
    messages: list[MessagePlanInput] = [
        await build_pending_batch_approval_notice_plan_entry(
            batch,
            event=event,
            locale=locale,
            media_service=media_service,
        )
    ]
    if media_service is not None:
        for index, result in enumerate(pending_results, start=1):
            messages.extend(
                await _build_pending_approval_detail_plan_entry(
                    result,
                    locale=locale,
                    media_service=media_service,
                    index=index,
                )
            )
    else:
        for index, result in enumerate(pending_results, start=1):
            messages.append(
                build_text_plan_entry(
                    "\n".join(
                        (
                            f"序号: {index}",
                            f"ID: {result.response_item_id}",
                            f"状态: {format_status_label(result.status)}",
                            f"触发词: {_trigger_summary(result)}",
                            f"响应词: {_response_summary(result)}",
                            f"创建者: {result.created_by or str(event.user_id)}",
                            (
                                "提交时间: "
                                f"{_event_submit_timestamp(event, result.created_at)}"
                            ),
                            f"范围: {format_scope_label(result.scope)}",
                            f"权重: {result.weight}",
                            f"规则: {_rule_summary(result)}",
                            f"响应模式: {response_mode_label(result)}",
                        )
                    )
                )
            )
            if (
                result.response_mode == "forward_whole"
                and result.forward_source_message_id
            ):
                messages.append(
                    _build_forward_source_entry(result.forward_source_message_id)
                )
    return DeliveryPlan(
        messages=tuple(messages),
        source_kind="wordbank_pending_approval_notice",
    )


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
        (
            "wordbank.approval.trigger_label",
            format_notice_content_summary(
                result.trigger_text,
                shape=result.trigger_shape,
            ),
            result.trigger_shape,
        ),
        (
            "wordbank.approval.response_label",
            format_notice_content_summary(
                result.response_text,
                shape=result.response_shape,
                response_mode=result.response_mode,
                forward_node_count=result.forward_node_count,
            ),
            result.response_shape,
        ),
    )
    for label_key, summary_text, shape in entries:
        if not _should_render_shape(shape):
            continue
        assert shape is not None
        label = (
            "触发词:" if label_key == "wordbank.approval.trigger_label" else "响应词:"
        )
        fields.append(
            _RenderedShapeField(
                label=label,
                summary=summary_text,
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

    plan = await _build_pending_approval_delivery_plan(
        result,
        event=event,
        locale=locale,
        media_service=media_service,
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
                plan=plan,
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
    plan: DeliveryPlan,
    result: WordbankAddResult,
    group_id: str,
    user_id: str,
    source_message_id: str,
) -> None:
    try:
        plan_result = await deliver_message_plan(
            bot,
            plan=plan,
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

    plan = await _build_pending_batch_approval_delivery_plan(
        batch,
        event=event,
        locale=locale,
        media_service=media_service,
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
                plan=plan,
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
    plan: DeliveryPlan,
    first_result: WordbankAddResult,
    response_item_ids: tuple[int, ...],
    group_id: str,
    user_id: str,
    source_message_id: str,
) -> None:
    try:
        plan_result = await deliver_message_plan(
            bot,
            plan=plan,
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
