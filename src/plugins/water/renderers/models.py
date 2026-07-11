"""Shared water image data models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pil_utils import BuildImage

from src.lib.i18n.runtime import tr, tr_template


@dataclass
class WaterInfo:
    user_id: str
    group_id: str
    created_at: int


@dataclass
class WaterProfileCardData:
    user_id: str
    group_id: str
    matrix_id: str
    group_name: str
    username: str
    global_level: tuple[int, int, int] | None
    matrix_level: tuple[int, int, int] | None
    global_rank: int | None
    group_user_rank: int | None
    matrix_user_rank: int | None
    matrix_rank: int | None
    group_rank: int | None
    matrix_total_level: tuple[int, int, int] | None
    matrix_groups: list[tuple[str, str]]
    achievement_items: list[tuple[str, str, str, int]]


@dataclass(frozen=True)
class WaterRankCardItem:
    entity_id: str
    display_name: str
    secondary_label: str
    avatar: BuildImage | None
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    current_rank: int
    trend: int | None
    group_count: int = 0

    @property
    def user_id(self) -> str:
        return self.entity_id

    @property
    def username(self) -> str:
        return self.display_name


@dataclass(frozen=True)
class WaterPeriodRankCardData:
    period: Literal["week", "month", "season", "year", "total"]
    title: str
    badge: str
    range_text: str
    compare_text: str
    generated_at: int
    total_msg_count: int
    active_entity_count: int
    hourly_counts: list[int]
    peak_hour: int
    previous_total_msg_count: int
    top_items: list[WaterRankCardItem]
    champion_gap: int
    champion_share: float
    report_tile_title: str = ""
    report_tile_subtitle: str = ""
    report_group_rank_title: str = ""
    report_group_rank_summary: str = ""
    report_group_rank_items: list["WaterGroupDailyRankCardItem"] | None = None
    report_group_rank_has_hidden_before: bool = False
    report_group_rank_has_hidden_after: bool = False
    report_show_overview: bool = True
    entity_label: str = tr("zh-CN", "water.report.entity_label")
    champion_summary_label: str = tr_template("zh-CN", "water.report.champion.summary")
    board_title: str = tr("zh-CN", "water.image.period.board.title")
    board_summary_label: str = tr_template("zh-CN", "water.image.period.board.summary")
    board_active_hours_label: str = tr_template(
        "zh-CN", "water.image.period.board.active_hours"
    )
    overview_title: str = tr("zh-CN", "water.image.period.overview.title")

    @property
    def active_user_count(self) -> int:
        return self.active_entity_count

    @property
    def top_users(self) -> list[WaterRankCardItem]:
        return self.top_items


@dataclass(frozen=True)
class WaterDayRankCardData:
    title: str
    group_id: str
    group_name: str
    scope_label: str
    subject_label: str
    leader_name: str
    leader_rank_label: str
    generated_at: int
    top_items: list[WaterRankCardItem]
    summary_label: str
    footer_label: str


@dataclass(frozen=True)
class WaterGroupDailyRankCardItem:
    group_id: str
    display_name: str
    avatar: BuildImage | None
    msg_count: int
    current_rank: int
    trend: int | None
    is_focus_group: bool = False


@dataclass(frozen=True)
class WaterGroupReportImageData:
    title: str
    badge: str
    range_text: str
    compare_text: str
    generated_at: int
    total_msg_count: int
    active_user_count: int
    hourly_counts: list[int]
    peak_hour: int
    previous_total_msg_count: int
    top_items: list[WaterRankCardItem]
    group_rank_title: str
    group_rank_summary: str
    group_rank_items: list[WaterGroupDailyRankCardItem]
    group_rank_has_hidden_before: bool = False
    group_rank_has_hidden_after: bool = False
