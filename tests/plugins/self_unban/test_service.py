from __future__ import annotations

from pathlib import Path
import sys
import types
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot
import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SELF_UNBAN_PKG_PATH = _ROOT / "src" / "plugins" / "self_unban"
if "src.plugins.self_unban" not in sys.modules:
    pkg = types.ModuleType("src.plugins.self_unban")
    pkg.__path__ = [str(_SELF_UNBAN_PKG_PATH)]  # type: ignore[attr-defined]
    sys.modules["src.plugins.self_unban"] = pkg

from src.lib.consts import GLOBAL_GROUP_FLAG, PERMANENT_BAN_FLAG
from src.plugins.self_unban.database import self_unban_repo
from src.plugins.self_unban.services import core as self_unban_core
from src.plugins.self_unban.services.core import (
    MAX_SELF_UNBAN_ATTEMPTS,
    MIN_REASON_LENGTH,
    PreparedSelfUnbanRequest,
    self_unban_service,
)


@pytest.fixture(autouse=True)
async def _reset_self_unban_runtime() -> None:
    await self_unban_service.reset_runtime_state()
    await self_unban_service.ensure_initialized()
    await self_unban_repo.clear_attempts()


@pytest.mark.asyncio
async def test_prepare_user_request_rejects_after_two_successes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        self_unban_core.blacklist_repo,
        "get_blacklist",
        AsyncMock(return_value=SimpleNamespace(expiry=PERMANENT_BAN_FLAG)),
    )
    for _ in range(MAX_SELF_UNBAN_ATTEMPTS):
        await self_unban_repo.create_attempt(
            subject_type="user",
            subject_id="10001",
            scope_group_id=GLOBAL_GROUP_FLAG,
            requester_user_id="10001",
            reason="reason",
            result="approved",
            consumes_quota=True,
        )

    result = await self_unban_service.prepare_user_request(
        requester_user_id="10001",
        scope_group_id=GLOBAL_GROUP_FLAG,
        locale="zh-CN",
        source_hint="private_global",
    )

    assert isinstance(result, str)
    assert "联系超管" in result


@pytest.mark.asyncio
async def test_submit_user_request_retries_on_short_reason() -> None:
    prepared = PreparedSelfUnbanRequest(
        kind="user",
        subject_type="user",
        subject_id="10001",
        scope_group_id=GLOBAL_GROUP_FLAG,
        requester_user_id="10001",
        remaining_attempts_before=2,
        locale="zh-CN",
        source_hint="private_global",
    )

    result = await self_unban_service.submit_request(
        cast(Bot, SimpleNamespace()),
        prepared=prepared,
        reason="太短",
    )

    assert result.should_retry is True
    assert str(MIN_REASON_LENGTH) in result.final_message
    assert (
        await self_unban_repo.count_consumed_attempts(
            subject_type="user",
            subject_id="10001",
        )
        == 0
    )


@pytest.mark.asyncio
async def test_submit_user_group_request_reports_still_global_ban(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_blacklist = AsyncMock(
        side_effect=[
            SimpleNamespace(expiry=PERMANENT_BAN_FLAG),
            SimpleNamespace(expiry=PERMANENT_BAN_FLAG),
            SimpleNamespace(expiry=PERMANENT_BAN_FLAG),
        ]
    )
    monkeypatch.setattr(self_unban_core.blacklist_repo, "get_blacklist", get_blacklist)
    monkeypatch.setattr(
        self_unban_core.blacklist_repo,
        "set_unban",
        AsyncMock(),
    )
    monkeypatch.setattr(
        self_unban_core,
        "deliver_admin_notification_plan",
        AsyncMock(return_value=()),
    )
    prepared = PreparedSelfUnbanRequest(
        kind="user",
        subject_type="user",
        subject_id="10001",
        scope_group_id="20001",
        requester_user_id="10001",
        remaining_attempts_before=2,
        locale="zh-CN",
        source_hint="group_scope",
    )

    result = await self_unban_service.submit_request(
        cast(Bot, SimpleNamespace()),
        prepared=prepared,
        reason="之前在群里刷屏的问题已经处理完毕，后续不会再犯",
    )

    assert "仍在全局黑名单中" in result.final_message
    assert (
        await self_unban_repo.count_consumed_attempts(
            subject_type="user",
            subject_id="10001",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_submit_group_request_switches_status_to_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        self_unban_core.group_repo,
        "get_group",
        AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(is_banned=True))),
    )
    monkeypatch.setattr(self_unban_core.group_repo, "update_status", AsyncMock())
    monkeypatch.setattr(
        self_unban_core,
        "resolve_group_name",
        AsyncMock(return_value="测试群"),
    )
    monkeypatch.setattr(
        self_unban_core,
        "deliver_admin_notification_plan",
        AsyncMock(return_value=()),
    )
    prepared = PreparedSelfUnbanRequest(
        kind="group",
        subject_type="group",
        subject_id="20001",
        scope_group_id="20001",
        requester_user_id="10002",
        remaining_attempts_before=1,
        locale="zh-CN",
        source_hint="group",
    )

    result = await self_unban_service.submit_request(
        cast(Bot, SimpleNamespace()),
        prepared=prepared,
        reason="原先的禁言风险已经处理完成，后续不会再出现相关行为",
    )

    assert "状态变更为未授权" in result.final_message
    assert (
        await self_unban_repo.count_consumed_attempts(
            subject_type="group",
            subject_id="20001",
        )
        == 1
    )
