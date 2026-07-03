"""Wordbank media-related handler helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from os import cpu_count
from pathlib import Path
from urllib.parse import urlparse

import httpx
from nonebot.adapters.onebot.v11.message import Message

from src.lib.i18n.runtime import tr
from src.lib.long_task import LongTaskRunner
from src.plugins.wordbank.message_model import (
    MessageInput,
    MessageShape,
    combine_shapes,
    iter_message_segments,
    shape_from_image,
    shape_from_message_input,
    shape_from_text,
    shape_from_trigger_text,
)
from src.plugins.wordbank.services.errors import WordbankUserError
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.text_parsing import has_meaningful_text


def _bounded_worker_count(multiplier: int, minimum: int, maximum: int) -> int:
    cores = max(1, cpu_count() or 1)
    return min(max(cores * multiplier, minimum), maximum)


IMAGE_DOWNLOAD_RETRY_ATTEMPTS = 3
IMAGE_DOWNLOAD_RETRY_DELAY_SECONDS = 0.8
GUIDED_MESSAGE_IMAGE_LIMIT = 4
ACTIVE_IMAGE_DOWNLOAD_CONCURRENCY = _bounded_worker_count(2, 4, 16)
ACTIVE_IMAGE_INGEST_CONCURRENCY = _bounded_worker_count(1, 2, 8)


@dataclass(slots=True, frozen=True)
class MessageImageRef:
    url: str
    name_hints: tuple[str, ...] = ()


@dataclass(slots=True)
class MessageShapeBuildContext:
    client: httpx.AsyncClient | None = None
    download_concurrency: int = ACTIVE_IMAGE_DOWNLOAD_CONCURRENCY
    ingest_concurrency: int = ACTIVE_IMAGE_INGEST_CONCURRENCY
    download_tasks: dict[str, asyncio.Task[bytes | None]] = field(default_factory=dict)
    resolution_tasks: dict[str, asyncio.Task[int | None]] = field(default_factory=dict)
    hint_cache: dict[tuple[str, ...], int | None] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    download_semaphore: asyncio.Semaphore = field(init=False)
    ingest_semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.download_semaphore = asyncio.Semaphore(max(1, self.download_concurrency))
        self.ingest_semaphore = asyncio.Semaphore(max(1, self.ingest_concurrency))


@asynccontextmanager
async def open_message_shape_build_context() -> AsyncIterator[MessageShapeBuildContext]:
    client = httpx.AsyncClient(timeout=5.0)
    try:
        yield MessageShapeBuildContext(client=client)
    finally:
        await client.aclose()


def extract_image_refs(message: MessageInput) -> list[MessageImageRef]:
    refs: list[MessageImageRef] = []
    for segment in iter_message_segments(message):
        if segment.type != "image":
            continue
        url = str(segment.data.get("url") or "").strip()
        if not url:
            continue
        hints: list[str] = [url]
        file_value = str(segment.data.get("file") or "").strip()
        if file_value:
            hints.append(file_value)
        url_name = Path(urlparse(url).path).name.strip()
        if url_name:
            hints.append(url_name)
        refs.append(MessageImageRef(url=url, name_hints=tuple(dict.fromkeys(hints))))
    return refs


def extract_image_urls(message: MessageInput) -> list[str]:
    return [ref.url for ref in extract_image_refs(message)]


async def fetch_image_bytes_with_retry(
    url: str,
    *,
    attempts: int = IMAGE_DOWNLOAD_RETRY_ATTEMPTS,
    retry_delay_seconds: float = IMAGE_DOWNLOAD_RETRY_DELAY_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> bytes | None:
    from .passive import fetch_image_bytes as _fetch_image_bytes

    for attempt_index in range(max(1, attempts)):
        data = await _fetch_image_bytes(url, client=client)
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
    build_context: MessageShapeBuildContext | None = None,
) -> tuple[bytes, ...]:
    refs = extract_image_refs(message)
    if not refs:
        return ()

    active_refs = tuple(refs[: max(1, limit)])
    if task is not None:
        await task.advance(
            "downloading",
            current=0,
            total=len(active_refs),
            metadata={"image_count": len(active_refs)},
        )

    semaphore = (
        build_context.download_semaphore
        if build_context is not None
        else asyncio.Semaphore(
            max(1, min(ACTIVE_IMAGE_DOWNLOAD_CONCURRENCY, len(active_refs)))
        )
    )

    async def _download(index: int, ref: MessageImageRef) -> tuple[int, bytes]:
        async with semaphore:
            data = await _download_image_bytes(ref.url, build_context=build_context)
        if data is None:
            raise WordbankUserError(
                tr("zh-CN", "wordbank.error.image_download_failed"),
                key="wordbank.error.image_download_failed",
            )
        return index, data

    downloaded = await asyncio.gather(
        *(_download(index, ref) for index, ref in enumerate(active_refs))
    )
    if task is not None:
        await task.advance(
            "downloading",
            current=len(downloaded),
            total=len(active_refs),
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
    build_context: MessageShapeBuildContext | None = None,
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

    semaphore = (
        build_context.ingest_semaphore
        if build_context is not None
        else asyncio.Semaphore(
            max(1, min(ACTIVE_IMAGE_INGEST_CONCURRENCY, len(image_bytes_items)))
        )
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
    build_context: MessageShapeBuildContext | None = None,
) -> MessageShape:
    image_ids = await resolve_message_image_ids(
        media_service,
        message,
        limit=image_limit,
        task=task,
        build_context=build_context,
    )
    if task is not None:
        await task.advance(
            "building_shape",
            metadata={"image_count": len(image_ids)},
        )
    return shape_from_message_input(message, image_ids=image_ids)


async def build_shape_from_text_and_images(
    media_service: WordbankMediaService,
    *,
    text: str,
    message: Message,
    image_limit: int = GUIDED_MESSAGE_IMAGE_LIMIT,
    parse_trigger_text: bool = False,
    task: LongTaskRunner | None = None,
    build_context: MessageShapeBuildContext | None = None,
) -> MessageShape:
    if build_context is None:
        image_bytes_items = await fetch_image_bytes_from_message(
            message,
            limit=image_limit,
            task=task,
        )
    else:
        image_bytes_items = await fetch_image_bytes_from_message(
            message,
            limit=image_limit,
            task=task,
            build_context=build_context,
        )
    parts: list[MessageShape] = []
    if has_meaningful_text(text):
        text_shape = (
            shape_from_trigger_text_value(text)
            if parse_trigger_text
            else shape_from_text_value(text)
        )
        parts.append(text_shape)
    if build_context is None:
        canonical_ids = await ingest_image_bytes_items(
            media_service,
            image_bytes_items,
            task=task,
        )
    else:
        canonical_ids = await ingest_image_bytes_items(
            media_service,
            image_bytes_items,
            task=task,
            build_context=build_context,
        )
    if task is not None:
        await task.advance(
            "building_shape",
            metadata={"image_count": len(canonical_ids)},
        )
    for canonical_id in canonical_ids:
        parts.append(shape_from_image(canonical_id))
    return combine_shapes(*parts)


async def resolve_message_image_ids(
    media_service: WordbankMediaService,
    message: MessageInput,
    *,
    limit: int = GUIDED_MESSAGE_IMAGE_LIMIT,
    task: LongTaskRunner | None = None,
    build_context: MessageShapeBuildContext | None = None,
) -> dict[int, int]:
    refs = extract_image_refs(message)
    if not refs:
        return {}

    active_refs = tuple(refs[: max(1, limit)])
    image_ids: dict[int, int] = {}
    unresolved: list[tuple[int, MessageImageRef]] = []
    hint_hits = 0

    if task is not None:
        await task.advance(
            "hint_resolving",
            current=0,
            total=len(active_refs),
            metadata={"image_count": len(active_refs)},
        )

    for index, ref in enumerate(active_refs):
        hinted = _resolve_canonical_id_from_hints(
            media_service,
            ref.name_hints,
            build_context=build_context,
        )
        if hinted is None:
            unresolved.append((index, ref))
            continue
        image_ids[index] = hinted
        hint_hits += 1

    if task is not None:
        await task.advance(
            "hint_resolving",
            current=len(active_refs),
            total=len(active_refs),
            metadata={
                "image_count": len(active_refs),
                "hint_hits": hint_hits,
                "unresolved": len(unresolved),
            },
        )
    if not unresolved:
        return image_ids

    if task is not None:
        await task.advance(
            "downloading",
            current=0,
            total=len(unresolved),
            metadata={"image_count": len(unresolved)},
        )

    resolved = await asyncio.gather(
        *(
            _resolve_message_image_ref(
                media_service,
                ref,
                build_context=build_context,
            )
            for _, ref in unresolved
        )
    )

    if task is not None:
        await task.advance(
            "downloading",
            current=len(unresolved),
            total=len(unresolved),
            metadata={"image_count": len(unresolved)},
        )

    for (index, _), canonical_id in zip(unresolved, resolved, strict=False):
        if canonical_id is not None:
            image_ids[index] = canonical_id
    return image_ids


def _resolve_canonical_id_from_hints(
    media_service: WordbankMediaService,
    name_hints: tuple[str, ...],
    *,
    build_context: MessageShapeBuildContext | None = None,
) -> int | None:
    if not name_hints:
        return None
    resolver = getattr(media_service, "resolve_canonical_id_from_hints", None)
    if resolver is None:
        return None
    if build_context is None:
        return resolver(name_hints)
    if name_hints in build_context.hint_cache:
        return build_context.hint_cache[name_hints]
    resolved = resolver(name_hints)
    build_context.hint_cache[name_hints] = resolved
    return resolved


async def _resolve_message_image_ref(
    media_service: WordbankMediaService,
    ref: MessageImageRef,
    *,
    build_context: MessageShapeBuildContext | None = None,
) -> int | None:
    if build_context is None or not ref.url:
        return await _resolve_message_image_ref_once(
            media_service,
            ref,
            build_context=build_context,
        )
    async with build_context._lock:
        task = build_context.resolution_tasks.get(ref.url)
        if task is None:
            task = asyncio.create_task(
                _resolve_message_image_ref_once(
                    media_service,
                    ref,
                    build_context=build_context,
                )
            )
            build_context.resolution_tasks[ref.url] = task
    return await task


async def _resolve_message_image_ref_once(
    media_service: WordbankMediaService,
    ref: MessageImageRef,
    *,
    build_context: MessageShapeBuildContext | None = None,
) -> int | None:
    hinted = _resolve_canonical_id_from_hints(
        media_service,
        ref.name_hints,
        build_context=build_context,
    )
    if hinted is not None:
        return hinted
    if not ref.url:
        return None
    data = await _download_image_bytes(ref.url, build_context=build_context)
    if data is None:
        return None
    resolver = getattr(media_service, "resolve_canonical_id", None)
    resolved = (
        resolver(data, name_hints=ref.name_hints) if resolver is not None else None
    )
    if resolved is not None:
        return resolved
    semaphore = (
        build_context.ingest_semaphore
        if build_context is not None
        else asyncio.Semaphore(1)
    )
    async with semaphore:
        image = await media_service.ingest_image_bytes(data)
    return image.canonical_id


async def _download_image_bytes(
    url: str,
    *,
    build_context: MessageShapeBuildContext | None = None,
) -> bytes | None:
    if build_context is None:
        return await fetch_image_bytes_with_retry(url)
    async with build_context._lock:
        task = build_context.download_tasks.get(url)
        if task is None:
            task = asyncio.create_task(
                fetch_image_bytes_with_retry(
                    url,
                    client=build_context.client,
                )
            )
            build_context.download_tasks[url] = task
    return await task
