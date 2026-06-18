"""图片搜索插件。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from httpx import AsyncClient
from nonebot import on_regex
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.typing import T_State
from PicImageSearch import Ascii2D, Network, SauceNAO

from src.config import config
from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.cooldown import (
    CooldownIsolateLevel,
    MemoryCooldown,
    build_cooldown_dependency,
)
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.messages import text_message
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_doc_demo_message,
    build_readme_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger

name = tr("zh-CN", "plugin.picsearch.name")
description = tr("zh-CN", "plugin.picsearch.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"
MAX_IMAGE_SELECTION = 3


class PicsearchEngine(StrEnum):
    SAUCENAO = "saucenao"
    ASCII2D = "ascii2d"


@dataclass(slots=True, frozen=True)
class PicsearchResult:
    engine: PicsearchEngine
    title: str
    author: str
    similarity: str
    source_url: str
    thumbnail_url: str


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    return build_readme_docs(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=ctx,
    )


def _build_error_demo(locale: LocaleCode, message: str) -> Message:
    return build_doc_demo_message(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        locale=locale,
        feature_query="main",
        prefix_text=message,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#748FFC",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "docs": create_docs_meta(
            visible=True,
            category="fun",
            order=120,
            source=DOCS_SOURCE,
            slug="picsearch",
            aliases=("图片搜索", "搜索图片", "搜图", "picsearch"),
        ),
    },
)

MULTI_IMAGE_PROMPT = text_message(tr("zh-CN", "picsearch.index_prompt"))
_picsearch_cooldown = MemoryCooldown(
    30,
    isolate_level=CooldownIsolateLevel.USER,
)

picsearch_matcher = on_regex(
    r"^\s*搜图(?:\s+(\S+))?\s*$",
    priority=5,
    block=True,
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


def parse_engine(text: str) -> PicsearchEngine:
    normalized = text.strip().lower()
    if normalized in {"", "saucenao", "sauce", "s"}:
        return PicsearchEngine.SAUCENAO
    if normalized in {"ascii2d", "ascii", "a"}:
        return PicsearchEngine.ASCII2D
    raise ValueError(normalized)


def get_engine_key(engine: PicsearchEngine) -> str | None:
    if engine is PicsearchEngine.SAUCENAO:
        return config.SAUCENAO_KEY
    return config.ASCII2D_KEY


def get_thumbnail_url(item: Any) -> str:
    for attr in ("thumbnail", "thumbnail_url"):
        value = getattr(item, attr, "")
        if isinstance(value, str) and value:
            return value
    return ""


def _to_result(
    engine: PicsearchEngine,
    item: Any,
    *,
    locale: LocaleCode = "zh-CN",
) -> PicsearchResult:
    if engine is PicsearchEngine.SAUCENAO:
        return PicsearchResult(
            engine=engine,
            title=str(
                getattr(item, "title", "")
                or tr(locale, "picsearch.result.unknown_title")
            ),
            author=str(
                getattr(item, "author", "")
                or tr(locale, "picsearch.result.unknown_author")
            ),
            similarity=str(
                getattr(item, "similarity", "")
                or tr(locale, "picsearch.result.unknown_similarity")
            ),
            source_url=str(
                getattr(item, "source", "")
                or tr(locale, "picsearch.result.unknown_source")
            ),
            thumbnail_url=get_thumbnail_url(item),
        )

    return PicsearchResult(
        engine=engine,
        title=str(
            getattr(item, "title", "") or tr(locale, "picsearch.result.unknown_title")
        ),
        author=str(
            getattr(item, "author", "") or tr(locale, "picsearch.result.unknown_author")
        ),
        similarity="N/A",
        source_url=str(
            getattr(item, "url", "") or tr(locale, "picsearch.result.unknown_source")
        ),
        thumbnail_url=get_thumbnail_url(item),
    )


async def search_image(
    image_url: str,
    engine: PicsearchEngine,
    *,
    locale: LocaleCode = "zh-CN",
) -> PicsearchResult | None:
    async with Network(proxies=config.HTTP_PROXY) as network:
        if engine is PicsearchEngine.SAUCENAO:
            response = await SauceNAO(
                api_key=get_engine_key(engine),
                client=network,
            ).search(url=image_url)
        else:
            response = await Ascii2D(
                bovw=False,
                client=network,
            ).search(url=image_url)

    raw_items = getattr(response, "raw", None)
    if not raw_items:
        return None

    return _to_result(engine, raw_items[0], locale=locale)


async def load_thumbnail_bytes(url: str) -> bytes | None:
    if not url:
        return None

    async with AsyncClient(proxy=config.HTTP_PROXY) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


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
    message = text_message("\n".join(lines))
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


@picsearch_matcher.handle(
    parameterless=[
        build_cooldown_dependency(
            _picsearch_cooldown,
            prompt_builder=build_cooldown_prompt,
        )
    ]
)
async def _(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)

    image_urls = extract_reply_image_urls(event)
    if not image_urls:
        await matcher.finish(
            _build_error_demo(locale, tr(locale, "picsearch.reply_required"))
        )

    try:
        engine = parse_request_text(event.get_plaintext())
    except ValueError:
        await matcher.finish(
            _build_error_demo(locale, tr(locale, "picsearch.engine_invalid"))
        )

    state["picsearch_engine"] = engine.value
    state["picsearch_image_urls"] = image_urls

    if len(image_urls) == 1:
        await run_search(
            matcher,
            locale=locale,
            engine=engine,
            image_urls=image_urls,
            indexes=[0],
        )


@picsearch_matcher.got("indexes", prompt=MULTI_IMAGE_PROMPT)
async def _choose_indexes(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    indexes: Message = Arg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    image_urls = state.get("picsearch_image_urls")
    engine_text = str(state.get("picsearch_engine", PicsearchEngine.SAUCENAO.value))

    if not isinstance(image_urls, list) or not all(
        isinstance(item, str) for item in image_urls
    ):
        await matcher.finish(tr(locale, "picsearch.reply_required"))

    try:
        parsed_indexes = parse_indexes(indexes.extract_plain_text(), len(image_urls))
        engine = PicsearchEngine(engine_text)
    except ValueError as exc:
        message_key = cast(
            MessageKey,
            {
                "empty": "picsearch.index_invalid",
                "invalid": "picsearch.index_invalid",
                "range": "picsearch.index_out_of_range",
                "too_many": "picsearch.index_too_many",
            }.get(str(exc), "picsearch.index_invalid"),
        )
        await matcher.reject(_build_error_demo(locale, tr(locale, message_key)))

    await run_search(
        matcher,
        locale=locale,
        engine=engine,
        image_urls=image_urls,
        indexes=parsed_indexes,
    )
