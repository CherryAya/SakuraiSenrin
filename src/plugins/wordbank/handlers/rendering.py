"""Async rendering helpers for wordbank rich messages."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import replace
import math
from typing import Any, cast

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import (
    AtRefBlock,
    ImageBytesBlock,
    MessagePlanBlock,
    MessagePlanEntry,
    TextBlock,
)
from src.lib.utils.img import QQAvatar
from src.plugins.wordbank.database.types import WordbankGroupDetail, WordbankSearchItem
from src.plugins.wordbank.debug import log_perf, perf_start
from src.plugins.wordbank.message_model import MessageShape
from src.plugins.wordbank.services.core import WordbankLeaderboardCardData
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.presentation import (
    format_creator_leaderboard,
    format_response_summary,
)

from .group_detail_cards import (
    GroupDetailCardPage,
)
from .group_detail_cards import (
    build_group_detail_card_plan_entry as build_group_detail_image_plan_entry,
)
from .leaderboard_cards import (
    build_wordbank_leaderboard_card_plan_entry as build_leaderboard_image_plan_entry,
)
from .search_cards import (
    SearchCardQuery,
)
from .search_cards import (
    build_search_results_card_plan_entry as build_search_results_image_plan_entry,
)

GROUP_PAGE_SIZE = 10
MISSING_IMAGE_PLACEHOLDER = tr("zh-CN", "wordbank.render.image_missing")


async def build_shape_plan_entry(
    shape: MessageShape,
    media_service: WordbankMediaService,
    *,
    locale: LocaleCode = "zh-CN",
    trace_fields: Mapping[str, object] | None = None,
    trace_sink: MutableMapping[str, object] | None = None,
) -> MessagePlanEntry:
    load_start = perf_start()
    image_bytes_by_id = await _load_shape_image_bytes(shape, media_service)
    payload_stats = _build_image_payload_stats(image_bytes_by_id)
    if trace_sink is not None:
        trace_sink.update(payload_stats)
    if trace_fields is not None:
        log_perf(
            "plugin.build_passive_message.render_shape.images_loaded",
            start=load_start,
            **cast(Any, payload_stats),
            **cast(Any, dict(trace_fields)),
        )
    blocks: list[MessagePlanBlock] = []
    image_segments = 0
    for atom in shape.atoms:
        if atom.kind == "text" and atom.text:
            blocks.append(TextBlock(atom.text))
        elif atom.kind == "at" and atom.target_id:
            blocks.append(AtRefBlock(atom.target_id))
        elif atom.kind == "image" and atom.canonical_image_id is not None:
            image_bytes = image_bytes_by_id.get(atom.canonical_image_id)
            if image_bytes is None:
                blocks.append(TextBlock(tr(locale, "wordbank.render.image_missing")))
                continue
            blocks.append(ImageBytesBlock(image_bytes))
            image_segments += 1
    entry = MessagePlanEntry(blocks=tuple(blocks))
    if trace_fields is not None:
        log_perf(
            "plugin.build_passive_message.render_shape.segment_built",
            segments=len(blocks),
            image_segments=image_segments,
            **cast(Any, payload_stats),
            **cast(Any, dict(trace_fields)),
        )
    return entry


async def build_search_items_text_plan_entry(
    *,
    items: tuple[WordbankSearchItem, ...] | list[WordbankSearchItem],
    locale: LocaleCode,
    media_service: WordbankMediaService,
    page: int = 1,
    limit: int = 10,
    has_more: bool = False,
) -> MessagePlanEntry:
    if not items:
        return MessagePlanEntry(
            blocks=(TextBlock(tr(locale, "wordbank.search.empty", page=page)),)
        )

    blocks: list[MessagePlanBlock] = [
        TextBlock(tr(locale, "wordbank.search.title", page=page))
    ]
    for item in items:
        response_preview = " / ".join(item.response_summaries[:3]) or item.response_text
        if item.has_more_responses:
            response_preview = f"{response_preview} (+{item.remaining_response_count})"
        blocks.append(
            TextBlock(
                "\n"
                + tr(
                    locale,
                    "wordbank.search.item",
                    entry_id=item.trigger_group_id,
                    status=item.status,
                    scope=item.scope,
                    trigger_text=item.trigger_text,
                    response_text=response_preview,
                )
            )
        )
        await _append_labeled_shape_blocks(
            blocks,
            shape=item.trigger_shape,
            media_service=media_service,
            locale=locale,
            label=tr(locale, "wordbank.group.trigger_label"),
        )
        await _append_labeled_shape_blocks(
            blocks,
            shape=item.response_shape,
            media_service=media_service,
            locale=locale,
            label=tr(locale, "wordbank.approval.response_label"),
        )
    if has_more:
        blocks.append(
            TextBlock(
                "\n"
                + tr(
                    locale,
                    "wordbank.search.more",
                    next_page=page + 1,
                    limit=limit,
                )
            )
        )
    return MessagePlanEntry(blocks=tuple(blocks))


async def build_pending_items_plan_entry(
    *,
    items: tuple[WordbankSearchItem, ...] | list[WordbankSearchItem],
    locale: LocaleCode,
    media_service: WordbankMediaService,
    page: int = 1,
    limit: int = 10,
    has_more: bool = False,
) -> MessagePlanEntry:
    if not items:
        return MessagePlanEntry(
            blocks=(
                TextBlock(tr(locale, "wordbank.approval.pending_empty", page=page)),
            )
        )

    blocks: list[MessagePlanBlock] = [
        TextBlock(tr(locale, "wordbank.approval.pending_title", page=page))
    ]
    for item in items:
        response_item_id = (
            item.response_item_ids[0]
            if item.response_item_ids
            else item.trigger_group_id
        )
        blocks.extend(
            await build_pending_item_blocks(
                entry_id=response_item_id,
                scope=item.scope,
                trigger_text=item.trigger_text,
                response_text=item.response_text,
                created_by=item.created_by,
                trigger_shape=item.trigger_shape,
                response_shape=item.response_shape,
                locale=locale,
                media_service=media_service,
            )
        )
    if has_more:
        blocks.append(
            TextBlock(
                "\n"
                + tr(
                    locale,
                    "wordbank.approval.pending_more",
                    next_page=page + 1,
                    limit=limit,
                )
            )
        )
    return MessagePlanEntry(blocks=tuple(blocks))


async def build_pending_item_blocks(
    *,
    entry_id: int,
    scope: str,
    trigger_text: str,
    response_text: str,
    created_by: str,
    trigger_shape: MessageShape | None,
    response_shape: MessageShape | None,
    locale: LocaleCode,
    media_service: WordbankMediaService,
    prefix: str = "\n",
) -> tuple[MessagePlanBlock, ...]:
    blocks: list[MessagePlanBlock] = [
        TextBlock(
            prefix
            + tr(
                locale,
                "wordbank.approval.pending_item",
                entry_id=entry_id,
                scope=scope,
                trigger_text=format_response_summary(
                    trigger_text,
                    shape=trigger_shape,
                ),
                response_text=format_response_summary(
                    response_text,
                    shape=response_shape,
                ),
                created_by=created_by,
            )
        )
    ]
    await _append_labeled_shape_blocks(
        blocks,
        shape=trigger_shape,
        media_service=media_service,
        locale=locale,
        label=tr(locale, "wordbank.group.trigger_label"),
    )
    await _append_labeled_shape_blocks(
        blocks,
        shape=response_shape,
        media_service=media_service,
        locale=locale,
        label=tr(locale, "wordbank.approval.response_label"),
    )
    return tuple(blocks)


async def build_reply_detail_plan_entry(
    *,
    detail: WordbankGroupDetail,
    locale: LocaleCode,
    media_service: WordbankMediaService,
    message_id: str,
    message_type: str,
) -> MessagePlanEntry:
    selected = detail.selected_response
    if selected is None:
        return MessagePlanEntry(
            blocks=(
                TextBlock(tr(locale, "wordbank.reply.entry_not_found", entry_id=0)),
            )
        )

    blocks: list[MessagePlanBlock] = [
        TextBlock(
            tr(
                locale,
                "wordbank.reply.info_header",
                entry_id=selected.response_item_id,
                status=selected.status,
                enabled=_format_enabled(selected.enabled, locale),
                deleted_at=str(selected.deleted_at) if selected.deleted_at else "0",
                scope=selected.scope,
                group_id=selected.group_id or "-",
                created_by=selected.created_by,
                probability=f"{detail.probability:g}",
                weight=selected.weight,
                message_id=message_id,
                message_type=message_type,
            )
        )
    ]
    await _append_labeled_shape_blocks(
        blocks,
        shape=detail.trigger_shape,
        media_service=media_service,
        locale=locale,
        label=tr(locale, "wordbank.group.trigger_label"),
        prefix="\n",
        include_text_only=True,
    )
    await _append_labeled_shape_blocks(
        blocks,
        shape=selected.response_shape,
        media_service=media_service,
        locale=locale,
        label=tr(locale, "wordbank.approval.response_label"),
        prefix="\n",
        include_text_only=True,
    )
    return MessagePlanEntry(blocks=tuple(blocks))


def _has_non_text_shape(shape: MessageShape | None) -> bool:
    return (
        shape is not None
        and not shape.is_empty()
        and any(atom.kind != "text" for atom in shape.atoms)
    )


async def build_search_results_card_plan_entry(
    *,
    items: tuple[WordbankSearchItem, ...],
    query: SearchCardQuery,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> MessagePlanEntry:
    preview_ids = {
        image_id
        for item in items
        for image_id in (
            item.trigger_preview_image_id,
            item.response_preview_image_id,
        )
        if image_id is not None
    }
    preview_bytes = await _load_image_bytes_map(preview_ids, media_service)
    return await _build_plan_with_fallback(
        preferred_builder=lambda: asyncio.to_thread(
            build_search_results_image_plan_entry,
            items=items,
            query=query,
            locale=locale,
            preview_bytes=preview_bytes,
        ),
        fallback_builder=lambda: build_search_items_text_plan_entry(
            items=items,
            locale=locale,
            media_service=media_service,
            page=query.page,
            limit=query.limit,
            has_more=query.page * query.limit < query.total_count,
        ),
        fallback_log_event="plugin.render_search_results_card_message.fallback_text",
        fallback_log_fields={
            "keyword": query.keyword,
            "page": query.page,
            "field": query.field,
            "has_image": query.has_image,
            "total_count": query.total_count,
        },
    )


async def build_group_detail_page_message_plan_entry(
    *,
    detail: WordbankGroupDetail,
    page: int,
    locale: LocaleCode,
    media_service: WordbankMediaService,
    page_size: int = GROUP_PAGE_SIZE,
) -> tuple[MessagePlanEntry, int]:
    total_pages = max(1, math.ceil(len(detail.responses) / max(page_size, 1)))
    page = min(max(page, 1), total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    responses = detail.responses[start:end]
    preview_ids = {
        image_id
        for shape in (
            detail.trigger_shape,
            *(response.response_shape for response in responses),
        )
        for image_id in _shape_preview_ids(shape)
    }
    preview_bytes = await _load_image_bytes_map(preview_ids, media_service)
    entry = await _build_plan_with_fallback(
        preferred_builder=lambda: asyncio.to_thread(
            build_group_detail_image_plan_entry,
            page_data=GroupDetailCardPage(
                detail=detail,
                page=page,
                total_pages=total_pages,
                page_size=page_size,
                start_index=start,
                responses=responses,
            ),
            locale=locale,
            preview_bytes=preview_bytes,
        ),
        fallback_builder=lambda: build_group_detail_page_plan_entry(
            detail=detail,
            page=page,
            total_pages=total_pages,
            locale=locale,
            media_service=media_service,
            page_size=page_size,
        ),
        fallback_log_event="plugin.render_group_detail_page_message.fallback_text",
        fallback_log_fields={
            "trigger_group_id": detail.trigger_group_id,
            "page": page,
            "total_pages": total_pages,
        },
    )
    return entry, total_pages


async def build_group_detail_page_plan_entry(
    *,
    detail: WordbankGroupDetail,
    page: int,
    total_pages: int,
    locale: LocaleCode,
    media_service: WordbankMediaService,
    page_size: int = GROUP_PAGE_SIZE,
) -> MessagePlanEntry:
    start = (page - 1) * page_size
    end = start + page_size
    responses = detail.responses[start:end]
    blocks: list[MessagePlanBlock] = [
        TextBlock(
            tr(
                locale,
                "wordbank.group.page_header",
                group_id=detail.trigger_group_id,
                status=detail.status,
                created_by=detail.created_by,
                probability=f"{detail.probability:g}",
                response_count=len(detail.responses),
                active_response_count=sum(
                    1
                    for item in detail.responses
                    if (
                        item.status == "approved"
                        and item.enabled == 1
                        and item.deleted_at == 0
                    )
                ),
                page=page,
                total_pages=total_pages,
            )
        )
    ]
    await _append_labeled_shape_blocks(
        blocks,
        shape=detail.trigger_shape,
        media_service=media_service,
        locale=locale,
        label=tr(locale, "wordbank.group.trigger_label"),
        prefix="\n",
        include_text_only=True,
    )
    for response in responses:
        blocks.append(
            TextBlock(
                "\n\n"
                + tr(
                    locale,
                    "wordbank.group.response_header",
                    response_item_id=response.response_item_id,
                    status=response.status,
                    enabled=_format_enabled(response.enabled, locale),
                    scope=response.scope,
                    weight=response.weight,
                    rule=_format_rule_text(response.rule),
                )
                + "\n"
            )
        )
        blocks.extend(
            (
                await build_shape_plan_entry(
                    response.response_shape,
                    media_service,
                    locale=locale,
                )
            ).blocks
        )
    if page < total_pages:
        blocks.append(
            TextBlock(
                "\n\n"
                + tr(
                    locale,
                    "wordbank.group.page_more",
                    next_page=page + 1,
                    group_id=detail.trigger_group_id,
                )
            )
        )
    return MessagePlanEntry(blocks=tuple(blocks))


async def build_creator_leaderboard_card_plan_entry(
    *,
    data: WordbankLeaderboardCardData,
    locale: LocaleCode,
) -> MessagePlanEntry:
    items = data.items
    if items:
        avatars = await asyncio.gather(
            *(QQAvatar.fetch_user(item.user_id, size=160) for item in items),
            return_exceptions=True,
        )
        items = tuple(
            replace(
                item,
                avatar=avatar if not isinstance(avatar, Exception) else None,
            )
            for item, avatar in zip(items, avatars, strict=False)
        )
        data = replace(data, items=items)
    return await _build_plan_with_fallback(
        preferred_builder=lambda: asyncio.to_thread(
            build_leaderboard_image_plan_entry,
            data=data,
            locale=locale,
        ),
        fallback_builder=lambda: _build_text_plan_entry(
            format_creator_leaderboard(data, locale=locale)
        ),
        fallback_log_event="plugin.render_creator_leaderboard_card_message.fallback_text",
        fallback_log_fields={
            "period": data.period,
            "items": len(data.items),
        },
    )


async def _append_labeled_shape_blocks(
    blocks: list[MessagePlanBlock],
    *,
    shape: MessageShape | None,
    media_service: WordbankMediaService,
    locale: LocaleCode,
    label: str,
    prefix: str = "",
    include_text_only: bool = False,
) -> None:
    if shape is None or shape.is_empty():
        return
    if not include_text_only and not _has_non_text_shape(shape):
        return
    blocks.append(TextBlock(f"{prefix}{label}\n"))
    blocks.extend(
        (
            await build_shape_plan_entry(
                shape,
                media_service,
                locale=locale,
            )
        ).blocks
    )


async def _build_plan_with_fallback[TPlanResult](
    *,
    preferred_builder: Callable[[], Awaitable[TPlanResult]],
    fallback_builder: Callable[[], Awaitable[TPlanResult] | TPlanResult],
    fallback_log_event: str,
    fallback_log_fields: Mapping[str, object],
) -> TPlanResult:
    try:
        return await preferred_builder()
    except Exception:
        log_perf(
            fallback_log_event,
            **cast(Any, dict(fallback_log_fields)),
        )
    fallback = fallback_builder()
    if isinstance(fallback, Awaitable):
        return await fallback
    return fallback


async def _build_text_plan_entry(text: str) -> MessagePlanEntry:
    return MessagePlanEntry(blocks=(TextBlock(text),))


def _format_enabled(enabled: int, locale: LocaleCode) -> str:
    return tr(
        locale,
        "wordbank.state.enabled" if enabled else "wordbank.state.disabled",
    )


def _format_rule_text(rule: dict[str, Any]) -> str:
    parts: list[str] = []
    role = str(rule.get("roles", "") or "").strip()
    if role:
        parts.append(f"roles={role}")
    call_count = rule.get("call_count")
    if isinstance(call_count, dict):
        window_seconds = int(call_count.get("window_seconds", 0))
        min_count = int(call_count.get("min", 0))
        max_count = int(call_count.get("max", 0))
        parts.append(f"call={window_seconds}:{min_count}:{max_count}")
    return ", ".join(parts) if parts else "-"


def _shape_preview_ids(shape: MessageShape) -> tuple[int, ...]:
    image_ids: list[int] = []
    for atom in shape.atoms:
        if atom.kind != "image" or atom.canonical_image_id is None:
            continue
        image_ids.append(atom.canonical_image_id)
        break
    return tuple(image_ids)


async def _load_shape_image_bytes(
    shape: MessageShape,
    media_service: WordbankMediaService,
) -> dict[int, bytes | None]:
    image_ids = {
        atom.canonical_image_id
        for atom in shape.atoms
        if atom.kind == "image" and atom.canonical_image_id is not None
    }
    return await _load_image_bytes_map(image_ids, media_service)


async def _load_image_bytes_map(
    image_ids: set[int],
    media_service: WordbankMediaService,
) -> dict[int, bytes | None]:
    if not image_ids:
        return {}
    loaded = await asyncio.gather(
        *(
            media_service.load_canonical_storage_bytes(image_id)
            for image_id in sorted(image_ids)
        )
    )
    return dict(zip(sorted(image_ids), loaded, strict=False))


def _build_image_payload_stats(
    image_bytes_by_id: Mapping[int, bytes | None],
) -> dict[str, object]:
    requested_image_ids = tuple(sorted(image_bytes_by_id))
    loaded_pairs = tuple(
        (image_id, len(image_bytes))
        for image_id, image_bytes in sorted(image_bytes_by_id.items())
        if image_bytes is not None
    )
    loaded_count = len(loaded_pairs)
    image_total_bytes = sum(size for _, size in loaded_pairs)
    return {
        "requested_image_ids": requested_image_ids,
        "loaded_image_ids": tuple(image_id for image_id, _ in loaded_pairs),
        "loaded_image_sizes": tuple(size for _, size in loaded_pairs),
        "loaded_count": loaded_count,
        "missing_count": len(image_bytes_by_id) - loaded_count,
        "image_total_bytes": image_total_bytes,
        "image_max_bytes": max((size for _, size in loaded_pairs), default=0),
    }
