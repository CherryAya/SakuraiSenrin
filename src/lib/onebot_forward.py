from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Any, Literal

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message, MessageSegment

from src.lib.message_assets import serialize_message
from src.lib.message_delivery import DeliveryTarget
from src.logger import logger

ForwardReuseMode = Literal["bundle_hit", "prefix_hit", "rebuild_all"]


async def resolve_forward_sender(
    bot: Bot,
    *,
    fallback_nickname: str,
) -> tuple[int, str]:
    user_id = int(str(bot.self_id))
    try:
        login_info = await bot.call_api("get_login_info")
    except Exception:
        return user_id, fallback_nickname
    if isinstance(login_info, dict):
        nickname = str(login_info.get("nickname", "")).strip()
        if nickname:
            return user_id, nickname
    return user_id, fallback_nickname


def build_custom_forward_nodes(
    messages: Sequence[Message],
    *,
    user_id: int,
    nickname: str,
) -> list[MessageSegment]:
    return [
        MessageSegment.node_custom(
            user_id=user_id,
            nickname=nickname,
            content=message,
        )
        for message in messages
    ]


def serialize_custom_forward_payload(
    messages: Sequence[Message],
    *,
    user_id: int,
    nickname: str,
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        payload.append(
            {
                "user_id": str(user_id),
                "nickname": nickname,
                "content": json.loads(serialize_message(message)),
            }
        )
    return payload


async def send_custom_forward(
    bot: Bot,
    messages: Sequence[Message],
    *,
    event: MessageEvent | None = None,
    target: DeliveryTarget | None = None,
    fallback_nickname: str,
    bundle_asset_key: str = "",
    reuse_mode: ForwardReuseMode = "rebuild_all",
) -> Any:
    delivery_target = target
    if delivery_target is None:
        if event is None:
            raise ValueError("event or target is required for custom forward delivery")
        if isinstance(event, GroupMessageEvent):
            delivery_target = DeliveryTarget(
                kind="group",
                target_id=str(event.group_id),
            )
        else:
            delivery_target = DeliveryTarget(
                kind="private",
                target_id=str(event.user_id),
            )

    user_id, nickname = await resolve_forward_sender(
        bot,
        fallback_nickname=fallback_nickname,
    )
    nodes = build_custom_forward_nodes(
        messages,
        user_id=user_id,
        nickname=nickname,
    )
    payload_summary = serialize_custom_forward_payload(
        messages,
        user_id=user_id,
        nickname=nickname,
    )
    if delivery_target.kind == "group":
        logger.debug(
            "[OneBotForward] send merged forward payload "
            f"bundle_asset_key={bundle_asset_key or '-'} "
            f"target=group:{delivery_target.target_id} node_count={len(nodes)} "
            f"reuse_mode={reuse_mode} payload={payload_summary}"
        )
        return await bot.call_api(
            "send_group_forward_msg",
            group_id=int(delivery_target.target_id),
            messages=nodes,
        )
    logger.debug(
        "[OneBotForward] send merged forward payload "
        f"bundle_asset_key={bundle_asset_key or '-'} "
        f"target=private:{delivery_target.target_id} node_count={len(nodes)} "
        f"reuse_mode={reuse_mode} payload={payload_summary}"
    )
    return await bot.call_api(
        "send_private_forward_msg",
        user_id=int(delivery_target.target_id),
        messages=nodes,
    )
