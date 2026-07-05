from __future__ import annotations

import asyncio
from dataclasses import dataclass
from os import cpu_count
from typing import Any, Literal

from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import MessageSegment

from src.lib.i18n.runtime import tr
from src.lib.long_task import LongTaskRunner
from src.logger import logger
from src.plugins.wordbank.debug import describe_message_segments, describe_shape
from src.plugins.wordbank.message_model import (
    MessageInput,
    MessageShape,
    combine_shapes,
    iter_message_segments,
)
from src.plugins.wordbank.services.errors import WordbankUserError

FORWARD_BATCH_NODE_LIMIT = 50
FORWARD_BATCH_MAX_DEPTH = 3
FORWARD_NODE_BUILD_CONCURRENCY = min(max(1, cpu_count() or 1), 8)


@dataclass(slots=True, frozen=True)
class ResponseInputPayload:
    input_kind: Literal["single", "forward"]
    whole_shape: MessageShape
    split_shapes: tuple[MessageShape, ...]
    source_message_id: str | None = None
    messages: tuple[MessageInput, ...] = ()


@dataclass(slots=True, frozen=True)
class ForwardBatchPayload:
    source_message_id: str
    node_count: int
    whole_shape: MessageShape
    split_shapes: tuple[MessageShape, ...]
    messages: tuple[MessageInput, ...] = ()


def is_forward_message(message: Message) -> bool:
    return any(segment.type == "forward" for segment in message)


def is_forward_reply(event: MessageEvent) -> bool:
    reply = getattr(event, "reply", None)
    if reply is None:
        return False
    message = getattr(reply, "message", None)
    if not isinstance(message, Message):
        return False
    return is_forward_message(message)


def is_forward_input(event: MessageEvent) -> bool:
    if is_forward_reply(event):
        return True
    message = getattr(event, "message", None)
    return isinstance(message, Message) and is_forward_message(message)


def extract_forward_source_message_id(event: MessageEvent) -> str | None:
    candidates: list[Message] = []
    reply = getattr(event, "reply", None)
    if reply is not None:
        reply_message = getattr(reply, "message", None)
        if isinstance(reply_message, Message):
            candidates.append(reply_message)
    message = getattr(event, "message", None)
    if isinstance(message, Message):
        candidates.append(message)
    for candidate in candidates:
        for segment in candidate:
            if segment.type != "forward":
                continue
            message_id = segment.data.get("id")
            if message_id is None:
                continue
            if isinstance(message_id, str):
                return message_id if message_id else None
            return str(message_id)
    return None


async def build_forward_batch_payload(
    bot: Bot,
    event: MessageEvent,
    *,
    media_service: Any,
    max_depth: int = FORWARD_BATCH_MAX_DEPTH,
    task: LongTaskRunner | None = None,
) -> ForwardBatchPayload:
    source_message_id = extract_forward_source_message_id(event)
    if source_message_id is None:
        raise WordbankUserError(
            tr("zh-CN", "wordbank.error.forward_message_not_found"),
            key="wordbank.error.forward_message_not_found",
        )
    return await build_forward_batch_payload_by_source_message_id(
        bot,
        media_service=media_service,
        source_message_id=source_message_id,
        max_depth=max_depth,
        task=task,
    )


