from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.lib.i18n.runtime import tr
from src.services import member_sync_admin as admin_sync_module
from src.services.member_sync_admin import (
    build_sync_members_all_running_summary,
    build_sync_members_completion_summary,
)
from src.services.sync import MemberSyncReport


def _build_report(
    group_id: str, group_name: str, *, ok: bool = True
) -> MemberSyncReport:
    return MemberSyncReport(
        group_id=group_id,
        group_name=group_name,
        trigger_source="admin_sync_all",
        started_at=1,
        finished_at=2,
        elapsed_ms=250,
        member_total=12,
        synced_members=12,
        ok=ok,
        error_type="" if ok else "RuntimeError",
        error_reason="" if ok else "boom",
    )


@pytest.mark.asyncio
async def test_run_sync_members_for_all_groups_reports_final_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_group_list = AsyncMock(
        return_value=[
            {"group_id": 20002, "group_name": "B 群"},
            {"group_id": 20001, "group_name": "A 群"},
        ]
    )
    bot = cast(admin_sync_module.Bot, SimpleNamespace(get_group_list=get_group_list))
    monkeypatch.setattr(admin_sync_module.config, "SUPERUSERS", {"1"})
    monkeypatch.setattr(
        admin_sync_module,
        "sync_members_from_api",
        AsyncMock(
            side_effect=[
                _build_report("20001", "A 群"),
                _build_report("20002", "B 群"),
            ]
        ),
    )
    deliver_mock = AsyncMock(return_value=())
    sleep_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        admin_sync_module,
        "deliver_admin_notification_plan",
        deliver_mock,
    )
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    state = await admin_sync_module.run_sync_members_for_all_groups(bot)

    assert state.status == "completed"
    assert state.total_groups == 2
    assert state.completed == 2
    assert state.succeeded == 2
    assert state.failed == 0
    sleep_mock.assert_awaited_once_with(
        admin_sync_module.SYNC_MEMBERS_ALL_INTERVAL_SECONDS
    )
    assert deliver_mock.await_count == 1
    delivered_plan = deliver_mock.await_args.kwargs["plan"]  # type: ignore[union-attr]
    assert delivered_plan.force_forward is True
    assert len(delivered_plan.messages) == 3
    assert tr("zh-CN", "admin.sync_members.report.title") in str(
        delivered_plan.messages[0]
    )
    assert "[001/002] [20001|A 群]" in str(delivered_plan.messages[1])
    assert "[002/002] [20002|B 群]" in str(delivered_plan.messages[2])
    assert "members=12" in str(delivered_plan.messages[1])


