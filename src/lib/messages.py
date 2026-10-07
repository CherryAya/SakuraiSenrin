from __future__ import annotations

from io import BytesIO
from pathlib import Path

from nonebot.adapters.onebot.v11.message import Message, MessageSegment


def empty_message() -> Message:
    return Message()


def text_message(text: str) -> Message:
    message = empty_message()
    message += MessageSegment.text(text)
    return message


def image_message(file: str | bytes | BytesIO | Path) -> Message:
    message = empty_message()
    message += MessageSegment.image(file)
    return message
