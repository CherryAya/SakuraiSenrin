from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
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
    command_start={"#", "/", "＃"},
    command_sep={"."},
)

# tests/conftest.py installs a package stub to avoid plugin entrypoint side effects
# during unit-test collection. These matcher tests intentionally load entrypoints.
sys.modules.pop("src.plugins.wordbank", None)

nonebot.load_plugin("src.plugins.wordbank")
nonebot.load_plugin("src.plugins.study")

from src.plugins import study as study_plugin
from src.plugins import wordbank as wordbank_plugin
from src.plugins.wordbank.database.types import WordbankSearchItem
from src.plugins.wordbank.handlers import commands as wordbank_commands
from src.plugins.wordbank.handlers.passive import PassiveResponse
from src.plugins.wordbank.services.core import WordbankAddResult
from tests.plugins.water.helpers import (
    build_friend_recall_event,
    build_group_increase_event,
    build_group_message_event,
    build_group_poke_event,
    build_group_recall_event,
    build_private_message_event,
)

wordbank_plugin._wordbank_initialized = True


def _add_result(
    *,
    entry_id: int = 42,
    trigger_text: str = "晚安",
    response_text: str = "做个好梦",
    trigger_mode: str = "contains",
    scope: str = "current_group",
    weight: int = 3,
) -> WordbankAddResult:
    return WordbankAddResult(
        entry_id=entry_id,
        trigger_text=trigger_text,
        response_text=response_text,
        trigger_mode=trigger_mode,
        scope=scope,
        probability=1.0,
        weight=weight,
    )


