from unittest.mock import AsyncMock

import pytest

from src.lib.messages import text_message
from src.plugins.water.database.repo import WaterActivitySeasonRecord
from src.plugins.water.services.query_router import (
    WaterQueryRouter,
    WaterQuerySpec,
    WaterRankInputDraft,
)
from src.plugins.water.services.rank_types import RANK_SHORTCUTS, WaterRankQuerySpec
from src.plugins.water.services.season import SeasonLookupAmbiguous


def test_parse_rank_menu_and_any_order_rank_spec() -> None:
    router = WaterQueryRouter()

    menu_spec = router.parse("")
    assert menu_spec.view == "menu"
    assert menu_spec.scope_type == "rank"

    rank_spec = router.parse("月榜 本群 用户榜")
    assert rank_spec.rank_spec == WaterRankQuerySpec(
        subject="user",
        scope="group",
        period="month",
    )

    shortcut_spec, shortcut_errors = router.parse_shortcut_command("#今日水王")
    assert shortcut_spec == WaterRankQuerySpec(
        subject="user",
        scope="group",
        period="day",
    )
    assert shortcut_errors == ()

    matrix_user_shortcut, matrix_user_errors = router.parse_shortcut_command(
        "#今日矩阵水王"
    )
    assert matrix_user_shortcut == WaterRankQuerySpec(
        subject="user",
        scope="matrix",
        period="day",
    )
    assert matrix_user_errors == ()

    global_user_shortcut, global_user_errors = router.parse_shortcut_command(
        "#本月全局水王"
    )
    assert global_user_shortcut == WaterRankQuerySpec(
        subject="user",
        scope="global",
        period="month",
    )
    assert global_user_errors == ()

    matrix_group_shortcut, matrix_group_errors = router.parse_shortcut_command(
        "#本周矩阵群聊榜"
    )
    assert matrix_group_shortcut == WaterRankQuerySpec(
        subject="group",
        scope="matrix",
        period="week",
    )
    assert matrix_group_errors == ()


def test_rank_menu_does_not_send_working() -> None:
    router = WaterQueryRouter()

    assert router.should_send_working(router.parse("")) is False
    assert router.should_send_working(router.parse("月榜")) is False
    assert router.should_send_working(router.parse("用户榜 本群 日榜")) is True


def test_rank_guided_prompts_follow_locale_catalog() -> None:
    router = WaterQueryRouter()

    intro = router.build_guided_intro("lzh")
    menu = router.build_rank_menu("lzh")
    summary = router.build_guided_summary(
        "x-meme",
        WaterRankQuerySpec(subject="user", scope="group", period="month"),
    )

    assert "請擇榜單主體" in intro
    assert "請擇範圍" in intro
    assert "請擇時間" in intro
    assert "revoke / recall" in intro
    assert "合法之組" in menu
    assert "捷徑入口" in menu
    assert "#今日水王" in menu
    assert "#今日矩阵水王" in menu
    assert "#今日全局水王" in menu
    assert "#今日矩阵群榜 / #今日矩阵群聊榜" in menu
    assert "季榜" in menu
    assert "#水王 矩阵榜 全局 总榜" not in menu
    assert "你刚刚选的是" in summary


def test_rank_period_visibility_depends_on_role() -> None:
    router = WaterQueryRouter()

    normal_menu = router.build_rank_menu("zh-CN")
    superuser_menu = router.build_rank_menu("zh-CN", is_superuser=True)
    normal_prompt = router.build_period_prompt_for_role("zh-CN", is_superuser=False)
    superuser_prompt = router.build_period_prompt_for_role("zh-CN", is_superuser=True)

    assert "#水王 矩阵榜 全局 季榜" in normal_menu
    assert "#水王 矩阵榜 全局 总榜" in superuser_menu
    assert "日榜/周榜/月榜/季榜" in normal_menu
    assert "日榜/周榜/月榜/季榜/年榜/总榜" in superuser_menu
    assert "请选择时间：日榜 / 周榜 / 月榜 / 季榜" in normal_prompt
    assert "请选择时间：日榜 / 周榜 / 月榜 / 季榜 / 年榜 / 总榜" in superuser_prompt


