from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.config import config
from src.plugins.wordbank.handlers.approval import (
    record_submission_approval_message,
    send_pending_approval_notice,
)
from src.plugins.wordbank.services.core import WordbankAddResult, WordbankService
from tests.plugins.water.helpers import build_group_message_event


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_private_msg(self, *, user_id: int, message: str) -> dict[str, int]:
        self.sent.append((user_id, message))
        return {"message_id": 77000 + len(self.sent)}


async def test_send_pending_approval_notice_records_superuser_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SUPERUSERS", {"10002"})
    bot = FakeBot()
    record_approval_message = AsyncMock()
    service = cast(
        WordbankService,
        SimpleNamespace(record_approval_message=record_approval_message),
    )
    event = build_group_message_event("#wordbank add 晚安 => 做个好梦")
    result = WordbankAddResult(
        entry_id=12,
        trigger_text="晚安",
        response_text="做个好梦",
        trigger_mode="contains",
        scope="current_group",
        probability=1.0,
        weight=3,
    )

    await send_pending_approval_notice(
        cast(Any, bot),
        service,
        event=event,
        result=result,
        locale="zh-CN",
    )

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 10002
    assert "新增词条待审核" in bot.sent[0][1]
    assert "回复 y / approve / 通过" in bot.sent[0][1]
    record_approval_message.assert_awaited_once_with(
        message_id="77001",
        entry_id=12,
        group_id="20001",
        user_id="10001",
        source_message_id="1",
        message_type="approval",
    )


async def test_record_submission_approval_message_records_sent_reply() -> None:
    record_approval_message = AsyncMock()
    service = cast(
        WordbankService,
        SimpleNamespace(record_approval_message=record_approval_message),
    )
    event = build_group_message_event("#study 晚安 => 做个好梦", message_id=123)
    result = WordbankAddResult(
        entry_id=12,
        trigger_text="晚安",
        response_text="做个好梦",
        trigger_mode="contains",
        scope="current_group",
        probability=1.0,
        weight=3,
    )

    await record_submission_approval_message(
        service,
        event=event,
        result=result,
        send_result={"message_id": 88001},
    )

    record_approval_message.assert_awaited_once_with(
        message_id="88001",
        entry_id=12,
        group_id="20001",
        user_id="10001",
        source_message_id="123",
        message_type="submission",
    )
