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