async def build_forward_batch_payload_by_source_message_id(
    bot: Bot,
    *,
    media_service: Any,
    source_message_id: str,
    max_depth: int = FORWARD_BATCH_MAX_DEPTH,
    task: LongTaskRunner | None = None,
) -> ForwardBatchPayload:
    from src.plugins.wordbank.handlers.media_helpers import (
        open_message_shape_build_context,
    )

    messages = await _collect_forward_messages_from_source_message_id(
        bot,
        source_message_id=source_message_id,
        max_depth=max_depth,
        depth=0,
        visited_ids=(),
    )
    if len(messages) > FORWARD_BATCH_NODE_LIMIT:
        raise WordbankUserError(
            tr(
                "zh-CN",
                "wordbank.error.forward_message_too_many",
                limit=FORWARD_BATCH_NODE_LIMIT,
            ),
            key="wordbank.error.forward_message_too_many",
            limit=FORWARD_BATCH_NODE_LIMIT,
        )
    if task is not None:
        await task.advance(
            "processing_items",
            current=0,
            total=len(messages),
            metadata={"source_message_id": source_message_id},
        )
    semaphore = asyncio.Semaphore(
        max(1, min(FORWARD_NODE_BUILD_CONCURRENCY, len(messages)))
    )

    async def _build_node_shape(
        index: int,
        message: MessageInput,
        build_context: Any,
    ) -> tuple[int, MessageShape]:
        from src.plugins.wordbank.handlers.media_helpers import (
            build_response_shape_from_message,
        )

        async with semaphore:
            shape = await build_response_shape_from_message(
                media_service,
                message,
                task=task,
                build_context=build_context,
            )
        logger.debug(
            "[Wordbank][forward] node shape | "
            f"source_message_id={source_message_id} node_index={index} "
            f"{describe_shape(shape)}"
        )
        return index, shape

    async with open_message_shape_build_context() as build_context:
        shaped = await asyncio.gather(
            *(
                _build_node_shape(index, message, build_context)
                for index, message in enumerate(messages, start=1)
            )
        )
    if task is not None:
        await task.advance(
            "processing_items",
            current=len(shaped),
            total=len(messages),
            metadata={"source_message_id": source_message_id},
        )
        await task.advance(
            "building_shape",
            metadata={"source_message_id": source_message_id},
        )
    shapes = [
        shape
        for _, shape in sorted(shaped, key=lambda item: item[0])
        if not shape.is_empty()
    ]
    if not shapes:
        raise WordbankUserError(
            tr("zh-CN", "wordbank.error.forward_message_empty"),
            key="wordbank.error.forward_message_empty",
        )
    whole = combine_shapes(*_with_separators(tuple(shapes)))
    logger.debug(
        "[Wordbank][forward] payload built | "
        f"source_message_id={source_message_id} parsed_nodes={len(messages)} "
        f"split_shapes={len(shapes)} whole={describe_shape(whole)}"
    )
    return ForwardBatchPayload(
        source_message_id=source_message_id,
        node_count=len(shapes),
        whole_shape=whole,
        split_shapes=tuple(shapes),
        messages=messages,
    )


async def build_response_input_payload(
    bot: Bot,
    event: MessageEvent,
    *,
    media_service: Any,
    max_forward_depth: int = FORWARD_BATCH_MAX_DEPTH,
    task: LongTaskRunner | None = None,
) -> ResponseInputPayload:
    from src.plugins.wordbank.handlers.media_helpers import (
        build_response_shape_from_message,
    )

    if is_forward_input(event):
        payload = await build_forward_batch_payload(
            bot,
            event,
            media_service=media_service,
            max_depth=max_forward_depth,
            task=task,
        )
        return ResponseInputPayload(
            input_kind="forward",
            whole_shape=payload.whole_shape,
            split_shapes=payload.split_shapes,
            source_message_id=payload.source_message_id,
            messages=payload.messages,
        )
    shape = await build_response_shape_from_message(
        media_service,
        event.message,
        task=task,
    )
    split_shapes = (shape,) if not shape.is_empty() else ()
    return ResponseInputPayload(
        input_kind="single",
        whole_shape=shape,
        split_shapes=split_shapes,
        source_message_id=None,
        messages=(),
    )


def _with_separators(shapes: tuple[MessageShape, ...]) -> tuple[MessageShape, ...]:
    parts: list[MessageShape] = []
    for index, shape in enumerate(shapes):
        if index > 0:
            from src.plugins.wordbank.message_model import shape_from_text

            parts.append(shape_from_text("\n"))
        parts.append(shape)
    return tuple(parts)


