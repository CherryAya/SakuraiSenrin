"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 17:25:22
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-02 19:39:04
Description: core db 基本类型
"""

from typing import NotRequired, TypedDict

from .consts import GroupStatus, Permission


# region 动态解包 Kwargs
class UserUpdateKwargs(TypedDict):
    user_name: NotRequired[str]
    permission: NotRequired[Permission]
    remark: NotRequired[str | None]


class GroupUpdateKwargs(TypedDict):
    group_name: NotRequired[str]
    status: NotRequired[GroupStatus]


class MemberUpdateKwargs(TypedDict):
    group_card: NotRequired[str]
    permission: NotRequired[Permission]


# endregion


# region 严格全量 Payload
class UserPayload(TypedDict):
    created_at: int
    updated_at: int

    user_id: str
    user_name: str
    permission: Permission
    remark: NotRequired[str | None]


class GroupPayload(TypedDict):
    created_at: int
    updated_at: int

    group_id: str
    group_name: str
    status: GroupStatus


class MemberPayload(TypedDict):
    created_at: int
    updated_at: int

    group_id: str
    user_id: str
    group_card: str
    permission: Permission


# endregion


# region 严格局部批量 Payload
class BulkUpdateUserNamePayload(TypedDict):
    updated_at: int

    user_id: str
    user_name: str


class BulkUpdateUserPermPayload(TypedDict):
    updated_at: int

    user_id: str
    permission: Permission


class BulkUpdateGroupNamePayload(TypedDict):
    updated_at: int

    group_id: str
    group_name: str


class BulkUpdateGroupStatusPayload(TypedDict):
    updated_at: int

    group_id: str
    status: GroupStatus


class BulkUpdateMemberCardPayload(TypedDict):
    updated_at: int

    group_id: str
    user_id: str
    group_card: str


class BulkUpdateMemberPermPayload(TypedDict):
    updated_at: int

    group_id: str
    user_id: str
    permission: Permission


# endregion
