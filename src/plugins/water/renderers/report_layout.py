"""Layout helpers for adaptive water group report rendering."""

from __future__ import annotations

from typing import Literal

WaterReportLayoutTier = Literal["compact", "balanced", "expanded"]

DEFAULT_GROUP_REPORT_SCALE = 2.0
GROUP_REPORT_LEFT_HEADER_HEIGHT = 40
GROUP_REPORT_LEFT_FOOTER_PADDING = 18
GROUP_REPORT_USER_CARD_HEIGHT = 108
GROUP_REPORT_USER_ROW_GAP = 10
GROUP_REPORT_RANK_HEADER_HEIGHT = 58
GROUP_REPORT_RANK_BODY_TOP_PADDING = 12
GROUP_REPORT_RANK_ROW_HEIGHT = 44
GROUP_REPORT_RANK_ROW_GAP = 6
GROUP_REPORT_RANK_BOTTOM_PADDING = 16


def estimate_group_report_left_height(
    user_count: int,
    *,
    scale: float = DEFAULT_GROUP_REPORT_SCALE,
) -> int:
    return (
        int(GROUP_REPORT_LEFT_HEADER_HEIGHT * scale)
        + user_count * int(GROUP_REPORT_USER_CARD_HEIGHT * scale)
        + max(0, user_count - 1) * int(GROUP_REPORT_USER_ROW_GAP * scale)
        + int(GROUP_REPORT_LEFT_FOOTER_PADDING * scale)
    )


def estimate_group_rank_card_height(
    item_count: int,
    *,
    has_hidden_before: bool,
    has_hidden_after: bool,
    scale: float = DEFAULT_GROUP_REPORT_SCALE,
) -> int:
    hidden_rows = int(has_hidden_before) + int(has_hidden_after)
    rank_row_count = item_count + hidden_rows
    return (
        int(GROUP_REPORT_RANK_HEADER_HEIGHT * scale)
        + int(GROUP_REPORT_RANK_BODY_TOP_PADDING * scale)
        + rank_row_count * int(GROUP_REPORT_RANK_ROW_HEIGHT * scale)
        + max(0, rank_row_count - 1) * int(GROUP_REPORT_RANK_ROW_GAP * scale)
        + int(GROUP_REPORT_RANK_BOTTOM_PADDING * scale)
    )


def compute_group_report_right_extra_height(
    *,
    user_count: int,
    rank_item_count: int,
    has_hidden_before: bool,
    has_hidden_after: bool,
    scale: float = DEFAULT_GROUP_REPORT_SCALE,
) -> int:
    left_h = estimate_group_report_left_height(user_count, scale=scale)
    right_card_h = estimate_group_rank_card_height(
        rank_item_count,
        has_hidden_before=has_hidden_before,
        has_hidden_after=has_hidden_after,
        scale=scale,
    )
    return max(0, left_h - right_card_h)


def pick_group_report_right_panel_tier(
    *,
    user_count: int,
    rank_item_count: int,
    has_hidden_before: bool,
    has_hidden_after: bool,
    has_trend_history: bool,
    scale: float = DEFAULT_GROUP_REPORT_SCALE,
) -> WaterReportLayoutTier:
    extra_height = compute_group_report_right_extra_height(
        user_count=user_count,
        rank_item_count=rank_item_count,
        has_hidden_before=has_hidden_before,
        has_hidden_after=has_hidden_after,
        scale=scale,
    )
    compact_threshold = int(120 * scale)
    expanded_threshold = int(260 * scale)
    if extra_height < compact_threshold:
        return "compact"
    if extra_height < expanded_threshold or not has_trend_history:
        return "balanced"
    return "expanded"
