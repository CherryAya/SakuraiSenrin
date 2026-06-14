"""Shared in-memory cooldown helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum, auto
from inspect import isawaitable
from time import monotonic
from typing import Any

from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import Depends

from src.config import config

CooldownPrompt = str | Any
CooldownPromptBuilder = Callable[
    [MessageEvent, int],
    CooldownPrompt | Awaitable[CooldownPrompt],
]
CooldownBypassChecker = Callable[
    [MessageEvent],
    bool | Awaitable[bool],
]


class CooldownIsolateLevel(IntEnum):
    """Cooldown isolation granularity."""

    GLOBAL = auto()
    GROUP = auto()
    USER = auto()
    GROUP_USER = auto()


@dataclass(frozen=True)
class CooldownAcquireResult:
    acquired: bool
    remaining_seconds: int


class MemoryCooldown:
    def __init__(
        self,
        cooldown: float,
        *,
        isolate_level: CooldownIsolateLevel = CooldownIsolateLevel.USER,
    ) -> None:
        self.cooldown = cooldown
        self.isolate_level = isolate_level
        self._expires_at: dict[str, float] = {}

    def clear(self) -> None:
        self._expires_at.clear()

    def acquire(
        self,
        *,
        user_id: str | None = None,
        group_id: str | None = None,
    ) -> CooldownAcquireResult:
        if getattr(config, "DEBUG", False):
            return CooldownAcquireResult(acquired=True, remaining_seconds=0)

        key = self.build_key(user_id=user_id, group_id=group_id)
        if not key:
            return CooldownAcquireResult(acquired=True, remaining_seconds=0)

        now = monotonic()
        expires_at = self._expires_at.get(key, 0.0)
        if expires_at > now:
            return CooldownAcquireResult(
                acquired=False,
                remaining_seconds=max(1, int(expires_at - now)),
            )

        self._expires_at[key] = now + self.cooldown
        return CooldownAcquireResult(acquired=True, remaining_seconds=0)

    def acquire_for_event(self, event: MessageEvent) -> CooldownAcquireResult:
        group_id = getattr(event, "group_id", None)
        try:
            user_id = event.get_user_id()
        except Exception:
            user_id = None
        return self.acquire(
            user_id=user_id,
            group_id=str(group_id) if group_id is not None else None,
        )

    def build_key(
        self,
        *,
        user_id: str | None = None,
        group_id: str | None = None,
    ) -> str | None:
        if self.isolate_level is CooldownIsolateLevel.GROUP:
            return group_id or user_id
        if self.isolate_level is CooldownIsolateLevel.USER:
            return user_id
        if self.isolate_level is CooldownIsolateLevel.GROUP_USER:
            return f"{group_id}_{user_id}" if group_id and user_id else user_id
        return CooldownIsolateLevel.GLOBAL.name


async def _maybe_await(value: Any) -> Any:
    if isawaitable(value):
        return await value
    return value


def build_cooldown_dependency(
    store: MemoryCooldown,
    *,
    prompt_builder: CooldownPromptBuilder,
    bypass_checker: CooldownBypassChecker | None = None,
    skip_when_matcher_has_target: bool = True,
) -> Any:
    async def dependency(matcher: Matcher, event: MessageEvent) -> None:
        if skip_when_matcher_has_target and matcher.get_target():
            return
        if bypass_checker is not None and await _maybe_await(bypass_checker(event)):
            return

        result = store.acquire_for_event(event)
        if result.acquired:
            return

        await matcher.finish(
            await _maybe_await(
                prompt_builder(event, result.remaining_seconds),
            )
        )

    return Depends(dependency)
