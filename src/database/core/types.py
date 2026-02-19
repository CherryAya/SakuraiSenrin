"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 17:25:22
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 22:32:25
Description: core db 基本类型
"""

from typing import NotRequired, TypedDict

from .consts import GroupStatus, Permission


class UserPayload(TypedDict):
    user_id: str
    user_name: str
    permission: NotRequired[Permission]
    remark: NotRequired[str | None]


class UserUpdateNamePayload(TypedDict):
    user_id: str
    user_name: str


class UserUpdatePermPayload(TypedDict):
    user_id: str
    permission: Permission


class GroupPayload(TypedDict):
    group_id: str
    group_name: str
    status: NotRequired[GroupStatus]


class GroupUpdateNamePayload(TypedDict):
    group_id: str
    group_name: str


class GroupUpdateStatusPayload(TypedDict):
    group_id: str
    status: GroupStatus


class MemberPayload(TypedDict):
    group_id: str
    user_id: str
    group_card: str
    permission: NotRequired[Permission]


class MemberUpdateCardPayload(TypedDict):
    group_id: str
    user_id: str
    group_card: str


class MemberUpdatePermPayload(TypedDict):
    group_id: str
    user_id: str
    permission: Permission
