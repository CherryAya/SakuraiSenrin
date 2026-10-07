"""多步骤引导式会话（guided session）的共享状态内核。

NoneBot 的 ``T_State`` 只是一个裸 dict，插件靠约定在其中存放流程状态。多个插件
（``wordbank`` 引导添加 / ``study`` 学习词库）实现了同一套机制：按步骤推进、每步
确认后写入 state、通过 recall 检查点在撤回时重建会话。两者的差异仅在于 **state
键前缀** 与 **各步骤的提示文案**，因此把与前缀无关的部分收敛到本模块。

本模块只依赖 nonebot 与 src.lib，不反向依赖任何插件，故 MessageShape 等插件类型
由调用方传入（见 ``state_value``）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
    state: Mapping[str, Any],
    key: str,
    expected: type[T],
) -> T | None:
    """按声明类型收窄读取 state。

    T_State 经由 recall 快照（见 ``src.lib.interactive_recall.rebuild_temp_matcher``）
    在 matcher 之间重建，键存在不等于值类型可信，因此非 bool 状态一律走本函数收窄。
    """
    value = state.get(key)
    return value if isinstance(value, expected) else None


def state_keys_with_prefix(state: Mapping[str, Any], prefix: str) -> list[str]:
    """列出属于当前流程的 state 键，供 debug 日志输出。"""
    return sorted(str(key) for key in state.keys() if str(key).startswith(prefix))


def guided_locale(state: Mapping[str, Any], *, locale_key: str) -> LocaleCode:
    """读取会话语言，不受支持时回落到默认语言。"""
    return normalize_locale_code(state.get(locale_key))


def copy_guided_state_snapshot(
    state: Mapping[str, Any],
    *,
    locale_key: str,
    source_event_key: str,
    keep_keys: tuple[str, ...],
) -> dict[str, Any]:
    """构造 recall 检查点快照：只保留会话身份与已确认的流程状态。

    ``keep_keys`` 由调用方给出，因为「走到第几步该保留哪些键」属于各插件自身的
    流程语义；本函数只负责搬运，不做判断。
    """
    snapshot: dict[str, Any] = {}
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
    state: Mapping[str, Any],
    cleanup_keys: tuple[str, ...],
) -> None:
    """撤回时清理流程残留状态（转发缓存、待处理标记等）。"""
    await cancel_state_resources(
        state,
        cleanup_keys,
        cleaners={},
    )
