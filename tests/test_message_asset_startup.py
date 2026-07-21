from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.lib.message_assets import is_message_asset_reuse_blocked
from src.lib.terminal_prompt import _parse_yes_no
from src.services import message_asset_startup as startup_module


def _reset_startup_state() -> None:
    startup_module._startup_check_completed = False


def test_terminal_prompt_empty_input_uses_default_value() -> None:
    assert _parse_yes_no("", default=False) is False
    assert _parse_yes_no("", default=True) is True


@pytest.mark.asyncio
async def test_run_startup_message_asset_check_keeps_cache_after_terminal_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_all_assets = AsyncMock(return_value=6)
    monkeypatch.setattr(
        startup_module.message_asset_repo,
        "count_assets",
        AsyncMock(return_value=6),
    )
    monkeypatch.setattr(
        startup_module.message_asset_repo,
        "clear_all_assets",
        clear_all_assets,
    )
    monkeypatch.setattr(
        startup_module,
        "ask_user_yes_no_with_timeout",
        lambda prompt, *, timeout, default, default_label: True,
    )
    _reset_startup_state()

    await startup_module.run_startup_message_asset_check()

    assert is_message_asset_reuse_blocked() is False
    clear_all_assets.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_startup_message_asset_check_clears_cache_on_terminal_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_all_assets = AsyncMock(return_value=5)
    monkeypatch.setattr(
        startup_module.message_asset_repo,
        "count_assets",
        AsyncMock(return_value=5),
    )
    monkeypatch.setattr(
        startup_module.message_asset_repo,
        "clear_all_assets",
        clear_all_assets,
    )
    monkeypatch.setattr(
        startup_module,
        "ask_user_yes_no_with_timeout",
        lambda prompt, *, timeout, default, default_label: False,
    )
    _reset_startup_state()

    await startup_module.run_startup_message_asset_check()

    assert is_message_asset_reuse_blocked() is False
    clear_all_assets.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_startup_message_asset_check_skips_when_cache_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_all_assets = AsyncMock()
    monkeypatch.setattr(
        startup_module.message_asset_repo,
        "count_assets",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        startup_module.message_asset_repo,
        "clear_all_assets",
        clear_all_assets,
    )
    prompt_called = False

    def _prompt(
        prompt: str, *, timeout: int, default: bool, default_label: str | None
    ) -> bool:
        _ = (prompt, timeout, default, default_label)
        nonlocal prompt_called
        prompt_called = True
        return False

    monkeypatch.setattr(startup_module, "ask_user_yes_no_with_timeout", _prompt)
    _reset_startup_state()

    await startup_module.run_startup_message_asset_check()

    assert is_message_asset_reuse_blocked() is False
    assert prompt_called is False
    clear_all_assets.assert_not_awaited()
