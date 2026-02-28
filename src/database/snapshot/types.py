"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-08 19:04:58
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-27 16:03:11
Description: snapshot db 基本类型
"""

from typing import TypedDict


class UserSnapshotPayload(TypedDict):
    created_at: int

    user_id: str
    content: str


class GroupSnapshotPayload(TypedDict):
    created_at: int

    group_id: str
    content: str


class MemberSnapshotPayload(TypedDict):
    created_at: int

    user_id: str
    group_id: str
    content: str
