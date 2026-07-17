"""Shared water repository dataclasses and pure helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import floor, sqrt
from secrets import token_hex

import arrow

from .tables import WaterActivitySeason
from .types import WaterMessagePayload, WaterMessageWritePayload


@dataclass
class RankItem:
    user_id: str
    msg_count: int
    current_rank: int
    trend: int | None


@dataclass
class GlobalPeriodRankItem:
    user_id: str
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    current_rank: int
    trend: int | None


@dataclass(frozen=True)
class NaturalRankItem:
    entity_id: str
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    current_rank: int
    trend: int | None
    group_count: int = 0


@dataclass
class GlobalPeriodOverview:
    total_msg_count: int
    active_user_count: int
    hourly_counts: list[int]
    previous_total_msg_count: int

    @property
    def delta_total_msg_count(self) -> int:
        return self.total_msg_count - self.previous_total_msg_count

    @property
    def peak_hour(self) -> int:
        if not any(self.hourly_counts):
            return 0
        return max(range(24), key=lambda hour: self.hourly_counts[hour])


@dataclass(frozen=True)
class NaturalRankOverview:
    total_msg_count: int
    active_entity_count: int
    hourly_counts: list[int]
    previous_total_msg_count: int

    @property
    def peak_hour(self) -> int:
        if not any(self.hourly_counts):
            return 0
        return max(range(24), key=lambda hour: self.hourly_counts[hour])


@dataclass(frozen=True)
class NaturalPeriodRankSnapshot:
    leaderboard: list[NaturalRankItem]
    overview: NaturalRankOverview


@dataclass(frozen=True)
class WaterGroupReportMember:
    user_id: str
    msg_count: int
    active_hours: int
    hourly_counts: list[int]
    current_rank: int
    trend: int | None


@dataclass(frozen=True)
class WaterGroupDailyRankItem:
    group_id: str
    msg_count: int
    active_user_count: int
    active_hours: int
    hourly_counts: list[int]
    current_rank: int
    trend: int | None


@dataclass(frozen=True)
class WaterGroupDailyRankSnapshot:
    focus_group_id: str
    record_date: int
    total_groups: int
    total_msg_count: int
    focus_rank: int
    focus_trend: int | None
    leaderboard: list[WaterGroupDailyRankItem]
    has_hidden_before: bool
    has_hidden_after: bool


@dataclass(frozen=True)
class WaterGroupReportSnapshot:
    group_id: str
    record_date: int
    total_msg_count: int
    active_user_count: int
    active_hours: int
    hourly_counts: list[int]
    previous_total_msg_count: int
    previous_active_user_count: int
    previous_active_hours: int
    previous_hourly_counts: list[int]
    leaderboard: list[WaterGroupReportMember]

    @property
    def peak_hour(self) -> int:
        if not any(self.hourly_counts):
            return 0
        return max(range(24), key=lambda hour: self.hourly_counts[hour])

    @property
    def delta_total_msg_count(self) -> int:
        return self.total_msg_count - self.previous_total_msg_count

    @property
    def delta_active_user_count(self) -> int:
        return self.active_user_count - self.previous_active_user_count

    @property
    def activity_score(self) -> int:
        return self.total_msg_count + 20 * self.active_user_count


@dataclass(frozen=True)
class WaterDailyReportCandidate:
    group_id: str
    record_date: int
    total_msg_count: int
    active_user_count: int
    active_hours: int
    activity_score: int


@dataclass(frozen=True)
class WaterDailyReportPreviewItem:
    group_id: str
    record_date: int
    total_msg_count: int
    active_user_count: int
    active_hours: int
    activity_score: int


@dataclass(frozen=True)
class WaterActivitySeasonRecord:
    season_id: str
    name: str
    normalized_name: str
    description: str
    start_date: int
    end_date: int
    status: str
    published_at: int | None
    created_by: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class SeasonUserAggregate:
    user_id: str
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    rank: int


@dataclass(frozen=True)
class SeasonGroupAggregate:
    group_id: str
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    rank: int


@dataclass(frozen=True)
class SeasonMatrixAggregate:
    matrix_id: str
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    rank: int


@dataclass
class _PeriodAggregateBucket:
    msg_count: int
    active_days: set[int]
    active_hours: int
    hourly_counts: list[int]
    group_ids: set[str]


@dataclass
class WaterMessageContext:
    group_id: str
    user_id: str
    created_at: int

    def to_payload(self) -> WaterMessagePayload:
        dt = arrow.get(self.created_at).to("Asia/Shanghai")
        return {
            "group_id": self.group_id,
            "user_id": self.user_id,
            "record_date": int(dt.format("YYYYMMDD")),
            "hour": int(dt.format("H")),
            "msg_count": 1,
        }

    def to_write_payload(self) -> WaterMessageWritePayload:
        payload = self.to_payload()
        return {
            **payload,
            "created_at": self.created_at,
        }


@dataclass
class DailyAggregateItem:
    matrix_id: str
    group_id: str
    user_id: str
    msg_count: int
    active_hours: int
    hourly_counts: list[int]


def calc_personal_delta_exp(msg_count: int, active_hours: int) -> int:
    return floor(10 * sqrt(msg_count) + 5 * active_hours)


def calc_weighted_global_exp(gains: Sequence[int]) -> int:
    total = 0
    ordered = sorted((gain for gain in gains if gain > 0), reverse=True)
    weights = (1.0, 0.5, 0.2)
    for idx, gain in enumerate(ordered):
        weight = weights[idx] if idx < len(weights) else 0.0
        total += floor(gain * weight)
    return total


def to_season_record(row: WaterActivitySeason) -> WaterActivitySeasonRecord:
    return WaterActivitySeasonRecord(
        season_id=row.season_id,
        name=row.name,
        normalized_name=row.normalized_name,
        description=row.description,
        start_date=row.start_date,
        end_date=row.end_date,
        status=row.status,
        published_at=row.published_at,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def gen_matrix_id() -> str:
    return f"mtx_{token_hex(4)}"


__all__ = [
    "DailyAggregateItem",
    "GlobalPeriodOverview",
    "GlobalPeriodRankItem",
    "NaturalPeriodRankSnapshot",
    "NaturalRankItem",
    "NaturalRankOverview",
    "RankItem",
    "SeasonGroupAggregate",
    "SeasonMatrixAggregate",
    "SeasonUserAggregate",
    "WaterActivitySeasonRecord",
    "WaterDailyReportCandidate",
    "WaterDailyReportPreviewItem",
    "WaterGroupDailyRankItem",
    "WaterGroupDailyRankSnapshot",
    "WaterGroupReportMember",
    "WaterGroupReportSnapshot",
    "WaterMessageContext",
    "_PeriodAggregateBucket",
    "calc_personal_delta_exp",
    "calc_weighted_global_exp",
    "gen_matrix_id",
    "to_season_record",
]