def _patch_wordbank_services(
    monkeypatch: pytest.MonkeyPatch,
    *,
    add_result: WordbankAddResult | None = None,
) -> AsyncMock:
    initialize = AsyncMock()
    add_text_entry = AsyncMock(return_value=add_result or _add_result())
    record_approval_message = AsyncMock()
    service = SimpleNamespace(
        initialize=initialize,
        add_text_entry=add_text_entry,
        record_approval_message=record_approval_message,
    )
    media_service = SimpleNamespace(
        rebuild_cache=AsyncMock(),
        load_canonical_storage_bytes=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(wordbank_plugin, "wordbank_service", service)
    monkeypatch.setattr(wordbank_plugin, "wordbank_media_service", media_service)

    import src.plugins.wordbank.handlers.approval as approval_module
    import src.plugins.wordbank.services as services_module

    monkeypatch.setattr(services_module, "wordbank_service", service)
    monkeypatch.setattr(services_module, "wordbank_media_service", media_service)
    monkeypatch.setattr(
        approval_module.config,
        "SUPERUSERS",
        set(),
    )
    return add_text_entry


def _message_event(
    message: str,
    *,
    message_id: int,
) -> Any:
    return build_group_message_event(message, message_id=message_id)


def _private_message_event(
    message: str,
    *,
    message_id: int,
) -> Any:
    return build_private_message_event(message, message_id=message_id)


def _patch_wordbank_search_services(
    monkeypatch: pytest.MonkeyPatch,
    *,
    search_items: list[WordbankSearchItem],
    image_scores: list[tuple[int, float]] | None = None,
) -> AsyncMock:
    search = AsyncMock(return_value=search_items)
    service = SimpleNamespace(
        initialize=AsyncMock(),
        search=search,
    )
    matches = [
        SimpleNamespace(canonical_id=canonical_id, score=score)
        for canonical_id, score in (image_scores or [])
    ]
    media_service = SimpleNamespace(
        rebuild_cache=AsyncMock(),
        search_similar_images=lambda _data: list(matches),
    )
    monkeypatch.setattr(wordbank_plugin, "wordbank_service", service)
    monkeypatch.setattr(wordbank_plugin, "wordbank_media_service", media_service)
    return search


@pytest.mark.asyncio
async def test_study_guided_flow_retries_invalid_mode_then_finishes(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(
        monkeypatch,
        add_result=_add_result(entry_id=101, weight=4),
    )

    async with app.test_matcher(study_plugin.study_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _message_event("#study", message_id=1)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择触发方式：\na. 对所有人有效\nm. 仅对自己有效",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        invalid_mode = _message_event("x", message_id=2)
        ctx.receive_event(bot, invalid_mode)
        ctx.should_call_send(
            invalid_mode,
            "触发方式输入错误，请输入 a 或 m。a 表示对所有人有效，m 表示仅对自己有效。",
            bot=bot,
        )
        ctx.should_rejected(study_plugin.study_command)

        mode = _message_event("a", message_id=3)
        ctx.receive_event(bot, mode)
        ctx.should_call_send(
            mode,
            (
                "是否开启群组隔离？\n"
                "t. 开启，仅当前群聊有效\n"
                "f. 关闭，按触发方式跨群或私聊生效"
            ),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        group_block = _message_event("t", message_id=4)
        ctx.receive_event(bot, group_block)
        ctx.should_call_send(
            group_block,
            "请输入触发词，或直接发送图片作为图片触发：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        trigger = _message_event("晚安", message_id=5)
        ctx.receive_event(bot, trigger)
        ctx.should_call_send(
            trigger,
            "请输入响应词，或发送图片作为图片回复：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        response = _message_event("做个好梦", message_id=6)
        ctx.receive_event(bot, response)
        ctx.should_call_send(
            response,
            "请输入响应词权重，1-5 之间的数字；直接发送 3 可用默认权重：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        weight = _message_event("4", message_id=7)
        ctx.receive_event(bot, weight)
        ctx.should_call_send(
            weight,
            Message(
                "词条已提交审核\n"
                "ID: 101\n"
                "状态: pending\n"
                "触发: 晚安\n"
                "响应: 做个好梦\n"
                "模式: contains\n"
                "范围: current_group\n"
                "概率: 1\n"
                "权重: 4\n"
                "管理员通过前不会触发。"
            ),
            bot=bot,
            result={"message_id": 7001},
        )
        ctx.should_finished(study_plugin.study_command)

    add_text_entry.assert_awaited_once_with(
        trigger_text="晚安",
        response_text="做个好梦",
        response_canonical_image_id=None,
        raw_rule={"scope": "current_group", "weight": 4},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )


@pytest.mark.asyncio
async def test_wordbank_search_command_returns_ranked_text_results(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = _patch_wordbank_search_services(
        monkeypatch,
        search_items=[
            WordbankSearchItem(
                entry_id=12,
                status="approved",
                trigger_text="晚安",
                trigger_mode="contains",
                trigger_canonical_image_id=None,
                response_text="做个好梦",
                scope="current_group",
                probability=1.0,
                weight=3,
                created_by="10001",
            )
        ],
    )

    async with app.test_matcher(wordbank_plugin.wordbank_search_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = _message_event("#搜索词条 晚安", message_id=1001)

        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            (
                "词库搜索结果 (第 1 页):\n"
                "#12 [approved/contains/current_group] 晚安 => 做个好梦"
            ),
            bot=bot,
        )

    assert search.await_args is not None
    request = search.await_args.args[0]
    assert request.keyword == "晚安"
    assert request.field == "all"
    assert not request.has_image


@pytest.mark.asyncio
async def test_wordbank_search_command_accepts_image_query(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = _patch_wordbank_search_services(
        monkeypatch,
        search_items=[
            WordbankSearchItem(
                entry_id=18,
                status="approved",
                trigger_text="[图片:7]",
                trigger_mode="fullmatch",
                trigger_canonical_image_id=7,
                response_text="识图命中",
                scope="current_group",
                probability=1.0,
                weight=3,
                created_by="10001",
            )
        ],
        image_scores=[(7, 0.95)],
    )
    monkeypatch.setattr(
        wordbank_commands,
        "fetch_image_bytes_with_retry",
        AsyncMock(return_value=b"image-bytes"),
    )

    async with app.test_matcher(wordbank_plugin.wordbank_search_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = _message_event(
            "#搜索词条 [CQ:image,url=https://example.test/query.png]",
            message_id=1002,
        )

        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            (
                "词库搜索结果 (第 1 页):\n"
                "#18 [approved/fullmatch/current_group] [图片:7] => 识图命中"
            ),
            bot=bot,
        )

    assert search.await_args is not None
    request = search.await_args.args[0]
    assert request.has_image
    assert request.image_scores == {7: 0.95}


@pytest.mark.asyncio
async def test_study_guided_flow_aborts_after_three_invalid_inputs(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(monkeypatch)

    async with app.test_matcher(study_plugin.study_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _message_event("#study", message_id=11)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择触发方式：\na. 对所有人有效\nm. 仅对自己有效",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        for index, text in enumerate(("x", "z"), start=12):
            event = _message_event(text, message_id=index)
            ctx.receive_event(bot, event)
            ctx.should_call_send(
                event,
                "触发方式输入错误，请输入 a 或 m。"
                "a 表示对所有人有效，m 表示仅对自己有效。",
                bot=bot,
            )
            ctx.should_rejected(study_plugin.study_command)

        third = _message_event("bad", message_id=14)
        ctx.receive_event(bot, third)
        ctx.should_call_send(third, "连续输入错误 3 次，本次操作已被取消。", bot=bot)
        ctx.should_finished(study_plugin.study_command)

    add_text_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_study_guided_flow_cancels_on_revoke_signal(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(monkeypatch)

    async with app.test_matcher(study_plugin.study_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _message_event("#study", message_id=21)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择触发方式：\na. 对所有人有效\nm. 仅对自己有效",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        revoke = _message_event("revoke", message_id=22)
        ctx.receive_event(bot, revoke)
        ctx.should_call_send(revoke, "本次操作已被取消。", bot=bot)
        ctx.should_finished(study_plugin.study_command)

    add_text_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_study_group_recall_of_root_cancels_session(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(monkeypatch)

    async with app.test_matcher(
        {
            0: [],
            5: [study_plugin.study_command, study_plugin.study_recall_notice],
        }
    ) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _message_event("#study", message_id=51)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择触发方式：\na. 对所有人有效\nm. 仅对自己有效",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        recall = build_group_recall_event(message_id=51)
        ctx.receive_event(bot, recall)
        ctx.should_call_send(recall, "本次操作已被取消。", bot=bot)

    add_text_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_study_friend_recall_of_latest_step_rewinds_current_step(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(
        monkeypatch,
        add_result=_add_result(entry_id=301, scope="private_only"),
    )

    async with app.test_matcher(
        {
            0: [],
            5: [study_plugin.study_command, study_plugin.study_recall_notice],
        }
    ) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _private_message_event("#study", message_id=61)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择触发方式：\na. 对所有人有效\nm. 仅对自己有效",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        mode = _private_message_event("a", message_id=62)
        ctx.receive_event(bot, mode)
        ctx.should_call_send(
            mode,
            (
                "是否开启群组隔离？\n"
                "t. 开启，仅当前群聊有效\n"
                "f. 关闭，按触发方式跨群或私聊生效"
            ),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        group_block = _private_message_event("f", message_id=63)
        ctx.receive_event(bot, group_block)
        ctx.should_call_send(
            group_block,
            "请输入触发词，或直接发送图片作为图片触发：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        recall = build_friend_recall_event(message_id=63)
        ctx.receive_event(bot, recall)
        ctx.should_call_send(
            recall,
            (
                "是否开启群组隔离？\n"
                "t. 开启，仅当前群聊有效\n"
                "f. 关闭，按触发方式跨群或私聊生效"
            ),
            bot=bot,
        )

        fixed_group_block = _private_message_event("f", message_id=64)
        ctx.receive_event(bot, fixed_group_block)
        ctx.should_call_send(
            fixed_group_block,
            "请输入触发词，或直接发送图片作为图片触发：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        trigger = _private_message_event("晚安", message_id=65)
        ctx.receive_event(bot, trigger)
        ctx.should_call_send(
            trigger,
            "请输入响应词，或发送图片作为图片回复：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        response = _private_message_event("做个好梦", message_id=66)
        ctx.receive_event(bot, response)
        ctx.should_call_send(
            response,
            "请输入响应词权重，1-5 之间的数字；直接发送 3 可用默认权重：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        weight = _private_message_event("3", message_id=67)
        ctx.receive_event(bot, weight)
        ctx.should_call_send(
            weight,
            Message(
                "词条已提交审核\n"
                "ID: 301\n"
                "状态: pending\n"
                "触发: 晚安\n"
                "响应: 做个好梦\n"
                "模式: contains\n"
                "范围: private_only\n"
                "概率: 1\n"
                "权重: 3\n"
                "管理员通过前不会触发。"
            ),
            bot=bot,
            result={"message_id": 7003},
        )
        ctx.should_finished(study_plugin.study_command)

    add_text_entry.assert_awaited_once_with(
        trigger_text="晚安",
        response_text="做个好梦",
        response_canonical_image_id=None,
        raw_rule={"scope": "private_only", "weight": 3},
        group_id="",
        user_id="10001",
        is_group=False,
    )


@pytest.mark.asyncio
async def test_study_recall_of_older_step_is_ignored(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(monkeypatch)

    async with app.test_matcher(
        {
            0: [],
            5: [study_plugin.study_command, study_plugin.study_recall_notice],
        }
    ) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _message_event("#study", message_id=71)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择触发方式：\na. 对所有人有效\nm. 仅对自己有效",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        mode = _message_event("a", message_id=72)
        ctx.receive_event(bot, mode)
        ctx.should_call_send(
            mode,
            (
                "是否开启群组隔离？\n"
                "t. 开启，仅当前群聊有效\n"
                "f. 关闭，按触发方式跨群或私聊生效"
            ),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        group_block = _message_event("t", message_id=73)
        ctx.receive_event(bot, group_block)
        ctx.should_call_send(
            group_block,
            "请输入触发词，或直接发送图片作为图片触发：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        trigger = _message_event("晚安", message_id=74)
        ctx.receive_event(bot, trigger)
        ctx.should_call_send(
            trigger,
            "请输入响应词，或发送图片作为图片回复：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        recall_old = build_group_recall_event(message_id=72)
        ctx.receive_event(bot, recall_old)

        response = _message_event("做个好梦", message_id=75)
        ctx.receive_event(bot, response)
        ctx.should_call_send(
            response,
            "请输入响应词权重，1-5 之间的数字；直接发送 3 可用默认权重：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

    add_text_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_study_guided_flow_reports_pending_images_before_finish(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(
        monkeypatch,
        add_result=_add_result(entry_id=401),
    )
    monkeypatch.setattr(study_plugin, "_study_pending_image_count", lambda state: 2)

    async with app.test_matcher(study_plugin.study_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _message_event("#study", message_id=81)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择触发方式：\na. 对所有人有效\nm. 仅对自己有效",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        mode = _message_event("a", message_id=82)
        ctx.receive_event(bot, mode)
        ctx.should_call_send(
            mode,
            (
                "是否开启群组隔离？\n"
                "t. 开启，仅当前群聊有效\n"
                "f. 关闭，按触发方式跨群或私聊生效"
            ),
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        group_block = _message_event("t", message_id=83)
        ctx.receive_event(bot, group_block)
        ctx.should_call_send(
            group_block,
            "请输入触发词，或直接发送图片作为图片触发：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        trigger = _message_event("晚安", message_id=84)
        ctx.receive_event(bot, trigger)
        ctx.should_call_send(
            trigger,
            "请输入响应词，或发送图片作为图片回复：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        response = _message_event("做个好梦", message_id=85)
        ctx.receive_event(bot, response)
        ctx.should_call_send(
            response,
            "请输入响应词权重，1-5 之间的数字；直接发送 3 可用默认权重：",
            bot=bot,
        )
        ctx.should_paused(study_plugin.study_command)

        weight = _message_event("3", message_id=86)
        ctx.receive_event(bot, weight)
        ctx.should_call_send(
            weight,
            "还有 2 张图片正在处理中，请稍等。",
            bot=bot,
        )
        ctx.should_call_send(
            weight,
            Message(
                "词条已提交审核\n"
                "ID: 401\n"
                "状态: pending\n"
                "触发: 晚安\n"
                "响应: 做个好梦\n"
                "模式: contains\n"
                "范围: current_group\n"
                "概率: 1\n"
                "权重: 3\n"
                "管理员通过前不会触发。"
            ),
            bot=bot,
            result={"message_id": 7004},
        )
        ctx.should_finished(study_plugin.study_command)

    add_text_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_wordbank_add_guided_flow_retries_invalid_scope_and_advanced_options(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(
        monkeypatch,
        add_result=_add_result(
            entry_id=202,
            trigger_text="早安",
            response_text="今天也要加油",
            trigger_mode="fullmatch",
            scope="current_group",
            weight=5,
        ),
    )

    async with app.test_matcher(wordbank_plugin.wordbank_add_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _message_event("#wordbank.add", message_id=31)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请输入触发词，或直接发送图片作为图片触发：",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        trigger = _message_event("早安", message_id=32)
        ctx.receive_event(bot, trigger)
        ctx.should_call_send(
            trigger,
            "请输入响应词，或发送图片作为图片回复：",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        response = _message_event("今天也要加油", message_id=33)
        ctx.receive_event(bot, response)
        ctx.should_call_send(
            response,
            "请选择生效范围：\n1. 当前群（默认）\n2. 所有群\n3. 仅自己\n4. 仅私聊",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        invalid_scope = _message_event("9", message_id=34)
        ctx.receive_event(bot, invalid_scope)
        ctx.should_call_send(
            invalid_scope,
            (
                "生效范围选择无效，请输入 1/2/3/4。"
                "1 当前群（默认），2 所有群，3 仅自己，4 仅私聊。"
            ),
            bot=bot,
        )
        ctx.should_rejected(wordbank_plugin.wordbank_add_command)

        scope = _message_event("1", message_id=35)
        ctx.receive_event(bot, scope)
        ctx.should_call_send(
            scope,
            "是否需要高级选项？发送 n 跳过；需要请直接输入参数，例如 "
            "--mode fullmatch --prob 0.5 --weight 3 --role admin --call 60:0:3",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        invalid_advanced = _message_event("unknown", message_id=36)
        ctx.receive_event(bot, invalid_advanced)
        ctx.should_call_send(
            invalid_advanced,
            "无法识别高级选项: unknown",
            bot=bot,
        )
        ctx.should_rejected(wordbank_plugin.wordbank_add_command)

        advanced = _message_event("--mode fullmatch --weight 5", message_id=37)
        ctx.receive_event(bot, advanced)
        ctx.should_call_send(
            advanced,
            Message(
                "词条已提交审核\n"
                "ID: 202\n"
                "状态: pending\n"
                "触发: 早安\n"
                "响应: 今天也要加油\n"
                "模式: fullmatch\n"
                "范围: current_group\n"
                "概率: 1\n"
                "权重: 5\n"
                "管理员通过前不会触发。"
            ),
            bot=bot,
            result={"message_id": 7002},
        )
        ctx.should_finished(wordbank_plugin.wordbank_add_command)

    add_text_entry.assert_awaited_once_with(
        trigger_text="早安",
        response_text="今天也要加油",
        response_canonical_image_id=None,
        trigger_mode="fullmatch",
        raw_rule={"scope": "current_group", "weight": "5"},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )


@pytest.mark.asyncio
async def test_wordbank_add_group_recall_of_root_cancels_session(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(monkeypatch)

    async with app.test_matcher(
        {
            0: [],
            5: [wordbank_plugin.wordbank_add_command],
            95: [wordbank_plugin.wordbank_notice],
        }
    ) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _message_event("#wordbank.add", message_id=91)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请输入触发词，或直接发送图片作为图片触发：",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        recall = build_group_recall_event(message_id=91)
        ctx.receive_event(bot, recall)
        ctx.should_call_send(recall, "本次操作已被取消。", bot=bot)

    add_text_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_wordbank_add_friend_recall_of_latest_step_rewinds_current_step(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(
        monkeypatch,
        add_result=_add_result(
            entry_id=302,
            trigger_text="晚安",
            response_text="做个好梦",
            scope="self",
        ),
    )

    async with app.test_matcher(
        {
            0: [],
            5: [wordbank_plugin.wordbank_add_command],
            95: [wordbank_plugin.wordbank_notice],
        }
    ) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _private_message_event("#wordbank.add", message_id=101)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请输入触发词，或直接发送图片作为图片触发：",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        trigger = _private_message_event("晚安", message_id=102)
        ctx.receive_event(bot, trigger)
        ctx.should_call_send(
            trigger,
            "请输入响应词，或发送图片作为图片回复：",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        response = _private_message_event("做个好梦", message_id=103)
        ctx.receive_event(bot, response)
        ctx.should_call_send(
            response,
            "请选择生效范围：\n1. 当前群（默认）\n2. 所有群\n3. 仅自己\n4. 仅私聊",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        recall = build_friend_recall_event(message_id=103)
        ctx.receive_event(bot, recall)
        ctx.should_call_send(
            recall,
            "请输入响应词，或发送图片作为图片回复：",
            bot=bot,
        )

        fixed_response = _private_message_event("早点休息", message_id=104)
        ctx.receive_event(bot, fixed_response)
        ctx.should_call_send(
            fixed_response,
            "请选择生效范围：\n1. 当前群（默认）\n2. 所有群\n3. 仅自己\n4. 仅私聊",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        scope = _private_message_event("3", message_id=105)
        ctx.receive_event(bot, scope)
        ctx.should_call_send(
            scope,
            "是否需要高级选项？发送 n 跳过；需要请直接输入参数，例如 "
            "--mode fullmatch --prob 0.5 --weight 3 --role admin --call 60:0:3",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        advanced = _private_message_event("n", message_id=106)
        ctx.receive_event(bot, advanced)
        ctx.should_call_send(
            advanced,
            Message(
                "词条已提交审核\n"
                "ID: 302\n"
                "状态: pending\n"
                "触发: 晚安\n"
                "响应: 做个好梦\n"
                "模式: contains\n"
                "范围: self\n"
                "概率: 1\n"
                "权重: 3\n"
                "管理员通过前不会触发。"
            ),
            bot=bot,
            result={"message_id": 7005},
        )
        ctx.should_finished(wordbank_plugin.wordbank_add_command)

    add_text_entry.assert_awaited_once_with(
        trigger_text="晚安",
        response_text="早点休息",
        response_canonical_image_id=None,
        trigger_mode=None,
        raw_rule={"scope": "self"},
        group_id="",
        user_id="10001",
        is_group=False,
    )


@pytest.mark.asyncio
async def test_wordbank_add_recall_of_older_step_is_ignored(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(monkeypatch)

    async with app.test_matcher(
        {
            0: [],
            5: [wordbank_plugin.wordbank_add_command],
            95: [wordbank_plugin.wordbank_notice],
        }
    ) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _message_event("#wordbank.add", message_id=111)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请输入触发词，或直接发送图片作为图片触发：",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        trigger = _message_event("晚安", message_id=112)
        ctx.receive_event(bot, trigger)
        ctx.should_call_send(
            trigger,
            "请输入响应词，或发送图片作为图片回复：",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        response = _message_event("做个好梦", message_id=113)
        ctx.receive_event(bot, response)
        ctx.should_call_send(
            response,
            "请选择生效范围：\n1. 当前群（默认）\n2. 所有群\n3. 仅自己\n4. 仅私聊",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        recall_old = build_group_recall_event(message_id=112)
        ctx.receive_event(bot, recall_old)

        scope = _message_event("1", message_id=114)
        ctx.receive_event(bot, scope)
        ctx.should_call_send(
            scope,
            "是否需要高级选项？发送 n 跳过；需要请直接输入参数，例如 "
            "--mode fullmatch --prob 0.5 --weight 3 --role admin --call 60:0:3",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

    add_text_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_wordbank_add_guided_flow_reports_pending_images_before_finish(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_text_entry = _patch_wordbank_services(
        monkeypatch,
        add_result=_add_result(entry_id=402),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "_wordbank_guided_pending_image_count",
        lambda state: 2,
    )

    async with app.test_matcher(wordbank_plugin.wordbank_add_command) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        first = _message_event("#wordbank.add", message_id=121)
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请输入触发词，或直接发送图片作为图片触发：",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        trigger = _message_event("晚安", message_id=122)
        ctx.receive_event(bot, trigger)
        ctx.should_call_send(
            trigger,
            "请输入响应词，或发送图片作为图片回复：",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        response = _message_event("做个好梦", message_id=123)
        ctx.receive_event(bot, response)
        ctx.should_call_send(
            response,
            "请选择生效范围：\n1. 当前群（默认）\n2. 所有群\n3. 仅自己\n4. 仅私聊",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        scope = _message_event("1", message_id=124)
        ctx.receive_event(bot, scope)
        ctx.should_call_send(
            scope,
            "是否需要高级选项？发送 n 跳过；需要请直接输入参数，例如 "
            "--mode fullmatch --prob 0.5 --weight 3 --role admin --call 60:0:3",
            bot=bot,
        )
        ctx.should_paused(wordbank_plugin.wordbank_add_command)

        advanced = _message_event("n", message_id=125)
        ctx.receive_event(bot, advanced)
        ctx.should_call_send(
            advanced,
            "还有 2 张图片正在处理中，请稍等。",
            bot=bot,
        )
        ctx.should_call_send(
            advanced,
            Message(
                "词条已提交审核\n"
                "ID: 402\n"
                "状态: pending\n"
                "触发: 晚安\n"
                "响应: 做个好梦\n"
                "模式: contains\n"
                "范围: current_group\n"
                "概率: 1\n"
                "权重: 3\n"
                "管理员通过前不会触发。"
            ),
            bot=bot,
            result={"message_id": 7006},
        )
        ctx.should_finished(wordbank_plugin.wordbank_add_command)

    add_text_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_wordbank_passive_message_sends_and_records_response(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = PassiveResponse(
        text="自动回复",
        entry_id=12,
        trigger_id=120,
        response_id=300,
        group_id="20001",
        user_id="10001",
        message_type="text",
    )
    record_response_message = AsyncMock()
    service = SimpleNamespace(
        initialize=AsyncMock(),
        record_response_message=record_response_message,
    )
    monkeypatch.setattr(wordbank_plugin, "wordbank_service", service)
    monkeypatch.setattr(
        wordbank_plugin,
        "wordbank_media_service",
        SimpleNamespace(rebuild_cache=AsyncMock()),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "handle_passive_message",
        AsyncMock(return_value=response),
    )

    event = _message_event("晚安", message_id=41)

    async with app.test_matcher(wordbank_plugin.wordbank_passive) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "自动回复",
            bot=bot,
            result={"message_id": 8001},
        )

    record_response_message.assert_awaited_once_with(
        message_id="8001",
        entry_id=12,
        trigger_id=120,
        response_id=300,
        group_id="20001",
        user_id="10001",
        message_type="text",
    )


@pytest.mark.asyncio
async def test_wordbank_notice_matcher_handles_poke_and_join_events(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_response_message = AsyncMock()
    service = SimpleNamespace(
        initialize=AsyncMock(),
        record_response_message=record_response_message,
    )
    monkeypatch.setattr(wordbank_plugin, "wordbank_service", service)
    monkeypatch.setattr(
        wordbank_plugin,
        "wordbank_media_service",
        SimpleNamespace(rebuild_cache=AsyncMock()),
    )
    monkeypatch.setattr(
        wordbank_plugin,
        "handle_passive_notice",
        AsyncMock(
            side_effect=[
                PassiveResponse(
                    text="别戳啦",
                    entry_id=13,
                    trigger_id=130,
                    response_id=301,
                    group_id="20001",
                    user_id="10001",
                    message_type="event",
                ),
                PassiveResponse(
                    text="欢迎入群",
                    entry_id=14,
                    trigger_id=140,
                    response_id=302,
                    group_id="20001",
                    user_id="10002",
                    message_type="event",
                ),
            ]
        ),
    )
    async with app.test_matcher(wordbank_plugin.wordbank_notice) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        poke = build_group_poke_event(target_id=99999)
        ctx.receive_event(bot, poke)
        ctx.should_call_send(
            poke,
            "别戳啦",
            bot=bot,
            result={"message_id": 8101},
        )

        join = build_group_increase_event(user_id=10002)
        ctx.receive_event(bot, join)
        ctx.should_call_send(
            join,
            "欢迎入群",
            bot=bot,
            result={"message_id": 8102},
        )

    assert record_response_message.await_args_list[0].kwargs == {
        "message_id": "8101",
        "entry_id": 13,
        "trigger_id": 130,
        "response_id": 301,
        "group_id": "20001",
        "user_id": "10001",
        "message_type": "event",
    }
    assert record_response_message.await_args_list[1].kwargs == {
        "message_id": "8102",
        "entry_id": 14,
        "trigger_id": 140,
        "response_id": 302,
        "group_id": "20001",
        "user_id": "10002",
        "message_type": "event",
    }
