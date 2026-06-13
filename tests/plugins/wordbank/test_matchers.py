import sys
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Bot, Message
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
from src.plugins.wordbank.handlers.passive import PassiveResponse
from src.plugins.wordbank.message_model import shape_from_text
from src.plugins.wordbank.services.core import WordbankAddResult
from tests.plugins.water.helpers import (
    build_group_message_event,
    build_group_poke_event,
)


@pytest.mark.asyncio
async def test_wordbank_add_without_args_enters_guided_trigger_prompt(
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

    async with app.test_matcher(wordbank_plugin.wordbank_add_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("#wordbank.add", message_id=1)

        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            tr("zh-CN", "wordbank.guided.add.trigger_prompt"),
            bot=bot,
        )
        ctx.should_paused()


@pytest.mark.asyncio
async def test_wordbank_add_direct_success_records_submission(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = WordbankAddResult(
        trigger_group_id=12,
        trigger_variant_id=21,
        response_item_id=22,
        trigger_text="晚安",
        response_text="做个好梦",
        scope="current_group",
        probability=1.0,
        weight=1,
        status="pending",
        trigger_shape=shape_from_text("晚安"),
        response_shape=shape_from_text("做个好梦"),
    )
    handle_add = AsyncMock(return_value=result)
    send_pending = AsyncMock(return_value=None)
    build_result_message = AsyncMock(return_value=Message("词条已提交审核"))
    record_submission = AsyncMock(return_value=None)

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
        "handle_add_text_result",
        handle_add,
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "send_pending_approval_notice",
        send_pending,
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "build_add_result_message",
        build_result_message,
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "record_submission_approval_message",
        record_submission,
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "fetch_first_image_bytes_from_message",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(wordbank_plugin.wordbank_add_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event(
            "#wordbank.add 晚安 => 做个好梦",
            message_id=1,
        )

        ctx.receive_event(bot, event)
        ctx.should_call_send(event, Message("词条已提交审核"), bot=bot)
        ctx.should_finished()

    handle_add.assert_awaited_once()
    assert handle_add.await_args_list[0].kwargs["text"] == "晚安 => 做个好梦"
    send_pending.assert_awaited_once()
    build_result_message.assert_awaited_once()
    record_submission.assert_awaited_once()


@pytest.mark.asyncio
async def test_wordbank_passive_matcher_sends_response(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = PassiveResponse(
        text="被动回复",
        trigger_group_id=12,
        trigger_variant_id=21,
        response_item_id=22,
        group_id="20001",
        user_id="10001",
        message_type="group",
        response_shape=None,
    )
    record_message = AsyncMock(return_value=None)

    monkeypatch.setattr(
        wordbank_plugin,
        "initialize_wordbank_plugin",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "handle_passive_message",
        AsyncMock(return_value=response),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "_record_passive_response_message",
        record_message,
    )

    async with app.test_matcher(wordbank_plugin.wordbank_passive) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("晚安", message_id=1)

        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "被动回复", bot=bot)

    record_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_wordbank_notice_matcher_sends_response(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = PassiveResponse(
        text="别戳了",
        trigger_group_id=12,
        trigger_variant_id=21,
        response_item_id=22,
        group_id="20001",
        user_id="10001",
        message_type="event",
        response_shape=None,
    )
    record_message = AsyncMock(return_value=None)

    monkeypatch.setattr(
        wordbank_plugin,
        "initialize_wordbank_plugin",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "handle_passive_notice",
        AsyncMock(return_value=response),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "_record_passive_response_message",
        record_message,
    )

    async with app.test_matcher(wordbank_plugin.wordbank_notice) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_poke_event(target_id=99999)

        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "别戳了", bot=bot)

    record_message.assert_awaited_once()
