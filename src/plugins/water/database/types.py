"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 20:11:52
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-03 17:17:50
Description: types
"""

from typing import TypedDict


class WaterMessagePayload(TypedDict):
    created_at: int

    group_id: str
    user_id: str


class WaterSummaryPayload(TypedDict):
    created_at: int
    updated_at: int

    group_id: str
    user_id: str
    record_date: int
    msg_count: int
    active_hours: int
    hourly_counts: list[int]


class WaterGroupMatrixMapPayload(TypedDict):
    group_id: str
    matrix_id: str
    created_at: int
    updated_at: int


class WaterUserExpPayload(TypedDict):
    user_id: str
    matrix_id: str
    delta_exp: int
    delta_season_exp: int
    created_at: int
    updated_at: int


class WaterMatrixExpPayload(TypedDict):
    matrix_id: str
    delta_exp: int
    delta_season_exp: int
    created_at: int
    updated_at: int


class WaterPenaltyPayload(TypedDict):
    created_at: int
    updated_at: int
    record_date: int
    user_id: str
    group_id: str
    matrix_id: str
    reason: str
    delta_exp: int
    is_revoked: int
    revoked_at: int | None
    extra: dict


class WaterSettlementJobPayload(TypedDict):
    record_date: int
    status: str
    started_at: int
    finished_at: int
    error: str
    created_at: int
    updated_at: int


class WaterMatrixMergeStatePayload(TypedDict):
    group_id: str
    first_seen_at: int | None
    is_ignored: int
    status: str
    target_matrix_id: str
    operator_id: str
    created_at: int
    updated_at: int


class WaterAchievementPayload(TypedDict):
    user_id: str
    achievement_id: str
    track_type: str
    season_id: str
    unlocked_at: int
    context: str
