import sys
from unittest.mock import AsyncMock, Mock

import nonebot
from nonebot.adapters.onebot.v11 import Bot, MessageSegment
from nonebug import App
import pytest

from src.lib.messages import empty_message, text_message

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

if nonebot.get_plugin("study") is None:
    sys.modules.pop("src.plugins.study", None)
    nonebot.load_plugin("src.plugins.study")

from src.lib.i18n.runtime import tr
from src.plugins import study as study_plugin
from src.plugins.wordbank import handlers as wordbank_handlers
from src.plugins.wordbank.message_model import shape_from_event, shape_from_text
from src.plugins.wordbank.services import wordbank_service
from src.plugins.wordbank.services.core import WordbankAddResult
from tests.plugins.water.helpers import build_group_message_event


@pytest.mark.asyncio
async def test_study_without_args_enters_guided_mode_prompt(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wordbank_service,
        "initialize",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        study_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )

    async with app.test_matcher(study_plugin.study_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("#study", message_id=1)

        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            tr("zh-CN", "wordbank.guided.study.mode_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)


@pytest.mark.asyncio
async def test_study_partial_args_jump_to_response_prompt(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wordbank_service,
        "initialize",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        study_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )

    async with app.test_matcher(study_plugin.study_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("#study M F 不嘻嘻", message_id=1)

        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            tr("zh-CN", "wordbank.guided.study.response_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)


@pytest.mark.asyncio
async def test_study_guided_flow_submits_pending_entry(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = WordbankAddResult(
        trigger_group_id=12,
        trigger_variant_id=21,
        response_item_id=22,
        trigger_text="不嘻嘻",
        response_text="消息回复如下",
        scope="all_groups",
        probability=1.0,
        weight=3,
        status="pending",
        trigger_shape=shape_from_text("不嘻嘻"),
        response_shape=shape_from_text("消息回复如下"),
    )
    handle_guided = AsyncMock(return_value=result)
    build_result_message = AsyncMock(return_value=text_message("词条已提交审核"))
    record_submission = AsyncMock(return_value=None)
    schedule_pending = Mock()

    monkeypatch.setattr(
        wordbank_service,
        "initialize",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        study_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "handle_guided_study_shape_result",
        handle_guided,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "build_add_result_message",
        build_result_message,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "record_submission_approval_message",
        record_submission,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "schedule_pending_approval_notice",
        schedule_pending,
    )

    async with app.test_matcher(study_plugin.study_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = build_group_message_event("#study", message_id=1)
        second = build_group_message_event("M", message_id=2)
        third = build_group_message_event("F", message_id=3)
        fourth = build_group_message_event("不嘻嘻", message_id=4)
        fifth = build_group_message_event("消息回复如下", message_id=5)
        sixth = build_group_message_event("3", message_id=6)

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            tr("zh-CN", "wordbank.guided.study.mode_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            tr("zh-CN", "wordbank.guided.study.group_block_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            tr("zh-CN", "wordbank.guided.study.trigger_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, fourth)
        ctx.should_call_send(
            fourth,
            tr("zh-CN", "wordbank.guided.study.response_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, fifth)
        ctx.should_call_send(
            fifth,
            tr("zh-CN", "wordbank.guided.study.weight_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, sixth)
        ctx.should_call_send(sixth, text_message("词条已提交审核"), bot=bot)
        ctx.should_finished(study_plugin.study_command)

    handle_guided.assert_awaited_once()
    guided_kwargs = handle_guided.await_args_list[0].kwargs
    assert guided_kwargs["trig_mode_text"] == "M"
    assert guided_kwargs["group_block_text"] == "F"
    assert guided_kwargs["weight_text"] == "3"
    schedule_pending.assert_called_once()
    build_result_message.assert_awaited_once()
    record_submission.assert_awaited_once()


@pytest.mark.asyncio
async def test_study_guided_flow_accepts_event_trigger_text(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = WordbankAddResult(
        trigger_group_id=12,
        trigger_variant_id=21,
        response_item_id=22,
        trigger_text="[事件:event:poke]",
        response_text="别戳啦",
        scope="all_groups",
        probability=1.0,
        weight=3,
        status="pending",
        trigger_shape=shape_from_event("event:poke"),
        response_shape=shape_from_text("别戳啦"),
    )
    handle_guided = AsyncMock(return_value=result)
    build_result_message = AsyncMock(return_value=text_message("词条已提交审核"))
    record_submission = AsyncMock(return_value=None)
    schedule_pending = Mock()

    monkeypatch.setattr(
        wordbank_service,
        "initialize",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        study_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "handle_guided_study_shape_result",
        handle_guided,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "build_add_result_message",
        build_result_message,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "record_submission_approval_message",
        record_submission,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "schedule_pending_approval_notice",
        schedule_pending,
    )

    async with app.test_matcher(study_plugin.study_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = build_group_message_event("#study", message_id=1)
        second = build_group_message_event("M", message_id=2)
        third = build_group_message_event("F", message_id=3)
        fourth = build_group_message_event("event:poke", message_id=4)
        fifth = build_group_message_event("别戳啦", message_id=5)
        sixth = build_group_message_event("3", message_id=6)

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            tr("zh-CN", "wordbank.guided.study.mode_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            tr("zh-CN", "wordbank.guided.study.group_block_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            tr("zh-CN", "wordbank.guided.study.trigger_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, fourth)
        ctx.should_call_send(
            fourth,
            tr("zh-CN", "wordbank.guided.study.response_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, fifth)
        ctx.should_call_send(
            fifth,
            tr("zh-CN", "wordbank.guided.study.weight_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, sixth)
        ctx.should_call_send(sixth, text_message("词条已提交审核"), bot=bot)
        ctx.should_finished(study_plugin.study_command)

    handle_guided.assert_awaited_once()
    guided_kwargs = handle_guided.await_args_list[0].kwargs
    assert guided_kwargs["trigger_shape"] == shape_from_event("event:poke")


@pytest.mark.asyncio
async def test_study_prefilled_trigger_parses_event_shape(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = WordbankAddResult(
        trigger_group_id=12,
        trigger_variant_id=21,
        response_item_id=22,
        trigger_text="[事件:event:join]",
        response_text="欢迎加入",
        scope="all_groups",
        probability=1.0,
        weight=3,
        status="pending",
        trigger_shape=shape_from_event("event:join"),
        response_shape=shape_from_text("欢迎加入"),
    )
    handle_guided = AsyncMock(return_value=result)
    build_result_message = AsyncMock(return_value=text_message("词条已提交审核"))
    record_submission = AsyncMock(return_value=None)
    schedule_pending = Mock()

    monkeypatch.setattr(
        wordbank_service,
        "initialize",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        study_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "handle_guided_study_shape_result",
        handle_guided,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "build_add_result_message",
        build_result_message,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "record_submission_approval_message",
        record_submission,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "schedule_pending_approval_notice",
        schedule_pending,
    )

    async with app.test_matcher(study_plugin.study_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = build_group_message_event("#study M F event:join", message_id=1)
        second = build_group_message_event("欢迎加入", message_id=2)
        third = build_group_message_event("3", message_id=3)

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            tr("zh-CN", "wordbank.guided.study.response_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            tr("zh-CN", "wordbank.guided.study.weight_prompt"),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        ctx.receive_event(bot, third)
        ctx.should_call_send(third, text_message("词条已提交审核"), bot=bot)
        ctx.should_finished(study_plugin.study_command)

    handle_guided.assert_awaited_once()
    guided_kwargs = handle_guided.await_args_list[0].kwargs
    assert guided_kwargs["trigger_shape"] == shape_from_event("event:join")


@pytest.mark.asyncio
async def test_study_direct_media_submission_sends_processing_hint_before_result(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = WordbankAddResult(
        trigger_group_id=12,
        trigger_variant_id=21,
        response_item_id=22,
        trigger_text="不嘻嘻",
        response_text="[图片:7]",
        scope="all_groups",
        probability=1.0,
        weight=3,
        status="pending",
        trigger_shape=shape_from_text("不嘻嘻"),
        response_shape=shape_from_text("[图片:7]"),
    )
    handle_with_media = AsyncMock(return_value=result)
    build_result_message = AsyncMock(return_value=text_message("词条已提交审核"))
    record_submission = AsyncMock(return_value=None)
    schedule_pending = Mock()

    monkeypatch.setattr(
        wordbank_service,
        "initialize",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        study_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "extract_image_urls",
        lambda _message: ["https://example.test/image.png"],
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "fetch_image_bytes_from_message",
        AsyncMock(return_value=[b"image-bytes"]),
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "handle_study_with_media_result",
        handle_with_media,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "build_add_result_message",
        build_result_message,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "record_submission_approval_message",
        record_submission,
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "schedule_pending_approval_notice",
        schedule_pending,
    )

    async with app.test_matcher(study_plugin.study_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("#study 触发词 => 响应词", message_id=1)
        event.message = (
            empty_message()
            + MessageSegment.text("#study 触发词 => 响应词")
            + MessageSegment.image("https://example.test/image.png")
        )
        event.original_message = event.message
        event.raw_message = "#study 触发词 => 响应词 [image]"

        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            tr("zh-CN", "wordbank.add.processing_with_media"),
            bot=bot,
        )
        ctx.should_call_send(event, text_message("词条已提交审核"), bot=bot)
        ctx.should_finished(study_plugin.study_command)

    schedule_pending.assert_called_once()
    record_submission.assert_awaited_once()
