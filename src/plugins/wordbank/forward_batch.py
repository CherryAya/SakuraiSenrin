from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import MessageSegment

from src.lib.i18n.runtime import tr
from src.logger import logger
from src.plugins.wordbank.debug import describe_message_segments, describe_shape
from src.plugins.wordbank.message_model import MessageShape, combine_shapes
from src.plugins.wordbank.services.errors import WordbankUserError

FORWARD_BATCH_NODE_LIMIT = 50
FORWARD_BATCH_MAX_DEPTH = 3


@dataclass(slots=True, frozen=True)
class ResponseInputPayload:
    input_kind: Literal["single", "forward"]
    whole_shape: MessageShape
    split_shapes: tuple[MessageShape, ...]
    source_message_id: int | None = None


@dataclass(slots=True, frozen=True)
class ForwardBatchPayload:
    source_message_id: int
    node_count: int
    whole_shape: MessageShape
    split_shapes: tuple[MessageShape, ...]


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


def extract_forward_source_message_id(event: MessageEvent) -> int | None:
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
            try:
                return int(str(message_id))
            except ValueError:
                return None
    return None


async def build_forward_batch_payload(
    bot: Bot,
    event: MessageEvent,
    *,
    media_service: Any,
    max_depth: int = FORWARD_BATCH_MAX_DEPTH,
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
    )


async def build_forward_batch_payload_by_source_message_id(
    bot: Bot,
    *,
    media_service: Any,
    source_message_id: int,
    max_depth: int = FORWARD_BATCH_MAX_DEPTH,
) -> ForwardBatchPayload:
    from src.plugins.wordbank.handlers import build_message_shape_from_message

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
    shapes: list[MessageShape] = []
    for index, message in enumerate(messages, start=1):
        shape = await build_message_shape_from_message(media_service, message)
        logger.debug(
            "[Wordbank][forward] node shape | "
            f"source_message_id={source_message_id} node_index={index} "
            f"{describe_shape(shape)}"
        )
        if not shape.is_empty():
            shapes.append(shape)
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
    )


async def build_response_input_payload(
    bot: Bot,
    event: MessageEvent,
    *,
    media_service: Any,
    max_forward_depth: int = FORWARD_BATCH_MAX_DEPTH,
) -> ResponseInputPayload:
    from src.plugins.wordbank.handlers import build_message_shape_from_message

    if is_forward_input(event):
        payload = await build_forward_batch_payload(
            bot,
            event,
            media_service=media_service,
            max_depth=max_forward_depth,
        )
        return ResponseInputPayload(
            input_kind="forward",
            whole_shape=payload.whole_shape,
            split_shapes=payload.split_shapes,
            source_message_id=payload.source_message_id,
        )
    shape = await build_message_shape_from_message(media_service, event.message)
    split_shapes = (shape,) if not shape.is_empty() else ()
    return ResponseInputPayload(
        input_kind="single",
        whole_shape=shape,
        split_shapes=split_shapes,
        source_message_id=None,
    )


def _with_separators(shapes: tuple[MessageShape, ...]) -> tuple[MessageShape, ...]:
    parts: list[MessageShape] = []
    for index, shape in enumerate(shapes):
        if index > 0:
            from src.plugins.wordbank.message_model import shape_from_text

            parts.append(shape_from_text("\n"))
        parts.append(shape)
    return tuple(parts)


def _extract_forward_messages(detail: Any) -> tuple[Message, ...]:
    messages: list[Message] = []
    for item in _extract_forward_raw_items(detail):
        message = _coerce_forward_message(item)
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


def _coerce_forward_message(raw: Any) -> Message | None:
    if isinstance(raw, Message):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return None
        return Message([MessageSegment.text(raw)])
    if isinstance(raw, list):
        try:
            return Message(raw)
        except Exception:
            segments: list[MessageSegment] = []
            for item in raw:
                segment = _coerce_forward_segment(item)
                if segment is not None:
                    segments.append(segment)
            return Message(segments) if segments else None
    if isinstance(raw, dict):
        segment = _coerce_forward_segment(raw)
        if segment is not None:
            return Message([segment])
        for key in ("content", "message", "messages", "raw_message"):
            nested = raw.get(key)
            message = _coerce_forward_message(nested)
            if message is not None:
                return message
        data = raw.get("data")
        if isinstance(data, dict):
            for key in ("content", "message", "messages", "raw_message"):
                nested = data.get(key)
                message = _coerce_forward_message(nested)
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
    source_message_id: int,
    max_depth: int,
    depth: int,
    visited_ids: tuple[int, ...],
) -> tuple[Message, ...]:
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
    detail = await bot.call_api("get_forward_msg", message_id=source_message_id)
    raw_items = _extract_forward_raw_items(detail)
    logger.debug(
        "[Wordbank][forward] payload fetch | "
        f"source_message_id={source_message_id} raw_node_count={len(raw_items)} "
        f"detail_type={type(detail).__name__} depth={depth}"
    )
    messages: list[Message] = []
    next_visited = (*visited_ids, source_message_id)
    for index, item in enumerate(raw_items, start=1):
        message = _coerce_forward_message(item)
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


async def _flatten_forward_message(
    bot: Bot,
    *,
    message: Message,
    max_depth: int,
    depth: int,
    visited_ids: tuple[int, ...],
) -> tuple[Message, ...]:
    if not is_forward_message(message):
        return (message,)
    parts: list[Message] = []
    buffer: list[MessageSegment] = []
    for segment in message:
        if segment.type != "forward":
            buffer.append(segment)
            continue
        if buffer:
            parts.append(Message(buffer.copy()))
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
        parts.append(Message(buffer.copy()))
    return tuple(part for part in parts if len(part) > 0)


def _coerce_forward_segment_message_id(segment: MessageSegment) -> int | None:
    if segment.type != "forward":
        return None
    raw_id = segment.data.get("id")
    if raw_id is None:
        return None
    try:
        parsed = int(str(raw_id))
    except ValueError:
        return None
    return parsed if parsed > 0 else None
