from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import MessageSegment

from src.plugins.wordbank.message_model import MessageShape, combine_shapes
from src.plugins.wordbank.services.errors import WordbankUserError

FORWARD_BATCH_NODE_LIMIT = 50


@dataclass(slots=True, frozen=True)
class ForwardBatchPayload:
    source_message_id: int
    node_count: int
    whole_shape: MessageShape
    split_shapes: tuple[MessageShape, ...]


def is_forward_reply(event: MessageEvent) -> bool:
    reply = getattr(event, "reply", None)
    if reply is None:
        return False
    message = getattr(reply, "message", None)
    if not isinstance(message, Message):
        return False
    return any(segment.type == "forward" for segment in message)


def extract_forward_source_message_id(event: MessageEvent) -> int | None:
    reply = getattr(event, "reply", None)
    if reply is None:
        return None
    message = getattr(reply, "message", None)
    if not isinstance(message, Message):
        return None
    for segment in message:
        if segment.type == "forward":
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
    source_message_id: int | None = None,
) -> ForwardBatchPayload:
    from src.plugins.wordbank.handlers import build_message_shape_from_message

    source_message_id = source_message_id or extract_forward_source_message_id(event)
    if source_message_id is None:
        raise WordbankUserError(
            "未找到可解析的合并转发消息。",
            key="wordbank.error.forward_message_not_found",
        )
    detail = await bot.call_api("get_forward_msg", message_id=source_message_id)
    messages = _extract_forward_messages(detail)
    if not messages:
        raise WordbankUserError(
            "合并转发里没有可用内容。",
            key="wordbank.error.forward_message_empty",
        )
    if len(messages) > FORWARD_BATCH_NODE_LIMIT:
        raise WordbankUserError(
            f"合并转发节点过多，最多支持 {FORWARD_BATCH_NODE_LIMIT} 个节点。",
            key="wordbank.error.forward_message_too_many",
            limit=FORWARD_BATCH_NODE_LIMIT,
        )
    shapes: list[MessageShape] = []
    for message in messages:
        shape = await build_message_shape_from_message(media_service, message)
        if not shape.is_empty():
            shapes.append(shape)
    if not shapes:
        raise WordbankUserError(
            "合并转发里没有可用内容。",
            key="wordbank.error.forward_message_empty",
        )
    whole = combine_shapes(*_with_separators(tuple(shapes)))
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
    raw = None
    if isinstance(detail, dict):
        raw = detail.get("messages")
        if raw is None and isinstance(detail.get("data"), dict):
            raw = detail["data"].get("messages")
        if raw is None:
            raw = detail.get("data")
    else:
        raw = getattr(detail, "messages", None)
    messages: list[Message] = []
    for item in raw or ():
        if isinstance(item, Message):
            messages.append(item)
            continue
        if isinstance(item, list):
            try:
                messages.append(Message(item))
            except Exception:
                continue
            continue
        if isinstance(item, dict):
            content = item.get("content")
            if isinstance(content, Message):
                messages.append(content)
                continue
            if isinstance(content, list):
                try:
                    messages.append(Message(content))
                except Exception:
                    continue
                continue
            if isinstance(content, str):
                messages.append(Message([MessageSegment.text(content)]))
                continue
            message = item.get("message")
            if isinstance(message, Message):
                messages.append(message)
                continue
            if isinstance(message, list):
                try:
                    messages.append(Message(message))
                except Exception:
                    continue
    return tuple(messages)
