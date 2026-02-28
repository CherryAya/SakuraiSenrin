"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 17:25:22
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-27 16:52:28
Description: core db 基本类型
"""

from typing import NotRequired, TypedDict

from .consts import GroupStatus, Permission


class UserPayload(TypedDict):
    created_at: int
    updated_at: int

    user_id: str
    user_name: str
    permission: NotRequired[Permission]
    remark: NotRequired[str | None]


class UserUpdateNamePayload(TypedDict):
    updated_at: int

    user_id: str
    user_name: str


class UserUpdatePermPayload(TypedDict):
    updated_at: int

    user_id: str
    permission: Permission


class GroupPayload(TypedDict):
    created_at: int
    updated_at: int

    group_id: str
    group_name: str
    status: NotRequired[GroupStatus]


class GroupUpdateNamePayload(TypedDict):
    updated_at: int

    group_id: str
    group_name: str


class GroupUpdateStatusPayload(TypedDict):
    updated_at: int

    group_id: str
    status: GroupStatus


class MemberPayload(TypedDict):
    created_at: int
    updated_at: int

    group_id: str
    user_id: str
    group_card: str
    permission: NotRequired[Permission]


class MemberUpdateCardPayload(TypedDict):
    updated_at: int

    group_id: str
    user_id: str
    group_card: str


class MemberUpdatePermPayload(TypedDict):
    updated_at: int

    group_id: str
    user_id: str
    permission: Permission
