"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-08 19:04:58
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 22:34:14
Description: snapshot db 基本类型
"""

from typing import TypedDict


class UserSnapshotPayload(TypedDict):
    user_id: str
    content: str


class GroupSnapshotPayload(TypedDict):
    group_id: str
    content: str


class MemberSnapshotPayload(TypedDict):
    user_id: str
    group_id: str
    content: str
