from types import SimpleNamespace
from typing import Any

import pytest

from src.lib.interaction import (
    abort_if_revoke_signal,
    is_revoke_signal,
    reject_or_abort_on_error,
)
from tests.plugins.water.helpers import build_group_message_event


class _MatcherStopped(RuntimeError):
    pass


class _InteractionMatcher:
    def __init__(self) -> None:
        self.finished_with: Any | None = None
        self.rejected_with: list[Any] = []

    async def finish(self, message: Any | None = None) -> None:
        self.finished_with = message
        raise _MatcherStopped("finished")

    async def reject(self, prompt: Any | None = None, **kwargs: Any) -> None:
        self.rejected_with.append(prompt)
        raise _MatcherStopped("rejected")


def test_is_revoke_signal_detects_event_fields_and_segments() -> None:
    assert is_revoke_signal(SimpleNamespace(notice_type="group_recall"))
    assert is_revoke_signal(SimpleNamespace(sub_type="message_revoke"))
    assert is_revoke_signal(
        SimpleNamespace(
            message=[
                SimpleNamespace(type="text", data={"action": "recall_message"}),
            ]
        )
    )
    assert not is_revoke_signal(SimpleNamespace(notice_type="group_increase"))


async def test_abort_if_revoke_signal_finishes_with_cancel_message() -> None:
    matcher = _InteractionMatcher()
    event = build_group_message_event("recall 晚安")

    with pytest.raises(_MatcherStopped, match="finished"):
        await abort_if_revoke_signal(event, matcher, message="本次操作已被取消。")

    assert matcher.finished_with == "本次操作已被取消。"


async def test_reject_or_abort_on_error_finishes_after_three_errors() -> None:
    matcher = _InteractionMatcher()
    state: dict[str, Any] = {}

    with pytest.raises(_MatcherStopped, match="rejected"):
        await reject_or_abort_on_error(matcher, state, "输入错误")
    with pytest.raises(_MatcherStopped, match="rejected"):
        await reject_or_abort_on_error(matcher, state, "输入错误")
    with pytest.raises(_MatcherStopped, match="finished"):
        await reject_or_abort_on_error(matcher, state, "输入错误")

    assert matcher.rejected_with == ["输入错误", "输入错误"]
    assert matcher.finished_with == "连续输入错误 3 次，本次操作已被取消。"
