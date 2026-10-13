"""多步骤引导式会话（guided session）的共享状态内核。

各插件给出 state 键前缀与步骤提示文案，公共部分收敛到本模块。
"""

from __future__ import annotations

from collections.abc import Mapping

from src.lib.i18n.types import LocaleCode, normalize_locale_code
from src.lib.interaction import clear_interaction_errors
from src.lib.interactive_recall import (
    INTERACTION_ROOT_MESSAGE_ID,
    INTERACTION_SESSION_KEY,
    cancel_state_resources,
    get_interaction_session_key,
)

__all__ = [
    "cancel_guided_state_resources",
    "copy_guided_state_snapshot",
    "guided_locale",
    "state_keys_with_prefix",
    "state_value",
]


def state_value[T](
    state: Mapping[str, object],
    key: str,
    expected: type[T],
) -> T | None:
    """按声明类型收窄读取 state；recall 快照重建后，类型校验走本函数。"""
    value = state.get(key)
    return value if isinstance(value, expected) else None


def state_keys_with_prefix(state: Mapping[str, object], prefix: str) -> list[str]:
    """列出属于当前流程的 state 键，供 debug 日志输出。"""
    return sorted(str(key) for key in state.keys() if str(key).startswith(prefix))


def guided_locale(state: Mapping[str, object], *, locale_key: str) -> LocaleCode:
    """读取会话语言，缺失时回落到默认语言。"""
    return normalize_locale_code(state.get(locale_key))


def copy_guided_state_snapshot(
    state: Mapping[str, object],
    *,
    locale_key: str,
    source_event_key: str,
    keep_keys: tuple[str, ...],
) -> dict[str, object]:
    """构造 recall 检查点快照：会话身份 + 调用方 keep_keys。"""
    snapshot: dict[str, object] = {}
    for key, value in state.items():
        if key.startswith("__nonebug"):
            snapshot[key] = value
    session_key = get_interaction_session_key(state)
    if session_key is not None:
        snapshot[INTERACTION_SESSION_KEY] = session_key
    if locale_key in state:
        snapshot[locale_key] = state[locale_key]
    if INTERACTION_ROOT_MESSAGE_ID in state:
        snapshot[INTERACTION_ROOT_MESSAGE_ID] = state[INTERACTION_ROOT_MESSAGE_ID]
    if source_event_key in state:
        snapshot[source_event_key] = state[source_event_key]
    for key in keep_keys:
        if key in state:
            snapshot[key] = state[key]
    clear_interaction_errors(snapshot)
    return snapshot


async def cancel_guided_state_resources(
    state: Mapping[str, object],
    cleanup_keys: tuple[str, ...],
) -> None:
    """撤回时清理流程残留状态（转发缓存、待处理标记等）。"""
    await cancel_state_resources(
        state,
        cleanup_keys,
        cleaners={},
    )
