from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    )


async def build_forward_batch_payload_by_source_message_id(
    bot: Bot,
    *,
    media_service: Any,
    source_message_id: int,
) -> ForwardBatchPayload:
    from src.plugins.wordbank.handlers import build_message_shape_from_message

    detail = await bot.call_api("get_forward_msg", message_id=source_message_id)
    raw_items = _extract_forward_raw_items(detail)
    logger.debug(
        "[Wordbank][forward] payload fetch | "
        f"source_message_id={source_message_id} raw_node_count={len(raw_items)} "
        f"detail_type={type(detail).__name__}"
    )
    messages: list[Message] = []
    for index, item in enumerate(raw_items, start=1):
        message = _coerce_forward_message(item)
        if message is None:
            logger.debug(
                "[Wordbank][forward] node skipped | "
                f"source_message_id={source_message_id} node_index={index} "
                f"raw_type={type(item).__name__} reason=coerce_failed"
            )
            continue
        logger.debug(
            "[Wordbank][forward] node parsed | "
            f"source_message_id={source_message_id} node_index={index} "
            f"{describe_message_segments(message)}"
        )
        messages.append(message)
    if not messages:
        raise WordbankUserError(
            tr("zh-CN", "wordbank.error.forward_message_empty"),
            key="wordbank.error.forward_message_empty",
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
