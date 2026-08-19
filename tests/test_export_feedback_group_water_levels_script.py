from __future__ import annotations

from scripts import export_feedback_group_water_levels as script


def test_normalize_limit() -> None:
    assert script._normalize_limit(0) is None
    assert script._normalize_limit(-1) is None
    assert script._normalize_limit(10) == 10


def test_render_table_returns_empty_message() -> None:
    assert script._render_table([]) == "No matching users found."


def test_render_table_contains_expected_columns() -> None:
    rendered = script._render_table(
        [
            {
                "rank": 1,
                "user_id": "10001",
                "display_name": "Alice",
                "level": 12,
                "exp": 14400,
                "season_exp": 800,
                "groups": "1107576103|反馈群A",
            }
        ]
    )

    assert "display_name" in rendered
    assert "Alice" in rendered
    assert "1107576103|反馈群A" in rendered
