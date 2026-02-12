from typing import TypedDict

from src.database.snapshot.consts import SnapshotEventType


class UserSnapshotPayload(TypedDict):
    user_id: str
    group_id: str
    event_type: SnapshotEventType
    content: str


class GroupSnapshotPayload(TypedDict):
    group_id: str
    event_type: SnapshotEventType
    content: str
