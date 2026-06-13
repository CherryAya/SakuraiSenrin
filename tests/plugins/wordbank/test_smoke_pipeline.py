import asyncio
from contextlib import suppress
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
if nonebot.get_plugin("study") is None:
    sys.modules.pop("src.plugins.study", None)
    nonebot.load_plugin("src.plugins.study")

from src.database.consts import WritePolicy
from src.lib.i18n.runtime import tr
from src.lib.utils.common import get_current_time
from src.plugins import study as study_plugin
from src.plugins import wordbank as wordbank_plugin
from src.plugins.wordbank import handlers as wordbank_handlers
from src.plugins.wordbank.message_model import (
    MessageShape,
    shape_from_event,
    shape_from_text,
)
from src.plugins.wordbank.services import wordbank_service
from src.plugins.wordbank.services.rules import RuleContext
from tests.plugins.water.helpers import build_group_message_event


async def _cancel_pending_rebuild() -> None:
    task = wordbank_service._rebuild_task
    if task is None:
        return
    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    wordbank_service._rebuild_task = None


async def _reset_wordbank_runtime() -> None:
    wordbank_plugin._wordbank_initialized = False
    wordbank_service._initialized = False
    wordbank_service._index.groups.clear()
    wordbank_service._index.exact_match.clear()
    await wordbank_plugin.initialize_wordbank_plugin()
    await wordbank_service.repository.reset_all_data(
        include_images=True,
        include_logs=True,
    )
    await _cancel_pending_rebuild()
    wordbank_service._dirty_group_ids.clear()
    wordbank_service._call_count_cache.clear()
    await wordbank_service.rebuild_index()