@pytest.mark.asyncio
async def test_run_sync_members_for_all_groups_respects_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_group_list = AsyncMock(
        return_value=[{"group_id": 20001, "group_name": ""}],
    )
    bot = cast(admin_sync_module.Bot, SimpleNamespace(get_group_list=get_group_list))
    monkeypatch.setattr(admin_sync_module.config, "SUPERUSERS", {"1"})
    monkeypatch.setattr(
        admin_sync_module,
        "sync_members_from_api",
        AsyncMock(side_effect=[_build_report("20001", "")]),
    )
    deliver_mock = AsyncMock(return_value=())
    monkeypatch.setattr(
        admin_sync_module,
        "deliver_admin_notification_plan",
        deliver_mock,
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    state = await admin_sync_module.run_sync_members_for_all_groups(bot, locale="lzh")

    assert state.succeeded == 1
    await_args = deliver_mock.await_args
    assert await_args is not None
    delivered_plan = await_args.kwargs["plan"]
    overview = str(delivered_plan.messages[0])
    assert tr("lzh", "admin.sync_members.report.title") in overview
    assert tr("lzh", "admin.sync_members.next_report.final") in overview


def test_detail_line_falls_back_to_localized_group_name() -> None:
    detail = admin_sync_module._build_detail_line(
        1,
        1,
        _build_report("20001", ""),
        locale="lzh",
    )

    assert "[001/001] [20001|群聊_0001]" in detail


def test_build_sync_members_all_running_summary_handles_idle_state() -> None:
    assert build_sync_members_all_running_summary(None) == tr(
        "zh-CN", "admin.sync_members.idle"
    )


def test_build_sync_members_all_running_summary_localizes_status_and_stage() -> None:
    state = admin_sync_module.SyncMembersAllTaskState(
        task_id="sync-1",
        started_at=1,
        total_groups=5,
        completed=2,
        succeeded=2,
        status="running",
        current_group_id="20001",
        current_group_name="A 群",
        current_stage="syncing_group",
        current_stage_started_at=1,
    )

    summary = build_sync_members_all_running_summary(state, locale="lzh")

    assert tr("lzh", "admin.sync_members.running") in summary
    assert tr("lzh", "admin.sync_members.status.running") in summary
    assert tr("lzh", "admin.sync_members.stage.syncing_group") in summary
    assert "[20001|A 群]" in summary


def test_build_sync_members_completion_summary_uses_shared_copy() -> None:
    state = admin_sync_module.SyncMembersAllTaskState(
        task_id="sync-1",
        started_at=1,
        total_groups=3,
        completed=3,
        succeeded=2,
        failed=1,
        skipped=0,
        status="completed",
    )

    summary = build_sync_members_completion_summary(state, locale="x-meme")

    assert summary == "\n".join(
        [
            tr("x-meme", "admin.sync_members.completed"),
            tr("x-meme", "admin.sync_members.summary.total", count=3),
            tr("x-meme", "admin.sync_members.summary.succeeded", count=2),
            tr("x-meme", "admin.sync_members.summary.failed", count=1),
            tr("x-meme", "admin.sync_members.summary.skipped", count=0),
        ]
    )


def test_build_sync_members_completion_summary_failure_localized() -> None:
    state = admin_sync_module.SyncMembersAllTaskState(
        task_id="sync-1",
        started_at=1,
        status="failed",
        total_groups=1,
        completed=1,
        failed=1,
    )

    summary = build_sync_members_completion_summary(state)

    assert tr("zh-CN", "admin.sync_members.summary.failed", count=1) in summary


@pytest.mark.asyncio
async def test_run_sync_members_for_all_groups_reports_every_ten_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groups = [
        {"group_id": 20000 + index, "group_name": f"G{index}"} for index in range(10)
    ]
    get_group_list = AsyncMock(return_value=groups)
    bot = cast(admin_sync_module.Bot, SimpleNamespace(get_group_list=get_group_list))
    monkeypatch.setattr(admin_sync_module.config, "SUPERUSERS", {"1"})
    monkeypatch.setattr(
        admin_sync_module,
        "sync_members_from_api",
        AsyncMock(
            side_effect=[
                _build_report(str(20000 + index), f"G{index}") for index in range(10)
            ]
        ),
    )
    deliver_mock = AsyncMock(return_value=())
    sleep_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        admin_sync_module,
        "deliver_admin_notification_plan",
        deliver_mock,
    )
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    state = await admin_sync_module.run_sync_members_for_all_groups(bot)

    assert state.completed == 10
    assert deliver_mock.await_count == 2
    first_plan = deliver_mock.await_args_list[0].kwargs["plan"]
    second_plan = deliver_mock.await_args_list[1].kwargs["plan"]
    assert len(first_plan.messages) == 11
    assert len(second_plan.messages) == 1
    assert tr("zh-CN", "admin.sync_members.next_report.final") in str(
        second_plan.messages[0]
    )


@pytest.mark.asyncio
async def test_run_sync_members_for_all_groups_rejects_concurrent_run() -> None:
    await admin_sync_module._sync_members_all_lock.acquire()
    try:
        bot = cast(
            admin_sync_module.Bot,
            SimpleNamespace(get_group_list=AsyncMock(return_value=[])),
        )
        with pytest.raises(RuntimeError, match="already running"):
            await admin_sync_module.run_sync_members_for_all_groups(bot)
    finally:
        admin_sync_module._sync_members_all_lock.release()
