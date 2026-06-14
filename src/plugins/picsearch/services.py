"""Picsearch services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from httpx import AsyncClient
from PicImageSearch import Ascii2D, Network, SauceNAO

from src.config import config
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode


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
