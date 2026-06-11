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
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import resolve_locale
from src.lib.plugin_docs import DocsRenderContext, build_readme_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

from .handlers import (
    extract_reply_image_urls,
    parse_indexes,
    parse_request_text,
    run_search,
)
from .services import PicsearchEngine

name = "图片搜索"
description = "回复图片后发送“搜图 [引擎]”进行搜索。"
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

picsearch_matcher = on_regex(
    r"^\s*搜图(?:\s+(\S+))?\s*$",
    priority=5,
    block=True,
)


@picsearch_matcher.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    from src.lib.i18n.runtime import tr

    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    from .handlers import check_cooldown

    await check_cooldown(matcher, event)

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


@picsearch_matcher.got(
    "indexes",
    prompt=Message(
        "检测到有多张图片，请输入对应的序号，最多允许 3 张，可以使用空格进行分割："
    ),
)
async def _choose_indexes(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    indexes: Message = Arg(),
) -> None:
    from src.lib.i18n.runtime import tr

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
