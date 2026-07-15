from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.services import member_sync_admin as admin_sync_module
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
    deliver_mock = AsyncMock(return_value=None)
    sleep_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(admin_sync_module, "deliver_message_plan", deliver_mock)
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
    assert "群成员全量同步进度" in str(delivered_plan.messages[0])
    assert "[001/002] [20001|A 群] 成功" in str(delivered_plan.messages[1])
    assert "[002/002] [20002|B 群] 成功" in str(delivered_plan.messages[2])


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
    deliver_mock = AsyncMock(return_value=None)
    sleep_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(admin_sync_module, "deliver_message_plan", deliver_mock)
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    state = await admin_sync_module.run_sync_members_for_all_groups(bot)

    assert state.completed == 10
    assert deliver_mock.await_count == 2
    first_plan = deliver_mock.await_args_list[0].kwargs["plan"]
    second_plan = deliver_mock.await_args_list[1].kwargs["plan"]
    assert len(first_plan.messages) == 11
    assert len(second_plan.messages) == 1
    assert "本次为最终汇总" in str(second_plan.messages[0])


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
