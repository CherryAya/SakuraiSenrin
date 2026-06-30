from __future__ import annotations

from typing import Any, cast

from nonebot.compat import model_dump
from nonebot.exception import MockApiException
from nonebot.adapters import Bot as BaseBot
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot
from nonebot.adapters.onebot.v11.event import Event
from nonebot.adapters.onebot.v11.message import Message, MessageSegment

from src.lib.message_delivery import (
    DeliveryTarget,
    deliver_single_message,
    should_bypass_message_api_hook,
)

_HOOKS_INSTALLED = False


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


async def delivery_send_handler(
    bot: OneBotV11Bot,
    event: Event,
    message: str | Message | MessageSegment,
    at_sender: bool = False,
    reply_message: bool = False,
    **params: Any,
) -> dict[str, Any]:
    event_dict = model_dump(event)

    if "message_id" not in event_dict:
        reply_message = False

    if "user_id" in event_dict:
        params.setdefault("user_id", event_dict["user_id"])
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
    if reply_message:
        full_message += MessageSegment.reply(event_dict["message_id"])
    if at_sender and message_type != "private":
        full_message += MessageSegment.at(params["user_id"]) + " "
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
    data: dict[str, Any],
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
    result = await deliver_single_message(
        cast(Any, bot),
        target=target,
        message=data["message"],
        source_kind="onebot_send_api",
    )
    raise MockApiException({"message_id": result.message_id})


def install_message_delivery_hooks() -> None:
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    OneBotV11Bot.send_handler = delivery_send_handler
    BaseBot.on_calling_api(intercept_message_send_api)
    _HOOKS_INSTALLED = True
