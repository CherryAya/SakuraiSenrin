import sys
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

from src.plugins import wordbank as wordbank_plugin
from src.plugins.wordbank import wordbank_search_command
from src.plugins.wordbank.database.types import WordbankSearchPage
from tests.plugins.water.helpers import build_group_message_event


class _FinishMatcher:
    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.finished: Message | None = None

    async def send(self, message: Message) -> None:
        self.sent.append(message)

    async def finish(self, message: Message | str) -> None:
        self.finished = Message(message)

    async def pause(self, _message: str) -> None:
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
async def test_wordbank_search_image_only_routes_to_guided_image_entry(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_guided_image = AsyncMock(return_value=None)
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
        "_start_guided_search_with_image",
        start_guided_image,
    )

    async with app.test_matcher(wordbank_search_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event(
            "#搜索词条 [CQ:image,url=https://example.test/search.png]"
        )
        ctx.receive_event(bot, event)

    start_guided_image.assert_awaited_once()


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
        lambda *args, **kwargs: Message("CARD"),
    )

    state = {
        "wordbank_guided_search_field": "all",
        "wordbank_guided_search_keyword": "晚安",
        "wordbank_guided_search_creator_id": "",
        "wordbank_guided_search_has_image": False,
        "wordbank_guided_search_image_scores": {},
    }

    await wordbank_plugin._finish_guided_search(
        cast(Matcher, matcher),
        state,
        "zh-CN",
        page_number=1,
    )

    assert matcher.finished == Message("CARD")
