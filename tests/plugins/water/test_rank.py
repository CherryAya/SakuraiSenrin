from typing import Any, cast
from unittest.mock import AsyncMock

from pil_utils import BuildImage
import pytest

from src.plugins.water.database.repo import GlobalPeriodRankItem
from src.plugins.water.handlers.rank import handle_period_rank
from src.plugins.water.img import (
    WaterPeriodRankCardData,
    WaterPeriodRankUserItem,
    build_water_period_rank_image,
)
from src.plugins.water.services.rank import (
    TOTAL_RANK_END_DATE,
    TOTAL_RANK_START_DATE,
    WaterRankService,
)
from tests.plugins.water.helpers import DummyMatcher, MatcherFinished


@pytest.mark.asyncio
async def test_handle_period_rank_returns_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import rank as rank_module

    matcher = DummyMatcher()
    monkeypatch.setattr(
        rank_module.water_rank_service,
        "build_period_rank_image",
        AsyncMock(return_value=b"fake-image"),
    )

    with pytest.raises(MatcherFinished):
        await handle_period_rank(cast(Any, matcher), "week", "zh-CN")

    assert matcher.sent == ["凛凛统计周榜中，请稍后喔……"]
    assert matcher.finished is not None
    assert "CQ:image" in str(matcher.finished)


@pytest.mark.asyncio
async def test_handle_period_rank_returns_fallback_when_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import rank as rank_module

    matcher = DummyMatcher()
    monkeypatch.setattr(
        rank_module.water_rank_service,
        "build_period_rank_image",
        AsyncMock(return_value=None),
    )

    with pytest.raises(MatcherFinished):
        await handle_period_rank(cast(Any, matcher), "year", "zh-CN")

    assert matcher.sent == ["凛凛统计年榜中，请稍后喔……"]
    assert matcher.finished == "凛凛翻了翻账本，这个周期还没有可用结算数据喔。"


@pytest.mark.asyncio
async def test_build_period_rank_data_uses_settled_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import rank as rank_service_module

    service = WaterRankService()
    captured: dict[str, tuple[int, int, int, int, int]] = {}

    async def _fake_leaderboard(
        start_date: int,
        end_date: int,
        previous_start_date: int,
        previous_end_date: int,
        limit: int = 10,
    ) -> list[GlobalPeriodRankItem]:
        captured["range"] = (
            start_date,
            end_date,
            previous_start_date,
            previous_end_date,
            limit,
        )
        return [
            GlobalPeriodRankItem(
                user_id="10001",
                msg_count=88,
                active_days=6,
                active_hours=18,
                hourly_counts=[2] * 24,
                current_rank=1,
                trend=2,
            )
        ]

    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_settlement_state",
        AsyncMock(return_value={"last_success_record_date": 20260523}),
    )
    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_global_period_leaderboard",
        _fake_leaderboard,
    )
    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_global_period_overview",
        AsyncMock(
            return_value=type(
                "Overview",
                (),
                {
                    "total_msg_count": 240,
                    "active_user_count": 36,
                    "hourly_counts": [5] * 24,
                    "peak_hour": 0,
                    "previous_total_msg_count": 180,
                },
            )()
        ),
    )
    monkeypatch.setattr(
        rank_service_module.user_repo,
        "get_name_by_uid",
        AsyncMock(return_value="Alice"),
    )
    monkeypatch.setattr(service, "_resolve_avatar", AsyncMock(return_value=None))

    data = await service.build_period_rank_data("week", "zh-CN")

    assert data is not None
    assert data.title == "水王周榜"
    assert data.badge == "2026 W21"
    assert captured["range"] == (20260518, 20260523, 20260512, 20260517, 10)
    assert data.top_users[0].username == "Alice"
    assert data.champion_gap == 88


@pytest.mark.asyncio
async def test_build_water_period_rank_image_smoke() -> None:
    avatar = BuildImage.new("RGBA", (128, 128), "#F6B7D2")
    data = WaterPeriodRankCardData(
        period="month",
        title="水王月榜",
        badge="2026.05",
        range_text="2026.05.01 - 2026.05.23",
        compare_text="对比区间 2026.04.01 - 2026.04.23",
        generated_at=1_747_960_000,
        total_msg_count=420,
        active_user_count=66,
        hourly_counts=[(idx % 6) + 1 for idx in range(24)],
        peak_hour=5,
        previous_total_msg_count=390,
        top_users=[
            WaterPeriodRankUserItem(
                user_id="10001",
                username="Alice",
                avatar=avatar,
                msg_count=120,
                active_days=15,
                active_hours=28,
                hourly_counts=[(idx % 5) + 1 for idx in range(24)],
                current_rank=1,
                trend=3,
            ),
            WaterPeriodRankUserItem(
                user_id="10002",
                username="Bob",
                avatar=avatar,
                msg_count=90,
                active_days=14,
                active_hours=24,
                hourly_counts=[(idx % 4) + 1 for idx in range(24)],
                current_rank=2,
                trend=-1,
            ),
        ],
        champion_gap=30,
        champion_share=120 / 420,
    )

    img = await build_water_period_rank_image(data, "zh-CN")

    assert img is not None
    assert img.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_build_total_rank_lines_uses_valid_date_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import rank as rank_service_module

    service = WaterRankService()
    captured: dict[str, tuple[int, int]] = {}

    async def _fake_rankings(
        start_date: int,
        end_date: int,
    ) -> list[Any]:
        captured["window"] = (start_date, end_date)
        return []

    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_user_season_rankings",
        _fake_rankings,
    )

    lines = await service.build_total_rank_lines("zh-CN")

    assert captured["window"] == (TOTAL_RANK_START_DATE, TOTAL_RANK_END_DATE)
    assert lines == ["===== 水王总榜 =====", "暂无全历史数据。"]
