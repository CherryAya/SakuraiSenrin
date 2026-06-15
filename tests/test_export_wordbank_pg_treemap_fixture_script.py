from scripts.export_wordbank_pg_treemap_fixture import (
    detect_match_source,
    sanitize_filename,
    summarize_legacy_message_payload,
    summarize_legacy_rule,
)


def test_summarize_legacy_message_payload_keeps_text_and_image_count() -> None:
    payload = (
        '[{"type":"text","text":"今日同您携手共进的是:（朝武芳乃）"},'
        '{"type":"image","file":"abc.webp"}]'
    )

    assert (
        summarize_legacy_message_payload(payload)
        == "今日同您携手共进的是:（朝武芳乃） [图片x1]"
    )


def test_summarize_legacy_message_payload_handles_pure_image_payload() -> None:
    payload = '[{"type":"image","file":"abc.webp"},{"type":"image","file":"def.webp"}]'

    assert summarize_legacy_message_payload(payload) == "[图片x2]"


def test_summarize_legacy_rule_formats_known_fields() -> None:
    payload = {
        "group_id": "858213019",
        "role": "admin",
        "call_count": {"window_seconds": 3600, "min": 3, "max": 10},
    }

    assert (
        summarize_legacy_rule(payload) == "群 858213019 / 角色 admin / 频次 3600:3:10"
    )


def test_detect_match_source_marks_mixed_hits() -> None:
    assert (
        detect_match_source(
            keyword="jrlp",
            trigger_text="jrlp",
            response_texts=("今日陪伴你的是 jrlp [图片x1]",),
        )
        == "text:mixed"
    )


def test_sanitize_filename_collapses_symbols() -> None:
    assert sanitize_filename("jrlp/朝武芳乃?.png") == "jrlp-朝武芳乃-.png"
