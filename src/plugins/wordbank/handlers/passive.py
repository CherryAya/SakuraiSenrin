"""Wordbank passive message handling."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupMessageEvent,
    MessageEvent,
    NoticeEvent,
)

from src.lib.interaction import is_revoke_signal
from src.logger import logger
from src.plugins.wordbank.debug import elapsed_ms, log_perf, perf_start
from src.plugins.wordbank.message_model import (
    MessageShape,
    shape_from_event,
    shape_from_message,
    shape_to_payload,
)
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.matching import SelectedMatch
from src.plugins.wordbank.services.media import MediaError, WordbankMediaService
from src.plugins.wordbank.services.rules import Role, RuleContext

MAX_PASSIVE_IMAGES = 4
MAX_IMAGE_DOWNLOAD_BYTES = 4 * 1024 * 1024


@dataclass(slots=True, frozen=True)
class PassiveResponse:
    text: str
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    group_id: str
    user_id: str
    message_type: str
    response_shape: MessageShape | None = None


@dataclass(slots=True, frozen=True)
class PassiveImageRef:
    url: str
    name_hints: tuple[str, ...] = ()


def build_rule_context(event: MessageEvent | NoticeEvent) -> RuleContext:
    role: Role = "member"
    sender = getattr(event, "sender", None)
    sender_role = str(getattr(sender, "role", "") or "")
    if sender_role == "owner":
        role = "owner"
    elif sender_role == "admin":
        role = "admin"
    group_id = str(getattr(event, "group_id", "") or "")
    return RuleContext(
        group_id=group_id,
        user_id=str(getattr(event, "user_id", "")),
        message_type=(
            "group" if isinstance(event, GroupMessageEvent) or group_id else "private"
        ),
        sender_role=role,
    )


def extract_image_refs(event: MessageEvent) -> list[PassiveImageRef]:
    refs: list[PassiveImageRef] = []
    message = getattr(event, "original_message", None) or event.message
    for segment in message:
        if segment.type != "image":
            continue
        url = str(segment.data.get("url") or "").strip()
        if not url:
            continue
        name_hints: list[str] = [url]
        file_value = str(segment.data.get("file") or "").strip()
        if file_value:
            name_hints.append(file_value)
        url_name = Path(urlparse(url).path).name.strip()
        if url_name:
            name_hints.append(url_name)
        refs.append(PassiveImageRef(url=url, name_hints=tuple(name_hints)))
    return refs


def extract_image_urls(event: MessageEvent) -> list[str]:
    return [item.url for item in extract_image_refs(event)]


def build_message_match_shapes(
    event: MessageEvent,
    *,
    image_ids: dict[int, int],
) -> tuple[tuple[str, MessageShape], ...]:
    shapes: list[tuple[str, MessageShape]] = []
    seen_keys: set[str] = set()
    for source, message in (
        ("message", getattr(event, "message", None)),
        ("original_message", getattr(event, "original_message", None)),
    ):
        if message is None:
            continue
        shape = shape_from_message(
            message,
            image_ids=image_ids,
            preserve_blank_text=True,
        )
        if shape.is_empty():
            continue
        key = shape_to_payload(shape)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        shapes.append((source, shape))
    return tuple(shapes)


def build_passive_response(
    selected: SelectedMatch,
    *,
    context: RuleContext,
    message_type: str,
) -> PassiveResponse:
    return PassiveResponse(
        text=selected.response.text,
        trigger_group_id=selected.candidate.group.id,
        trigger_variant_id=selected.candidate.trigger.id,
        response_item_id=selected.response.id,
        group_id=context.group_id,
        user_id=context.user_id,
        message_type=message_type,
        response_shape=selected.response.message_shape,
    )


def build_event_triggers(
    event: MessageEvent | NoticeEvent,
    bot: Bot,
) -> tuple[str, ...]:
    if isinstance(event, MessageEvent):
        for message in (
            getattr(event, "message", None),
            getattr(event, "original_message", None),
        ):
            if message is None:
                continue
            for segment in message:
                if segment.type != "at":
                    continue
                target = str(segment.data.get("qq", ""))
                if target == str(bot.self_id):
                    return ("event:at", "event:mention")
        return ()

    notice_type = str(getattr(event, "notice_type", ""))
    sub_type = str(getattr(event, "sub_type", ""))
    if notice_type == "notify" and sub_type == "poke":
        target_id = str(getattr(event, "target_id", ""))
        if target_id != str(bot.self_id):
            return ()
        return ("event:poke",)
    if notice_type == "group_increase":
        return ("event:join", "event:group_join", "event:group_increase")
    if notice_type == "group_decrease":
        return ("event:leave", "event:group_leave", "event:group_decrease")
    return ()


async def fetch_image_bytes(
    url: str,
    *,
    max_bytes: int = MAX_IMAGE_DOWNLOAD_BYTES,
    client: httpx.AsyncClient | None = None,
) -> bytes | None:
    start = perf_start()
    try:
        owned_client = client is None
        active_client = client or httpx.AsyncClient(timeout=5.0)
        try:
            async with active_client.stream("GET", url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    log_perf(
                        "passive.fetch_image_bytes.too_large",
                        start=start,
                        url=url,
                        content_length=content_length,
                        max_bytes=max_bytes,
                    )
                    return None

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        log_perf(
                            "passive.fetch_image_bytes.truncated",
                            start=start,
                            url=url,
                            downloaded_bytes=total,
                            max_bytes=max_bytes,
                        )
                        return None
                    chunks.append(chunk)
                data = b"".join(chunks)
                log_perf(
                    "passive.fetch_image_bytes.done",
                    start=start,
                    url=url,
                    bytes=len(data),
                    reused_client=not owned_client,
                )
                return data
        finally:
            if owned_client:
                await active_client.aclose()
    except Exception as exc:
        log_perf(
            "passive.fetch_image_bytes.failed",
            start=start,
            url=url,
            error=type(exc).__name__,
        )
        logger.warning(f"[Wordbank] image fetch skipped: {exc}")
        return None


async def resolve_message_image_ids(
    media_service: WordbankMediaService,
    image_refs: Sequence[PassiveImageRef],
) -> dict[int, int]:
    start = perf_start()
    canonical_ids: dict[int, int] = {}
    hint_hits = 0
    download_attempts = 0
    resolve_hits = 0
    active_refs = tuple(image_refs[:MAX_PASSIVE_IMAGES])
    shared_client: httpx.AsyncClient | None = None
    try:
        for image_ref in active_refs:
            hinted_canonical_id = media_service.resolve_canonical_id_from_hints(
                image_ref.name_hints,
            )
            if hinted_canonical_id is not None:
                hint_hits += 1
                canonical_ids[len(canonical_ids)] = hinted_canonical_id
                continue
            if shared_client is None:
                shared_client = httpx.AsyncClient(timeout=5.0)
            download_attempts += 1
            data = await fetch_image_bytes(image_ref.url, client=shared_client)
            if data is None:
                continue
            try:
                canonical_id = media_service.resolve_canonical_id(
                    data,
                    name_hints=image_ref.name_hints,
                )
            except MediaError as exc:
                logger.warning(f"[Wordbank] image match skipped: {exc}")
                continue
            if canonical_id is not None:
                resolve_hits += 1
                canonical_ids[len(canonical_ids)] = canonical_id
    finally:
        if shared_client is not None:
            await shared_client.aclose()
    log_perf(
        "passive.resolve_message_image_ids",
        start=start,
        refs=len(active_refs),
        resolved=len(canonical_ids),
        hint_hits=hint_hits,
        download_attempts=download_attempts,
        resolve_hits=resolve_hits,
    )
    return canonical_ids


async def handle_passive_message(
    bot: Bot,
    event: MessageEvent,
    service: WordbankService,
    media_service: WordbankMediaService,
) -> PassiveResponse | None:
    start = perf_start()
    if is_revoke_signal(event):
        log_perf(
            "passive.handle_message.skipped",
            start=start,
            reason="revoke_signal",
        )
        return None

    if str(event.user_id) == str(bot.self_id):
        log_perf(
            "passive.handle_message.skipped",
            start=start,
            reason="self_message",
        )
        return None

    context = build_rule_context(event)
    image_refs = extract_image_refs(event)
    resolve_start = perf_start()
    image_ids = await resolve_message_image_ids(media_service, image_refs)
    resolve_elapsed = elapsed_ms(resolve_start)
    message_shapes = build_message_match_shapes(event, image_ids=image_ids)
    primary_message_shape = (
        message_shapes[0][1]
        if message_shapes
        else shape_from_message(
            event.message,
            image_ids=image_ids,
            preserve_blank_text=True,
        )
    )
    selected: SelectedMatch | None = None
    message_match_elapsed = 0.0
    attempted_message_sources: list[str] = []
    for message_source, message_shape in message_shapes:
        attempted_message_sources.append(message_source)
        match_start = perf_start()
        selected = await service.match_message(
            message_shape,
            context=context,
            message_type="message",
        )
        message_match_elapsed += elapsed_ms(match_start)
        if selected is not None:
            log_perf(
                "passive.handle_message.matched",
                start=start,
                group_id=context.group_id or "-",
                user_id=context.user_id,
                image_refs=len(image_refs),
                resolved_images=len(image_ids),
                shape_atoms=len(message_shape.atoms),
                match_stage="message",
                message_source=message_source,
                message_attempts=len(attempted_message_sources),
                message_match_ms=f"{message_match_elapsed:.2f}",
                image_resolve_ms=f"{resolve_elapsed:.2f}",
                response_item_id=selected.response.id,
            )
            return build_passive_response(
                selected,
                context=context,
                message_type="message",
            )

    event_triggers = build_event_triggers(event, bot)
    event_match_count = 0
    if event_triggers:
        for event_trigger in event_triggers:
            event_match_count += 1
            match_start = perf_start()
            selected = await service.match_message(
                shape_from_event(event_trigger),
                context=context,
                message_type="event",
            )
            event_match_elapsed = elapsed_ms(match_start)
            if selected is not None:
                log_perf(
                    "passive.handle_message.matched",
                    start=start,
                    group_id=context.group_id or "-",
                    user_id=context.user_id,
                    image_refs=len(image_refs),
                    resolved_images=len(image_ids),
                    shape_atoms=len(primary_message_shape.atoms),
                    match_stage=event_trigger,
                    message_attempts=len(attempted_message_sources),
                    message_match_ms=f"{message_match_elapsed:.2f}",
                    event_match_ms=f"{event_match_elapsed:.2f}",
                    image_resolve_ms=f"{resolve_elapsed:.2f}",
                    response_item_id=selected.response.id,
                )
                return build_passive_response(
                    selected,
                    context=context,
                    message_type="event",
                )
    log_perf(
        "passive.handle_message.miss",
        start=start,
        group_id=context.group_id or "-",
        user_id=context.user_id,
        image_refs=len(image_refs),
        resolved_images=len(image_ids),
        shape_atoms=len(primary_message_shape.atoms),
        message_attempts=len(attempted_message_sources),
        message_match_ms=f"{message_match_elapsed:.2f}",
        image_resolve_ms=f"{resolve_elapsed:.2f}",
        event_triggers=event_match_count,
    )
    return None


async def handle_passive_notice(
    bot: Bot,
    event: NoticeEvent,
    service: WordbankService,
) -> PassiveResponse | None:
    start = perf_start()
    if is_revoke_signal(event):
        log_perf(
            "passive.handle_notice.skipped",
            start=start,
            reason="revoke_signal",
        )
        return None

    event_triggers = build_event_triggers(event, bot)
    if not event_triggers:
        log_perf(
            "passive.handle_notice.skipped",
            start=start,
            reason="no_event_trigger",
        )
        return None

    context = build_rule_context(event)
    for event_trigger in event_triggers:
        match_start = perf_start()
        selected = await service.match_message(
            shape_from_event(event_trigger),
            context=context,
            message_type="event",
        )
        match_elapsed = elapsed_ms(match_start)
        if selected is not None:
            log_perf(
                "passive.handle_notice.matched",
                start=start,
                group_id=context.group_id or "-",
                user_id=context.user_id,
                event_trigger=event_trigger,
                event_match_ms=f"{match_elapsed:.2f}",
                response_item_id=selected.response.id,
            )
            return build_passive_response(
                selected,
                context=context,
                message_type="event",
            )
    log_perf(
        "passive.handle_notice.miss",
        start=start,
        group_id=context.group_id or "-",
        user_id=context.user_id,
        event_triggers=len(event_triggers),
    )
    return None