def _extract_forward_messages(detail: Any) -> tuple[MessageInput, ...]:
    messages: list[MessageInput] = []
    for item in _extract_forward_raw_items(detail):
        message = _coerce_forward_input(item)
        if message is not None:
            messages.append(message)
    return tuple(messages)


def _extract_forward_raw_items(detail: Any) -> tuple[Any, ...]:
    raw = None
    if isinstance(detail, dict):
        raw = detail.get("messages")
        if raw is None and isinstance(detail.get("data"), dict):
            raw = detail["data"].get("messages")
        if raw is None:
            raw = detail.get("data")
    else:
        raw = getattr(detail, "messages", None)
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(raw)
    return (raw,)


def _coerce_forward_input(raw: Any) -> MessageInput | None:
    if isinstance(raw, Message):
        return raw
    if isinstance(raw, MessageSegment):
        return raw
    if isinstance(raw, str):
        return raw if raw.strip() else None
    if isinstance(raw, (list, tuple)):
        items = tuple(item for item in raw if item is not None)
        return items if items else None
    if isinstance(raw, dict):
        segment = _coerce_forward_segment(raw)
        if segment is not None:
            return segment
        for key in ("content", "message", "messages", "raw_message"):
            nested = raw.get(key)
            message = _coerce_forward_input(nested)
            if message is not None:
                return message
        data = raw.get("data")
        if isinstance(data, dict):
            for key in ("content", "message", "messages", "raw_message"):
                nested = data.get(key)
                message = _coerce_forward_input(nested)
                if message is not None:
                    return message
    return None


def _coerce_forward_segment(raw: Any) -> MessageSegment | None:
    if isinstance(raw, MessageSegment):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return None
        return MessageSegment.text(raw)
    if not isinstance(raw, dict):
        return None
    segment_type = raw.get("type")
    data = raw.get("data")
    if not isinstance(segment_type, str) or not isinstance(data, dict):
        return None
    try:
        return MessageSegment(segment_type, data)
    except TypeError:
        return None


async def _collect_forward_messages_from_source_message_id(
    bot: Bot,
    *,
    source_message_id: str,
    max_depth: int,
    depth: int,
    visited_ids: tuple[str, ...],
) -> tuple[MessageInput, ...]:
    if depth >= max_depth:
        raise WordbankUserError(
            tr(
                "zh-CN",
                "wordbank.error.forward_message_too_deep",
                limit=max_depth,
            ),
            key="wordbank.error.forward_message_too_deep",
            limit=max_depth,
        )
    if source_message_id in visited_ids:
        raise WordbankUserError(
            tr(
                "zh-CN",
                "wordbank.error.forward_message_too_deep",
                limit=max_depth,
            ),
            key="wordbank.error.forward_message_too_deep",
            limit=max_depth,
        )
    detail = await _fetch_forward_message_detail(
        bot,
        source_message_id=source_message_id,
    )
    raw_items = _extract_forward_raw_items(detail)
    logger.debug(
        "[Wordbank][forward] payload fetch | "
        f"source_message_id={source_message_id} raw_node_count={len(raw_items)} "
        f"detail_type={type(detail).__name__} depth={depth}"
    )
    messages: list[MessageInput] = []
    next_visited = (*visited_ids, source_message_id)
    for index, item in enumerate(raw_items, start=1):
        message = _coerce_forward_input(item)
        if message is None:
            logger.debug(
                "[Wordbank][forward] node skipped | "
                f"source_message_id={source_message_id} node_index={index} "
                f"raw_type={type(item).__name__} reason=coerce_failed depth={depth}"
            )
            continue
        logger.debug(
            "[Wordbank][forward] node parsed | "
            f"source_message_id={source_message_id} node_index={index} depth={depth} "
            f"{describe_message_segments(message)}"
        )
        flattened = await _flatten_forward_message(
            bot,
            message=message,
            max_depth=max_depth,
            depth=depth,
            visited_ids=next_visited,
        )
        messages.extend(flattened)
    if not messages:
        raise WordbankUserError(
            tr("zh-CN", "wordbank.error.forward_message_empty"),
            key="wordbank.error.forward_message_empty",
        )
    return tuple(messages)


