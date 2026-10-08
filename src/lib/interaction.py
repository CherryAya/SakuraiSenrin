"""Shared helpers for cancellable interactive matchers."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Protocol, cast

from nonebot.adapters import MessageTemplate
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.matcher import Matcher

from src.lib.i18n.runtime import tr
from src.lib.message_plan import (
    MessagePlanInput,
    finish_with_message,
    reject_with_message,
)
from src.lib.types import is_object_mapping

REVOKE_MARKERS = ("revoke", "recall", "exit")
DEFAULT_ABORT_MESSAGE = tr("zh-CN", "interaction.cancelled")
DEFAULT_TOO_MANY_ERRORS_MESSAGE = tr("zh-CN", "interaction.too_many_errors")
INTERACTION_ERROR_COUNT_KEY = "__interaction_error_count__"


class SupportsFinish(Protocol):
    """nonebot Matcher 的 finish 能力面。

    参数类型必须与 ``nonebot.internal.matcher.Matcher.finish`` 的真实签名一致：
    ``str | Message | MessageSegment | MessageTemplate[str] | None``。
    本协议原先写成 ``MessagePlanInput | None``，其中的 ``MessagePlanEntry``
    并不被 finish 接受，导致真实 Matcher 无法满足该 Protocol（协议比实现更宽
    时，实现方反而「不兼容」）。这里按真实契约收窄，Protocol 才成立。
    """

    async def finish(
        self,
        message: str | Message | MessageSegment | MessageTemplate[str] | None = None,
        **kwargs: object,
    ) -> object: ...


class SupportsReject(Protocol):
    """nonebot Matcher 的 reject 能力面，签名同样对齐真实实现。

    与 SupportsFinish 同理：原先写 ``MessagePlanInput | None``，其中
    ``MessagePlanEntry`` 不被 reject 接受，导致真实 Matcher 反而不满足协议。
    """

    async def reject(
        self,
        prompt: str | Message | MessageSegment | MessageTemplate[str] | None = None,
        **kwargs: object,
    ) -> object: ...


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
        if is_object_mapping(data) and any(
            _contains_revoke_marker(key) or _contains_revoke_marker(value)
            for key, value in data.items()
        ):
            return True
    return False


async def abort_if_revoke_signal(
    event: object,
    matcher: SupportsFinish,
    *,
    message: MessagePlanInput | None = DEFAULT_ABORT_MESSAGE,
) -> None:
    if not is_revoke_signal(event):
        return
    if message is None:
        await matcher.finish()
        return
    await finish_with_message(
        None,
        cast(Matcher, matcher),
        message=message,
        source_kind="interaction_abort",
    )


def clear_interaction_errors(
    state: MutableMapping[str, object],
    *,
    key: str = INTERACTION_ERROR_COUNT_KEY,
) -> None:
    state.pop(key, None)


def record_interaction_error(
    state: MutableMapping[str, object],
    *,
    key: str = INTERACTION_ERROR_COUNT_KEY,
) -> int:
    raw_count = state.get(key, 0)
    current = (
        raw_count
        if isinstance(raw_count, int) and not isinstance(raw_count, bool)
        else 0
    )
    count = current + 1
    state[key] = count
    return count


async def reject_or_abort_on_error(
    matcher: SupportsInteractiveAbort,
    state: MutableMapping[str, object],
    error_message: MessagePlanInput,
    *,
    max_errors: int = 3,
    abort_message: MessagePlanInput | None = DEFAULT_TOO_MANY_ERRORS_MESSAGE,
    key: str = INTERACTION_ERROR_COUNT_KEY,
) -> None:
    count = record_interaction_error(state, key=key)
    if count >= max_errors:
        if abort_message is None:
            await matcher.finish()
            return
        await finish_with_message(
            None,
            cast(Matcher, matcher),
            message=abort_message,
            source_kind="interaction_abort",
        )
        return
    await reject_with_message(cast(Matcher, matcher), message=error_message)
