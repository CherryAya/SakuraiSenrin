from __future__ import annotations

from nonebot.adapters.onebot.v11.event import (
    FriendRequestEvent,
    GroupIncreaseNoticeEvent,
    GroupRequestEvent,
)

MISSING_INVITED_ID = object()


def build_friend_request_event(
    *, user_id: int = 10001, flag: str = "friend-flag", self_id: int = 99999
) -> FriendRequestEvent:
    return FriendRequestEvent.model_validate(
        {
            "time": 1_700_000_000,
            "self_id": self_id,
            "post_type": "request",
            "request_type": "friend",
            "user_id": user_id,
            "comment": "",
            "flag": flag,
        }
    )


def build_group_request_event(
    *,
    invited_id: object = 99999,
    user_id: int = 10001,
    group_id: int = 20001,
    flag: str = "flag-1",
    sub_type: str = "invite",
    self_id: int = 99999,
    time: int = 1_700_000_000,
    extra: dict[str, object] | None = None,
) -> GroupRequestEvent:
    payload: dict[str, object] = {
        "time": time,
        "self_id": str(self_id),
        "post_type": "request",
        "request_type": "group",
        "sub_type": sub_type,
        "user_id": user_id,
        "group_id": group_id,
        "comment": "",
        "flag": flag,
    }
    if invited_id is not MISSING_INVITED_ID:
        payload["invited_id"] = invited_id
    if extra is not None:
        payload.update(extra)
    return GroupRequestEvent.model_validate(payload)


def build_bot_join_event(
    *,
    user_id: int = 99999,
    group_id: int = 20001,
    operator_id: int = 10001,
    sub_type: str = "invite",
    self_id: int = 99999,
    time: int = 1_700_000_000,
    extra: dict[str, object] | None = None,
) -> GroupIncreaseNoticeEvent:
    return GroupIncreaseNoticeEvent.model_validate(
        {
            "time": time,
            "self_id": str(self_id),
            "post_type": "notice",
            "notice_type": "group_increase",
            "sub_type": sub_type,
            "user_id": user_id,
            "group_id": group_id,
            "operator_id": operator_id,
            **(extra or {}),
        }
    )
