from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
import pytest

from src.plugins.wordbank.forward_batch import (
    build_forward_batch_payload,
    extract_forward_source_message_id,
    is_forward_input,
)
from src.plugins.wordbank.message_model import shape_to_summary_text
from tests.plugins.water.helpers import (
    attach_reply_message,
    build_group_message_event,
    build_private_message_event,
)


def test_forward_input_accepts_direct_forward_message() -> None:
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])

    assert is_forward_input(event) is True
    assert extract_forward_source_message_id(event) == 7657605421581295285


def test_forward_input_accepts_direct_forward_message_in_private() -> None:
    event = build_private_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])

    assert is_forward_input(event) is True
    assert extract_forward_source_message_id(event) == 7657605421581295285


def test_forward_input_accepts_reply_forward_message() -> None:
    event = build_group_message_event("response", message_id=1)
    attach_reply_message(event, MessageSegment.forward("7657605421581295285"))

    assert is_forward_input(event) is True
    assert extract_forward_source_message_id(event) == 7657605421581295285


@pytest.mark.asyncio
async def test_build_forward_batch_payload_fetches_forward_msg_content() -> None:
    call_api = AsyncMock(
        return_value={
            "messages": [
                Message([MessageSegment.text("第一条")]),
                Message([MessageSegment.text("第二条")]),
            ]
        }
    )
    bot = cast(Bot, SimpleNamespace(call_api=call_api))
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])

    payload = await build_forward_batch_payload(
        bot,
        event,
        media_service=SimpleNamespace(),
    )

    call_api.assert_awaited_once_with(
        "get_forward_msg",
        message_id=7657605421581295285,
    )
    assert payload.source_message_id == 7657605421581295285
    assert payload.node_count == 2
    assert not payload.whole_shape.is_empty()
    assert len(payload.split_shapes) == 2


@pytest.mark.asyncio
async def test_build_forward_batch_payload_accepts_nested_onebot_nodes() -> None:
    call_api = AsyncMock(
        return_value={
            "data": {
                "messages": [
                    {
                        "content": [
                            {
                                "type": "text",
                                "data": {"text": "第一条"},
                            }
                        ]
                    },
                    {
                        "data": {
                            "message": {
                                "type": "text",
                                "data": {"text": "第二条"},
                            }
                        }
                    },
                ]
            }
        }
    )
    bot = cast(Bot, SimpleNamespace(call_api=call_api))
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])

    payload = await build_forward_batch_payload(
        bot,
        event,
        media_service=SimpleNamespace(),
    )

    assert payload.node_count == 2
    assert len(payload.split_shapes) == 2
    assert shape_to_summary_text(payload.split_shapes[0]) == "第一条"
    assert shape_to_summary_text(payload.split_shapes[1]) == "第二条"
