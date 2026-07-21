"""Startup confirmation flow for message asset cache reuse."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from nonebot.adapters.onebot.v11 import Bot

from src.config import config
from src.lib.message_assets import (
    message_asset_repo,
    set_message_asset_reuse_blocked,
)
from src.lib.message_delivery import DeliveryResult, DeliveryTarget
from src.lib.message_plan import DeliveryPlan, deliver_message_plan
from src.logger import logger

MESSAGE_ASSET_CONFIRM_TIMEOUT_SECONDS = 15


@dataclass(slots=True, frozen=True)
class PendingMessageAssetStartupDecision:
    prompt_message_id: str
    asset_count: int


_startup_check_started = False
_startup_check_lock = asyncio.Lock()
_pending_decisions_by_prompt: dict[str, PendingMessageAssetStartupDecision] = {}
_timeout_task: asyncio.Task[None] | None = None


def is_message_asset_startup_reply_text(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {
        "y",
        "yes",
        "clear",
        "n",
        "no",
        "keep",
        "清理",
        "清空",
        "保留",
        "跳过",
    }


def resolve_message_asset_startup_reply_decision(text: str) -> bool | None:
    normalized = text.strip().lower()
    if normalized in {"y", "yes", "clear", "清理", "清空"}:
        return True
    if normalized in {"n", "no", "keep", "保留", "跳过"}:
        return False
    return None


async def run_startup_message_asset_check(bot: Bot) -> None:
    global _startup_check_started
    if _startup_check_started:
        return
    async with _startup_check_lock:
        if _startup_check_started:
            return
        _startup_check_started = True

        asset_count = await message_asset_repo.count_assets()
        if asset_count <= 0:
            set_message_asset_reuse_blocked(False, reason="startup_cache_empty")
            return

        set_message_asset_reuse_blocked(
            True,
            reason="startup_message_asset_confirmation_pending",
        )
        await _notify_superusers_for_message_asset_cache(bot, asset_count=asset_count)
        _schedule_message_asset_timeout_clear(asset_count=asset_count)


async def handle_message_asset_startup_reply(
    bot: Bot,
    *,
    reply_message_id: str,
    text: str,
) -> str | None:
    _ = bot
    decision = resolve_message_asset_startup_reply_decision(text)
    if decision is None:
        return None
    if reply_message_id not in _pending_decisions_by_prompt:
        return None
    if _consume_pending_message_asset_decisions() is None:
        return None
    _cancel_timeout_task()
    if decision:
        cleared = await message_asset_repo.clear_all_assets()
        set_message_asset_reuse_blocked(False, reason="startup_message_asset_cleared")
        return f"已清空消息缓存。本次共删除 {cleared} 条消息资产记录。"
    set_message_asset_reuse_blocked(False, reason="startup_message_asset_kept")
    return (
        "已保留现有消息缓存。"
        "注意：若服务端重启后历史 msgid 已失效，"
        "后续复用命中时仍可能自动回退并标记失效。"
    )


async def handle_message_asset_startup_timeout() -> int:
    if _consume_pending_message_asset_decisions() is None:
        return 0
    cleared = await message_asset_repo.clear_all_assets()
    set_message_asset_reuse_blocked(False, reason="startup_message_asset_timeout_clear")
    logger.warning(
        "[MessageAsset] startup confirmation timed out "
        f"timeout_s={MESSAGE_ASSET_CONFIRM_TIMEOUT_SECONDS} cleared={cleared}"
    )
    return cleared


async def _notify_superusers_for_message_asset_cache(
    bot: Bot,
    *,
    asset_count: int,
) -> None:
    prompt = (
        "检测到上一次进程留下的消息缓存。\n"
        f"当前缓存记录数: {asset_count}\n"
        "服务端重启后历史 msgid 可能已经失效。\n"
        f"回复 y / clear / 清空 可立即清理缓存；回复 n / keep / 保留 则暂时保留。"
        f"{MESSAGE_ASSET_CONFIRM_TIMEOUT_SECONDS} 秒内未确认将自动清空。"
    )
    for superuser_id in config.SUPERUSERS:
        try:
            plan_result = await deliver_message_plan(
                bot,
                target=DeliveryTarget(kind="private", target_id=str(superuser_id)),
                plan=DeliveryPlan(
                    messages=(prompt,),
                    source_kind="message_asset_startup",
                    allow_asset_reuse=False,
                    force_forward=False,
                ),
            )
        except Exception as exc:
            logger.warning(
                f"[MessageAsset] failed to notify superuser {superuser_id}: {exc}"
            )
            continue
        if not plan_result.results:
            continue
        _register_pending_message_asset_prompt(
            plan_result.results[0],
            asset_count=asset_count,
        )


def _register_pending_message_asset_prompt(
    send_result: DeliveryResult,
    *,
    asset_count: int,
) -> None:
    if not send_result.message_id:
        return
    _pending_decisions_by_prompt[send_result.message_id] = (
        PendingMessageAssetStartupDecision(
            prompt_message_id=send_result.message_id,
            asset_count=asset_count,
        )
    )


def _schedule_message_asset_timeout_clear(*, asset_count: int) -> None:
    global _timeout_task
    _cancel_timeout_task()
    logger.info(
        "[MessageAsset] startup confirmation scheduled "
        f"timeout_s={MESSAGE_ASSET_CONFIRM_TIMEOUT_SECONDS} asset_count={asset_count}"
    )
    _timeout_task = asyncio.create_task(_run_message_asset_timeout_clear())


async def _run_message_asset_timeout_clear() -> None:
    try:
        await asyncio.sleep(MESSAGE_ASSET_CONFIRM_TIMEOUT_SECONDS)
        await handle_message_asset_startup_timeout()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception(f"[MessageAsset] startup timeout clear failed: {exc}")


def _cancel_timeout_task() -> None:
    global _timeout_task
    task = _timeout_task
    _timeout_task = None
    if task is not None and not task.done():
        task.cancel()


def _consume_pending_message_asset_decisions() -> (
    PendingMessageAssetStartupDecision | None
):
    if not _pending_decisions_by_prompt:
        return None
    first_pending = next(iter(_pending_decisions_by_prompt.values()))
    _pending_decisions_by_prompt.clear()
    return first_pending


__all__ = [
    "MESSAGE_ASSET_CONFIRM_TIMEOUT_SECONDS",
    "handle_message_asset_startup_reply",
    "handle_message_asset_startup_timeout",
    "is_message_asset_startup_reply_text",
    "resolve_message_asset_startup_reply_decision",
    "run_startup_message_asset_check",
]
