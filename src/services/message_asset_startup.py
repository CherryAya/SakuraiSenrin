"""Blocking startup confirmation for message asset cache."""

from __future__ import annotations

from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.message_assets import (
    message_asset_repo,
    set_message_asset_reuse_blocked,
)
from src.lib.terminal_prompt import ask_user_yes_no_with_timeout
from src.logger import logger

MESSAGE_ASSET_CONFIRM_TIMEOUT_SECONDS = 15
_startup_check_completed = False


async def run_startup_message_asset_check() -> None:
    global _startup_check_completed
    if _startup_check_completed:
        return

    asset_count = await message_asset_repo.count_assets()
    if asset_count <= 0:
        set_message_asset_reuse_blocked(False, reason="startup_cache_empty")
        _startup_check_completed = True
        return

    set_message_asset_reuse_blocked(
        True,
        reason="startup_terminal_confirmation_pending",
    )
    locale = await resolve_locale(None)
    keep_cache = ask_user_yes_no_with_timeout(
        tr(
            locale,
            "startup.asset_cache.prompt",
            count=asset_count,
        ),
        timeout=MESSAGE_ASSET_CONFIRM_TIMEOUT_SECONDS,
        default=False,
        default_label=tr(locale, "startup.asset_cache.clear_label"),
        locale=locale,
    )
    if keep_cache:
        set_message_asset_reuse_blocked(False, reason="startup_message_asset_kept")
        logger.warning(
            "[MessageAsset] startup cache kept by terminal confirmation "
            f"asset_count={asset_count}"
        )
        _startup_check_completed = True
        return

    cleared = await message_asset_repo.clear_all_assets()
    set_message_asset_reuse_blocked(False, reason="startup_message_asset_cleared")
    logger.warning(
        "[MessageAsset] startup cache cleared by terminal confirmation "
        f"asset_count={asset_count} cleared={cleared}"
    )
    _startup_check_completed = True


__all__ = [
    "MESSAGE_ASSET_CONFIRM_TIMEOUT_SECONDS",
    "run_startup_message_asset_check",
]
