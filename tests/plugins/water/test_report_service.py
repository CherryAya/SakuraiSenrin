from __future__ import annotations

import pathlib
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import arrow
from pil_utils import BuildImage
import pytest

from src.lib.long_task import LongTaskRunner
from src.lib.message_plan import render_message_plan_input
from src.lib.messages import text_message
from src.plugins.water.database.repo_models import (
    WaterDailyReportCandidate,
    WaterDailyReportPreviewItem,
    WaterGroupDailyRankItem,
    WaterGroupDailyRankSnapshot,
    WaterGroupReportMember,
    WaterGroupReportSnapshot,
)
from src.plugins.water.services.report import (
    TODAY_REPORT_COOLDOWN_SECONDS,
    WaterDailyReportBatchResult,
    WaterDailyReportDryRunResult,
    WaterDailyReportPrepareResult,
    water_report_service,
)
from src.plugins.water.services.worker_jobs import WaterPreparedReportItem


def test_today_report_cooldown_is_group_shared() -> None:
    water_report_service.clear_today_report_cooldowns()

    acquired, remain = water_report_service.try_acquire_today_report_cooldown("20001")
    assert acquired is True
    assert remain == 0

    acquired, remain = water_report_service.try_acquire_today_report_cooldown("20001")
    assert acquired is False
    assert 1 <= remain <= TODAY_REPORT_COOLDOWN_SECONDS


def test_resolve_next_daily_report_push_uses_current_slot_before_0040() -> None:
    next_push_at, record_date = water_report_service.resolve_next_daily_report_push(
        now_ts=arrow.get(
            "2026-07-16 00:39:59",
            "YYYY-MM-DD HH:mm:ss",
            tzinfo="Asia/Shanghai",
        ).int_timestamp
    )

    assert (
        arrow.get(next_push_at).to("Asia/Shanghai").format("YYYY-MM-DD HH:mm:ss")
        == "2026-07-16 00:40:00"
    )
    assert record_date == 20260715


def test_resolve_next_daily_report_push_uses_next_slot_after_0040() -> None:
    next_push_at, record_date = water_report_service.resolve_next_daily_report_push(
        now_ts=arrow.get(
            "2026-07-16 00:40:01",
            "YYYY-MM-DD HH:mm:ss",
            tzinfo="Asia/Shanghai",
        ).int_timestamp
    )

    assert (
        arrow.get(next_push_at).to("Asia/Shanghai").format("YYYY-MM-DD HH:mm:ss")
        == "2026-07-17 00:40:00"
    )
    assert record_date == 20260716


