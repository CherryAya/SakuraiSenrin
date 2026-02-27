"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 20:11:52
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 20:11:53
Description: types
"""

from typing import TypedDict


class WaterMessagePayload(TypedDict):
    group_id: str
    user_id: str
    created_at: int
