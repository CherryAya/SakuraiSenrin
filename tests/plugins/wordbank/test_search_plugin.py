import sys
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.matcher import Matcher
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
    command_start={"#", "/"},
    command_sep={"."},
)

if nonebot.get_plugin("wordbank") is None:
    sys.modules.pop("src.plugins.wordbank", None)
    nonebot.load_plugin("src.plugins.wordbank")

from src.lib.i18n.runtime import tr
from src.plugins import wordbank as wordbank_plugin
from src.plugins.wordbank import wordbank_search_command
from src.plugins.wordbank.database.types import (
    WordbankMessageRefRecord,
    WordbankSearchItem,
    WordbankSearchPage,
)
from tests.plugins.water.helpers import attach_reply_message, build_group_message_event

_SEARCH_DIMENSIONS_PROMPT = tr("zh-CN", "wordbank.guided.search.mode_prompt")
_SEARCH_QUERY_PROMPT = tr("zh-CN", "wordbank.guided.search.keyword_prompt")
_SEARCH_CREATOR_PROMPT = tr("zh-CN", "wordbank.guided.search.creator_prompt")
_SEARCH_SESSION_PROMPT = tr(
    "zh-CN",
    "wordbank.guided.search.page_prompt",
    total_pages=1,
)


class _FinishMatcher:
    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.finished: Message | None = None
        self.paused: list[str] = []

    async def send(self, message: Message | str) -> None:
        self.sent.append(Message(message))

    async def finish(self, message: Message | str | None = None) -> None:
        self.finished = Message(message or "")

    async def pause(self, message: str) -> None:
        self.paused.append(message)
        return None


