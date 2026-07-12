from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.lib.cooldown import (
    CooldownIsolateLevel,
    MemoryCooldown,
)


def test_memory_cooldown_user_level_blocks_second_acquire() -> None:
    store = MemoryCooldown(30, isolate_level=CooldownIsolateLevel.USER)

    first = store.acquire(user_id="10001")
    second = store.acquire(user_id="10001")

    assert first.acquired is True
    assert first.remaining_seconds == 0
    assert second.acquired is False
    assert second.remaining_seconds >= 1


def test_memory_cooldown_group_level_shares_same_bucket() -> None:
    store = MemoryCooldown(30, isolate_level=CooldownIsolateLevel.GROUP)

    first = store.acquire(group_id="20001", user_id="10001")
    second = store.acquire(group_id="20001", user_id="10002")

    assert first.acquired is True
    assert second.acquired is False


def test_memory_cooldown_group_user_level_isolated_per_member() -> None:
    store = MemoryCooldown(30, isolate_level=CooldownIsolateLevel.GROUP_USER)

    first = store.acquire(group_id="20001", user_id="10001")
    second = store.acquire(group_id="20001", user_id="10002")

    assert first.acquired is True
    assert second.acquired is True


def test_memory_cooldown_skips_when_debug_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib import cooldown as cooldown_module

    store = MemoryCooldown(30, isolate_level=CooldownIsolateLevel.USER)
    monkeypatch.setattr(cooldown_module, "config", SimpleNamespace(DEBUG=True))

    first = store.acquire(user_id="10001")
    second = store.acquire(user_id="10001")

    assert first.acquired is True
    assert second.acquired is True
