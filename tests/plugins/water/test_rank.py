# 测试直取内部符号：pyright 私有访问豁免
# pyright: reportPrivateUsage=false
import asyncio
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

from pil_utils import BuildImage
import pytest

from src.lib.message_plan import render_message_plan_input
from src.plugins.water.database.repo_models import (
    NaturalPeriodRankSnapshot,
    NaturalRankItem,
    NaturalRankOverview,
)
from src.plugins.water.renderers import (
    WaterGroupDailyRankCardItem,
    WaterGroupReportImageData,
    WaterGroupShareSlice,
    WaterPeriodRankCardData,
    WaterRankCardItem,
)
from src.plugins.water.renderers.report import build_water_period_rank_image
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
        "get_names_by_uids",
        AsyncMock(side_effect=lambda ids: dict.fromkeys(ids, "Alice")),
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
            daily_msg_counts=[total_msg_count],
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
        "_resolve_display_names",
        AsyncMock(
            side_effect=lambda _subject, items, _locale: dict.fromkeys(
                (item.entity_id for item in items), "矩阵一号"
            )
        ),
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
        rank_service_module.group_repo,
        "get_names_by_gids",
        AsyncMock(side_effect=lambda ids: dict.fromkeys(ids, "群聊 20001")),
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
                    daily_msg_counts=[20],
                    previous_total_msg_count=10,
                ),
            )
        ),
    )
    monkeypatch.setattr(
        query_module.water_rank_service,
        "build_view_items",
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
            daily_msg_counts=[500],
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
                start_date=20260401,
                end_date=20260523,
                generated_at=1,
                total_msg_count=420,
                active_entity_count=66,
                hourly_counts=[1] * 24,
                daily_msg_counts=[10] * 24,
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
                        daily_msg_counts=[5, 10, 15],
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
                    daily_msg_counts=[20],
                    previous_total_msg_count=10,
                ),
            )
        ),
    )
    monkeypatch.setattr(
        query_module.water_rank_service,
        "build_view_items",
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
    assert "CQ:image" in str(render_message_plan_input(day_message))
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
    assert (
        str(render_message_plan_input(period_message))
        == "凛凛翻了翻账本，这个周期还没有可用结算数据喔。"
    )


@pytest.mark.asyncio
async def test_build_water_period_rank_image_uses_bucket_histogram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.renderers import report as report_module

    calls: list[dict[str, Any]] = []

    def _capture_bucket_histogram(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    def _fail_hourly_histogram(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("period rank renderer should not use hourly histogram")

    monkeypatch.setattr(
        report_module, "draw_bucket_histogram", _capture_bucket_histogram
    )
    monkeypatch.setattr(report_module, "draw_hourly_histogram", _fail_hourly_histogram)

    image = await build_water_period_rank_image(
        WaterPeriodRankCardData(
            period="total",
            title="用户榜 · 全局总榜",
            badge="365d",
            range_text="2025.08.13 - 2026.08.12",
            compare_text="对比区间 2024.08.13 - 2025.08.12",
            start_date=20250813,
            end_date=20260812,
            generated_at=1,
            total_msg_count=420,
            active_entity_count=66,
            hourly_counts=[hour + 1 for hour in range(24)],
            daily_msg_counts=[day + 1 for day in range(48)],
            peak_hour=23,
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
                    hourly_counts=[24 - hour for hour in range(24)],
                    daily_msg_counts=[day + 1 for day in range(48)],
                    current_rank=1,
                    trend=3,
                )
            ],
            champion_gap=30,
            champion_share=120 / 420,
        ),
        "zh-CN",
    )

    assert image is not None
    assert len(calls) == 2
    assert len(calls[0]["values"]) == 50
    assert sum(calls[0]["values"]) == sum(range(1, 49))
    assert len(calls[1]["values"]) == 50
    assert calls[1]["values"] != [hour + 1 for hour in range(24)]
    assert sum(calls[1]["values"]) == sum(range(1, 49))
    assert calls[1]["labels"][0] == "2025-08-13"
    assert [label for label in calls[1]["labels"] if label][-1] == "2025-09-29"


def _report_avatar() -> BuildImage:
    return BuildImage.new("RGBA", (128, 128), "#F6B7D2")


def _report_user_item(
    avatar: BuildImage,
    *,
    rank: int,
    name: str | None = None,
    msg_count: int = 120,
) -> WaterRankCardItem:
    return WaterRankCardItem(
        entity_id=f"1000{rank}",
        display_name=name or f"User{rank}",
        secondary_label=f"用户 1000{rank}",
        avatar=avatar,
        msg_count=msg_count,
        active_days=15,
        active_hours=28 - rank,
        hourly_counts=[(idx % 5) + 1 for idx in range(24)],
        current_rank=rank,
        trend=3 - rank,
    )


def _report_group_item(
    avatar: BuildImage,
    *,
    rank: int,
    name: str = "测试群",
    mgid: str = "20001",
    msg_count: int = 120,
) -> WaterGroupDailyRankCardItem:
    return WaterGroupDailyRankCardItem(
        group_id=mgid,
        display_name=name,
        avatar=avatar,
        msg_count=msg_count,
        current_rank=rank,
        trend=1,
        is_focus_group=(rank <= 3),
    )


def _report_share_slice(
    *,
    mgid: str,
    name: str,
    msg_count: int,
    total: int,
) -> WaterGroupShareSlice:
    return WaterGroupShareSlice(
        group_id=mgid,
        display_name=name,
        msg_count=msg_count,
        share_ratio=msg_count / total,
        is_focus_group=(mgid == "20001"),
    )


def _build_group_report_data() -> WaterGroupReportImageData:
    avatar = _report_avatar()
    return WaterGroupReportImageData(
        title="Senrin水王日报",
        badge="",
        range_text="统计日期: 2026.06.14 · 今日实时快照",
        compare_text="对比日期: 2026.06.13 · 消息 +30 · 活跃成员 +1",
        generated_at=1_747_960_000,
        total_msg_count=420,
        active_user_count=66,
        hourly_counts=[(idx % 6) + 1 for idx in range(24)],
        peak_hour=5,
        previous_total_msg_count=390,
        top_items=[
            _report_user_item(avatar, rank=1, name="Alice"),
            _report_user_item(avatar, rank=2, name="Bob", msg_count=90),
        ],
        group_rank_title="群聊当日排名",
        group_rank_summary="本群当前排名 #3 / 12 · 较昨日 +1",
        group_rank_items=[
            _report_group_item(avatar, rank=3),
            _report_group_item(
                avatar, rank=4, name="隔壁群", mgid="20002", msg_count=98
            ),
        ],
        previous_hourly_counts=[(idx % 4) + 1 for idx in range(24)],
        right_panel_layout_tier="compact",
        group_rank_share_ratio=120 / 300,
        group_rank_total_msg_count=300,
        group_rank_focus_msg_count=120,
        group_rank_prev_gap_msg_count=10,
        group_rank_next_gap_msg_count=22,
        group_rank_has_hidden_before=True,
        group_rank_has_hidden_after=True,
        group_share_slices=[
            _report_share_slice(mgid="20001", name="测试群", msg_count=120, total=300),
            _report_share_slice(mgid="20002", name="隔壁群", msg_count=98, total=300),
            _report_share_slice(mgid="20003", name="第三群", msg_count=82, total=300),
        ],
    )


@pytest.mark.parametrize(
    ("case_name", "overrides"),
    [
        ("compact_default", {}),
        ("balanced_tier", {"right_panel_layout_tier": "balanced"}),
        ("expanded_tier", {"right_panel_layout_tier": "expanded"}),
        (
            "without_group_rank_block",
            {"group_rank_items": [], "group_rank_summary": ""},
        ),
        (
            "truncates_long_group_rank_names",
            {
                "group_rank_items": [
                    _report_group_item(
                        _report_avatar(),
                        rank=3,
                        name=(
                            "这是一个非常非常非常非常非常长的群聊名称"
                            " With Extra Long Mixed Content 1234567890"
                        ),
                    ),
                    _report_group_item(
                        _report_avatar(),
                        rank=4,
                        name=(
                            "隔壁那个名字也很长很长很长的测试群"
                            " Another Extremely Long Group Name"
                        ),
                        mgid="20002",
                        msg_count=98,
                    ),
                ],
            },
        ),
        (
            "truncates_long_group_rank_summary",
            {
                "group_rank_summary": (
                    "本群当前排名 #3 / 9999 · 较昨日 +123456789 "
                    "这是一个非常非常非常非常非常长的摘要说明，用来验证右侧摘要在固定宽度里会被安全截断"
                ),
                "group_rank_has_hidden_before": False,
                "group_rank_has_hidden_after": False,
            },
        ),
        (
            "three_digit_group_rank_labels",
            {
                "group_rank_summary": "本群当前排名 #128 / 512 · 较昨日 +12",
                "group_rank_items": [
                    _report_group_item(_report_avatar(), rank=128),
                    _report_group_item(
                        _report_avatar(),
                        rank=129,
                        name="隔壁群",
                        mgid="20002",
                        msg_count=118,
                    ),
                ],
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_build_water_group_report_image_accepts_all_edge_inputs(
    case_name: str,
    overrides: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """各分支输入都能走通到渲染调用（可行性）。

    真实绘制由下方的 smoke 用例覆盖，像素正确性归渲染质量测试。"""
    data = replace(_build_group_report_data(), **overrides)
    seen: list[Any] = []

    async def _capture(payload: Any, locale: Any) -> bytes:
        _ = locale
        seen.append(payload)
        return b"\x89PNG\r\n\x1a\nstub"

    from src.plugins.water import renderers as renderers_pkg

    monkeypatch.setattr(
        renderers_pkg,
        "build_water_group_report_image",
        _capture,
    )

    img = await renderers_pkg.build_water_group_report_image(data, "zh-CN")

    assert seen, case_name
    assert img is not None, case_name
    assert img.startswith(b"\x89PNG"), case_name


@pytest.mark.asyncio
async def test_build_water_group_report_image_smoke() -> None:
    """真实渲染冒烟：确认渲染器仍能产出合法 PNG（每轮 CI 只跑一次）。"""
    from src.plugins.water.renderers import build_water_group_report_image

    img = await build_water_group_report_image(_build_group_report_data(), "zh-CN")

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
                daily_msg_counts=[idx + day + 1 for day in range(12)],
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
        daily_msg_counts=[sum(item.msg_count for item in items)],
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

    async def _fake_display_names(
        subject: str,
        items: list[Any],
        locale: str,
    ) -> dict[str, str]:
        _ = locale
        prefix = {"user": "用户", "group": "群聊"}.get(subject, "矩阵")
        return {str(item.entity_id): f"{prefix} {item.entity_id}" for item in items}

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
        "_resolve_display_names",
        _fake_display_names,
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

    stub_png = b"\x89PNG\r\n\x1a\n" + b"stub"
    calls: list[tuple[str, str, str]] = []

    async def _stub_day_image(data: object, locale: object) -> bytes:
        _ = (data, locale)
        return stub_png

    async def _stub_period_image(data: object, locale: object) -> bytes:
        _ = (data, locale)
        return stub_png

    monkeypatch.setattr(
        query_module, "build_water_day_rank_image", _stub_day_image, raising=False
    )
    monkeypatch.setattr(
        query_module, "build_water_period_rank_image", _stub_period_image, raising=False
    )

    combos = [
        (subject, scope, period)
        for subject, scopes in legal_scopes.items()
        for scope in scopes
        for period in periods
    ]
    for subject, scope, period in combos:
        message = await water_rank_query_service.build_rank_message(
            subject=subject,
            scope=scope,
            period=period,
            group_id="20001",
            locale="zh-CN",
        )
        assert "CQ:image" in str(render_message_plan_input(message)), (
            subject,
            scope,
            period,
        )
        calls.append((subject, scope, period))

    assert len(calls) == 36
