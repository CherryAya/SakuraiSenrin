"""群邀请的对象校验、实时成员判断与事件/审批串行化。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from weakref import WeakValueDictionary

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupIncreaseNoticeEvent,
    GroupRequestEvent,
)

from src.database.core.consts import InvitationStage
from src.lib.request_flags import is_valid_request_flag
from src.lib.types import is_object_mapping, is_object_sequence

_invitation_locks: WeakValueDictionary[tuple[str, str], asyncio.Lock] = (
    WeakValueDictionary()
)


class InvitationStateError(RuntimeError):
    """平台无法提供可信的机器人当前群成员状态。"""


@dataclass(frozen=True, slots=True)
class InvitationGroupState:
    joined: bool
    group_name: str | None = None


def invitation_display_name(value: object) -> str | None:
    """名称只接受非空白文本，保留平台原文并交给安全消息计划输出。"""
    return value if isinstance(value, str) and value.strip() else None


def invitation_event_names(
    event: GroupRequestEvent | GroupIncreaseNoticeEvent, inviter_id: str
) -> tuple[str | None, str | None]:
    """兼容上游附加名称；入群 notice 的普通 nickname 可能属于机器人。"""
    group_name = invitation_display_name(getattr(event, "group_name", None))
    inviter_name = None
    profile: object = None
    if isinstance(event, GroupRequestEvent) and str(event.user_id) == inviter_id:
        inviter_name = invitation_display_name(
            getattr(event, "nickname", None)
        ) or invitation_display_name(getattr(event, "inviter_name", None))
        profile = getattr(event, "sender", None)
    elif (
        isinstance(event, GroupIncreaseNoticeEvent)
        and str(event.operator_id) == inviter_id
    ):
        inviter_name = invitation_display_name(getattr(event, "operator_name", None))
        profile = getattr(event, "operator", None)
    if (
        is_object_mapping(profile)
        and _positive_id(profile.get("user_id")) == inviter_id
    ):
        inviter_name = inviter_name or invitation_display_name(profile.get("nickname"))
    return group_name, inviter_name


def invitation_lock(bot_id: str, group_id: str) -> asyncio.Lock:
    """通知与管理员审批共享锁，序列化同一机器人的同群业务。"""
    key = (bot_id, group_id)
    lock = _invitation_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _invitation_locks[key] = lock
    return lock


def _positive_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if isinstance(value, str):
        value = value.strip()
        if value.isascii() and value.isdecimal():
            return value.lstrip("0") or None
    return None


def is_invite_target(bot: Bot, event: GroupRequestEvent) -> bool:
    """invite 也可能表示他人申请；必须明确核对扩展字段中的被邀请者。"""
    target: object = getattr(event, "invited_id", None)
    target_id = _positive_id(target)
    return (
        event.sub_type == "invite"
        and is_valid_request_flag(event.flag)
        and target_id is not None
        and target_id == _positive_id(bot.self_id)
    )


def is_verified_invitation(sub_type: str) -> bool:
    """旧记录的协议 subtype 无法证明邀请对象，不能据此调用平台审批。"""
    return sub_type == InvitationStage.INVITED


def is_request_unavailable(error: Exception) -> bool:
    return any(
        wording in str(error).casefold()
        for wording in ("matching group request not found", "already agree msg")
    )


async def read_invitation_group_state(bot: Bot, group_id: str) -> InvitationGroupState:
    """复用实时群列表中的名称；成员缓存不会删除离群成员，不能证明在群。"""
    try:
        result: object = await bot.get_group_list()
    except Exception as exc:
        raise InvitationStateError("get_group_list failed") from exc
    if not is_object_sequence(result):
        raise InvitationStateError("get_group_list returned a non-list response")
    joined = False
    group_name = None
    for item in result:
        if not is_object_mapping(item):
            raise InvitationStateError("get_group_list returned an invalid group")
        current_id = _positive_id(item.get("group_id"))
        if current_id is None:
            raise InvitationStateError("get_group_list returned an invalid group_id")
        if current_id == group_id:
            joined = True
            group_name = invitation_display_name(item.get("group_name")) or group_name
    return InvitationGroupState(joined=joined, group_name=group_name)


async def is_bot_in_group(bot: Bot, group_id: str) -> bool:
    return (await read_invitation_group_state(bot, group_id)).joined
