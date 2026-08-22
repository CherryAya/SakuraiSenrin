from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.lib.messages import text_message
from src.plugins.wordbank.message_model import (
    RESPONSE_TARGET_SENDER,
    fingerprint_shape,
    format_at_fallback_text,
    is_safe_executable_at_target,
    is_valid_message_text,
    shape_from_message,
    shape_from_payload,
    shape_from_response_text,
    shape_from_text,
    shape_to_payload,
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


def test_shape_from_message_preserves_face_segments() -> None:
    shape = shape_from_message(
        MessageSegment.text("早安")
        + MessageSegment.face(1)
        + MessageSegment.text("晚安")
    )

    assert [atom.kind for atom in shape.atoms] == ["text", "face", "text"]
    assert shape.atoms[1].face_id == 1
    assert shape_to_summary_text(shape) == "早安 [表情 ID：1] 晚安"


def test_shape_payload_roundtrip_preserves_face_segments() -> None:
    shape = shape_from_message(Message([MessageSegment.face(123)]))

    assert shape_from_payload(shape_to_payload(shape)) == shape
    assert fingerprint_shape(shape).structure_key == "face"


def test_is_safe_executable_at_target_only_allows_digits_or_sender() -> None:
    assert is_safe_executable_at_target("10002") is True
    assert is_safe_executable_at_target(RESPONSE_TARGET_SENDER) is True
    assert is_safe_executable_at_target("all") is False
    assert is_safe_executable_at_target("abc") is False


def test_format_at_fallback_text_formats_all_as_literal_broadcast() -> None:
    assert format_at_fallback_text("all") == "@全体成员"
    assert format_at_fallback_text("10002") == "@用户(10002)"


def test_shape_from_response_text_parses_sender_placeholders() -> None:
    shape = shape_from_response_text("你好 [@触发者] [戳触发者]")

    assert [atom.kind for atom in shape.atoms] == ["text", "at", "event"]
    assert shape.atoms[1].target_id == RESPONSE_TARGET_SENDER
    assert shape.atoms[2].event_name == "event:poke"
    assert shape.atoms[2].target_id == RESPONSE_TARGET_SENDER
    assert shape_to_summary_text(shape) == "你好 @触发者 戳一戳触发者"


def test_shape_from_response_text_parses_fixed_poke_placeholder() -> None:
    shape = shape_from_response_text("[戳:10002]")

    assert shape.atoms[0].kind == "event"
    assert shape.atoms[0].event_name == "event:poke"
    assert shape.atoms[0].target_id == "10002"
    assert shape_to_summary_text(shape) == "戳一戳用户(10002)"


def test_shape_from_response_text_parses_profile_placeholders() -> None:
    shape = shape_from_response_text("[账号] [昵称] [群名片] [头像] [xx]")

    assert [atom.kind for atom in shape.atoms] == [
        "placeholder",
        "placeholder",
        "placeholder",
        "placeholder",
        "placeholder",
    ]
    assert [atom.placeholder_name for atom in shape.atoms] == [
        "account",
        "nickname",
        "group_card",
        "avatar",
        "profile_combo",
    ]
    assert shape_to_summary_text(shape) == "[账号] [昵称] [群名片] [头像] [xx]"


def test_shape_from_response_text_keeps_unrecognized_placeholder_literal() -> None:
    shape = shape_from_response_text("【戳一戳】")

    assert shape == shape_from_text("【戳一戳】")


def test_shape_payload_roundtrip_preserves_response_event_targets() -> None:
    shape = shape_from_response_text("[@触发者] [戳:10002]")

    assert shape_from_payload(shape_to_payload(shape)) == shape


def test_shape_payload_roundtrip_preserves_profile_placeholders() -> None:
    shape = shape_from_response_text("[账号] [昵称] [群名片] [头像] [xx]")

    assert shape_from_payload(shape_to_payload(shape)) == shape
