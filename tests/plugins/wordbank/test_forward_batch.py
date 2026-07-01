from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins.wordbank.forward_batch import (
    extract_forward_source_message_id,
    is_forward_input,
)
from tests.plugins.water.helpers import attach_reply_message, build_group_message_event


def test_forward_input_accepts_direct_forward_message() -> None:
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])

    assert is_forward_input(event) is True
    assert extract_forward_source_message_id(event) == 7657605421581295285


def test_forward_input_accepts_reply_forward_message() -> None:
    event = build_group_message_event("response", message_id=1)
    attach_reply_message(event, MessageSegment.forward("7657605421581295285"))

    assert is_forward_input(event) is True
    assert extract_forward_source_message_id(event) == 7657605421581295285
