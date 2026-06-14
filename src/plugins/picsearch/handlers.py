"""Picsearch handler helpers."""

from __future__ import annotations

from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.matcher import Matcher

from src.lib.cooldown import CooldownIsolateLevel, MemoryCooldown
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.logger import logger

from .services import (
    PicsearchEngine,
    PicsearchResult,
    get_engine_key,
    load_thumbnail_bytes,
    parse_engine,
    search_image,
)

MAX_IMAGE_SELECTION = 3
_picsearch_cooldown = MemoryCooldown(
    30,
    isolate_level=CooldownIsolateLevel.USER,
)


def extract_reply_image_urls(event: MessageEvent) -> list[str]:
    if event.reply is None:
        return []
    urls: list[str] = []
    for segment in event.reply.message:
        if segment.type != "image":
            continue
        url = str(segment.data.get("url", "") or segment.data.get("file", "")).strip()
        if url:
            urls.append(url)
    return urls


def parse_request_text(text: str) -> PicsearchEngine:
    _, _, rest = text.strip().partition(" ")
    return parse_engine(rest)


def parse_indexes(raw_text: str, image_count: int) -> list[int]:
    parts = [part for part in raw_text.strip().split() if part]
    if not parts:
        raise ValueError("empty")
    if len(parts) > MAX_IMAGE_SELECTION:
        raise ValueError("too_many")

    indexes: list[int] = []
    for part in parts:
        if not part.isdigit():
            raise ValueError("invalid")
        parsed = int(part)
        if parsed < 1 or parsed > image_count:
            raise ValueError("range")
        indexes.append(parsed - 1)
    return indexes


def build_result_message(
    index: int,
    result: PicsearchResult,
    thumbnail_bytes: bytes | None,
    *,
    locale: LocaleCode,
) -> Message:
    lines = [
        tr(locale, "picsearch.result.header", index=index),
        tr(locale, "picsearch.result.engine", engine=result.engine.value),
        tr(locale, "picsearch.result.similarity", similarity=result.similarity),
        tr(locale, "picsearch.result.title", title=result.title),
        tr(locale, "picsearch.result.author", author=result.author),
        tr(locale, "picsearch.result.link", link=result.source_url),
    ]
    message = Message("\n".join(lines))
    if thumbnail_bytes is not None:
        message += MessageSegment.image(thumbnail_bytes)
    return message


async def build_cooldown_prompt(
    event: MessageEvent,
    remaining_seconds: int,
) -> str:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    return tr(locale, "picsearch.cooldown", seconds=remaining_seconds)


def clear_picsearch_cooldowns() -> None:
    _picsearch_cooldown.clear()


async def run_search(
    matcher: Matcher,
    *,
    locale: LocaleCode,
    engine: PicsearchEngine,
    image_urls: list[str],
    indexes: list[int],
) -> None:
    if engine is PicsearchEngine.SAUCENAO and get_engine_key(engine) is None:
        await matcher.finish(tr(locale, "picsearch.engine_key_missing", engine=engine))

    for selected in indexes:
        await matcher.send(
            tr(
                locale,
                "picsearch.searching",
                index=selected + 1,
                engine=engine.value,
            )
        )
        try:
            result = await search_image(image_urls[selected], engine, locale=locale)
        except Exception as exc:
            logger.warning(
                "[Picsearch] search failed: "
                f"engine={engine.value} index={selected + 1}: {exc}"
            )
            await matcher.send(
                tr(
                    locale,
                    "picsearch.search_failed",
                    index=selected + 1,
                    engine=engine.value,
                )
            )
            continue

        if result is None:
            await matcher.send(
                tr(
                    locale,
                    "picsearch.no_result",
                    index=selected + 1,
                    engine=engine.value,
                )
            )
            continue

        thumbnail_bytes: bytes | None = None
        try:
            thumbnail_bytes = await load_thumbnail_bytes(result.thumbnail_url)
        except Exception as exc:
            logger.warning(
                "[Picsearch] thumbnail load failed: "
                f"engine={engine.value} index={selected + 1}: {exc}"
            )

        await matcher.send(
            build_result_message(
                selected + 1,
                result,
                thumbnail_bytes,
                locale=locale,
            )
        )

    await matcher.finish()