@pytest.mark.asyncio
async def test_build_daily_report_dry_run_live_preview_when_unsettled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import report as report_module

    monkeypatch.setattr(
        report_module.water_repo,
        "get_settlement_state",
        AsyncMock(
            return_value={
                "last_success_record_date": 20260715,
                "latest_record_date": 20260715,
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
        AsyncMock(return_value=["20001", "20002", "20003"]),
    )
    preview_items = [
        WaterDailyReportPreviewItem(
            group_id="20001",
            record_date=20260716,
            total_msg_count=120,
            active_user_count=12,
            active_hours=8,
            activity_score=360,
        ),
        WaterDailyReportPreviewItem(
            group_id="20002",
            record_date=20260716,
            total_msg_count=80,
            active_user_count=5,
            active_hours=4,
            activity_score=180,
        ),
        WaterDailyReportPreviewItem(
            group_id="20003",
            record_date=20260716,
            total_msg_count=0,
            active_user_count=0,
            active_hours=0,
            activity_score=0,
        ),
    ]
    list_preview_mock = AsyncMock(return_value=preview_items)
    monkeypatch.setattr(
        report_module.water_repo,
        "list_daily_report_preview_items",
        list_preview_mock,
    )

    result = await water_report_service.build_daily_report_dry_run(
        now_ts=arrow.get(
            "2026-07-16 12:00:00",
            "YYYY-MM-DD HH:mm:ss",
            tzinfo="Asia/Shanghai",
        ).int_timestamp,
    )

    assert isinstance(result, WaterDailyReportDryRunResult)
    assert result.preview_mode == "live"
    assert result.record_date == 20260716
    assert result.working_groups == 3
    assert result.candidate_groups == 1
    assert result.coverage_ratio == pytest.approx(1 / 3)
    list_preview_mock.assert_awaited_once_with(
        record_date=20260716,
        working_group_ids=["20001", "20002", "20003"],
        live=True,
    )


@pytest.mark.asyncio
async def test_build_daily_report_dry_run_summary_formats_threshold_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dryrun_result = WaterDailyReportDryRunResult(
        next_push_at=arrow.get(
            "2026-07-17 00:40:00",
            "YYYY-MM-DD HH:mm:ss",
            tzinfo="Asia/Shanghai",
        ).int_timestamp,
        record_date=20260716,
        working_groups=2,
        candidate_groups=1,
        threshold=300,
        threshold_factor=20,
        preview_mode="live",
        items=(
            WaterDailyReportPreviewItem(
                group_id="20001",
                record_date=20260716,
                total_msg_count=140,
                active_user_count=9,
                active_hours=6,
                activity_score=320,
            ),
            WaterDailyReportPreviewItem(
                group_id="20002",
                record_date=20260716,
                total_msg_count=60,
                active_user_count=6,
                active_hours=3,
                activity_score=180,
            ),
        ),
    )
    monkeypatch.setattr(
        water_report_service,
        "build_daily_report_dry_run",
        AsyncMock(return_value=dryrun_result),
    )

    summary = await water_report_service.build_daily_report_dry_run_summary(
        locale="zh-CN"
    )

    assert "下一次推送: 2026-07-17 00:40:00" in summary
    assert "目标日报日期: 20260716" in summary
    assert "预览模式: 实时预估" in summary
    assert "工作群总数: 2" in summary
    assert "预计发送日报: 1" in summary
    assert "覆盖占比: 50.0%" in summary
    assert "阈值规则: msg_count + 20 * active_user_count >= 300" in summary
    assert "[SEND] 群 20001 | score=320 | 140 + 20*9" in summary
    assert "[SKIP] 群 20002 | score=180 | 60 + 20*6" in summary


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
        "_build_daily_report_batch_context",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        report_module.water_report_service,
        "build_group_report_message",
        AsyncMock(side_effect=[text_message("R2"), text_message("R1")]),
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr(report_module.asyncio, "sleep", sleep_mock)

    bot = AsyncMock()
    result = await water_report_service.run_daily_group_report_push(bot=bot)

    assert result.candidate_groups == 2
    assert result.rendered_groups == 2
    assert result.sent_groups == 2
    assert bot.call_api.await_count == 2
    first_call = bot.call_api.await_args_list[0]
    second_call = bot.call_api.await_args_list[1]
    assert first_call.args == ("send_group_msg",)
    assert second_call.args == ("send_group_msg",)
    assert first_call.kwargs["group_id"] == 20002
    assert second_call.kwargs["group_id"] == 20001
    assert sleep_mock.await_count == 2


@pytest.mark.asyncio
async def test_run_daily_group_report_push_notifies_superusers_on_start_and_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import report as report_module

    monkeypatch.setattr(report_module.config, "SUPERUSERS", {"1"})
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
        "_build_daily_report_batch_context",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        report_module.water_report_service,
        "build_group_report_message",
        AsyncMock(side_effect=[text_message("R2"), text_message("R1")]),
    )
    deliver_mock = AsyncMock(return_value=None)
    sleep_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(report_module, "deliver_message_plan", deliver_mock)
    monkeypatch.setattr(report_module.asyncio, "sleep", sleep_mock)

    bot = cast(report_module.Bot, SimpleNamespace())
    result = await water_report_service.run_daily_group_report_push(bot=bot)

    assert result.sent_groups == 2
    admin_calls = [
        call
        for call in deliver_mock.await_args_list
        if call.kwargs["target"].kind == "private"
    ]
    group_calls = [
        call
        for call in deliver_mock.await_args_list
        if call.kwargs["target"].kind == "group"
    ]
    assert len(admin_calls) == 2
    assert len(group_calls) == 2
    start_plan = admin_calls[0].kwargs["plan"]
    final_plan = admin_calls[1].kwargs["plan"]
    assert start_plan.force_forward is True
    assert len(start_plan.messages) == 1
    assert "水王日报推送进度" in str(start_plan.messages[0])
    assert "候选群数：2" in str(start_plan.messages[0])
    assert "本次为最终汇总" in str(final_plan.messages[0])
    final_details = "\n".join(str(item) for item in final_plan.messages[1:])
    assert "[20001] 发送成功" in final_details
    assert "[20002] 发送成功" in final_details
    sleep_mock.assert_awaited()


@pytest.mark.asyncio
async def test_run_daily_group_report_push_reports_batch_progress_every_ten_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import report as report_module

    monkeypatch.setattr(report_module.config, "SUPERUSERS", {"1"})
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
    candidates = [
        WaterDailyReportCandidate(
            group_id=str(20000 + index),
            record_date=20260613,
            total_msg_count=500 - index,
            active_user_count=10 + index,
            active_hours=6 + index,
            activity_score=700 - index,
        )
        for index in range(11)
    ]
    monkeypatch.setattr(
        report_module.group_repo,
        "get_working_group_ids",
        AsyncMock(return_value=[candidate.group_id for candidate in candidates]),
    )
    monkeypatch.setattr(
        report_module.water_repo,
        "list_daily_report_candidates",
        AsyncMock(return_value=candidates),
    )
    monkeypatch.setattr(
        report_module.water_report_service,
        "_build_daily_report_batch_context",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        report_module.water_report_service,
        "build_group_report_message",
        AsyncMock(side_effect=[text_message(f"R{index}") for index in range(11)]),
    )
    deliver_mock = AsyncMock(return_value=None)
    sleep_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(report_module, "deliver_message_plan", deliver_mock)
    monkeypatch.setattr(report_module.asyncio, "sleep", sleep_mock)

    bot = cast(report_module.Bot, SimpleNamespace())
    result = await water_report_service.run_daily_group_report_push(bot=bot)

    assert result.sent_groups == 11
    admin_calls = [
        call
        for call in deliver_mock.await_args_list
        if call.kwargs["target"].kind == "private"
    ]
    assert len(admin_calls) == 3
    start_plan = admin_calls[0].kwargs["plan"]
    batch_plan = admin_calls[1].kwargs["plan"]
    final_plan = admin_calls[2].kwargs["plan"]
    assert len(start_plan.messages) == 1
    assert len(batch_plan.messages) == 11
    assert "下次上报：每完成 10 个群或任务结束。" in str(batch_plan.messages[0])
    assert len(final_plan.messages) == 2
    assert "本次为最终汇总" in str(final_plan.messages[0])


@pytest.mark.asyncio
async def test_prepare_daily_group_report_push_writes_text_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
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
    candidates = [
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
    monkeypatch.setattr(
        report_module.group_repo,
        "get_working_group_ids",
        AsyncMock(return_value=["20001", "20002"]),
    )
    monkeypatch.setattr(
        report_module.water_repo,
        "list_daily_report_candidates",
        AsyncMock(return_value=candidates),
    )
    monkeypatch.setattr(
        report_module.water_report_service,
        "_build_daily_report_batch_context",
        AsyncMock(return_value=SimpleNamespace()),
    )

    async def _build_message(**kwargs: object) -> object:
        return text_message(f"R-{kwargs['group_id']}")

    monkeypatch.setattr(
        report_module.water_report_service,
        "build_group_report_message",
        AsyncMock(side_effect=_build_message),
    )

    result = await water_report_service.prepare_daily_group_report_push(
        output_dir=tmp_path,
    )

    assert isinstance(result, WaterDailyReportPrepareResult)
    assert result.candidate_groups == 2
    assert result.rendered_groups == 2
    assert (tmp_path / "20001.txt").read_text(encoding="utf-8") == "R-20001"
    assert (tmp_path / "20002.txt").read_text(encoding="utf-8") == "R-20002"


@pytest.mark.asyncio
async def test_send_prepared_daily_group_report_push_reads_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    from src.plugins.water.services import report as report_module

    monkeypatch.setattr(report_module.config, "SUPERUSERS", set())
    monkeypatch.setattr(report_module.asyncio, "sleep", AsyncMock(return_value=None))
    deliver_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(report_module, "deliver_message_plan", deliver_mock)
    (tmp_path / "20001.txt").write_text("R-20001", encoding="utf-8")
    (tmp_path / "20002.txt").write_text("R-20002", encoding="utf-8")
    prepared = WaterDailyReportPrepareResult(
        record_date=20260613,
        candidate_groups=2,
        rendered_groups=2,
        skipped_groups=0,
        failed_groups=0,
        total_elapsed_ms=12.0,
        report_items=(
            WaterPreparedReportItem(
                group_id="20002",
                record_date=20260613,
                message_kind="text",
                payload_name="20002.txt",
                activity_score=610,
                total_msg_count=450,
                active_user_count=8,
            ),
            WaterPreparedReportItem(
                group_id="20001",
                record_date=20260613,
                message_kind="text",
                payload_name="20001.txt",
                activity_score=460,
                total_msg_count=320,
                active_user_count=7,
            ),
        ),
    )

    result = await water_report_service.send_prepared_daily_group_report_push(
        bot=cast(report_module.Bot, SimpleNamespace()),
        prepared=prepared,
        output_dir=tmp_path,
    )

    assert result.sent_groups == 2
    assert len(deliver_mock.await_args_list) == 2
    first_plan = deliver_mock.await_args_list[0].kwargs["plan"]
    second_plan = deliver_mock.await_args_list[1].kwargs["plan"]
    assert str(render_message_plan_input(first_plan.messages[0])) == "R-20002"
    assert str(render_message_plan_input(second_plan.messages[0])) == "R-20001"


@pytest.mark.asyncio
async def test_build_card_data_keeps_group_report_core_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import report as report_module

    batch_names_mock = AsyncMock(return_value={"20001": "测试群"})
    monkeypatch.setattr(
        report_module.group_repo,
        "get_names_by_gids",
        batch_names_mock,
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
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        report_module.water_repo,
        "get_group_daily_distribution_items",
        AsyncMock(return_value=[]),
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

    assert data.title == "测试群 | 2026.06.13水王日报"
    assert data.badge == ""
    assert data.total_msg_count == 42
    assert data.active_user_count == 1
    assert data.top_items[0].display_name == "Alice"
    batch_names_mock.assert_awaited_once_with(["20001"])


@pytest.mark.asyncio
async def test_build_group_report_message_advances_long_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import report as report_module

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
        leaderboard=[],
    )
    monkeypatch.setattr(
        report_module.water_report_service,
        "_get_snapshot",
        AsyncMock(return_value=snapshot),
    )
    monkeypatch.setattr(
        report_module.water_report_service,
        "_build_card_data",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        report_module,
        "build_water_group_report_image",
        AsyncMock(return_value=b"fake-image"),
    )
    advance_mock = AsyncMock()
    task = cast(LongTaskRunner, SimpleNamespace(advance=advance_mock))

    message = await water_report_service.build_group_report_message(
        window="today_live",
        group_id="20001",
        locale="zh-CN",
        task=task,
    )
    rendered = render_message_plan_input(message)

    assert len(rendered) == 1
    assert rendered[0].type == "image"
    assert advance_mock.await_count == 3


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
        total_msg_count=81,
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

    batch_names_mock = AsyncMock(
        return_value={
            "20001": "测试群",
            "20002": "隔壁群",
        }
    )
    monkeypatch.setattr(
        report_module.group_repo,
        "get_names_by_gids",
        batch_names_mock,
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
    monkeypatch.setattr(
        report_module.water_repo,
        "get_group_daily_distribution_items",
        AsyncMock(
            return_value=[
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
            ]
        ),
    )
    monkeypatch.setattr(
        report_module.water_repo,
        "get_group_daily_rank_history",
        AsyncMock(
            return_value={
                "20001": [
                    (20260611, 4),
                    (20260612, 4),
                    (20260613, 3),
                ],
                "20002": [
                    (20260611, 3),
                    (20260612, 3),
                    (20260613, 4),
                ],
            }
        ),
    )
    monkeypatch.setattr(
        report_module.QQAvatar,
        "fetch_group",
        AsyncMock(return_value=BuildImage.new("RGBA", (64, 64), "#F6B7D2")),
    )

    data = await water_report_service._build_card_data(
        "today_live",
        snapshot,
        "zh-CN",
    )

    assert data.group_rank_title == "群聊当日排名"
    assert data.group_rank_summary == "本群当前排名 #3 / 12 · 较昨日 +1"
    assert [item.display_name for item in data.group_rank_items] == [
        "测试群",
        "隔壁群",
    ]
    assert data.group_rank_items[0].avatar is not None
    assert data.group_rank_items[0].is_focus_group is True
    assert data.group_rank_items[1].is_focus_group is False
    assert data.previous_hourly_counts == [0] * 24
    assert data.right_panel_layout_tier == "compact"
    assert data.group_rank_share_ratio == pytest.approx(42 / 81)
    assert [item.display_name for item in data.group_share_slices] == [
        "测试群",
        "隔壁群",
    ]
    assert data.group_share_slices[0].is_focus_group is True
    assert data.group_rank_trend_labels == ["06/11", "06/12", "06/13"]
    assert [item.group_id for item in data.group_rank_trend_series] == [
        "20001",
        "20002",
    ]
    assert data.group_rank_trend_series[0].is_focus_group is True
    assert data.group_rank_trend_series[0].ranks == [4, 4, 3]
    assert data.group_rank_total_msg_count == 81
    assert data.group_rank_focus_msg_count == 42
    assert data.group_rank_prev_gap_msg_count is None
    assert data.group_rank_next_gap_msg_count == 3
    batch_names_mock.assert_awaited_once_with(["20001", "20002"])
    assert [(item.label, item.value) for item in data.group_rank_insights] == [
        ("本群占比", "51.9%"),
        ("峰值时段", "02:00"),
        ("距上一名", "榜首"),
        ("领先下一名", "3 条"),
    ]
    assert data.group_rank_has_hidden_before is True
    assert data.group_rank_has_hidden_after is True


def test_build_group_rank_summary_uses_numeric_delta_when_no_previous_rank() -> None:
    snapshot = WaterGroupDailyRankSnapshot(
        focus_group_id="20001",
        record_date=20260613,
        total_groups=7,
        total_msg_count=210,
        focus_rank=5,
        focus_trend=None,
        leaderboard=[],
        has_hidden_before=False,
        has_hidden_after=False,
    )

    summary = water_report_service._build_group_rank_summary(snapshot, "zh-CN")

    assert summary == "本群当前排名 #5 / 7 · 较昨日 +2"


def test_build_group_rank_summary_returns_empty_without_snapshot() -> None:
    assert water_report_service._build_group_rank_summary(None, "zh-CN") == ""


def test_select_trend_group_ids_keeps_focus_group_within_plus_minus_four() -> None:
    snapshot = WaterGroupDailyRankSnapshot(
        focus_group_id="20005",
        record_date=20260613,
        total_groups=10,
        total_msg_count=500,
        focus_rank=5,
        focus_trend=1,
        leaderboard=[
            WaterGroupDailyRankItem(
                group_id=f"2000{idx}",
                msg_count=100 - idx,
                active_user_count=idx,
                active_hours=idx,
                hourly_counts=[0] * 24,
                current_rank=idx,
                trend=0,
            )
            for idx in range(1, 9)
        ],
        has_hidden_before=True,
        has_hidden_after=True,
    )

    group_ids = water_report_service._select_trend_group_ids(snapshot)

    assert group_ids == [
        "20001",
        "20002",
        "20003",
        "20004",
        "20005",
        "20006",
        "20007",
        "20008",
    ]


def test_try_acquire_today_report_cooldown_skips_in_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib import cooldown as cooldown_module

    water_report_service.clear_today_report_cooldowns()
    monkeypatch.setattr(cooldown_module.config, "DEBUG", True)

    first = water_report_service.try_acquire_today_report_cooldown("20001")
    second = water_report_service.try_acquire_today_report_cooldown("20001")

    assert first == (True, 0)
    assert second == (True, 0)


@pytest.mark.asyncio
async def test_build_group_rank_snapshot_uses_live_snapshot_for_today_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import report as report_module

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
        leaderboard=[],
    )
    rank_snapshot = WaterGroupDailyRankSnapshot(
        focus_group_id="20001",
        record_date=20260613,
        total_groups=99,
        total_msg_count=999,
        focus_rank=8,
        focus_trend=2,
        leaderboard=[],
        has_hidden_before=True,
        has_hidden_after=True,
    )
    get_rank_mock = AsyncMock(return_value=rank_snapshot)
    group_ids_mock = AsyncMock(return_value=["20001", "20002"])

    monkeypatch.setattr(
        report_module.water_repo,
        "get_group_daily_rank_snapshot",
        get_rank_mock,
    )

    result = await water_report_service._build_group_rank_snapshot(
        "today_live",
        snapshot,
    )

    assert result == rank_snapshot
    get_rank_mock.assert_awaited_once_with(
        group_id="20001",
        record_date=20260613,
        radius=4,
        min_window_size=9,
        live=True,
    )
    group_ids_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_group_rank_snapshot_uses_summary_snapshot_for_settled_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.services import report as report_module

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
        leaderboard=[],
    )
    rank_snapshot = WaterGroupDailyRankSnapshot(
        focus_group_id="20001",
        record_date=20260613,
        total_groups=99,
        total_msg_count=999,
        focus_rank=8,
        focus_trend=2,
        leaderboard=[],
        has_hidden_before=True,
        has_hidden_after=True,
    )
    get_rank_mock = AsyncMock(return_value=rank_snapshot)

    monkeypatch.setattr(
        report_module.water_repo,
        "get_group_daily_rank_snapshot",
        get_rank_mock,
    )

    result = await water_report_service._build_group_rank_snapshot(
        "yesterday_settled",
        snapshot,
    )

    assert result == rank_snapshot
    get_rank_mock.assert_awaited_once_with(
        group_id="20001",
        record_date=20260613,
        radius=4,
        min_window_size=9,
        live=False,
    )
