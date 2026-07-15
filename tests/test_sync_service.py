from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.services import sync as sync_module


class _Bot:
    def __init__(self, *, api_list: list[dict[str, object]] | None = None) -> None:
        self._api_list = api_list if api_list is not None else []
        self.get_group_member_list = AsyncMock(return_value=self._api_list)


@pytest.mark.asyncio
async def test_sync_members_from_api_returns_success_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = _Bot(
        api_list=[
            {
                "group_id": 20001,
                "user_id": 10001,
                "nickname": "alice",
                "card": "",
                "role": "member",
            },
            {
                "group_id": 20001,
                "user_id": 0,
                "nickname": "ignored",
                "card": "",
                "role": "member",
            },
            {
                "group_id": 20001,
                "user_id": 10002,
                "nickname": "bob",
                "card": "B",
                "role": "admin",
            },
        ]
    )

    monkeypatch.setattr(
        sync_module,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    save_user = AsyncMock()
    _save_group = AsyncMock()
    save_member = AsyncMock()
    monkeypatch.setattr(sync_module.user_repo, "save_user", save_user)
    monkeypatch.setattr(sync_module.group_repo, "save_group", _save_group)
    monkeypatch.setattr(sync_module.member_repo, "save_member", save_member)

    report = await sync_module.sync_members_from_api(
        cast(sync_module.Bot, bot),
        "20001",
        trigger_source="test_case",
    )

    assert report.ok is True
    assert report.group_id == "20001"
    assert report.group_name == "测试群"
    assert report.member_total == 2
    assert report.synced_members == 2
    assert report.trigger_source == "test_case"
    assert report.error_type == ""
    save_user.assert_awaited()
    assert save_member.await_count == 2


@pytest.mark.asyncio
async def test_sync_members_from_api_returns_failure_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = _Bot()
    bot.get_group_member_list = AsyncMock(side_effect=RuntimeError("boom"))

    monkeypatch.setattr(
        sync_module,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    _save_user = AsyncMock()
    _save_group = AsyncMock()
    save_member = AsyncMock()
    monkeypatch.setattr(sync_module.user_repo, "save_user", _save_user)
    monkeypatch.setattr(sync_module.group_repo, "save_group", _save_group)
    monkeypatch.setattr(sync_module.member_repo, "save_member", save_member)

    report = await sync_module.sync_members_from_api(
        cast(sync_module.Bot, bot),
        "20001",
        trigger_source="test_case",
    )

    assert report.ok is False
    assert report.group_name == "测试群"
    assert report.error_type == "RuntimeError"
    assert report.error_reason == "boom"
    save_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_members_from_api_serializes_same_group_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    in_flight = 0
    max_in_flight = 0
    call_count = 0

    async def _get_group_member_list(*, group_id: int) -> list[dict[str, object]]:
        nonlocal in_flight, max_in_flight, call_count
        _ = group_id
        call_count += 1
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        started.set()
        await release.wait()
        in_flight -= 1
        return []

    bot = _Bot()
    bot.get_group_member_list = _get_group_member_list  # type: ignore[assignment]

    monkeypatch.setattr(
        sync_module,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    monkeypatch.setattr(sync_module.user_repo, "save_user", AsyncMock())
    monkeypatch.setattr(sync_module.group_repo, "save_group", AsyncMock())
    monkeypatch.setattr(sync_module.member_repo, "save_member", AsyncMock())

    task1 = asyncio.create_task(
        sync_module.sync_members_from_api(cast(sync_module.Bot, bot), "20001")
    )
    await started.wait()
    task2 = asyncio.create_task(
        sync_module.sync_members_from_api(cast(sync_module.Bot, bot), "20001")
    )
    await asyncio.sleep(0)

    assert call_count == 1
    assert max_in_flight == 1

    release.set()
    await asyncio.gather(task1, task2)

    assert call_count == 2
    assert max_in_flight == 1
