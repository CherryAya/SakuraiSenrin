import asyncio
from time import perf_counter
from typing import Any
from unittest.mock import AsyncMock

from pil_utils import BuildImage
import pytest

from src.plugins.water.database.repo import (
    NaturalPeriodRankSnapshot,
    NaturalRankItem,
    NaturalRankOverview,
)
from src.plugins.water.img import (
    WaterGroupDailyRankCardItem,
    WaterGroupReportImageData,
    WaterPeriodRankCardData,
    WaterRankCardItem,
)
from src.plugins.water.services.rank import WaterRankService
from src.plugins.water.services.rank_query import water_rank_query_service
from src.plugins.water.services.rank_types import (
    WaterRankPeriod,
    WaterRankScope,
    WaterRankSubject,
)


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

    async def _fake_period_snapshot(**kwargs: Any) -> NaturalPeriodRankSnapshot:
        return await _fake_snapshot(kwargs, _fake_leaderboard)

    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_settlement_state",
        AsyncMock(return_value={"last_success_record_date": 20260523}),
    )
    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_natural_period_snapshot",
        AsyncMock(side_effect=_fake_period_snapshot),
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


def _build_snapshot(
    items: list[NaturalRankItem],
    *,
    total_msg_count: int,
    active_entity_count: int,
    previous_total_msg_count: int,
) -> NaturalPeriodRankSnapshot:
    return NaturalPeriodRankSnapshot(
        leaderboard=items,
        overview=NaturalRankOverview(
            total_msg_count=total_msg_count,
            active_entity_count=active_entity_count,
            hourly_counts=[5] * 24,
            previous_total_msg_count=previous_total_msg_count,
        ),
    )


async def _fake_snapshot(
    kwargs: dict[str, Any],
    builder: Any,
) -> NaturalPeriodRankSnapshot:
    items = await builder(**kwargs)
    return _build_snapshot(
        items,
        total_msg_count=240,
        active_entity_count=36,
        previous_total_msg_count=180,
    )


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

    async def _fake_period_snapshot(**kwargs: Any) -> NaturalPeriodRankSnapshot:
        return await _fake_total_snapshot(kwargs, _fake_leaderboard)

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
        "get_natural_period_snapshot",
        AsyncMock(side_effect=_fake_period_snapshot),
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
async def test_build_natural_period_rank_data_uses_i18n_display_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import rank as rank_service_module

    service = WaterRankService()

    async def _fake_period_snapshot(**kwargs: Any) -> NaturalPeriodRankSnapshot:
        _ = kwargs
        return _build_snapshot(
            [
                NaturalRankItem(
                    entity_id="20001",
                    msg_count=88,
                    active_days=6,
                    active_hours=18,
                    hourly_counts=[2] * 24,
                    current_rank=1,
                    trend=2,
                )
            ],
            total_msg_count=240,
            active_entity_count=36,
            previous_total_msg_count=180,
        )

    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_settlement_state",
        AsyncMock(return_value={"last_success_record_date": 20260523}),
    )
    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_natural_period_snapshot",
        AsyncMock(side_effect=_fake_period_snapshot),
    )
    monkeypatch.setattr(
        service,
        "_resolve_display_name",
        AsyncMock(return_value="群聊 20001"),
    )
    monkeypatch.setattr(service, "_resolve_avatar", AsyncMock(return_value=None))
    monkeypatch.setattr(
        service,
        "_resolve_secondary_label",
        AsyncMock(return_value="群號 20001"),
    )

    data = await service.build_natural_period_rank_data(
        subject="group",
        scope="global",
        period="week",
        group_id="20001",
        locale="lzh",
    )

    assert data is not None
    assert data.title == "群聊榜 · 全局週榜"
    assert data.entity_label == "群聊"
    assert data.board_title == "群聊前十榜"
    assert "{msg_count}" in data.board_summary_label


