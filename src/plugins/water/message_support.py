"""Water plugin message-plan helpers."""

from __future__ import annotations

from collections.abc import Awaitable

from src.lib.message_plan import (
    ImageBytesBlock,
    MessagePlanEntry,
    TextBlock,
)


def build_text_plan_entry(text: str) -> MessagePlanEntry:
    return MessagePlanEntry(blocks=(TextBlock(text),))


def build_image_plan_entry(image_bytes: bytes) -> MessagePlanEntry:
    return MessagePlanEntry(blocks=(ImageBytesBlock(image_bytes),))


async def build_image_or_text_plan_entry(
    *,
    image_bytes: bytes | None,
    fallback_text: str | Awaitable[str],
) -> MessagePlanEntry:
    if image_bytes is not None:
        return build_image_plan_entry(image_bytes)
    if isinstance(fallback_text, Awaitable):
        fallback_text = await fallback_text
    return build_text_plan_entry(fallback_text)