@pytest.mark.asyncio
async def test_wordbank_search_without_args_routes_to_guided_entry(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_guided = AsyncMock(return_value=None)
    monkeypatch.setattr(
        wordbank_plugin,
        "initialize_wordbank_plugin",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(wordbank_plugin, "_start_guided_search", start_guided)

    async with app.test_matcher(wordbank_search_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("#搜索词条")
        ctx.receive_event(bot, event)

    start_guided.assert_awaited_once()


@pytest.mark.asyncio
async def test_wordbank_search_image_only_runs_unified_search_flow(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle_command = AsyncMock(return_value=None)
    monkeypatch.setattr(
        wordbank_plugin,
        "initialize_wordbank_plugin",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "_handle_wordbank_command_message",
        handle_command,
    )

    async with app.test_matcher(wordbank_search_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event(
            "#搜索词条 [CQ:image,url=https://example.test/search.png]"
        )
        ctx.receive_event(bot, event)

    handle_command.assert_awaited_once()
    await_args = handle_command.await_args
    assert await_args is not None
    assert await_args.kwargs["forced_action"] == "search"


@pytest.mark.asyncio
async def test_guided_search_dimension_selection_prompts_for_query_content(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wordbank_plugin,
        "initialize_wordbank_plugin",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )

    async with app.test_matcher(wordbank_search_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        first = build_group_message_event("#搜索词条", message_id=1)
        second = build_group_message_event("12", message_id=2)

        ctx.receive_event(bot, first)
        ctx.should_call_send(first, _SEARCH_DIMENSIONS_PROMPT, bot=bot)
        ctx.should_paused()

        ctx.receive_event(bot, second)
        ctx.should_call_send(second, _SEARCH_QUERY_PROMPT, bot=bot)
        ctx.should_paused()


@pytest.mark.asyncio
async def test_guided_search_creator_only_prompts_for_creator_account(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wordbank_plugin,
        "initialize_wordbank_plugin",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )

    async with app.test_matcher(wordbank_search_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        first = build_group_message_event("#搜索词条", message_id=1)
        second = build_group_message_event("3", message_id=2)

        ctx.receive_event(bot, first)
        ctx.should_call_send(first, _SEARCH_DIMENSIONS_PROMPT, bot=bot)
        ctx.should_paused()

        ctx.receive_event(bot, second)
        ctx.should_call_send(second, _SEARCH_CREATOR_PROMPT, bot=bot)
        ctx.should_paused()


@pytest.mark.asyncio
async def test_guided_search_query_stage_accepts_image_message(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finish_guided_search = AsyncMock(return_value=None)

    monkeypatch.setattr(
        wordbank_plugin,
        "initialize_wordbank_plugin",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "_finish_guided_search",
        finish_guided_search,
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "fetch_first_image_bytes_from_message",
        AsyncMock(return_value=b"image-bytes"),
    )
    monkeypatch.setattr(
        wordbank_plugin.wordbank_media_service,
        "search_similar_images",
        lambda _data: (SimpleNamespace(canonical_id=7, score=0.91),),
    )

    async with app.test_matcher(wordbank_search_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        first = build_group_message_event("#搜索词条", message_id=1)
        second = build_group_message_event("12", message_id=2)
        third = build_group_message_event(
            "[CQ:image,url=https://example.test/search.png]",
            message_id=3,
        )

        ctx.receive_event(bot, first)
        ctx.should_call_send(first, _SEARCH_DIMENSIONS_PROMPT, bot=bot)
        ctx.should_paused()

        ctx.receive_event(bot, second)
        ctx.should_call_send(second, _SEARCH_QUERY_PROMPT, bot=bot)
        ctx.should_paused()

        ctx.receive_event(bot, third)

    finish_guided_search.assert_awaited_once()
    await_args = finish_guided_search.await_args
    assert await_args is not None
    state = await_args.args[1]
    assert state["wordbank_guided_search_keyword"] == ""
    assert state["wordbank_guided_search_has_image"] is True
    assert state["wordbank_guided_search_image_scores"] == {7: 0.91}


@pytest.mark.asyncio
async def test_finish_guided_search_finishes_with_rendered_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = _FinishMatcher()
    monkeypatch.setattr(
        wordbank_plugin,
        "execute_search_page",
        AsyncMock(
            return_value=WordbankSearchPage(
                items=(),
                total_count=0,
                offset=0,
                limit=10,
            )
        ),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "render_search_page_message",
        AsyncMock(return_value=Message("CARD")),
    )

    state = {
        "wordbank_guided_search_field": "all",
        "wordbank_guided_search_keyword": "晚安",
        "wordbank_guided_search_creator_id": "",
        "wordbank_guided_search_has_image": False,
        "wordbank_guided_search_image_scores": {},
    }
    event = build_group_message_event("#搜索词条 晚安")

    await wordbank_plugin._finish_guided_search(
        cast(Matcher, matcher),
        state,
        event,
        "zh-CN",
        page_number=1,
    )

    assert matcher.sent == [Message("CARD")]
    assert matcher.finished == Message("")


@pytest.mark.asyncio
async def test_finish_guided_search_keeps_search_session_when_results_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = _FinishMatcher()
    monkeypatch.setattr(
        wordbank_plugin,
        "execute_search_page",
        AsyncMock(
            return_value=WordbankSearchPage(
                items=(
                    WordbankSearchItem(
                        trigger_group_id=271,
                        status="approved",
                        trigger_text="晚安",
                        response_text="做个好梦",
                        response_summaries=("做个好梦",),
                        response_count=1,
                        scope="current_group",
                        probability=1.0,
                        weight=3,
                        created_by="10001",
                    ),
                ),
                total_count=1,
                offset=0,
                limit=10,
            )
        ),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "render_search_page_message",
        AsyncMock(return_value=Message("CARD")),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "_resolve_search_delete_target_ids",
        AsyncMock(return_value=(12,)),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "_record_search_result_view_message",
        AsyncMock(return_value=None),
    )

    state = {
        "wordbank_guided_search_field": "all",
        "wordbank_guided_search_keyword": "晚安",
        "wordbank_guided_search_creator_id": "",
        "wordbank_guided_search_has_image": False,
        "wordbank_guided_search_image_scores": {},
    }
    event = build_group_message_event("#搜索词条 晚安")

    await wordbank_plugin._finish_guided_search(
        cast(Matcher, matcher),
        state,
        event,
        "zh-CN",
        page_number=1,
    )

    assert matcher.sent == [Message("CARD")]
    assert matcher.finished is None
    assert matcher.paused == [_SEARCH_SESSION_PROMPT]
    assert state["wordbank_guided_search_current_page"] == 1
    assert state["wordbank_guided_search_total_pages"] == 1
    assert state["wordbank_guided_search_group_ids"] == (271,)
    assert state["wordbank_guided_search_delete_target_ids"] == (12,)


@pytest.mark.asyncio
async def test_handle_search_session_delete_refreshes_current_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = _FinishMatcher()
    monkeypatch.setattr(
        wordbank_plugin,
        "handle_delete",
        AsyncMock(return_value="词条 #12 已删除。"),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "_finish_guided_search",
        AsyncMock(return_value=None),
    )

    state = {
        "wordbank_guided_search_stage": (
            wordbank_plugin.WORDBANK_GUIDED_SEARCH_STAGE_PAGE
        ),
        "wordbank_guided_search_current_page": 1,
        "wordbank_guided_search_total_pages": 2,
        "wordbank_guided_search_group_ids": (271,),
        "wordbank_guided_search_delete_target_ids": (12,),
        "wordbank_guided_search_field": "all",
        "wordbank_guided_search_keyword": "晚安",
        "wordbank_guided_search_creator_id": "",
        "wordbank_guided_search_has_image": False,
        "wordbank_guided_search_image_scores": {},
    }
    event = build_group_message_event("del 1")

    await wordbank_plugin._handle_search_session_event(
        cast(Matcher, matcher),
        event,
        state,
        "zh-CN",
    )

    assert matcher.sent == [Message("词条 #12 已删除。")]
    assert isinstance(wordbank_plugin.handle_delete, AsyncMock)
    wordbank_plugin.handle_delete.assert_awaited_once()
    assert isinstance(wordbank_plugin._finish_guided_search, AsyncMock)
    wordbank_plugin._finish_guided_search.assert_awaited_once()
    await_args = wordbank_plugin._finish_guided_search.await_args
    assert await_args is not None
    assert await_args.kwargs["page_number"] == 1
    assert await_args.kwargs["clamp_page"] is True


@pytest.mark.asyncio
async def test_view_reply_matcher_routes_search_result_reply_to_group_detail(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wordbank_plugin,
        "initialize_wordbank_plugin",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "_send_group_detail_view",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin.wordbank_service,
        "get_message_ref",
        AsyncMock(
            return_value=WordbankMessageRefRecord(
                message_id="90001",
                ref_kind="view",
                shard_key="2026_06",
                context_type="search_result",
                trigger_group_id=0,
                trigger_variant_id=0,
                response_item_id=0,
                current_page=1,
                keyword="jrlp",
                field="all",
                creator_id="",
                has_image=False,
                group_ids=(271,),
                group_id="20001",
                user_id="10001",
                message_type="group",
                source_message_id="",
            )
        ),
    )

    async with app.test_matcher(wordbank_plugin.wordbank_view_reply_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("[CQ:at,qq=99999] 详情 271")
        event.to_me = True
        attach_reply_message(event, message_id=90001)
        ctx.receive_event(bot, event)

    assert isinstance(wordbank_plugin._send_group_detail_view, AsyncMock)
    wordbank_plugin._send_group_detail_view.assert_awaited_once()


@pytest.mark.asyncio
async def test_view_reply_matcher_routes_group_detail_reply_to_next_page(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wordbank_plugin,
        "initialize_wordbank_plugin",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    send_group_detail_view = AsyncMock(return_value=None)
    monkeypatch.setattr(
        wordbank_plugin,
        "_send_group_detail_view",
        send_group_detail_view,
    )
    monkeypatch.setattr(
        wordbank_plugin.wordbank_service,
        "get_message_ref",
        AsyncMock(
            return_value=WordbankMessageRefRecord(
                message_id="90002",
                ref_kind="view",
                shard_key="2026_06",
                context_type="group_detail",
                trigger_group_id=271,
                trigger_variant_id=0,
                response_item_id=0,
                current_page=2,
                keyword="",
                field="",
                creator_id="",
                has_image=False,
                group_ids=(271,),
                group_id="20001",
                user_id="10001",
                message_type="group",
                source_message_id="",
            )
        ),
    )

    async with app.test_matcher(wordbank_plugin.wordbank_view_reply_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("[CQ:at,qq=99999] 下一页")
        event.to_me = True
        attach_reply_message(event, message_id=90002)
        ctx.receive_event(bot, event)

    send_group_detail_view.assert_awaited_once()
    await_args = send_group_detail_view.await_args
    assert await_args is not None
    kwargs = await_args.kwargs
    assert kwargs["trigger_group_id"] == 271
    assert kwargs["page"] == 3
