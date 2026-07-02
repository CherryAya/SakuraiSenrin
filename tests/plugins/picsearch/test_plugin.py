from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Bot, MessageSegment
from nonebug import App
import pytest

from src.lib.message_assets import message_asset_repo
from src.lib.messages import text_message

nonebot.init(
    SUPERUSERS={"1"},
    IGNORED_USERS=set(),
    MAIN_GROUP_ID="10001",
    GITHUB_TOKEN="test-token",
    GITHUB_REPO="owner/repo",
    GITHUB_BRANCH="main",
    WORDBANK_MEDIA_PROVIDER="local",
)
if nonebot.get_plugin("picsearch") is None:
    sys.modules.pop("src.plugins.picsearch", None)
    nonebot.load_plugin("src.plugins.picsearch")
if nonebot.get_plugin("help") is None:
    nonebot.load_plugin("src.plugins.help")

from src.plugins import picsearch as picsearch_plugin
from src.plugins.help import (
    _iter_docs_entries,
    _resolve_actor_permission,
    _resolve_docs_message,
    help_matcher,
)
from src.plugins.picsearch import (
    PicsearchEngine,
    PicsearchResult,
    _build_error_demo,
    picsearch_matcher,
)
from tests.plugins.water.helpers import (
    attach_reply_message,
    build_group_message_event,
    build_message,
)

MULTI_IMAGE_PROMPT = text_message(
    "检测到有多张图片，请输入对应的序号，最多允许 3 张，可以使用空格进行分割："
)


@pytest.fixture(autouse=True)
def _disable_runtime_processor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.hooks.processor._runtime_sync", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "src.hooks.processor._runtime_check",
        AsyncMock(return_value=None),
    )


def _freeze_error_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.lib.plugin_docs.render_feature_deep_dive",
        lambda *args, **kwargs: b"feature-demo",
    )


@pytest.mark.asyncio
async def test_picsearch_requires_reply_image(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picsearch_plugin.clear_picsearch_cooldowns()
    _freeze_error_demo(monkeypatch)
    event = build_group_message_event("搜图")

    async with app.test_matcher(picsearch_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            _build_error_demo(
                "zh-CN",
                "请先回复一条包含图片的消息，再发送搜图指令。",
            ),
            bot=bot,
        )
        ctx.should_finished(picsearch_matcher)


@pytest.mark.asyncio
async def test_picsearch_single_image_uses_default_saucenao(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picsearch_plugin.clear_picsearch_cooldowns()
    event = build_group_message_event("搜图", message_id=1)
    attach_reply_message(
        event,
        MessageSegment("image", {"url": "https://example.com/1.png"}),
    )

    monkeypatch.setattr(picsearch_plugin, "get_engine_key", lambda engine: "key")
    monkeypatch.setattr(
        picsearch_plugin,
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
        picsearch_plugin,
        "load_thumbnail_bytes",
        AsyncMock(return_value=b"thumb"),
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_asset",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(picsearch_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": 20001,
                "message": build_message(
                    MessageSegment.text(
                        "第 1 张图片搜索结果：\n"
                        "引擎：saucenao\n"
                        "相似度：91.2\n"
                        "标题：标题\n"
                        "作者：作者\n"
                        "链接：https://example.com/source"
                    ),
                    MessageSegment.image(b"thumb"),
                ),
            },
            result={"message_id": 1},
        )
        ctx.should_finished(picsearch_matcher)


@pytest.mark.asyncio
async def test_picsearch_rejects_invalid_index_on_multi_image(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picsearch_plugin.clear_picsearch_cooldowns()
    _freeze_error_demo(monkeypatch)
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
        ctx.should_call_send(
            second,
            _build_error_demo("zh-CN", "图片序号超出范围，请重新输入。"),
            bot=bot,
        )
        ctx.should_rejected(picsearch_matcher)


@pytest.mark.asyncio
async def test_picsearch_multi_image_ascii2d_success(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picsearch_plugin.clear_picsearch_cooldowns()
    first = build_group_message_event("搜图 ascii2d", message_id=1)
    attach_reply_message(
        first,
        MessageSegment("image", {"url": "https://example.com/1.png"}),
        MessageSegment("image", {"url": "https://example.com/2.png"}),
    )
    second = build_group_message_event("2", message_id=2)

    monkeypatch.setattr(
        picsearch_plugin,
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
    monkeypatch.setattr(
        message_asset_repo,
        "get_asset",
        AsyncMock(return_value=None),
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
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": 20001,
                "message": text_message(
                    "第 2 张图片搜索结果：\n"
                    "引擎：ascii2d\n"
                    "相似度：N/A\n"
                    "标题：ASCII 标题\n"
                    "作者：ASCII 作者\n"
                    "链接：https://example.com/ascii"
                ),
            },
            result={"message_id": 1},
        )
        ctx.should_finished(picsearch_matcher)


@pytest.mark.asyncio
async def test_build_result_message_uses_locale_labels() -> None:
    message = picsearch_plugin.build_result_message(
        1,
        PicsearchResult(
            engine=PicsearchEngine.ASCII2D,
            title="标题",
            author="作者",
            similarity="N/A",
            source_url="https://example.com/ascii",
            thumbnail_url="",
        ),
        None,
        locale="lzh",
    )

    text = str(message)
    assert "第 1 張圖搜圖結果：" in text
    assert "標題：标题" in text
    assert "鏈接：https://example.com/ascii" in text


@pytest.mark.asyncio
async def test_picsearch_requires_saucenao_key(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picsearch_plugin.clear_picsearch_cooldowns()
    event = build_group_message_event("搜图", message_id=1)
    attach_reply_message(
        event,
        MessageSegment("image", {"url": "https://example.com/1.png"}),
    )

    monkeypatch.setattr(picsearch_plugin, "get_engine_key", lambda engine: None)

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
    picsearch_plugin.clear_picsearch_cooldowns()
    event = build_group_message_event("搜图", message_id=1)
    attach_reply_message(
        event,
        MessageSegment("image", {"url": "https://example.com/1.png"}),
    )

    monkeypatch.setattr(picsearch_plugin, "get_engine_key", lambda engine: "key")
    monkeypatch.setattr(
        picsearch_plugin,
        "search_image",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_asset",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(picsearch_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": 20001,
                "message": text_message("第 1 张图片没有找到结果。当前引擎：saucenao"),
            },
            result={"message_id": 1},
        )
        ctx.should_finished(picsearch_matcher)


@pytest.mark.asyncio
async def test_help_can_find_picsearch_docs(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_group_message_event("#help 图片搜索")
    entries = _iter_docs_entries("zh-CN")
    picsearch_entry = next(
        entry for entry in entries if entry.display_name == "图片搜索"
    )
    monkeypatch.setattr(
        "src.plugins.help.render_feature_deep_dive",
        lambda *args, **kwargs: b"feature-demo",
    )

    async with app.test_matcher(help_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        actor_permission = await _resolve_actor_permission(bot, event)
        expected = await _resolve_docs_message(
            picsearch_entry,
            "zh-CN",
            actor_permission=actor_permission,
            all_entries=entries,
        )
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, bot=bot)
        ctx.should_finished(help_matcher)
