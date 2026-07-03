from nonebot.adapters.onebot.v11 import MessageSegment

from src.lib.messages import text_message
from src.plugins.wordbank.message_model import (
    is_valid_message_text,
    shape_from_message,
    shape_from_text,
    shape_to_summary_text,
)


def test_shape_from_text_preserves_user_text_verbatim() -> None:
    shape = shape_from_text("第一行\n第二行  第三列")

    assert shape.atoms[0].text == "第一行\n第二行  第三列"
    assert shape_to_summary_text(shape) == "第一行\n第二行  第三列"


def test_shape_from_text_rejects_blank_text_without_mutation() -> None:
    assert shape_from_text(" ").is_empty()
    assert shape_from_text("\n\t").is_empty()
    assert is_valid_message_text(" ") is False


def test_shape_from_message_preserves_text_segments_verbatim() -> None:
    shape = shape_from_message(text_message("第一行\n第二行  第三列"))

    assert shape.atoms[0].text == "第一行\n第二行  第三列"


def test_shape_from_message_rejects_blank_text_segments() -> None:
    shape = shape_from_message(text_message(" \n\t"))

    assert shape.is_empty()


def test_shape_from_message_formats_at_as_fallback_text() -> None:
    shape = shape_from_message(MessageSegment.at("10002") + MessageSegment.text(""))

    assert shape_to_summary_text(shape) == "@用户(10002)"
