from src.plugins.water.renderers.report_layout import (
    GROUP_REPORT_LEFT_FOOTER_PADDING,
    GROUP_REPORT_LEFT_HEADER_HEIGHT,
    GROUP_REPORT_USER_CARD_HEIGHT,
    GROUP_REPORT_USER_ROW_GAP,
    compute_group_report_right_extra_height,
    estimate_group_report_left_height,
    pick_group_report_right_panel_tier,
)


def test_group_report_right_panel_tier_is_compact_when_extra_height_is_tight() -> None:
    tier = pick_group_report_right_panel_tier(
        user_count=1,
        rank_item_count=3,
        has_hidden_before=True,
        has_hidden_after=True,
        has_trend_history=True,
    )

    assert tier == "compact"


def test_group_report_right_panel_tier_is_balanced_for_middle_height() -> None:
    extra_height = compute_group_report_right_extra_height(
        user_count=3,
        rank_item_count=3,
        has_hidden_before=False,
        has_hidden_after=False,
    )
    tier = pick_group_report_right_panel_tier(
        user_count=3,
        rank_item_count=3,
        has_hidden_before=False,
        has_hidden_after=False,
        has_trend_history=True,
    )

    assert 240 <= extra_height < 520
    assert tier == "balanced"


def test_group_report_left_height_uses_shared_layout_constants() -> None:
    expected = (
        GROUP_REPORT_LEFT_HEADER_HEIGHT
        + 3 * GROUP_REPORT_USER_CARD_HEIGHT
        + 2 * GROUP_REPORT_USER_ROW_GAP
        + GROUP_REPORT_LEFT_FOOTER_PADDING
    )

    assert estimate_group_report_left_height(3, scale=1.0) == expected


def test_group_report_right_panel_tier_is_expanded_for_tall_left_column() -> None:
    tier = pick_group_report_right_panel_tier(
        user_count=10,
        rank_item_count=3,
        has_hidden_before=False,
        has_hidden_after=False,
        has_trend_history=True,
    )

    assert tier == "expanded"


def test_group_report_right_panel_tier_falls_back_to_balanced_without_history() -> None:
    tier = pick_group_report_right_panel_tier(
        user_count=10,
        rank_item_count=3,
        has_hidden_before=False,
        has_hidden_after=False,
        has_trend_history=False,
    )

    assert tier == "balanced"