async def _fetch_forward_message_detail(
    bot: Bot,
    *,
    source_message_id: str,
) -> Any:
    candidates = _build_forward_message_id_candidates(source_message_id)
    last_exc: Exception | None = None
    for index, candidate in enumerate(candidates, start=1):
        try:
            detail = await bot.call_api("get_forward_msg", message_id=candidate)
        except Exception as exc:  # pragma: no cover - adapter specific
            last_exc = exc
            logger.debug(
                "[Wordbank][forward] payload fetch failed | "
                f"source_message_id={source_message_id} "
                f"candidate={candidate!r} candidate_type={type(candidate).__name__} "
                f"attempt={index}/{len(candidates)} exc_type={type(exc).__name__} "
                f"exc={exc}"
            )
            continue
        if index > 1:
            logger.debug(
                "[Wordbank][forward] payload fetch fallback succeeded | "
                f"source_message_id={source_message_id} "
                f"candidate={candidate!r} candidate_type={type(candidate).__name__}"
            )
        return detail
    if last_exc is not None:
        raise last_exc
    raise WordbankUserError(
        tr("zh-CN", "wordbank.error.forward_message_not_found"),
        key="wordbank.error.forward_message_not_found",
    )


def _build_forward_message_id_candidates(
    source_message_id: str,
) -> tuple[str | int, ...]:
    candidates: list[str | int] = [source_message_id]
    numeric_id = _parse_forward_source_message_id_as_int(source_message_id)
    if numeric_id is not None:
        candidates.append(numeric_id)
    return tuple(candidates)


def _parse_forward_source_message_id_as_int(source_message_id: str) -> int | None:
    normalized = source_message_id.strip()
    if not normalized.isdigit():
        return None
    try:
        return int(normalized)
    except ValueError:
        return None


async def _flatten_forward_message(
    bot: Bot,
    *,
    message: MessageInput,
    max_depth: int,
    depth: int,
    visited_ids: tuple[str, ...],
) -> tuple[MessageInput, ...]:
    if isinstance(message, Message) and not is_forward_message(message):
        return (message,)
    if not isinstance(message, Message):
        coerced = _coerce_forward_input(message)
        if coerced is None:
            return ()
        if not (isinstance(coerced, Message) and is_forward_message(coerced)):
            segments = tuple(iter_message_segments(coerced))
            return (segments,) if segments else ()
        message = coerced
    parts: list[MessageInput] = []
    buffer: list[MessageSegment] = []
    for segment in message:
        if segment.type != "forward":
            buffer.append(segment)
            continue
        if buffer:
            parts.append(tuple(buffer))
            buffer.clear()
        nested_source_id = _coerce_forward_segment_message_id(segment)
        if nested_source_id is None:
            logger.debug(
                "[Wordbank][forward] nested segment skipped | "
                f"depth={depth} reason=missing_source_id"
            )
            continue
        nested_messages = await _collect_forward_messages_from_source_message_id(
            bot,
            source_message_id=nested_source_id,
            max_depth=max_depth,
            depth=depth + 1,
            visited_ids=visited_ids,
        )
        parts.extend(nested_messages)
    if buffer:
        parts.append(tuple(buffer))
    return tuple(part for part in parts if not isinstance(part, tuple) or len(part) > 0)


def _coerce_forward_segment_message_id(segment: MessageSegment) -> str | None:
    if segment.type != "forward":
        return None
    raw_id = segment.data.get("id")
    if raw_id is None:
        return None
    if isinstance(raw_id, str):
        return raw_id if raw_id else None
    return str(raw_id)
