from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot
import pytest

from src.lib.message_assets import is_message_asset_reuse_blocked
from src.services import message_asset_startup as startup_module


def _reset_startup_state() -> None:
    startup_module._startup_check_started = False
    startup_module._pending_decisions_by_prompt.clear()
    startup_module._cancel_timeout_task()


@pytest.mark.asyncio
async def test_run_startup_message_asset_check_prompts_and_blocks_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, str]] = []

    async def _deliver_message_plan(bot: object, **kwargs: Any) -> object:
        _ = bot
        sent.append((kwargs["target"].target_id, str(kwargs["plan"].messages[0])))
        return SimpleNamespace(
            results=[SimpleNamespace(message_id="9001")],
            wait_result=None,
            used_forward=False,
        )

    monkeypatch.setattr(startup_module.config, "SUPERUSERS", {"1"})
    monkeypatch.setattr(
        startup_module.message_asset_repo,
        "count_assets",
        AsyncMock(return_value=6),
    )
    monkeypatch.setattr(
        startup_module,
        "deliver_message_plan",
        _deliver_message_plan,
    )
    _reset_startup_state()

    await startup_module.run_startup_message_asset_check(
        cast(Bot, SimpleNamespace(self_id="99999"))
    )

    assert len(sent) == 1
    assert sent[0][0] == "1"
    assert "15 秒内未确认将自动清空" in sent[0][1]
    assert "当前缓存记录数: 6" in sent[0][1]
    assert is_message_asset_reuse_blocked() is True
    assert "9001" in startup_module._pending_decisions_by_prompt

    _reset_startup_state()


@pytest.mark.asyncio
async def test_handle_message_asset_startup_reply_keep_unblocks_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_all_assets = AsyncMock(return_value=5)
    monkeypatch.setattr(
        startup_module.message_asset_repo,
        "clear_all_assets",
        clear_all_assets,
    )
    _reset_startup_state()
    startup_module._pending_decisions_by_prompt["9001"] = (
        startup_module.PendingMessageAssetStartupDecision(
            prompt_message_id="9001",
            asset_count=5,
        )
    )
    startup_module.set_message_asset_reuse_blocked(
        True,
        reason="test_pending_confirmation",
    )

    result = await startup_module.handle_message_asset_startup_reply(
        cast(Bot, SimpleNamespace(self_id="99999")),
        reply_message_id="9001",
        text="保留",
    )

    assert result is not None
    assert "已保留现有消息缓存" in result
    assert is_message_asset_reuse_blocked() is False
    clear_all_assets.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_asset_startup_timeout_clears_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_all_assets = AsyncMock(return_value=7)
    monkeypatch.setattr(
        startup_module.message_asset_repo,
        "clear_all_assets",
        clear_all_assets,
    )
    _reset_startup_state()
    startup_module._pending_decisions_by_prompt["9001"] = (
        startup_module.PendingMessageAssetStartupDecision(
            prompt_message_id="9001",
            asset_count=7,
        )
    )
    startup_module.set_message_asset_reuse_blocked(
        True,
        reason="test_pending_timeout",
    )

    cleared = await startup_module.handle_message_asset_startup_timeout()

    assert cleared == 7
    assert is_message_asset_reuse_blocked() is False
    clear_all_assets.assert_awaited_once()
    assert not startup_module._pending_decisions_by_prompt
