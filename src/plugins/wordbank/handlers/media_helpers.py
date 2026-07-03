"""Wordbank media-related handler helpers."""

from __future__ import annotations

import asyncio

from nonebot.adapters.onebot.v11.message import Message

from src.lib.i18n.runtime import tr
from src.lib.long_task import LongTaskRunner
from src.plugins.wordbank.message_model import (
    MessageInput,
    MessageShape,
    combine_shapes,
    shape_from_image,
    shape_from_message_input,
    shape_from_text,
    shape_from_trigger_text,
)
from src.plugins.wordbank.services.errors import WordbankUserError
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.text_parsing import has_meaningful_text

IMAGE_DOWNLOAD_RETRY_ATTEMPTS = 3
IMAGE_DOWNLOAD_RETRY_DELAY_SECONDS = 0.8
GUIDED_MESSAGE_IMAGE_LIMIT = 4
ACTIVE_IMAGE_DOWNLOAD_CONCURRENCY = 4
ACTIVE_IMAGE_INGEST_CONCURRENCY = 2


def extract_image_urls(message: MessageInput) -> list[str]:
    if not isinstance(message, Message):
        return []
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
    message: MessageInput,
    *,
    limit: int = 2,
    task: LongTaskRunner | None = None,
) -> tuple[bytes, ...]:
    urls = extract_image_urls(message)
    if not urls:
        return ()

    active_urls = tuple(urls[: max(1, limit)])
    if task is not None:
        await task.advance(
            "downloading",
            current=0,
            total=len(active_urls),
            metadata={"image_count": len(active_urls)},
        )

    semaphore = asyncio.Semaphore(
        max(1, min(ACTIVE_IMAGE_DOWNLOAD_CONCURRENCY, len(active_urls)))
    )

    async def _download(index: int, url: str) -> tuple[int, bytes]:
        async with semaphore:
            data = await fetch_image_bytes_with_retry(url)
        if data is None:
            raise WordbankUserError(
                tr("zh-CN", "wordbank.error.image_download_failed"),
                key="wordbank.error.image_download_failed",
            )
        return index, data

    downloaded = await asyncio.gather(
        *(_download(index, url) for index, url in enumerate(active_urls))
    )
    if task is not None:
        await task.advance(
            "downloading",
            current=len(downloaded),
            total=len(active_urls),
            metadata={"image_count": len(downloaded)},
        )
    return tuple(data for _, data in sorted(downloaded, key=lambda item: item[0]))


async def fetch_first_image_bytes_from_message(
    message: MessageInput,
    *,
    task: LongTaskRunner | None = None,
) -> bytes | None:
    items = await fetch_image_bytes_from_message(message, limit=1, task=task)
    return items[0] if items else None


async def ingest_image_bytes_items(
    media_service: WordbankMediaService,
    image_bytes_items: tuple[bytes, ...],
    *,
    task: LongTaskRunner | None = None,
) -> tuple[int, ...]:
    if not image_bytes_items:
        return ()
    if task is not None:
        await task.advance(
            "ingesting",
            current=0,
            total=len(image_bytes_items),
            metadata={"image_count": len(image_bytes_items)},
        )

    semaphore = asyncio.Semaphore(
        max(1, min(ACTIVE_IMAGE_INGEST_CONCURRENCY, len(image_bytes_items)))
    )

    async def _ingest(index: int, image_bytes: bytes) -> tuple[int, int]:
        async with semaphore:
            image = await media_service.ingest_image_bytes(image_bytes)
        return index, image.canonical_id

    ingested = await asyncio.gather(
        *(
            _ingest(index, image_bytes)
            for index, image_bytes in enumerate(image_bytes_items)
        )
    )
    if task is not None:
        await task.advance(
            "ingesting",
            current=len(ingested),
            total=len(image_bytes_items),
            metadata={"image_count": len(ingested)},
        )
    return tuple(
        canonical_id for _, canonical_id in sorted(ingested, key=lambda item: item[0])
    )


def shape_from_text_value(text: str) -> MessageShape:
    return shape_from_text(text)


def shape_from_trigger_text_value(text: str) -> MessageShape:
    return shape_from_trigger_text(text)


def shape_from_response_parts(
    text: str, *, image_id: int | None = None
) -> MessageShape:
    parts = [shape_from_text_value(text)]
    if image_id is not None:
        parts.append(shape_from_image(image_id))
    return combine_shapes(*parts)


def shape_from_trigger_parts(text: str, *, image_id: int | None = None) -> MessageShape:
    parts = [shape_from_trigger_text_value(text)]
    if image_id is not None:
        parts.append(shape_from_image(image_id))
    return combine_shapes(*parts)


async def build_message_shape_from_message(
    media_service: WordbankMediaService,
    message: MessageInput,
    *,
    image_limit: int = GUIDED_MESSAGE_IMAGE_LIMIT,
    task: LongTaskRunner | None = None,
) -> MessageShape:
    image_bytes_items = await fetch_image_bytes_from_message(
        message,
        limit=image_limit,
        task=task,
    )
    image_ids: dict[int, int] = {}
    canonical_ids = await ingest_image_bytes_items(
        media_service,
        image_bytes_items,
        task=task,
    )
    if task is not None:
        await task.advance(
            "building_shape",
            metadata={"image_count": len(canonical_ids)},
        )
    for index, canonical_id in enumerate(canonical_ids):
        image_ids[index] = canonical_id
    return shape_from_message_input(message, image_ids=image_ids)


async def build_shape_from_text_and_images(
    media_service: WordbankMediaService,
    *,
    text: str,
    message: Message,
    image_limit: int = GUIDED_MESSAGE_IMAGE_LIMIT,
    parse_trigger_text: bool = False,
    task: LongTaskRunner | None = None,
) -> MessageShape:
    image_bytes_items = await fetch_image_bytes_from_message(
        message,
        limit=image_limit,
        task=task,
    )
    parts: list[MessageShape] = []
    if has_meaningful_text(text):
        text_shape = (
            shape_from_trigger_text_value(text)
            if parse_trigger_text
            else shape_from_text_value(text)
        )
        parts.append(text_shape)
    canonical_ids = await ingest_image_bytes_items(
        media_service,
        image_bytes_items,
        task=task,
    )
    if task is not None:
        await task.advance(
            "building_shape",
            metadata={"image_count": len(canonical_ids)},
        )
    for canonical_id in canonical_ids:
        parts.append(shape_from_image(canonical_id))
    return combine_shapes(*parts)
