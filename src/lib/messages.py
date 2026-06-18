from __future__ import annotations

from typing import Any

from nonebot.adapters.onebot.v11.message import Message, MessageSegment


def empty_message() -> Message:
    return Message()


def text_message(text: str) -> Message:
    message = empty_message()
    message += MessageSegment.text(text)
    return message


def image_message(file: Any) -> Message:
    message = empty_message()
    message += MessageSegment.image(file)
    return message