def test_parse_rank_errors() -> None:
    router = WaterQueryRouter()

    missing = router.parse("用户榜 月榜")
    assert missing.rank_spec is None
    assert missing.errors[0] == "missing_dimensions"
    assert missing.errors[1:] == ("scope",)

    invalid = router.parse("群聊榜 本群 月榜")
    assert invalid.rank_spec == WaterRankQuerySpec(
        subject="group",
        scope="group",
        period="month",
    )
    assert invalid.errors == ("invalid_combo",)

    period_only = router.parse("月榜")
    assert period_only.rank_spec is None
    assert period_only.errors == ("missing_dimensions", "subject", "scope")

    shortcut_with_args = router.parse_shortcut_command("#今日水王 多余参数")
    assert shortcut_with_args == (
        WaterRankQuerySpec(subject="user", scope="group", period="day"),
        ("shortcut_with_args", "今日水王"),
    )


def test_parse_rank_input_supports_partial_fill_and_error_reporting() -> None:
    router = WaterQueryRouter()

    assert router.parse_rank_input("用户榜 月榜") == WaterRankInputDraft(
        subject="user",
        period="month",
    )
    assert router.parse_rank_input("本群") == WaterRankInputDraft(scope="group")
    assert router.parse_rank_input("用户榜 群聊榜") == WaterRankInputDraft(
        subject="user",
        errors=("duplicate_subject",),
    )
    assert router.parse_rank_input("火星榜") == WaterRankInputDraft(
        errors=("unknown_tokens", "火星榜"),
    )


def test_rank_shortcuts_cover_all_visible_legal_combinations() -> None:
    covered = {(item.subject, item.scope, item.period) for item in RANK_SHORTCUTS}

    assert covered == {
        ("user", "group", "day"),
        ("user", "group", "week"),
        ("user", "group", "month"),
        ("user", "group", "season"),
        ("user", "matrix", "day"),
        ("user", "matrix", "week"),
        ("user", "matrix", "month"),
        ("user", "matrix", "season"),
        ("user", "global", "day"),
        ("user", "global", "week"),
        ("user", "global", "month"),
        ("user", "global", "season"),
        ("group", "matrix", "day"),
        ("group", "matrix", "week"),
        ("group", "matrix", "month"),
        ("group", "matrix", "season"),
        ("group", "global", "day"),
        ("group", "global", "week"),
        ("group", "global", "month"),
        ("group", "global", "season"),
        ("matrix", "global", "day"),
        ("matrix", "global", "week"),
        ("matrix", "global", "month"),
        ("matrix", "global", "season"),
    }


@pytest.mark.asyncio
async def test_execute_rank_menu_and_invalid_combo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = WaterQueryRouter()

    menu_message = await router.execute(
        spec=router.parse(""),
        user_id="10001",
        group_id="20001",
        locale="zh-CN",
    )
    assert "主体 + 范围 + 时间" in str(menu_message)

    invalid_message = await router.execute(
        spec=router.parse("群聊榜 本群 月榜"),
        user_id="10001",
        group_id="20001",
        locale="zh-CN",
    )
    assert "这个主体和范围组合不成立" in str(invalid_message)
    assert "#水王 群聊榜 本矩阵 月榜" in str(invalid_message)

    from src.plugins.water.services import query_router as router_module

    monkeypatch.setattr(
        router_module.water_rank_query_service,
        "build_rank_message",
        AsyncMock(return_value=text_message("RANK_OK")),
    )
    rank_message = await router.execute(
        spec=router.parse("用户榜 本群 日榜"),
        user_id="10001",
        group_id="20001",
        locale="zh-CN",
    )
    assert str(rank_message) == "RANK_OK"

    shortcut_error_message = await router.execute(
        spec=WaterQuerySpec(
            subject="personal",
            scope_type="rank",
            scope_value="day",
            view="rank",
            mode="simple",
            rank_spec=WaterRankQuerySpec(subject="user", scope="group", period="day"),
            errors=("shortcut_with_args", "今日水王"),
        ),
        user_id="10001",
        group_id="20001",
        locale="zh-CN",
    )
    assert "快捷入口不需要额外参数" in str(shortcut_error_message)
    assert "#今日水王" in str(shortcut_error_message)


