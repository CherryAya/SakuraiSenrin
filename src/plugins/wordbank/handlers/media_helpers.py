"""Wordbank media-related handler helpers."""

from __future__ import annotations

import asyncio

from nonebot.adapters.onebot.v11.message import Message

from src.lib.i18n.runtime import tr
from src.plugins.wordbank.message_model import (
    MessageShape,
    combine_shapes,
    shape_from_image,
    shape_from_message,
    shape_from_text,
)
from src.plugins.wordbank.services.errors import WordbankUserError
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.text_parsing import has_meaningful_text

IMAGE_DOWNLOAD_RETRY_ATTEMPTS = 3
IMAGE_DOWNLOAD_RETRY_DELAY_SECONDS = 0.8
GUIDED_MESSAGE_IMAGE_LIMIT = 4


def extract_image_urls(message: Message) -> list[str]:
    urls: list[str] = []
    for segment in message:
        if segment.type != "image":
            continue
        url = str(segment.data.get("url") or "").strip()
        if url:
            urls.append(url)
    return urls


async def fetch_image_bytes_with_retry(
    url: str,
    *,
    attempts: int = IMAGE_DOWNLOAD_RETRY_ATTEMPTS,
    retry_delay_seconds: float = IMAGE_DOWNLOAD_RETRY_DELAY_SECONDS,
) -> bytes | None:
    from .passive import fetch_image_bytes as _fetch_image_bytes

    for attempt_index in range(max(1, attempts)):
        data = await _fetch_image_bytes(url)
        if data is not None:
            return data
        if attempt_index < attempts - 1:
            await asyncio.sleep(retry_delay_seconds)
    return None


async def fetch_image_bytes_from_message(
    message: Message,
    *,
    limit: int = 2,
) -> tuple[bytes, ...]:
    from .commands import fetch_image_bytes_with_retry as _fetch_image_bytes_with_retry

    urls = extract_image_urls(message)
    if not urls:
        return ()

    items: list[bytes] = []
    for url in urls[: max(1, limit)]:
        data = await _fetch_image_bytes_with_retry(url)
        if data is None:
            raise WordbankUserError(
                tr("zh-CN", "wordbank.error.image_download_failed"),
                key="wordbank.error.image_download_failed",
            )
        items.append(data)
    return tuple(items)


async def fetch_first_image_bytes_from_message(message: Message) -> bytes | None:
    items = await fetch_image_bytes_from_message(message, limit=1)
    return items[0] if items else None


def shape_from_text_value(text: str) -> MessageShape:
    return shape_from_text(text)


def shape_from_response_parts(text: str, *, image_id: int | None = None) -> MessageShape:
    parts = [shape_from_text_value(text)]
    if image_id is not None:
        parts.append(shape_from_image(image_id))
    return combine_shapes(*parts)


def shape_from_trigger_parts(text: str, *, image_id: int | None = None) -> MessageShape:
    parts = [shape_from_text_value(text)]
    if image_id is not None:
        parts.append(shape_from_image(image_id))
    return combine_shapes(*parts)


async def build_message_shape_from_message(
    media_service: WordbankMediaService,
    message: Message,
    *,
    image_limit: int = GUIDED_MESSAGE_IMAGE_LIMIT,
) -> MessageShape:
    from .commands import fetch_image_bytes_from_message as _fetch_image_bytes_from_message

    image_bytes_items = await _fetch_image_bytes_from_message(
        message,
        limit=image_limit,
    )
    image_ids: dict[int, int] = {}
    for index, image_bytes in enumerate(image_bytes_items):
        image = await media_service.ingest_image_bytes(image_bytes)
        image_ids[index] = image.canonical_id
    return shape_from_message(message, image_ids=image_ids)


async def build_shape_from_text_and_images(
    media_service: WordbankMediaService,
    *,
    text: str,
    message: Message,
    image_limit: int = GUIDED_MESSAGE_IMAGE_LIMIT,
) -> MessageShape:
    from .commands import fetch_image_bytes_from_message as _fetch_image_bytes_from_message

    image_bytes_items = await _fetch_image_bytes_from_message(
        message,
        limit=image_limit,
    )
    parts: list[MessageShape] = []
    if has_meaningful_text(text):
        parts.append(shape_from_text(text))
    for image_bytes in image_bytes_items:
        image = await media_service.ingest_image_bytes(image_bytes)
        parts.append(shape_from_image(image.canonical_id))
    return combine_shapes(*parts)
