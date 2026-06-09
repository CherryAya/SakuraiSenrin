"""Wordbank passive message handling."""

from __future__ import annotations

from collections.abc import Sequence

import httpx
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupMessageEvent,
    MessageEvent,
    NoticeEvent,
)

from src.logger import logger
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import MediaError, WordbankMediaService
from src.plugins.wordbank.services.rules import Role, RuleContext

REVOKE_MARKERS = ("revoke", "recall")
MAX_PASSIVE_IMAGES = 4


def _contains_revoke_marker(value: object) -> bool:
    text = str(value).casefold()
    return any(marker in text for marker in REVOKE_MARKERS)


def is_revoke_signal(event: object) -> bool:
    event_name = event.__class__.__name__
    if _contains_revoke_marker(event_name):
        return True

    for attr in ("post_type", "notice_type", "sub_type", "event_name", "type"):
        value = getattr(event, attr, "")
        if value and _contains_revoke_marker(value):
            return True

    raw_message = getattr(event, "raw_message", "")
    if raw_message and _contains_revoke_marker(raw_message):
        return True

    message = getattr(event, "message", None)
    if message is None:
        return False
    for segment in message:
        if _contains_revoke_marker(getattr(segment, "type", "")):
            return True
        data = getattr(segment, "data", {})
        if isinstance(data, dict) and any(
            _contains_revoke_marker(key) or _contains_revoke_marker(value)
            for key, value in data.items()
        ):
            return True
    return False


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


def extract_image_urls(event: MessageEvent) -> list[str]:
    urls: list[str] = []
    for segment in event.message:
        if segment.type != "image":
            continue
        url = str(segment.data.get("url") or "").strip()
        if url:
            urls.append(url)
    return urls


def build_event_triggers(
    event: MessageEvent | NoticeEvent,
    bot: Bot,
) -> tuple[str, ...]:
    if isinstance(event, MessageEvent):
        for segment in event.message:
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


async def fetch_image_bytes(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content
    except Exception as exc:
        logger.warning(f"[Wordbank] image fetch skipped: {exc}")
        return None


async def resolve_message_image_ids(
    media_service: WordbankMediaService,
    urls: Sequence[str],
) -> list[int]:
    canonical_ids: list[int] = []
    for url in urls[:MAX_PASSIVE_IMAGES]:
        data = await fetch_image_bytes(url)
        if data is None:
            continue
        try:
            canonical_id = media_service.resolve_canonical_id(data)
        except MediaError as exc:
            logger.warning(f"[Wordbank] image match skipped: {exc}")
            continue
        if canonical_id is not None:
            canonical_ids.append(canonical_id)
    return canonical_ids


async def handle_passive_message(
    bot: Bot,
    event: MessageEvent,
    service: WordbankService,
    media_service: WordbankMediaService,
) -> str | None:
    if is_revoke_signal(event):
        return None

    if str(event.user_id) == str(bot.self_id):
        return None

    context = build_rule_context(event)
    text = event.message.extract_plain_text()
    if text.strip():
        selected = await service.match_text(text, context=context)
        if selected is not None:
            return selected.response.text

    event_triggers = build_event_triggers(event, bot)
    if event_triggers:
        selected = await service.match_event(event_triggers, context=context)
        if selected is not None:
            return selected.response.text

    image_urls = extract_image_urls(event)
    if not image_urls:
        return None
    image_ids = await resolve_message_image_ids(media_service, image_urls)
    if not image_ids:
        return None
    selected = await service.match_images(image_ids, context=context)
    return selected.response.text if selected is not None else None


async def handle_passive_notice(
    bot: Bot,
    event: NoticeEvent,
    service: WordbankService,
) -> str | None:
    if is_revoke_signal(event):
        return None

    event_triggers = build_event_triggers(event, bot)
    if not event_triggers:
        return None

    context = build_rule_context(event)
    selected = await service.match_event(event_triggers, context=context)
    return selected.response.text if selected is not None else None
