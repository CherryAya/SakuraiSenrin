"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 20:11:52
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-27 16:51:36
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
    hourly_counts: list[int]