@pytest.mark.asyncio
async def test_execute_rank_restricted_period_requires_superuser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = WaterQueryRouter()

    from src.plugins.water.services import query_router as router_module

    build_rank_mock = AsyncMock(return_value=text_message("SHOULD_NOT_RUN"))
    monkeypatch.setattr(
        router_module.water_rank_query_service,
        "build_rank_message",
        build_rank_mock,
    )

    normal_message = await router.execute(
        spec=router.parse("用户榜 本群 年榜"),
        user_id="10001",
        group_id="20001",
        locale="zh-CN",
        is_superuser=False,
    )
    assert "时间不合法" in str(normal_message)
    assert "超管可用" not in str(normal_message)
    assert "#水王 矩阵榜 全局 季榜" in str(normal_message)

    superuser_message = await router.execute(
        spec=router.parse("用户榜 本群 年榜"),
        user_id="1",
        group_id="20001",
        locale="zh-CN",
        is_superuser=True,
    )
    assert str(superuser_message) == "SHOULD_NOT_RUN"
    build_rank_mock.assert_awaited_once_with(
        subject="user",
        scope="group",
        period="year",
        group_id="20001",
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_build_profile_message_skips_history_ranks_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = WaterQueryRouter()

    from src.plugins.water.services import query_router as router_module

    build_profile_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        router_module.profile_service,
        "build_profile_data",
        build_profile_mock,
    )

    _ = await router.build_profile_message(
        user_id="10001",
        group_id="20001",
        locale="zh-CN",
        mode="full",
    )

    build_profile_mock.assert_awaited_once()
    await_args = build_profile_mock.await_args
    assert await_args is not None
    assert await_args.kwargs["include_group_history_ranks"] is False


@pytest.mark.asyncio
async def test_execute_activity_default_queries_all_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = WaterQueryRouter()
    spec = router.parse("赛季")

    season_a = WaterActivitySeasonRecord(
        season_id="spring_a",
        name="春日 A",
        normalized_name="春日 a",
        description="",
        start_date=20260301,
        end_date=20260331,
        status="published",
        published_at=1,
        created_by="1",
        created_at=1,
        updated_at=1,
    )
    season_b = WaterActivitySeasonRecord(
        season_id="spring_b",
        name="春日 B",
        normalized_name="春日 b",
        description="",
        start_date=20260315,
        end_date=20260415,
        status="published",
        published_at=1,
        created_by="1",
        created_at=1,
        updated_at=1,
    )

    from src.plugins.water.services import query_router as router_module

    monkeypatch.setattr(
        router_module.season_service,
        "resolve_one_or_many",
        AsyncMock(return_value=[season_a, season_b]),
    )
    monkeypatch.setattr(
        router_module.season_rank_service,
        "build_message",
        AsyncMock(side_effect=["A_OVERVIEW", "B_OVERVIEW"]),
    )

    message = await router.execute(
        spec=spec,
        user_id="10001",
        group_id="20001",
        locale="zh-CN",
    )
    text = str(message)

    assert "spring_a" in text
    assert "spring_b" in text
    assert "A_OVERVIEW" in text
    assert "B_OVERVIEW" in text


@pytest.mark.asyncio
async def test_execute_activity_ambiguity_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = WaterQueryRouter()
    spec = router.parse("赛季 新年季")

    from src.plugins.water.services import query_router as router_module

    monkeypatch.setattr(
        router_module.season_service,
        "resolve_one_or_many",
        AsyncMock(
            return_value=SeasonLookupAmbiguous(
                keyword="新年季",
                candidates=[
                    WaterActivitySeasonRecord(
                        season_id="ny_a",
                        name="新年季 A",
                        normalized_name="新年季 a",
                        description="",
                        start_date=20260101,
                        end_date=20260115,
                        status="published",
                        published_at=1,
                        created_by="1",
                        created_at=1,
                        updated_at=1,
                    ),
                    WaterActivitySeasonRecord(
                        season_id="ny_b",
                        name="新年季 B",
                        normalized_name="新年季 b",
                        description="",
                        start_date=20260116,
                        end_date=20260131,
                        status="published",
                        published_at=1,
                        created_by="1",
                        created_at=1,
                        updated_at=1,
                    ),
                ],
            )
        ),
    )

    message = await router.execute(
        spec=spec,
        user_id="10001",
        group_id="20001",
        locale="zh-CN",
    )

    assert "歧义" in str(message)
    assert "ny_a" in str(message)
    assert "ny_b" in str(message)
