from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from nonebot.exception import MockApiException

from src.lib.message_api_hooks import delivery_send_handler, intercept_message_send_api
from src.lib.message_assets import message_asset_repo
from src.lib.messages import text_message
from tests.plugins.water.helpers import build_group_message_event


@pytest.mark.asyncio
async def test_delivery_send_handler_routes_group_reply_via_message_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_api = AsyncMock(return_value={"message_id": 1})
    bot = cast(Any, SimpleNamespace(self_id="99999", call_api=call_api))
    event = build_group_message_event("hello", message_id=42)
    monkeypatch.setattr(
        message_asset_repo,
        "get_asset",
        AsyncMock(return_value=None),
    )

    result = await delivery_send_handler(
        bot,
        event,
        text_message("world"),
        reply_message=True,
    )

    assert result == {"message_id": "1"}
    assert call_api.await_args.args == ("send_group_msg",)
    sent_message = call_api.await_args.kwargs["message"]
    assert sent_message[0].type == "reply"
    assert str(sent_message).endswith("world")


@pytest.mark.asyncio
async def test_intercept_message_send_api_raises_mock_with_delivery_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_api = AsyncMock(return_value={"message_id": 9})
    bot = cast(Any, SimpleNamespace(self_id="99999", call_api=call_api))
    monkeypatch.setattr(
        message_asset_repo,
        "get_asset",
        AsyncMock(return_value=None),
    )

    with pytest.raises(MockApiException) as exc_info:
        await intercept_message_send_api(
            bot,
            "send_group_msg",
            {
                "group_id": 20001,
                "message": text_message("hook-message"),
            },
        )

    assert exc_info.value.result == {"message_id": "9"}
    assert call_api.await_args.args == ("send_group_msg",)
    assert call_api.await_args.kwargs["group_id"] == 20001
    assert str(call_api.await_args.kwargs["message"]) == "hook-message"
