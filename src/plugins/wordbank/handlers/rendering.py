"""Async rendering helpers for wordbank rich messages."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, MutableMapping
import math
from typing import Any, cast

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.database.types import WordbankGroupDetail, WordbankSearchItem
from src.plugins.wordbank.debug import log_perf, perf_start
from src.plugins.wordbank.message_model import MessageShape
from src.plugins.wordbank.services.media import WordbankMediaService

from .search_cards import SearchCardQuery, render_search_results_card_bytes

GROUP_PAGE_SIZE = 10
MISSING_IMAGE_PLACEHOLDER = "[图片加载失败]"


async def render_shape_message(
    shape: MessageShape,
    media_service: WordbankMediaService,
    *,
    trace_fields: Mapping[str, object] | None = None,
    trace_sink: MutableMapping[str, object] | None = None,
) -> Message:
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
    message = Message()
    image_segments = 0
    for atom in shape.atoms:
        if atom.kind == "text" and atom.text:
            message += MessageSegment.text(atom.text)
        elif atom.kind == "at" and atom.target_id:
            message += MessageSegment.at(atom.target_id)
        elif atom.kind == "image" and atom.canonical_image_id is not None:
            image_bytes = image_bytes_by_id.get(atom.canonical_image_id)
            if image_bytes is None:
                message += MessageSegment.text(MISSING_IMAGE_PLACEHOLDER)
                continue
            message += MessageSegment.image(image_bytes)
            image_segments += 1
    if trace_fields is not None:
        log_perf(
            "plugin.build_passive_message.render_shape.segment_built",
            segments=len(list(message)),
            image_segments=image_segments,
            **cast(Any, payload_stats),
            **cast(Any, dict(trace_fields)),
        )
    return message


async def render_search_results_card_message(
    *,
    items: tuple[WordbankSearchItem, ...],
    query: SearchCardQuery,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> Message:
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
    image_bytes = await asyncio.to_thread(
        render_search_results_card_bytes,
        items=items,
        query=query,
        locale=locale,
        preview_bytes=preview_bytes,
    )
    return Message(MessageSegment.image(image_bytes))


async def render_group_detail_page_message(
    *,
    detail: WordbankGroupDetail,
    page: int,
    locale: LocaleCode,
    media_service: WordbankMediaService,
    page_size: int = GROUP_PAGE_SIZE,
) -> tuple[Message, int]:
    total_pages = max(1, math.ceil(len(detail.responses) / max(page_size, 1)))
    page = min(max(page, 1), total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    responses = detail.responses[start:end]

    message = Message(
        tr(
            locale,
            "wordbank.group.page_header",
            group_id=detail.trigger_group_id,
            status=detail.status,
            created_by=detail.created_by,
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
    message += MessageSegment.text("\n触发:\n")
    message += await render_shape_message(detail.trigger_shape, media_service)
    for response in responses:
        message += MessageSegment.text(
            "\n\n"
            + tr(
                locale,
                "wordbank.group.response_header",
                response_item_id=response.response_item_id,
                status=response.status,
                enabled=_format_enabled(response.enabled),
                scope=response.scope,
                probability=f"{response.probability:g}",
                weight=response.weight,
                rule=_format_rule_text(response.rule),
            )
            + "\n"
        )
        message += await render_shape_message(response.response_shape, media_service)
    if page < total_pages:
        message += MessageSegment.text(
            "\n\n"
            + tr(
                locale,
                "wordbank.group.page_more",
                next_page=page + 1,
                group_id=detail.trigger_group_id,
            )
        )
    return message, total_pages


def _format_enabled(enabled: int) -> str:
    return "开启" if enabled else "关闭"


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
