"""Shared helpers for cancellable interactive matchers."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Protocol

REVOKE_MARKERS = ("revoke", "recall")
DEFAULT_ABORT_MESSAGE = "本次操作已被取消。"
DEFAULT_TOO_MANY_ERRORS_MESSAGE = "连续输入错误 3 次，本次操作已被取消。"
INTERACTION_ERROR_COUNT_KEY = "__interaction_error_count__"


class SupportsFinish(Protocol):
    async def finish(self, message: Any | None = None) -> Any: ...


class SupportsReject(Protocol):
    async def reject(self, prompt: Any | None = None, **kwargs: Any) -> Any: ...


class SupportsInteractiveAbort(SupportsFinish, SupportsReject, Protocol):
    pass


def _contains_revoke_marker(value: object) -> bool:
    text = str(value).casefold()
    return any(marker in text for marker in REVOKE_MARKERS)


def is_revoke_signal(event: object) -> bool:
    event_name = event.__class__.__name__
    if _contains_revoke_marker(event_name):
        return True

    for attr in ("post_type", "notice_type", "sub_type", "event_name", "type"):
        value = getattr(event, attr, "")
        if value and _contains_revoke_marker(value):
            return True

    raw_message = getattr(event, "raw_message", "")
    if raw_message and _contains_revoke_marker(raw_message):
        return True

    message = getattr(event, "message", None)
    if message is None:
        return False
    for segment in message:
        if _contains_revoke_marker(getattr(segment, "type", "")):
            return True
        data = getattr(segment, "data", {})
        if isinstance(data, dict) and any(
            _contains_revoke_marker(key) or _contains_revoke_marker(value)
            for key, value in data.items()
        ):
            return True
    return False


async def abort_if_revoke_signal(
    event: object,
    matcher: SupportsFinish,
    *,
    message: Any | None = DEFAULT_ABORT_MESSAGE,
) -> None:
    if not is_revoke_signal(event):
        return
    await matcher.finish(message)


def clear_interaction_errors(
    state: MutableMapping[str, Any],
    *,
    key: str = INTERACTION_ERROR_COUNT_KEY,
) -> None:
    state.pop(key, None)


def record_interaction_error(
    state: MutableMapping[str, Any],
    *,
    key: str = INTERACTION_ERROR_COUNT_KEY,
) -> int:
    count = int(state.get(key, 0)) + 1
    state[key] = count
    return count


async def reject_or_abort_on_error(
    matcher: SupportsInteractiveAbort,
    state: MutableMapping[str, Any],
    error_message: Any,
    *,
    max_errors: int = 3,
    abort_message: Any = DEFAULT_TOO_MANY_ERRORS_MESSAGE,
    key: str = INTERACTION_ERROR_COUNT_KEY,
) -> None:
    count = record_interaction_error(state, key=key)
    if count >= max_errors:
        await matcher.finish(abort_message)
        return
    await matcher.reject(error_message)
