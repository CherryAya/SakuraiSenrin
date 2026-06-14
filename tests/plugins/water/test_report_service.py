from __future__ import annotations

from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Message
import pytest

from src.plugins.water.database.repo import (
    WaterDailyReportCandidate,
    WaterGroupDailyRankItem,
    WaterGroupDailyRankSnapshot,
    WaterGroupReportMember,
    WaterGroupReportSnapshot,
)
from src.plugins.water.services.report import (
    TODAY_REPORT_COOLDOWN_SECONDS,
    WaterDailyReportBatchResult,
    water_report_service,
)


def test_today_report_cooldown_is_group_shared() -> None:
    water_report_service.clear_today_report_cooldowns()

    acquired, remain = water_report_service.try_acquire_today_report_cooldown("20001")
    assert acquired is True
    assert remain == 0

    acquired, remain = water_report_service.try_acquire_today_report_cooldown("20001")
    assert acquired is False
    assert 1 <= remain <= TODAY_REPORT_COOLDOWN_SECONDS


@pytest.mark.asyncio
async def test_run_daily_group_report_push_skips_when_settlement_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import report as report_module

    monkeypatch.setattr(
        report_module.water_repo,
        "get_settlement_state",
        AsyncMock(
            return_value={
                "last_success_record_date": 0,
                "latest_record_date": 0,
                "latest_status": "none",
                "latest_started_at": 0,
                "latest_finished_at": 0,
                "ignored_count": 0,
            }
        ),
    )

    bot = AsyncMock()
    result = await water_report_service.run_daily_group_report_push(bot=bot)

    assert isinstance(result, WaterDailyReportBatchResult)
    assert result.candidate_groups == 0
    bot.send_group_msg.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_daily_group_report_push_renders_parallel_and_sends_serially(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import report as report_module

    monkeypatch.setattr(
        report_module.water_repo,
        "get_settlement_state",
        AsyncMock(
            return_value={
                "last_success_record_date": 20260613,
                "latest_record_date": 20260613,
                "latest_status": "success",
                "latest_started_at": 0,
                "latest_finished_at": 0,
                "ignored_count": 0,
            }
        ),
    )
    monkeypatch.setattr(
        report_module.group_repo,
        "get_working_group_ids",
        AsyncMock(return_value=["20001", "20002"]),
    )
    monkeypatch.setattr(
        report_module.water_repo,
        "list_daily_report_candidates",
        AsyncMock(
            return_value=[
                WaterDailyReportCandidate(
                    group_id="20002",
                    record_date=20260613,
                    total_msg_count=450,
                    active_user_count=8,
                    active_hours=12,
                    activity_score=610,
                ),
                WaterDailyReportCandidate(
                    group_id="20001",
                    record_date=20260613,
                    total_msg_count=320,
                    active_user_count=7,
                    active_hours=10,
                    activity_score=460,
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        report_module.water_report_service,
        "build_group_report_message",
        AsyncMock(side_effect=[Message("R2"), Message("R1")]),
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr(report_module.asyncio, "sleep", sleep_mock)

    bot = AsyncMock()
    result = await water_report_service.run_daily_group_report_push(bot=bot)

    assert result.candidate_groups == 2
    assert result.rendered_groups == 2
    assert result.sent_groups == 2
    assert bot.send_group_msg.await_count == 2
    first_call = bot.send_group_msg.await_args_list[0]
    second_call = bot.send_group_msg.await_args_list[1]
    assert first_call.kwargs["group_id"] == 20002
    assert second_call.kwargs["group_id"] == 20001
    assert sleep_mock.await_count == 2


@pytest.mark.asyncio
async def test_build_card_data_keeps_report_templates_unformatted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import report as report_module

    monkeypatch.setattr(
        report_module,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    monkeypatch.setattr(
        report_module.water_report_service,
        "_build_view_items",
        AsyncMock(
            return_value=[
                report_module.WaterRankCardItem(
                    entity_id="10001",
                    display_name="Alice",
                    secondary_label="群成员",
                    avatar=None,
                    msg_count=42,
                    active_days=1,
                    active_hours=6,
                    hourly_counts=[0] * 24,
                    current_rank=1,
                    trend=1,
                )
            ]
        ),
    )
    snapshot = WaterGroupReportSnapshot(
        group_id="20001",
        record_date=20260613,
        total_msg_count=42,
        active_user_count=1,
        active_hours=6,
        hourly_counts=[0] * 24,
        previous_total_msg_count=21,
        previous_active_user_count=1,
        previous_active_hours=4,
        previous_hourly_counts=[0] * 24,
        leaderboard=[
            WaterGroupReportMember(
                user_id="10001",
                msg_count=42,
                active_hours=6,
                hourly_counts=[0] * 24,
                current_rank=1,
                trend=1,
            )
        ],
    )

    data = await water_report_service._build_card_data(
        "today_live",
        snapshot,
        "zh-CN",
    )

    assert "{msg_count}" in data.champion_summary_label
    assert "{msg_count}" in data.board_summary_label
    assert "{active_hours}" in data.board_active_hours_label


@pytest.mark.asyncio
async def test_build_card_data_includes_group_rank_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import report as report_module

    snapshot = WaterGroupReportSnapshot(
        group_id="20001",
        record_date=20260613,
        total_msg_count=42,
        active_user_count=1,
        active_hours=6,
        hourly_counts=[hour % 3 for hour in range(24)],
        previous_total_msg_count=21,
        previous_active_user_count=1,
        previous_active_hours=4,
        previous_hourly_counts=[0] * 24,
        leaderboard=[
            WaterGroupReportMember(
                user_id="10001",
                msg_count=42,
                active_hours=6,
                hourly_counts=[0] * 24,
                current_rank=1,
                trend=1,
            )
        ],
    )
    group_rank_snapshot = WaterGroupDailyRankSnapshot(
        focus_group_id="20001",
        record_date=20260613,
        total_groups=12,
        focus_rank=3,
        focus_trend=1,
        leaderboard=[
            WaterGroupDailyRankItem(
                group_id="20001",
                msg_count=42,
                active_user_count=9,
                active_hours=6,
                hourly_counts=[hour % 3 for hour in range(24)],
                current_rank=3,
                trend=1,
            ),
            WaterGroupDailyRankItem(
                group_id="20002",
                msg_count=39,
                active_user_count=8,
                active_hours=5,
                hourly_counts=[0] * 24,
                current_rank=4,
                trend=-1,
            ),
        ],
        has_hidden_before=True,
        has_hidden_after=True,
    )

    monkeypatch.setattr(
        report_module,
        "resolve_group_name",
        AsyncMock(
            side_effect=lambda _bot, group_id: (
                "测试群" if group_id == "20001" else "隔壁群"
            )
        ),
    )
    monkeypatch.setattr(
        report_module.water_report_service,
        "_build_view_items",
        AsyncMock(
            return_value=[
                report_module.WaterRankCardItem(
                    entity_id="10001",
                    display_name="Alice",
                    secondary_label="群成员",
                    avatar=None,
                    msg_count=42,
                    active_days=1,
                    active_hours=6,
                    hourly_counts=[0] * 24,
                    current_rank=1,
                    trend=1,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        report_module.water_report_service,
        "_build_group_rank_snapshot",
        AsyncMock(return_value=group_rank_snapshot),
    )

    data = await water_report_service._build_card_data(
        "today_live",
        snapshot,
        "zh-CN",
    )

    assert data.report_tile_title == "本群当日瓷砖图"
    assert data.report_group_rank_title == "群聊当日排名"
    assert data.report_group_rank_summary == "本群当前排名 #3 / 12 · 较昨日 +1"
    assert data.report_group_rank_items is not None
    assert [item.display_name for item in data.report_group_rank_items] == [
        "测试群",
        "隔壁群",
    ]
    assert data.report_group_rank_has_hidden_before is True
    assert data.report_group_rank_has_hidden_after is True


def test_build_group_rank_summary_uses_new_when_no_previous_rank() -> None:
    snapshot = WaterGroupDailyRankSnapshot(
        focus_group_id="20001",
        record_date=20260613,
        total_groups=7,
        focus_rank=5,
        focus_trend=None,
        leaderboard=[],
        has_hidden_before=False,
        has_hidden_after=False,
    )

    summary = water_report_service._build_group_rank_summary(snapshot, "zh-CN")

    assert summary == "本群当前排名 #5 / 7 · 较昨日 NEW"


def test_build_group_rank_summary_returns_empty_without_snapshot() -> None:
    assert water_report_service._build_group_rank_summary(None, "zh-CN") == ""
