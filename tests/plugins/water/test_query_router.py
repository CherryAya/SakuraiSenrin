from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Message
import pytest

from src.plugins.water.database.repo import WaterActivitySeasonRecord
from src.plugins.water.services.query_router import WaterQueryRouter
from src.plugins.water.services.rank_types import WaterRankQuerySpec
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
    assert "revoke / recall" in intro
    assert "合法之組" in menu
    assert "你刚刚选的是" in summary


def test_parse_rank_errors() -> None:
    router = WaterQueryRouter()

    missing = router.parse("用户榜 月榜")
    assert missing.rank_spec is None
    assert missing.errors[0] == "missing_dimensions"

    invalid = router.parse("群聊榜 本群 月榜")
    assert invalid.rank_spec == WaterRankQuerySpec(
        subject="group",
        scope="group",
        period="month",
    )
    assert invalid.errors == ("invalid_combo",)

    legacy = router.parse("月榜")
    assert legacy.errors == ("legacy_rank",)


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
        AsyncMock(return_value=Message("RANK_OK")),
    )
    rank_message = await router.execute(
        spec=router.parse("用户榜 本群 日榜"),
        user_id="10001",
        group_id="20001",
        locale="zh-CN",
    )
    assert str(rank_message) == "RANK_OK"


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