async def _pending_response_item_ids() -> tuple[int, ...]:
    items = await wordbank_service.list_pending_entries(
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    return tuple(
        response_item_id
        for item in items
        for response_item_id in item.response_item_ids
    )


async def _approve_all_pending() -> None:
    for response_item_id in await _pending_response_item_ids():
        ok = await wordbank_service.approve_response_item(
            response_item_id,
            actor_user_id="10002",
            actor_group_id="20001",
            can_moderate_group=True,
            is_superuser=False,
        )
        assert ok is True
    await _cancel_pending_rebuild()
    await wordbank_service.rebuild_index()


async def _add_approved_entry(
    *,
    trigger_shape: MessageShape,
    response_text: str,
    raw_rule: dict | None = None,
    user_id: str = "10001",
) -> int:
    created = await wordbank_service.add_message_entry(
        trigger_shape=trigger_shape,
        response_shape=shape_from_text(response_text),
        group_id="20001",
        user_id=user_id,
        is_group=True,
        raw_rule=raw_rule,
    )
    ok = await wordbank_service.approve_response_item(
        created.response_item_id,
        actor_user_id="10002",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    assert ok is True
    await _cancel_pending_rebuild()
    await wordbank_service.rebuild_index()
    return created.response_item_id


@pytest.mark.asyncio
async def test_wordbank_add_command_pipeline_matches_after_approval(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _reset_wordbank_runtime()
    monkeypatch.setattr(
        wordbank_plugin,
        "send_pending_approval_notice",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "record_submission_approval_message",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "build_add_result_message",
        AsyncMock(return_value=Message("ADD_OK")),
    )

    async with app.test_matcher(wordbank_plugin.wordbank_add_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event(
            "#wordbank.add 晚安 => 做个好梦",
            message_id=1,
        )

        ctx.receive_event(bot, event)
        ctx.should_call_send(event, Message("ADD_OK"), bot=bot)
        ctx.should_finished()

    pending_ids = await _pending_response_item_ids()
    assert len(pending_ids) == 1

    await _approve_all_pending()

    async with app.test_matcher(wordbank_plugin.wordbank_passive) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("晚安", message_id=2)

        ctx.receive_event(bot, event)
        ctx.should_call_send(event, Message("做个好梦"), bot=bot)


@pytest.mark.asyncio
async def test_study_guided_pipeline_matches_after_approval(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _reset_wordbank_runtime()
    monkeypatch.setattr(
        wordbank_handlers,
        "send_pending_approval_notice",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "record_submission_approval_message",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        wordbank_handlers,
        "build_add_result_message",
        AsyncMock(return_value=Message("STUDY_OK")),
    )
    monkeypatch.setattr(
        study_plugin,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
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
        ctx.should_paused()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            tr("zh-CN", "wordbank.guided.study.group_block_prompt"),
            bot=bot,
        )
        ctx.should_paused()

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            tr("zh-CN", "wordbank.guided.study.trigger_prompt"),
            bot=bot,
        )
        ctx.should_paused()

        ctx.receive_event(bot, fourth)
        ctx.should_call_send(
            fourth,
            tr("zh-CN", "wordbank.guided.study.response_prompt"),
            bot=bot,
        )
        ctx.should_paused()

        ctx.receive_event(bot, fifth)
        ctx.should_call_send(
            fifth,
            tr("zh-CN", "wordbank.guided.study.weight_prompt"),
            bot=bot,
        )
        ctx.should_paused()

        ctx.receive_event(bot, sixth)
        ctx.should_call_send(sixth, Message("STUDY_OK"), bot=bot)
        ctx.should_finished()

    pending_ids = await _pending_response_item_ids()
    assert len(pending_ids) == 1

    await _approve_all_pending()

    async with app.test_matcher(wordbank_plugin.wordbank_passive) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("不嘻嘻", message_id=7)

        ctx.receive_event(bot, event)
        ctx.should_call_send(event, Message("消息回复如下"), bot=bot)


@pytest.mark.asyncio
async def test_passive_event_at_pipeline_hits_real_event_trigger(app: App) -> None:
    await _reset_wordbank_runtime()
    await _add_approved_entry(
        trigger_shape=shape_from_event("event:at"),
        response_text="收到艾特",
        raw_rule={"scope": "current_group"},
    )

    async with app.test_matcher(wordbank_plugin.wordbank_passive) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_message_event("[CQ:at,qq=99999]", message_id=1)

        ctx.receive_event(bot, event)
        ctx.should_call_send(event, Message("收到艾特"), bot=bot)


@pytest.mark.asyncio
async def test_passive_rule_priority_and_call_count_pipeline(app: App) -> None:
    await _reset_wordbank_runtime()
    await _add_approved_entry(
        trigger_shape=shape_from_text("权限测试"),
        response_text="管理员专属",
        raw_rule={"scope": "current_group", "roles": "admin", "priority": 9},
    )
    await _add_approved_entry(
        trigger_shape=shape_from_text("首发测试"),
        response_text="首发限次",
        raw_rule={
            "scope": "current_group",
            "call_count": {"window_seconds": 3600, "min": 0, "max": 1},
        },
    )

    selected = await wordbank_service.match_message(
        shape_from_text("首发测试"),
        context=RuleContext(
            group_id="20001",
            user_id="10003",
            message_type="group",
            sender_role="member",
        ),
    )
    assert selected is not None
    assert selected.response.text == "首发限次"

    fallback_result = await wordbank_service.add_message_entry(
        trigger_shape=shape_from_text("限次测试"),
        response_shape=shape_from_text("普通回退"),
        group_id="20001",
        user_id="10001",
        is_group=True,
        raw_rule={"scope": "current_group", "priority": 1},
    )
    limited_result = await wordbank_service.add_message_entry(
        trigger_shape=shape_from_text("限次测试"),
        response_shape=shape_from_text("首发限次"),
        group_id="20001",
        user_id="10001",
        is_group=True,
        raw_rule={
            "scope": "current_group",
            "priority": 9,
            "call_count": {"window_seconds": 3600, "min": 0, "max": 1},
        },
    )
    for response_item_id in (
        fallback_result.response_item_id,
        limited_result.response_item_id,
    ):
        ok = await wordbank_service.approve_response_item(
            response_item_id,
            actor_user_id="10002",
            actor_group_id="20001",
            can_moderate_group=True,
            is_superuser=False,
        )
        assert ok is True
    await _cancel_pending_rebuild()
    await wordbank_service.rebuild_index()
    await wordbank_service.repository.save_log(
        {
            "trigger_group_id": limited_result.trigger_group_id,
            "trigger_variant_id": limited_result.trigger_variant_id,
            "response_item_id": limited_result.response_item_id,
            "group_id": "20001",
            "user_id": "10003",
            "message_type": "group",
            "created_at": get_current_time(),
        },
        policy=WritePolicy.IMMEDIATE,
    )
    await wordbank_service.repository.save_log(
        {
            "trigger_group_id": limited_result.trigger_group_id,
            "trigger_variant_id": limited_result.trigger_variant_id,
            "response_item_id": limited_result.response_item_id,
            "group_id": "20001",
            "user_id": "10003",
            "message_type": "group",
            "created_at": get_current_time(),
        },
        policy=WritePolicy.IMMEDIATE,
    )
    wordbank_service._call_count_cache.clear()

    async with app.test_matcher(wordbank_plugin.wordbank_passive) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        admin_event = build_group_message_event(
            "权限测试",
            role="admin",
            user_id=10002,
            message_id=1,
        )
        first_limited = build_group_message_event(
            "限次测试",
            role="member",
            user_id=10003,
            message_id=2,
        )

        ctx.receive_event(bot, admin_event)
        ctx.should_call_send(admin_event, Message("管理员专属"), bot=bot)

        ctx.receive_event(bot, first_limited)
        ctx.should_call_send(first_limited, Message("普通回退"), bot=bot)
