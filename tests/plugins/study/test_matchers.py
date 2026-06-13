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

if nonebot.get_plugin("study") is None:
    sys.modules.pop("src.plugins.study", None)
    nonebot.load_plugin("src.plugins.study")

from src.lib.i18n.runtime import tr
from src.plugins import study as study_plugin
from src.plugins.wordbank import handlers as wordbank_handlers
from src.plugins.wordbank.message_model import shape_from_text
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
    send_pending = AsyncMock(return_value=None)
    build_result_message = AsyncMock(return_value=Message("词条已提交审核"))
    record_submission = AsyncMock(return_value=None)

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
        "send_pending_approval_notice",
        send_pending,
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
        ctx.should_call_send(sixth, Message("词条已提交审核"), bot=bot)
        ctx.should_finished(study_plugin.study_command)

    handle_guided.assert_awaited_once()
    guided_kwargs = handle_guided.await_args_list[0].kwargs
    assert guided_kwargs["trig_mode_text"] == "M"
    assert guided_kwargs["group_block_text"] == "F"
    assert guided_kwargs["weight_text"] == "3"
    send_pending.assert_awaited_once()
    build_result_message.assert_awaited_once()
    record_submission.assert_awaited_once()
