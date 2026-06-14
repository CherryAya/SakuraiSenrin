"""图片搜索插件。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from nonebot import on_regex
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.typing import T_State

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.cooldown import build_cooldown_dependency
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.plugin_docs import DocsRenderContext, build_readme_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

from .handlers import (
    _picsearch_cooldown,
    build_cooldown_prompt,
    extract_reply_image_urls,
    parse_indexes,
    parse_request_text,
    run_search,
)
from .services import PicsearchEngine

name = tr("zh-CN", "plugin.picsearch.name")
description = tr("zh-CN", "plugin.picsearch.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    return build_readme_docs(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=ctx,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "docs": create_docs_meta(
            visible=True,
            category="fun",
            order=120,
            source=DOCS_SOURCE,
            slug="picsearch",
            aliases=("图片搜索",),
        ),
    },
)

MULTI_IMAGE_PROMPT = Message(tr("zh-CN", "picsearch.index_prompt"))

picsearch_matcher = on_regex(
    r"^\s*搜图(?:\s+(\S+))?\s*$",
    priority=5,
    block=True,
)


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
        await matcher.finish(tr(locale, "picsearch.reply_required"))

    try:
        engine = parse_request_text(event.get_plaintext())
    except ValueError:
        await matcher.finish(tr(locale, "picsearch.engine_invalid"))

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
        await matcher.reject(tr(locale, message_key))

    await run_search(
        matcher,
        locale=locale,
        engine=engine,
        image_urls=image_urls,
        indexes=parsed_indexes,
    )
