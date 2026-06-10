from __future__ import annotations

from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebug import App
import pytest

nonebot.init(
    SUPERUSERS={"1"},
    IGNORED_USERS=set(),
    MAIN_GROUP_ID="10001",
    GITHUB_TOKEN="test-token",
    GITHUB_REPO="owner/repo",
    GITHUB_BRANCH="main",
    WORDBANK_MEDIA_PROVIDER="local",
)
nonebot.load_plugin("src.plugins.picsearch")

from src.lib.plugin_docs import DocsRenderContext
from src.plugins.help import help_matcher
from src.plugins.picsearch import build_docs, picsearch_matcher
from src.plugins.picsearch import handlers as picsearch_handlers
from src.plugins.picsearch.services import PicsearchEngine, PicsearchResult
from tests.plugins.water.helpers import attach_reply_message, build_group_message_event

MULTI_IMAGE_PROMPT = Message(
    "检测到有多张图片，请输入对应的序号，最多允许 3 张，可以使用空格进行分割："
)


@pytest.mark.asyncio
async def test_picsearch_requires_reply_image(app: App) -> None:
    picsearch_handlers._picsearch_cooldowns.clear()
    event = build_group_message_event("搜图")

    async with app.test_matcher(picsearch_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "请先回复一条包含图片的消息，再发送搜图指令。",
            bot=bot,
        )
        ctx.should_finished(picsearch_matcher)


@pytest.mark.asyncio
async def test_picsearch_single_image_uses_default_saucenao(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picsearch_handlers._picsearch_cooldowns.clear()
    event = build_group_message_event("搜图", message_id=1)
    attach_reply_message(
        event,
        MessageSegment("image", {"url": "https://example.com/1.png"}),
    )

    monkeypatch.setattr(picsearch_handlers, "get_engine_key", lambda engine: "key")
    monkeypatch.setattr(
        picsearch_handlers,
        "search_image",
        AsyncMock(
            return_value=PicsearchResult(
                engine=PicsearchEngine.SAUCENAO,
                title="标题",
                author="作者",
                similarity="91.2",
                source_url="https://example.com/source",
                thumbnail_url="https://example.com/thumb.png",
            )
        ),
    )
    monkeypatch.setattr(
        picsearch_handlers,
        "load_thumbnail_bytes",
        AsyncMock(return_value=b"thumb"),
    )

    async with app.test_matcher(picsearch_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "正在搜索第 1 张图片，请稍后...（引擎：saucenao）",
            bot=bot,
        )
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.text(
                        "第 1 张图片搜索结果：\n"
                        "引擎：saucenao\n"
                        "相似度：91.2\n"
                        "标题：标题\n"
                        "作者：作者\n"
                        "链接：https://example.com/source"
                    ),
                    MessageSegment.image(b"thumb"),
                ]
            ),
            bot=bot,
        )
        ctx.should_finished(picsearch_matcher)


@pytest.mark.asyncio
async def test_picsearch_rejects_invalid_index_on_multi_image(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picsearch_handlers._picsearch_cooldowns.clear()
    first = build_group_message_event("搜图 ascii2d", message_id=1)
    attach_reply_message(
        first,
        MessageSegment("image", {"url": "https://example.com/1.png"}),
        MessageSegment("image", {"url": "https://example.com/2.png"}),
    )
    second = build_group_message_event("3", message_id=2)

    async with app.test_matcher(picsearch_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            MULTI_IMAGE_PROMPT,
            bot=bot,
        )
        ctx.should_rejected(picsearch_matcher)

        ctx.receive_event(bot, second)
        ctx.should_call_send(second, "图片序号超出范围，请重新输入。", bot=bot)
        ctx.should_rejected(picsearch_matcher)


@pytest.mark.asyncio
async def test_picsearch_multi_image_ascii2d_success(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picsearch_handlers._picsearch_cooldowns.clear()
    first = build_group_message_event("搜图 ascii2d", message_id=1)
    attach_reply_message(
        first,
        MessageSegment("image", {"url": "https://example.com/1.png"}),
        MessageSegment("image", {"url": "https://example.com/2.png"}),
    )
    second = build_group_message_event("2", message_id=2)

    monkeypatch.setattr(
        picsearch_handlers,
        "search_image",
        AsyncMock(
            return_value=PicsearchResult(
                engine=PicsearchEngine.ASCII2D,
                title="ASCII 标题",
                author="ASCII 作者",
                similarity="N/A",
                source_url="https://example.com/ascii",
                thumbnail_url="",
            )
        ),
    )

    async with app.test_matcher(picsearch_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            MULTI_IMAGE_PROMPT,
            bot=bot,
        )
        ctx.should_rejected(picsearch_matcher)

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            "正在搜索第 2 张图片，请稍后...（引擎：ascii2d）",
            bot=bot,
        )
        ctx.should_call_send(
            second,
            Message(
                "第 2 张图片搜索结果：\n"
                "引擎：ascii2d\n"
                "相似度：N/A\n"
                "标题：ASCII 标题\n"
                "作者：ASCII 作者\n"
                "链接：https://example.com/ascii"
            ),
            bot=bot,
        )
        ctx.should_finished(picsearch_matcher)


@pytest.mark.asyncio
async def test_picsearch_requires_saucenao_key(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picsearch_handlers._picsearch_cooldowns.clear()
    event = build_group_message_event("搜图", message_id=1)
    attach_reply_message(
        event,
        MessageSegment("image", {"url": "https://example.com/1.png"}),
    )

    monkeypatch.setattr(picsearch_handlers, "get_engine_key", lambda engine: None)

    async with app.test_matcher(picsearch_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "当前未配置 saucenao 所需的访问凭据，暂时无法使用该引擎。",
            bot=bot,
        )
        ctx.should_finished(picsearch_matcher)


@pytest.mark.asyncio
async def test_picsearch_handles_no_result(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picsearch_handlers._picsearch_cooldowns.clear()
    event = build_group_message_event("搜图", message_id=1)
    attach_reply_message(
        event,
        MessageSegment("image", {"url": "https://example.com/1.png"}),
    )

    monkeypatch.setattr(picsearch_handlers, "get_engine_key", lambda engine: "key")
    monkeypatch.setattr(
        picsearch_handlers,
        "search_image",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(picsearch_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "正在搜索第 1 张图片，请稍后...（引擎：saucenao）",
            bot=bot,
        )
        ctx.should_call_send(
            event,
            "第 1 张图片没有找到结果。当前引擎：saucenao",
            bot=bot,
        )
        ctx.should_finished(picsearch_matcher)


@pytest.mark.asyncio
async def test_help_can_find_picsearch_docs(app: App) -> None:
    event = build_group_message_event("#help 图片搜索")
    expected = build_docs(
        DocsRenderContext(
            locale="zh-CN",
            view="plugin",
        )
    )

    async with app.test_matcher(help_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, bot=bot)
        ctx.should_finished(help_matcher)
