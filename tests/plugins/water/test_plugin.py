from __future__ import annotations

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
nonebot.require("nonebot_plugin_apscheduler")
sys.modules.pop("src.plugins.water", None)
nonebot.load_plugin("src.plugins.water")

from src.plugins import water as water_plugin
from tests.plugins.water.helpers import build_group_message_event


@pytest.mark.asyncio
async def test_water_query_guides_empty_command_step_by_step(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin._water_query_cooldowns.clear()
    build_rank_message = AsyncMock(return_value=Message("RANK_OK"))
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        build_rank_message,
    )

    first = build_group_message_event("#水王", message_id=1)
    second = build_group_message_event("用户榜", message_id=2)
    third = build_group_message_event("本群", message_id=3)
    fourth = build_group_message_event("月榜", message_id=4)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择榜单主体：用户榜 / 群聊榜 / 矩阵榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜 / 年榜 / 总榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, fourth)
        ctx.should_call_send(
            fourth,
            "已选择：用户榜 / 本群 / 月榜",
            bot=bot,
        )
        ctx.should_call_send(fourth, "凛凛统计中，请稍后喔……", bot=bot)
        ctx.should_call_send(fourth, Message("RANK_OK"), bot=bot)
        ctx.should_finished()

    build_rank_message.assert_awaited_once_with(
        subject="user",
        scope="group",
        period="month",
        group_id="20001",
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_water_query_guided_scope_retry_and_cancel(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin._water_query_cooldowns.clear()
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        AsyncMock(return_value=Message("SHOULD_NOT_RUN")),
    )

    first = build_group_message_event("#水王", message_id=1)
    second = build_group_message_event("群聊榜", message_id=2)
    third = build_group_message_event("本群", message_id=3)
    fourth = build_group_message_event("取消", message_id=4)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            water_plugin.water_query_router.build_guided_intro("zh-CN"),
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            "请选择范围：本矩阵 / 全局\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            "这个主体和范围组合不成立。推荐改成 #水王 群聊榜 本矩阵 <时间>\n"
            "请选择范围：本矩阵 / 全局\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, fourth)
        ctx.should_call_send(fourth, "本次操作已被取消。", bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_water_query_direct_rank_still_runs_without_guided_flow(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin._water_query_cooldowns.clear()
    build_rank_message = AsyncMock(return_value=Message("DIRECT_OK"))
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        build_rank_message,
    )

    event = build_group_message_event("#水王 用户榜 本群 日榜", message_id=1)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "凛凛统计中，请稍后喔……", bot=bot)
        ctx.should_call_send(event, Message("DIRECT_OK"), bot=bot)
        ctx.should_finished()

    build_rank_message.assert_awaited_once_with(
        subject="user",
        scope="group",
        period="day",
        group_id="20001",
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_water_query_guided_intro_uses_resolved_locale(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin._water_query_cooldowns.clear()
    monkeypatch.setattr(
        water_plugin,
        "resolve_locale",
        AsyncMock(return_value="lzh"),
    )

    event = build_group_message_event("#水王", message_id=1)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "請擇榜單主體：用户榜 / 群聊榜 / 矩阵榜\n"
            "發 revoke / recall 可止之，連錯 3 次則自動退出。",
            bot=bot,
        )
        ctx.should_rejected()


@pytest.mark.asyncio
async def test_water_query_guided_three_errors_abort(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin._water_query_cooldowns.clear()
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        AsyncMock(return_value=Message("SHOULD_NOT_RUN")),
    )

    first = build_group_message_event("#水王", message_id=1)
    second = build_group_message_event("乱写", message_id=2)
    third = build_group_message_event("还是乱写", message_id=3)
    fourth = build_group_message_event("继续乱写", message_id=4)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择榜单主体：用户榜 / 群聊榜 / 矩阵榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            "主体输入无效，请发送：用户榜 / 群聊榜 / 矩阵榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            "主体输入无效，请发送：用户榜 / 群聊榜 / 矩阵榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, fourth)
        ctx.should_call_send(fourth, "连续输入错误 3 次，本次操作已被取消。", bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_water_query_guided_revoke_aborts(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin._water_query_cooldowns.clear()
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        AsyncMock(return_value=Message("SHOULD_NOT_RUN")),
    )

    first = build_group_message_event("#水王", message_id=1)
    second = build_group_message_event("revoke", message_id=2)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择榜单主体：用户榜 / 群聊榜 / 矩阵榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(second, "本次操作已被取消。", bot=bot)
        ctx.should_finished()
