from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Message
import pytest

from src.plugins.water.database.repo import WaterActivitySeasonRecord
from src.plugins.water.services.query_router import WaterQueryRouter
from src.plugins.water.services.season import SeasonLookupAmbiguous


@pytest.mark.asyncio
async def test_parse_default_and_season_variants() -> None:
    router = WaterQueryRouter()

    default_spec = router.parse("")
    assert default_spec.view == "rank"
    assert default_spec.scope_type == "absolute"
    assert default_spec.scope_value == "day"
    assert default_spec.mode == "simple"

    season_spec = router.parse("赛季")
    assert season_spec.scope_type == "activity"
    assert season_spec.scope_value == "当前"
    assert season_spec.view == "overview"

    detail_spec = router.parse("赛季 春日特别季 群聊 排名")
    assert detail_spec.subject == "group"
    assert detail_spec.view == "rank"


@pytest.mark.asyncio
async def test_execute_default_queries_group_day_rank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = WaterQueryRouter()

    from src.plugins.water.services import query_router as router_module

    monkeypatch.setattr(
        router_module.absolute_rank_service,
        "build_group_day_rank",
        AsyncMock(return_value=Message("DAY_RANK")),
    )

    message = await router.execute(
        spec=router.parse(""),
        user_id="10001",
        group_id="20001",
        locale="zh-CN",
    )

    assert str(message) == "DAY_RANK"


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
