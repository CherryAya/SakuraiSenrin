from dataclasses import dataclass, field

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, PrivateMessageEvent
from nonebot.adapters.onebot.v11.event import (
    GroupDecreaseNoticeEvent,
    GroupIncreaseNoticeEvent,
    PokeNotifyEvent,
)
from nonebot.adapters.onebot.v11.message import MessageSegment


class MatcherFinished(Exception):
    pass


@dataclass
class DummyMatcher:
    sent: list[str] = field(default_factory=list)
    finished: str | None = None

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def finish(self, message: str) -> None:
        self.finished = message
        raise MatcherFinished


def build_group_message_event(
    message: str,
    *,
    user_id: int = 10001,
    group_id: int = 20001,
    role: str = "member",
    message_id: int = 1,
    self_id: int = 99999,
    time: int = 1_700_000_000,
) -> GroupMessageEvent:
    return GroupMessageEvent.model_validate(
        {
            "time": time,
            "self_id": str(self_id),
            "post_type": "message",
            "sub_type": "normal",
            "user_id": user_id,
            "message_type": "group",
            "message_id": message_id,
            "message": Message(message),
            "original_message": Message(message),
            "raw_message": message,
            "font": 0,
            "sender": {
                "user_id": user_id,
                "nickname": "tester",
                "card": "",
                "role": role,
            },
            "to_me": False,
            "group_id": group_id,
        }
    )


def build_private_message_event(
    message: str,
    *,
    user_id: int = 10001,
    message_id: int = 1,
    self_id: int = 99999,
    time: int = 1_700_000_000,
) -> PrivateMessageEvent:
    return PrivateMessageEvent.model_validate(
        {
            "time": time,
            "self_id": str(self_id),
            "post_type": "message",
            "sub_type": "friend",
            "user_id": user_id,
            "message_type": "private",
            "message_id": message_id,
            "message": Message(message),
            "original_message": Message(message),
            "raw_message": message,
            "font": 0,
            "sender": {
                "user_id": user_id,
                "nickname": "tester",
                "card": "",
                "role": "member",
            },
            "to_me": False,
        }
    )


def attach_reply_message(
    event: GroupMessageEvent | PrivateMessageEvent,
    *segments: MessageSegment,
    message_id: int = 90001,
    user_id: int = 10002,
    time: int = 1_700_000_000,
) -> GroupMessageEvent | PrivateMessageEvent:
    reply_field = type(event).model_fields["reply"]
    reply_type = next(
        arg
        for arg in reply_field.annotation.__args__  # type: ignore[attr-defined]
        if arg is not type(None)
    )
    event.reply = reply_type.model_validate(
        {
            "time": time,
            "message_type": getattr(event, "message_type", "group"),
            "message_id": message_id,
            "real_id": message_id,
            "sender": {
                "user_id": user_id,
                "nickname": "reply-user",
                "card": "",
                "role": "member",
            },
            "message": Message(list(segments)),
        }
    )
    return event


def build_group_increase_event(
    *,
    user_id: int = 10002,
    group_id: int = 20001,
    operator_id: int = 10001,
    self_id: int = 99999,
    time: int = 1_700_000_000,
) -> GroupIncreaseNoticeEvent:
    return GroupIncreaseNoticeEvent.model_validate(
        {
            "time": time,
            "self_id": str(self_id),
            "post_type": "notice",
            "notice_type": "group_increase",
            "sub_type": "approve",
            "user_id": user_id,
            "group_id": group_id,
            "operator_id": operator_id,
        }
    )


def build_group_decrease_event(
    *,
    user_id: int = 10002,
    group_id: int = 20001,
    operator_id: int = 10001,
    self_id: int = 99999,
    time: int = 1_700_000_000,
) -> GroupDecreaseNoticeEvent:
    return GroupDecreaseNoticeEvent.model_validate(
        {
            "time": time,
            "self_id": str(self_id),
            "post_type": "notice",
            "notice_type": "group_decrease",
            "sub_type": "leave",
            "user_id": user_id,
            "group_id": group_id,
            "operator_id": operator_id,
        }
    )


def build_group_poke_event(
    *,
    user_id: int = 10001,
    group_id: int = 20001,
    target_id: int = 99999,
    self_id: int = 99999,
    time: int = 1_700_000_000,
) -> PokeNotifyEvent:
    return PokeNotifyEvent.model_validate(
        {
            "time": time,
            "self_id": str(self_id),
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "user_id": user_id,
            "group_id": group_id,
            "target_id": target_id,
        }
    )
