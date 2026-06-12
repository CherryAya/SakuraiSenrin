from typing import Any
from unittest.mock import AsyncMock

from pil_utils import BuildImage
import pytest

from src.plugins.water.database.repo import NaturalRankItem
from src.plugins.water.img import WaterPeriodRankCardData, WaterRankCardItem
from src.plugins.water.services.rank import WaterRankService
from src.plugins.water.services.rank_query import water_rank_query_service


@pytest.mark.asyncio
async def test_build_natural_period_rank_data_uses_settled_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import rank as rank_service_module

    service = WaterRankService()
    captured: dict[str, tuple[int, int, int, int, int]] = {}

    async def _fake_leaderboard(
        *,
        subject: str,
        scope: str,
        group_id: str,
        start_date: int,
        end_date: int,
        previous_start_date: int,
        previous_end_date: int,
        limit: int = 10,
    ) -> list[NaturalRankItem]:
        _ = (subject, scope, group_id)
        captured["range"] = (
            start_date,
            end_date,
            previous_start_date,
            previous_end_date,
            limit,
        )
        return [
            NaturalRankItem(
                entity_id="10001",
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
        "get_natural_period_leaderboard",
        _fake_leaderboard,
    )
    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_natural_period_overview",
        AsyncMock(
            return_value=type(
                "Overview",
                (),
                {
                    "total_msg_count": 240,
                    "active_entity_count": 36,
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
    monkeypatch.setattr(
        service,
        "_resolve_secondary_label",
        AsyncMock(return_value="用户 10001"),
    )

    data = await service.build_natural_period_rank_data(
        subject="user",
        scope="group",
        period="week",
        group_id="20001",
        locale="zh-CN",
    )

    assert data is not None
    assert data.title == "用户榜 · 本群周榜"
    assert data.badge == "2026 W21"
    assert captured["range"] == (20260518, 20260523, 20260512, 20260517, 10)
    assert data.top_items[0].display_name == "Alice"
    assert data.champion_gap == 88


@pytest.mark.asyncio
async def test_build_natural_total_rank_data_uses_first_record_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import rank as rank_service_module

    service = WaterRankService()
    captured: dict[str, tuple[int, int, int, int]] = {}

    async def _fake_leaderboard(**kwargs: Any) -> list[NaturalRankItem]:
        captured["window"] = (
            kwargs["start_date"],
            kwargs["end_date"],
            kwargs["previous_start_date"],
            kwargs["previous_end_date"],
        )
        return [
            NaturalRankItem(
                entity_id="mtx_abcd1234",
                msg_count=120,
                active_days=40,
                active_hours=220,
                hourly_counts=[1] * 24,
                current_rank=1,
                trend=None,
                group_count=3,
            )
        ]

    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_settlement_state",
        AsyncMock(return_value={"last_success_record_date": 20260523}),
    )
    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_first_summary_record_date",
        AsyncMock(return_value=20260401),
    )
    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_natural_period_leaderboard",
        _fake_leaderboard,
    )
    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_natural_period_overview",
        AsyncMock(
            return_value=type(
                "Overview",
                (),
                {
                    "total_msg_count": 500,
                    "active_entity_count": 8,
                    "hourly_counts": [2] * 24,
                    "peak_hour": 0,
                    "previous_total_msg_count": 300,
                },
            )()
        ),
    )
    monkeypatch.setattr(
        service,
        "_resolve_display_name",
        AsyncMock(return_value="矩阵一号"),
    )
    monkeypatch.setattr(
        service,
        "_resolve_secondary_label",
        AsyncMock(return_value="矩阵 mtx_abcd1234 · 3 群"),
    )
    monkeypatch.setattr(
        service,
        "_resolve_avatar",
        AsyncMock(side_effect=RuntimeError("no avatar")),
    )

    data = await service.build_natural_period_rank_data(
        subject="matrix",
        scope="global",
        period="total",
        group_id="20001",
        locale="zh-CN",
    )

    assert data is not None
    assert captured["window"] == (20260401, 20260523, 20260207, 20260331)
    assert data.title == "矩阵榜 · 全局总榜"
    assert data.board_title == "TOP 10 矩阵榜"