@pytest.mark.asyncio
async def test_build_day_rank_data_uses_i18n_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import rank_query as query_module

    monkeypatch.setattr(
        query_module.water_repo,
        "get_natural_day_snapshot",
        AsyncMock(
            return_value=NaturalPeriodRankSnapshot(
                leaderboard=[
                    NaturalRankItem(
                        entity_id="20001",
                        msg_count=12,
                        active_days=1,
                        active_hours=4,
                        hourly_counts=[1] * 24,
                        current_rank=1,
                        trend=1,
                    )
                ],
                overview=NaturalRankOverview(
                    total_msg_count=20,
                    active_entity_count=3,
                    hourly_counts=[1] * 24,
                    previous_total_msg_count=10,
                ),
            )
        ),
    )
    monkeypatch.setattr(
        query_module.water_rank_service,
        "_build_view_items",
        AsyncMock(
            return_value=[
                WaterRankCardItem(
                    entity_id="20001",
                    display_name="群聊 20001",
                    secondary_label="群號 20001",
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

    data = await query_module.water_rank_query_service._build_day_rank_data(
        subject="group",
        scope="global",
        group_id="20001",
        locale="lzh",
        limit=10,
    )

    assert data is not None
    assert data.title == "群聊榜 · 全局日榜"
    assert data.scope_label == "全局 · 群聊榜"
    assert data.subject_label == "群聊榜"
    assert data.summary_label.startswith("今日領跑:")


async def _fake_total_snapshot(
    kwargs: dict[str, Any],
    builder: Any,
) -> NaturalPeriodRankSnapshot:
    items = await builder(**kwargs)
    return NaturalPeriodRankSnapshot(
        leaderboard=items,
        overview=NaturalRankOverview(
            total_msg_count=500,
            active_entity_count=8,
            hourly_counts=[2] * 24,
            previous_total_msg_count=300,
        ),
    )


async def _fake_snapshot_from_builders(
    kwargs: dict[str, Any],
    leaderboard_builder: Any,
    overview_builder: Any,
) -> NaturalPeriodRankSnapshot:
    leaderboard, overview = await asyncio.gather(
        leaderboard_builder(**kwargs),
        overview_builder(**kwargs),
    )
    return NaturalPeriodRankSnapshot(
        leaderboard=leaderboard,
        overview=overview,
    )


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
        "build_water_day_rank_image",
        AsyncMock(return_value=b"fake-image"),
    )
    period_image_mock = AsyncMock(return_value=b"period-image")
    monkeypatch.setattr(
        query_module,
        "build_water_period_rank_image",
        period_image_mock,
    )
    monkeypatch.setattr(
        query_module.water_repo,
        "get_natural_day_snapshot",
        AsyncMock(
            return_value=NaturalPeriodRankSnapshot(
                leaderboard=[
                    NaturalRankItem(
                        entity_id="10001",
                        msg_count=12,
                        active_days=1,
                        active_hours=4,
                        hourly_counts=[1] * 24,
                        current_rank=1,
                        trend=1,
                    )
                ],
                overview=NaturalRankOverview(
                    total_msg_count=20,
                    active_entity_count=3,
                    hourly_counts=[1] * 24,
                    previous_total_msg_count=10,
                ),
            )
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
    period_image_mock.assert_not_awaited()

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
async def test_build_water_group_report_image_smoke() -> None:
    avatar = BuildImage.new("RGBA", (128, 128), "#F6B7D2")
    data = WaterGroupReportImageData(
        title="测试群 · 今日群报告",
        badge="测试群",
        range_text="统计日期: 2026.06.14 · 今日实时快照",
        compare_text="对比日期: 2026.06.13 · 消息 +30 · 活跃成员 +1",
        generated_at=1_747_960_000,
        total_msg_count=420,
        active_user_count=66,
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
        group_rank_title="群聊当日排名",
        group_rank_summary="本群当前排名 #3 / 12 · 较昨日 +1",
        group_rank_items=[
            WaterGroupDailyRankCardItem(
                group_id="20001",
                display_name="测试群",
                msg_count=120,
                current_rank=3,
                trend=1,
            ),
            WaterGroupDailyRankCardItem(
                group_id="20002",
                display_name="隔壁群",
                msg_count=98,
                current_rank=4,
                trend=-1,
            ),
        ],
        group_rank_has_hidden_before=True,
        group_rank_has_hidden_after=True,
    )

    from src.plugins.water.img import build_water_group_report_image

    img = await build_water_group_report_image(data, "zh-CN")

    assert img is not None
    assert img.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_build_water_group_report_image_without_group_rank_block() -> None:
    avatar = BuildImage.new("RGBA", (128, 128), "#F6B7D2")
    data = WaterGroupReportImageData(
        title="测试群 · 今日群报告",
        badge="测试群",
        range_text="统计日期: 2026.06.14 · 今日实时快照",
        compare_text="对比日期: 2026.06.13 · 消息 +30 · 活跃成员 +1",
        generated_at=1_747_960_000,
        total_msg_count=420,
        active_user_count=66,
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
            )
        ],
        group_rank_title="群聊当日排名",
        group_rank_summary="",
        group_rank_items=[],
    )

    from src.plugins.water.img import build_water_group_report_image

    img = await build_water_group_report_image(data, "zh-CN")

    assert img is not None
    assert img.startswith(b"\x89PNG")


def _build_fake_natural_items(
    subject: str,
    *,
    count: int = 3,
) -> list[NaturalRankItem]:
    items: list[NaturalRankItem] = []
    for idx in range(count):
        entity_id = (
            f"{10001 + idx}"
            if subject == "user"
            else f"{20001 + idx}"
            if subject == "group"
            else f"mtx_{idx + 1:02d}"
        )
        items.append(
            NaturalRankItem(
                entity_id=entity_id,
                msg_count=180 - idx * 25,
                active_days=max(1, 12 - idx),
                active_hours=max(1, 36 - idx * 3),
                hourly_counts=[((hour + idx) % 6) + 1 for hour in range(24)],
                current_rank=idx + 1,
                trend=1 - idx,
                group_count=idx + 2 if subject == "matrix" else 0,
            )
        )
    return items


def _build_fake_overview(items: list[NaturalRankItem]) -> NaturalRankOverview:
    return NaturalRankOverview(
        total_msg_count=sum(item.msg_count for item in items),
        active_entity_count=len(items),
        hourly_counts=[
            sum(item.hourly_counts[hour] for item in items) for hour in range(24)
        ],
        previous_total_msg_count=max(0, sum(item.msg_count for item in items) - 60),
    )


@pytest.mark.asyncio
async def test_build_rank_message_renders_all_legal_combinations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import rank as rank_service_module
    from src.plugins.water.services import rank_query as query_module

    avatar = BuildImage.new("RGBA", (96, 96), "#F6B7D2")
    legal_scopes: dict[WaterRankSubject, tuple[WaterRankScope, ...]] = {
        "user": ("group", "matrix", "global"),
        "group": ("matrix", "global"),
        "matrix": ("global",),
    }
    periods: tuple[WaterRankPeriod, ...] = (
        "day",
        "week",
        "month",
        "season",
        "year",
        "total",
    )

    async def _fake_day_leaderboard(
        *,
        subject: str,
        scope: str,
        group_id: str,
        limit: int = 10,
    ) -> list[NaturalRankItem]:
        _ = (scope, group_id, limit)
        return _build_fake_natural_items(subject)

    async def _fake_day_overview(
        *,
        subject: str,
        scope: str,
        group_id: str,
        limit: int = 10,
    ) -> NaturalRankOverview:
        _ = (scope, group_id, limit)
        return _build_fake_overview(_build_fake_natural_items(subject))

    async def _fake_period_leaderboard(
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
        _ = (
            scope,
            group_id,
            start_date,
            end_date,
            previous_start_date,
            previous_end_date,
            limit,
        )
        return _build_fake_natural_items(subject)

    async def _fake_period_overview(
        *,
        subject: str,
        scope: str,
        group_id: str,
        start_date: int,
        end_date: int,
        previous_start_date: int,
        previous_end_date: int,
        limit: int = 10,
    ) -> NaturalRankOverview:
        _ = (
            scope,
            group_id,
            start_date,
            end_date,
            previous_start_date,
            previous_end_date,
            limit,
        )
        return _build_fake_overview(_build_fake_natural_items(subject))

    async def _fake_display_name(subject: str, entity_id: str, locale: str) -> str:
        _ = locale
        if subject == "user":
            return f"用户 {entity_id}"
        if subject == "group":
            return f"群聊 {entity_id}"
        return f"矩阵 {entity_id}"

    async def _fake_secondary_label(
        subject: str,
        entity_id: str,
        group_count: int,
        locale: str,
    ) -> str:
        _ = locale
        if subject == "user":
            return f"用户 {entity_id}"
        if subject == "group":
            return f"群号 {entity_id}"
        return f"矩阵 {entity_id} · {group_count} 群"

    async def _fake_avatar(subject: str, entity_id: str) -> BuildImage:
        _ = (subject, entity_id)
        return avatar

    async def _fake_day_snapshot(**kwargs: Any) -> NaturalPeriodRankSnapshot:
        return await _fake_snapshot_from_builders(
            kwargs,
            _fake_day_leaderboard,
            _fake_day_overview,
        )

    async def _fake_period_snapshot(**kwargs: Any) -> NaturalPeriodRankSnapshot:
        return await _fake_snapshot_from_builders(
            kwargs,
            _fake_period_leaderboard,
            _fake_period_overview,
        )

    monkeypatch.setattr(
        query_module.water_repo,
        "get_natural_day_snapshot",
        AsyncMock(side_effect=_fake_day_snapshot),
    )
    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_settlement_state",
        AsyncMock(return_value={"last_success_record_date": 20260523}),
    )
    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_first_summary_record_date",
        AsyncMock(return_value=20260101),
    )
    monkeypatch.setattr(
        rank_service_module.water_repo,
        "get_natural_period_snapshot",
        AsyncMock(side_effect=_fake_period_snapshot),
    )
    monkeypatch.setattr(
        query_module.water_rank_service,
        "_resolve_display_name",
        _fake_display_name,
    )
    monkeypatch.setattr(
        query_module.water_rank_service,
        "_resolve_secondary_label",
        _fake_secondary_label,
    )
    monkeypatch.setattr(
        query_module.water_rank_service,
        "_resolve_avatar",
        _fake_avatar,
    )

    timings: list[tuple[str, str, str, float]] = []
    for subject, scopes in legal_scopes.items():
        for scope in scopes:
            for period in periods:
                started = perf_counter()
                message = await water_rank_query_service.build_rank_message(
                    subject=subject,
                    scope=scope,
                    period=period,
                    group_id="20001",
                    locale="zh-CN",
                )
                timings.append(
                    (subject, scope, period, (perf_counter() - started) * 1000)
                )
                assert "CQ:image" in str(message), (subject, scope, period)

    assert max(elapsed_ms for *_rest, elapsed_ms in timings) >= 0
    assert len(timings) == 36
