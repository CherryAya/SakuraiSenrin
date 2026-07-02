import sys
from unittest.mock import AsyncMock, Mock

import nonebot
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebug import App
import pytest

from src.lib.messages import text_message

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
from src.lib.message_assets import message_asset_repo
from src.plugins import wordbank as wordbank_plugin
from src.plugins.wordbank.handlers import submission as wordbank_submission_handlers
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
async def test_wordbank_add_guided_exit_cancels_session(
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
        first = build_group_message_event("#wordbank.add", message_id=1)
        second = build_group_message_event("exit", message_id=2)

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            tr("zh-CN", "wordbank.guided.add.trigger_prompt"),
            bot=bot,
        )
        ctx.should_paused()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            tr("zh-CN", "interaction.cancelled"),
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_wordbank_add_guided_forward_reply_prompts_import_mode(
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
        first = build_group_message_event("#wordbank.add", message_id=1)
        second = build_group_message_event("jrlp", message_id=2)
        third = build_group_message_event("", message_id=3)
        third.message = Message([MessageSegment.forward("54321")])

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            tr("zh-CN", "wordbank.guided.add.trigger_prompt"),
            bot=bot,
        )
        ctx.should_paused()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            tr("zh-CN", "wordbank.guided.add.response_prompt"),
            bot=bot,
        )
        ctx.should_paused()

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            "检测到合并转发消息，请回复 1 作为整体响应，或回复 2 拆开成多条响应。",
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
    build_result_message = AsyncMock(return_value=text_message("词条已提交审核"))
    record_submission = AsyncMock(return_value=None)
    schedule_pending = Mock()

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
        wordbank_submission_handlers,
        "build_add_result_message",
        build_result_message,
    )
    monkeypatch.setattr(
        wordbank_submission_handlers,
        "record_submission_approval_message",
        record_submission,
    )
    monkeypatch.setattr(
        wordbank_submission_handlers,
        "schedule_submission_approval_notice",
        schedule_pending,
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "fetch_first_image_bytes_from_message",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_asset",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(wordbank_plugin.wordbank_add_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event(
            "#wordbank.add 晚安 => 做个好梦",
            message_id=1,
        )

        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": 20001,
                "message": text_message("词条已提交审核"),
            },
            result={"message_id": 1},
        )
        ctx.should_finished()

    handle_add.assert_awaited_once()
    assert handle_add.await_args_list[0].kwargs["text"] == "晚安 => 做个好梦"
    schedule_pending.assert_called_once()
    build_result_message.assert_awaited_once()
    record_submission.assert_awaited_once()


@pytest.mark.asyncio
async def test_wordbank_search_command_preserves_keyword_whitespace(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send_search = AsyncMock(return_value=None)

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
        "fetch_first_image_bytes_from_message",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "_send_search_result_view",
        send_search,
    )

    async with app.test_matcher(wordbank_plugin.wordbank_search_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event(
            "#wordbank.search   第一行  第二列  ",
            message_id=1,
        )

        ctx.receive_event(bot, event)
        ctx.should_finished()

    send_search.assert_awaited_once()
    assert send_search.await_args_list[0].kwargs["keyword"] == "第一行  第二列  "


@pytest.mark.asyncio
async def test_wordbank_rank_command_routes_to_forced_rank_action(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle_command = AsyncMock(return_value=None)

    monkeypatch.setattr(
        wordbank_plugin,
        "_handle_wordbank_command_message",
        handle_command,
    )

    async with app.test_matcher(wordbank_plugin.wordbank_rank_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("#苦瓜榜", message_id=1)

        ctx.receive_event(bot, event)

    handle_command.assert_awaited_once()
    await_args = handle_command.await_args
    assert await_args is not None
    assert await_args.kwargs["forced_action"] == "rank"


@pytest.mark.asyncio
async def test_wordbank_add_direct_media_submission_sends_processing_hint(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = WordbankAddResult(
        trigger_group_id=12,
        trigger_variant_id=21,
        response_item_id=22,
        trigger_text="晚安",
        response_text="[图片:7]",
        scope="current_group",
        probability=1.0,
        weight=1,
        status="pending",
        trigger_shape=shape_from_text("晚安"),
        response_shape=shape_from_text("[图片:7]"),
    )
    handle_add = AsyncMock(return_value=result)
    build_result_message = AsyncMock(return_value=text_message("词条已提交审核"))
    record_submission = AsyncMock(return_value=None)
    schedule_pending = Mock()

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
        "handle_add_with_media_result",
        handle_add,
    )
    monkeypatch.setattr(
        wordbank_submission_handlers,
        "build_add_result_message",
        build_result_message,
    )
    monkeypatch.setattr(
        wordbank_submission_handlers,
        "record_submission_approval_message",
        record_submission,
    )
    monkeypatch.setattr(
        wordbank_submission_handlers,
        "schedule_submission_approval_notice",
        schedule_pending,
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "extract_image_urls",
        lambda _message: ["https://example.test/image.png"],
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "fetch_first_image_bytes_from_message",
        AsyncMock(return_value=b"image-bytes"),
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_asset",
        AsyncMock(return_value=None),
    )

    async with app.test_matcher(wordbank_plugin.wordbank_add_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event(
            "#wordbank.add 晚安 => [图片]",
            message_id=1,
        )

        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": 20001,
                "message": text_message(
                    tr("zh-CN", "wordbank.add.processing_with_media")
                ),
            },
            result={"message_id": 1},
        )
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": 20001,
                "message": text_message("词条已提交审核"),
            },
            result={"message_id": 1},
        )
        ctx.should_finished()

    schedule_pending.assert_called_once()
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
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": 20001,
                "message": text_message("被动回复"),
            },
            result={"message_id": 1},
        )

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
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": 20001,
                "message": text_message("别戳了"),
            },
            result={"message_id": 1},
        )

    record_message.assert_awaited_once()