@pytest.mark.asyncio
async def test_build_total_rank_lines_uses_period_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WaterRankService()

    monkeypatch.setattr(
        service,
        "build_natural_period_rank_data",
        AsyncMock(
            return_value=WaterPeriodRankCardData(
                period="season",
                title="用户榜 · 全局总榜",
                badge="53d",
                range_text="2026.04.01 - 2026.05.23",
                compare_text="对比区间 2026.02.08 - 2026.03.31",
                generated_at=1,
                total_msg_count=420,
                active_entity_count=66,
                hourly_counts=[1] * 24,
                peak_hour=0,
                previous_total_msg_count=390,
                top_items=[
                    WaterRankCardItem(
                        entity_id="10001",
                        display_name="Alice",
                        secondary_label="用户 10001",
                        avatar=None,
                        msg_count=120,
                        active_days=15,
                        active_hours=28,
                        hourly_counts=[1] * 24,
                        current_rank=1,
                        trend=3,
                    )
                ],
                champion_gap=30,
                champion_share=120 / 420,
            )
        ),
    )

    lines = await service.build_total_rank_lines("zh-CN")

    assert lines == ["===== 水王总榜 =====", "- #1 Alice: 120 条 / 15 天"]


@pytest.mark.asyncio
async def test_rank_query_service_routes_day_and_non_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import rank_query as query_module

    monkeypatch.setattr(
        query_module,
        "build_water_period_rank_image",
        AsyncMock(return_value=b"fake-image"),
    )
    monkeypatch.setattr(
        query_module.water_repo,
        "get_natural_day_leaderboard",
        AsyncMock(
            return_value=[
                NaturalRankItem(
                    entity_id="10001",
                    msg_count=12,
                    active_days=1,
                    active_hours=4,
                    hourly_counts=[1] * 24,
                    current_rank=1,
                    trend=1,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        query_module.water_repo,
        "get_natural_day_overview",
        AsyncMock(
            return_value=type(
                "Overview",
                (),
                {
                    "total_msg_count": 20,
                    "active_entity_count": 3,
                    "hourly_counts": [1] * 24,
                    "peak_hour": 0,
                    "previous_total_msg_count": 10,
                },
            )()
        ),
    )
    monkeypatch.setattr(
        query_module.water_rank_service,
        "_build_view_items",
        AsyncMock(
            return_value=[
                WaterRankCardItem(
                    entity_id="10001",
                    display_name="Alice",
                    secondary_label="用户 10001",
                    avatar=None,
                    msg_count=12,
                    active_days=1,
                    active_hours=4,
                    hourly_counts=[1] * 24,
                    current_rank=1,
                    trend=1,
                )
            ]
        ),
    )

    day_message = await water_rank_query_service.build_rank_message(
        subject="user",
        scope="group",
        period="day",
        group_id="20001",
        locale="zh-CN",
    )
    assert "CQ:image" in str(day_message)

    monkeypatch.setattr(
        query_module.water_rank_service,
        "build_natural_period_rank_data",
        AsyncMock(return_value=None),
    )
    period_message = await water_rank_query_service.build_rank_message(
        subject="matrix",
        scope="global",
        period="total",
        group_id="20001",
        locale="zh-CN",
    )
    assert str(period_message) == "凛凛翻了翻账本，这个周期还没有可用结算数据喔。"


@pytest.mark.asyncio
async def test_build_water_period_rank_image_smoke() -> None:
    avatar = BuildImage.new("RGBA", (128, 128), "#F6B7D2")
    data = WaterPeriodRankCardData(
        period="month",
        title="用户榜 · 本群月榜",
        badge="2026.05",
        range_text="2026.05.01 - 2026.05.23",
        compare_text="对比区间 2026.04.01 - 2026.04.23",
        generated_at=1_747_960_000,
        total_msg_count=420,
        active_entity_count=66,
        hourly_counts=[(idx % 6) + 1 for idx in range(24)],
        peak_hour=5,
        previous_total_msg_count=390,
        top_items=[
            WaterRankCardItem(
                entity_id="10001",
                display_name="Alice",
                secondary_label="用户 10001",
                avatar=avatar,
                msg_count=120,
                active_days=15,
                active_hours=28,
                hourly_counts=[(idx % 5) + 1 for idx in range(24)],
                current_rank=1,
                trend=3,
            ),
            WaterRankCardItem(
                entity_id="10002",
                display_name="Bob",
                secondary_label="用户 10002",
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

    from src.plugins.water.img import build_water_period_rank_image

    img = await build_water_period_rank_image(data, "zh-CN")

    assert img is not None
    assert img.startswith(b"\x89PNG")
