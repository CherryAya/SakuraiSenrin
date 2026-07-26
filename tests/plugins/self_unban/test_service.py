from __future__ import annotations

from pathlib import Path
import sys
import types
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot
import pytest

from src.database.core.consts import GroupStatus
from src.lib.consts import GLOBAL_GROUP_FLAG, PERMANENT_BAN_FLAG

_ROOT = Path(__file__).resolve().parents[3]
_SELF_UNBAN_PKG_PATH = _ROOT / "src" / "plugins" / "self_unban"
if "src.plugins.self_unban" not in sys.modules:
    pkg = types.ModuleType("src.plugins.self_unban")
    pkg.__path__ = [str(_SELF_UNBAN_PKG_PATH)]  # type: ignore[attr-defined]
    sys.modules["src.plugins.self_unban"] = pkg

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
async def test_prepare_selection_session_respects_shared_user_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        self_unban_core.blacklist_repo,
        "get_blacklist",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        self_unban_core.member_repo,
        "get_admin_member_by_uid",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    group_id="20001",
                    group=SimpleNamespace(group_name="测试群A"),
                ),
                SimpleNamespace(
                    group_id="20002",
                    group=SimpleNamespace(group_name="测试群B"),
                ),
            ]
        ),
    )

    async def _get_group(group_id: str) -> object:
        return SimpleNamespace(
            status=GroupStatus.BANNED,
            display_name=f"群{group_id}",
        )

    monkeypatch.setattr(self_unban_core.group_repo, "get_group", _get_group)

    await self_unban_repo.create_attempt(
        subject_type="user",
        subject_id="10001",
        scope_group_id=GLOBAL_GROUP_FLAG,
        requester_user_id="10001",
        reason="历史成功申请",
        result="approved",
        consumes_quota=True,
    )
    for _ in range(MAX_SELF_UNBAN_ATTEMPTS):
        await self_unban_repo.create_attempt(
            subject_type="group",
            subject_id="20001",
            scope_group_id="20001",
            requester_user_id="99999",
            reason="群历史成功申请",
            result="approved_group_quota",
            consumes_quota=True,
        )

    session = await self_unban_service.prepare_selection_session(
        requester_user_id="10001",
        locale="zh-CN",
        current_group_id=None,
    )

    assert not isinstance(session, str)
    assert session.user_candidate is None
    assert len(session.group_candidates) == 1
    assert session.group_candidates[0].group_id == "20002"
    assert session.group_candidates[0].prepared.user_remaining_attempts_before == 1
    assert session.group_candidates[0].prepared.group_remaining_attempts_before == 2


@pytest.mark.asyncio
async def test_submit_group_request_consumes_user_and_group_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        self_unban_core.group_repo,
        "get_group",
        AsyncMock(return_value=SimpleNamespace(status=GroupStatus.BANNED)),
    )
    monkeypatch.setattr(
        self_unban_core.group_repo,
        "restore_pre_ban_status",
        AsyncMock(
            return_value=SimpleNamespace(
                restored_status=GroupStatus.AUTHORIZED,
                used_fallback=False,
            )
        ),
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
        requester_user_id="10001",
        user_remaining_attempts_before=2,
        group_remaining_attempts_before=2,
        locale="zh-CN",
        source_hint="managed_group",
        target_group_name="测试群",
    )

    result = await self_unban_service.submit_request(
        cast(Bot, SimpleNamespace()),
        prepared=prepared,
        reason="原先的风控问题已经处理完成，后续不会再出现禁言机器人行为",
    )

    assert "状态恢复为已授权" in result.final_message
    assert await self_unban_service.count_user_consumed_attempts("10001") == 1
    assert await self_unban_service.count_group_consumed_attempts("20001") == 1


@pytest.mark.asyncio
async def test_submit_group_request_rejects_after_shared_user_quota_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for scope_group_id in ("20001", "20002"):
        await self_unban_repo.create_attempt(
            subject_type="group",
            subject_id=scope_group_id,
            scope_group_id=scope_group_id,
            requester_user_id="10001",
            reason="旧版本群解封成功",
            result="approved",
            consumes_quota=True,
        )

    prepared = PreparedSelfUnbanRequest(
        kind="group",
        subject_type="group",
        subject_id="20003",
        scope_group_id="20003",
        requester_user_id="10001",
        user_remaining_attempts_before=0,
        group_remaining_attempts_before=2,
        locale="zh-CN",
        source_hint="managed_group",
        target_group_name="第三群",
    )
    result = await self_unban_service.submit_request(
        cast(Bot, SimpleNamespace()),
        prepared=prepared,
        reason="该群问题已经处理完毕，后续会严格遵守相关规则",
    )

    assert "联系超管" in result.final_message
    assert await self_unban_service.count_user_consumed_attempts("10001") == 2


@pytest.mark.asyncio
async def test_submit_request_retries_on_short_reason() -> None:
    prepared = PreparedSelfUnbanRequest(
        kind="user",
        subject_type="user",
        subject_id="10001",
        scope_group_id=GLOBAL_GROUP_FLAG,
        requester_user_id="10001",
        user_remaining_attempts_before=2,
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
    assert await self_unban_service.count_user_consumed_attempts("10001") == 0


@pytest.mark.asyncio
async def test_prepare_selection_session_rejects_when_user_quota_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        self_unban_core.blacklist_repo,
        "get_blacklist",
        AsyncMock(return_value=SimpleNamespace(expiry=PERMANENT_BAN_FLAG)),
    )
    monkeypatch.setattr(
        self_unban_core.member_repo,
        "get_admin_member_by_uid",
        AsyncMock(return_value=[]),
    )
    for _ in range(MAX_SELF_UNBAN_ATTEMPTS):
        await self_unban_repo.create_attempt(
            subject_type="user",
            subject_id="10001",
            scope_group_id=GLOBAL_GROUP_FLAG,
            requester_user_id="10001",
            reason="历史成功申请",
            result="approved",
            consumes_quota=True,
        )

    result = await self_unban_service.prepare_selection_session(
        requester_user_id="10001",
        locale="zh-CN",
        current_group_id=None,
    )

    assert isinstance(result, str)
    assert "联系超管" in result
