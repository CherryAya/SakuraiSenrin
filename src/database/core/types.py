from typing import NotRequired, TypedDict

from .consts import Permission, UserStatus


class UserPayload(TypedDict):
    user_id: str
    user_name: str
    status: NotRequired[UserStatus]
    permission: NotRequired[Permission]
    remark: NotRequired[str | None]


class UserUpdateNamePayload(TypedDict):
    user_id: str
    user_name: str


class GroupPayload(TypedDict):
    group_id: str
    group_name: str
    status: NotRequired[UserStatus]


class GroupUpdateNamePayload(TypedDict):
    group_id: str
    group_name: str


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
