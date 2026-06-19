from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Bot
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
nonebot.require("nonebot_plugin_apscheduler")
sys.modules.pop("src.plugins.water", None)
nonebot.load_plugin("src.plugins.water")

from src.plugins import water as water_plugin
from src.plugins.water.services.rank_types import WaterRankQuerySpec
from tests.plugins.water.helpers import build_group_message_event


@pytest.mark.asyncio
async def test_water_query_guides_empty_command_step_by_step(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    build_rank_message = AsyncMock(return_value=text_message("RANK_OK"))
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
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
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
        ctx.should_call_send(fourth, text_message("RANK_OK"), bot=bot)
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
    water_plugin.clear_water_query_cooldowns()
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        AsyncMock(return_value=text_message("SHOULD_NOT_RUN")),
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
            "请选择榜单主体：用户榜 / 群聊榜 / 矩阵榜\n"
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            "请选择范围：本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            "这个主体和范围组合不成立。推荐改成 #水王 群聊榜 本矩阵 <时间>\n"
            "请选择范围：本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
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
    water_plugin.clear_water_query_cooldowns()
    build_rank_message = AsyncMock(return_value=text_message("DIRECT_OK"))
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
        ctx.should_call_send(event, text_message("DIRECT_OK"), bot=bot)
        ctx.should_finished()

    build_rank_message.assert_awaited_once_with(
        subject="user",
        scope="group",
        period="day",
        group_id="20001",
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_water_query_shortcut_alias_runs_direct_rank(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    build_rank_message = AsyncMock(return_value=text_message("SHORTCUT_OK"))
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        build_rank_message,
    )

    event = build_group_message_event("#今日水王", message_id=1)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "凛凛统计中，请稍后喔……", bot=bot)
        ctx.should_call_send(event, text_message("SHORTCUT_OK"), bot=bot)
        ctx.should_finished()

    build_rank_message.assert_awaited_once_with(
        subject="user",
        scope="group",
        period="day",
        group_id="20001",
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_water_query_shortcut_alias_with_args_shows_menu_error(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        AsyncMock(return_value=text_message("SHOULD_NOT_RUN")),
    )

    event = build_group_message_event("#今日水王 多余参数", message_id=1)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            text_message(
                water_plugin.water_query_router.build_rank_menu(
                    "zh-CN",
                    WaterRankQuerySpec(subject="user", scope="group", period="day"),
                    ("shortcut_with_args", "今日水王"),
                    is_superuser=False,
                )
            ),
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_water_query_direct_restricted_period_requires_superuser(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        AsyncMock(return_value=text_message("SHOULD_NOT_RUN")),
    )

    event = build_group_message_event("#水王 用户榜 本群 年榜", message_id=1)
    expected_menu = water_plugin.water_query_router.build_rank_menu(
        "zh-CN",
        WaterRankQuerySpec(subject="user", scope="group", period="year"),
        ("invalid_period",),
        is_superuser=False,
    )

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, text_message(expected_menu), bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_water_query_guided_restricted_period_retries_for_normal_user(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        AsyncMock(return_value=text_message("SHOULD_NOT_RUN")),
    )

    first = build_group_message_event("#水王", message_id=1)
    second = build_group_message_event("用户榜", message_id=2)
    third = build_group_message_event("本群", message_id=3)
    fourth = build_group_message_event("年榜", message_id=4)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择榜单主体：用户榜 / 群聊榜 / 矩阵榜\n"
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, fourth)
        ctx.should_call_send(
            fourth,
            "时间不合法。可用时间：日榜/周榜/月榜/季榜\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()


@pytest.mark.asyncio
async def test_water_query_superuser_can_use_restricted_periods(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    build_rank_message = AsyncMock(return_value=text_message("SUPERUSER_OK"))
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        build_rank_message,
    )

    first = build_group_message_event("#水王", user_id=1, message_id=1)
    second = build_group_message_event("用户榜", user_id=1, message_id=2)
    third = build_group_message_event("本群", user_id=1, message_id=3)
    fourth = build_group_message_event("总榜", user_id=1, message_id=4)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择榜单主体：用户榜 / 群聊榜 / 矩阵榜\n"
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜 / 年榜 / 总榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜 / 年榜 / 总榜\n"
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
            "已选择：用户榜 / 本群 / 总榜",
            bot=bot,
        )
        ctx.should_call_send(fourth, "凛凛统计中，请稍后喔……", bot=bot)
        ctx.should_call_send(fourth, text_message("SUPERUSER_OK"), bot=bot)
        ctx.should_finished()

    build_rank_message.assert_awaited_once_with(
        subject="user",
        scope="group",
        period="total",
        group_id="20001",
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_water_query_guided_intro_uses_resolved_locale(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
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
            "請擇範圍：本群 / 本矩阵 / 全局\n"
            "請擇時間：日榜 / 周榜 / 月榜 / 季榜\n"
            "發 revoke / recall 可止之，連錯 3 次則自動退出。",
            bot=bot,
        )
        ctx.should_rejected()


@pytest.mark.asyncio
async def test_water_query_guided_three_errors_abort(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        AsyncMock(return_value=text_message("SHOULD_NOT_RUN")),
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
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            "有未识别的关键词: 乱写。\n"
            "请选择榜单主体：用户榜 / 群聊榜 / 矩阵榜\n"
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            "有未识别的关键词: 还是乱写。\n"
            "请选择榜单主体：用户榜 / 群聊榜 / 矩阵榜\n"
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
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
    water_plugin.clear_water_query_cooldowns()
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        AsyncMock(return_value=text_message("SHOULD_NOT_RUN")),
    )

    first = build_group_message_event("#水王", message_id=1)
    second = build_group_message_event("revoke", message_id=2)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择榜单主体：用户榜 / 群聊榜 / 矩阵榜\n"
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(second, "本次操作已被取消。", bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_water_query_guided_accepts_all_dimensions_in_one_reply(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    build_rank_message = AsyncMock(return_value=text_message("ONE_SHOT_OK"))
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        build_rank_message,
    )

    first = build_group_message_event("#水王", message_id=1)
    second = build_group_message_event("用户榜 本群 月榜", message_id=2)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")

        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择榜单主体：用户榜 / 群聊榜 / 矩阵榜\n"
            "请选择范围：本群 / 本矩阵 / 全局\n"
            "请选择时间：日榜 / 周榜 / 月榜 / 季榜\n"
            "发送 revoke / recall 可取消，连续输错 3 次会自动退出。",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(second, "已选择：用户榜 / 本群 / 月榜", bot=bot)
        ctx.should_call_send(second, "凛凛统计中，请稍后喔……", bot=bot)
        ctx.should_call_send(second, text_message("ONE_SHOT_OK"), bot=bot)
        ctx.should_finished()

    build_rank_message.assert_awaited_once_with(
        subject="user",
        scope="group",
        period="month",
        group_id="20001",
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_water_today_report_requires_group_admin(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    water_plugin.water_report_service.clear_today_report_cooldowns()
    monkeypatch.setattr(
        water_plugin.water_report_service,
        "build_group_report_message",
        AsyncMock(return_value=text_message("SHOULD_NOT_RUN")),
    )

    event = build_group_message_event("#水王 今日报告", role="member", message_id=1)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "这条要群管理员或群主来确认喔~", bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_water_today_report_runs_for_group_admin(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    water_plugin.water_report_service.clear_today_report_cooldowns()
    build_report_message = AsyncMock(return_value=text_message("REPORT_OK"))
    monkeypatch.setattr(
        water_plugin.water_report_service,
        "build_group_report_message",
        build_report_message,
    )

    event = build_group_message_event("#水王 今日报告", role="admin", message_id=1)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "凛凛统计中，请稍后喔……", bot=bot)
        ctx.should_call_send(event, text_message("REPORT_OK"), bot=bot)
        ctx.should_finished()

    build_report_message.assert_awaited_once_with(
        window="today_live",
        group_id="20001",
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_water_today_report_alias_runs(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    water_plugin.water_report_service.clear_today_report_cooldowns()
    build_report_message = AsyncMock(return_value=text_message("REPORT_ALIAS_OK"))
    monkeypatch.setattr(
        water_plugin.water_report_service,
        "build_group_report_message",
        build_report_message,
    )

    event = build_group_message_event("#水王日报", role="owner", message_id=1)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "凛凛统计中，请稍后喔……", bot=bot)
        ctx.should_call_send(event, text_message("REPORT_ALIAS_OK"), bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_water_today_report_group_shared_cooldown(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    water_plugin.water_report_service.clear_today_report_cooldowns()
    build_report_message = AsyncMock(return_value=text_message("REPORT_OK"))
    monkeypatch.setattr(
        water_plugin.water_report_service,
        "build_group_report_message",
        build_report_message,
    )

    first = build_group_message_event("#水王 今日报告", role="admin", message_id=1)
    second = build_group_message_event(
        "#水王 今日报告",
        user_id=10002,
        role="owner",
        message_id=2,
    )

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, first)
        ctx.should_call_send(first, "凛凛统计中，请稍后喔……", bot=bot)
        ctx.should_call_send(first, text_message("REPORT_OK"), bot=bot)
        ctx.should_finished()

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            "本群报告冷却中，请 59s 后再试 qwq",
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_water_today_report_skips_user_query_cooldown(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    water_plugin.clear_water_query_cooldowns()
    water_plugin.water_report_service.clear_today_report_cooldowns()
    build_rank_message = AsyncMock(return_value=text_message("RANK_OK"))
    build_report_message = AsyncMock(return_value=text_message("REPORT_OK"))
    monkeypatch.setattr(
        water_plugin.water_rank_query_service,
        "build_rank_message",
        build_rank_message,
    )
    monkeypatch.setattr(
        water_plugin.water_report_service,
        "build_group_report_message",
        build_report_message,
    )

    first = build_group_message_event(
        "#水王 用户榜 本群 日榜",
        role="admin",
        message_id=1,
    )
    second = build_group_message_event("#水王 今日报告", role="admin", message_id=2)

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, first)
        ctx.should_call_send(first, "凛凛统计中，请稍后喔……", bot=bot)
        ctx.should_call_send(first, text_message("RANK_OK"), bot=bot)
        ctx.should_finished()

    async with app.test_matcher(water_plugin.water_query) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, second)
        ctx.should_call_send(second, "凛凛统计中，请稍后喔……", bot=bot)
        ctx.should_call_send(second, text_message("REPORT_OK"), bot=bot)
        ctx.should_finished()
