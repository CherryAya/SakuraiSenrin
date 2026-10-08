from __future__ import annotations

from typing import cast

from nonebot.adapters import Bot as BaseBot
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot
from nonebot.adapters.onebot.v11.event import Event
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.compat import model_dump
from nonebot.exception import MockApiException

from src.lib.message_delivery import (
    DeliveryTarget,
    deliver_single_message,
    should_bypass_message_api_hook,
)
from src.lib.types import JsonValue

_hooks_installed = False


def _resolve_send_target(
    *,
    message_type: str | None,
    group_id: object | None,
    user_id: object | None,
) -> DeliveryTarget | None:
    if message_type == "group" and group_id is not None:
        return DeliveryTarget(kind="group", target_id=str(group_id))
    if message_type == "private" and user_id is not None:
        return DeliveryTarget(kind="private", target_id=str(user_id))
    if group_id is not None:
        return DeliveryTarget(kind="group", target_id=str(group_id))
    if user_id is not None:
        return DeliveryTarget(kind="private", target_id=str(user_id))
    return None


def _as_sender_id(value: JsonValue) -> int | str | None:
    """把 JSON 值收窄成 ``at`` 可接受的发送者 ID。

    ``bool`` 是 ``int`` 子类，需显式排除，避免 ``True`` 被当作 ID 1。
    """
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, str)) else None


async def delivery_send_handler(
    bot: OneBotV11Bot,
    event: Event,
    message: str | Message | MessageSegment,
    at_sender: bool = False,
    reply_message: bool = False,
    **params: JsonValue,
) -> dict[str, JsonValue]:
    event_dict = model_dump(event)

    message_id = _as_sender_id(event_dict.get("message_id"))
    if message_id is None:
        reply_message = False

    sender_id = _as_sender_id(event_dict.get("user_id"))
    if sender_id is not None:
        params.setdefault("user_id", sender_id)
    else:
        at_sender = False

    if "group_id" in event_dict:
        params.setdefault("group_id", event_dict["group_id"])

    if "message_type" in event_dict:
        params.setdefault("message_type", event_dict["message_type"])

    message_type = cast(str | None, params.get("message_type"))
    if message_type is None:
        if params.get("group_id") is not None:
            message_type = "group"
        elif params.get("user_id") is not None:
            message_type = "private"
        else:
            raise ValueError("Cannot guess message type to reply!")

    full_message = Message()
    if reply_message and message_id is not None:
        full_message += MessageSegment.reply(int(message_id))
    if at_sender and message_type != "private" and sender_id is not None:
        full_message += MessageSegment.at(sender_id) + " "
    full_message += message

    target = _resolve_send_target(
        message_type=message_type,
        group_id=params.get("group_id"),
        user_id=params.get("user_id"),
    )
    if target is None:
        raise ValueError("Cannot resolve delivery target to reply!")

    result = await deliver_single_message(
        bot,
        target=target,
        message=full_message,
        source_kind="onebot_send_handler",
    )
    return {"message_id": result.message_id}


async def intercept_message_send_api(
    bot: BaseBot,
    api: str,
    data: dict[str, JsonValue],
) -> None:
    if should_bypass_message_api_hook():
        return
    if api not in {"send_msg", "send_group_msg", "send_private_msg"}:
        return

    target: DeliveryTarget | None = None
    if api == "send_group_msg":
        group_id = data.get("group_id")
        if group_id is None:
            return
        target = DeliveryTarget(kind="group", target_id=str(group_id))
    elif api == "send_private_msg":
        user_id = data.get("user_id")
        if user_id is None:
            return
        target = DeliveryTarget(kind="private", target_id=str(user_id))
    else:
        target = _resolve_send_target(
            message_type=cast(str | None, data.get("message_type")),
            group_id=data.get("group_id"),
            user_id=data.get("user_id"),
        )
        if target is None:
            return

    if "message" not in data:
        return
    outgoing = data["message"]
    if not isinstance(outgoing, (str, Message)):
        return
    result = await deliver_single_message(
        cast(OneBotV11Bot, bot),
        target=target,
        message=outgoing,
        source_kind="onebot_send_api",
    )
    raise MockApiException({"message_id": result.message_id})


def install_message_delivery_hooks() -> None:
    global _hooks_installed
    if _hooks_installed:
        return
    setattr(OneBotV11Bot, "send_handler", delivery_send_handler)
    BaseBot.on_calling_api(intercept_message_send_api)
    _hooks_installed = True
