from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message, MessageSegment

from src.lib.message_assets import describe_message_asset, message_asset_repo

TargetKind = Literal["group", "private"]


@dataclass(slots=True, frozen=True)
class DeliveryTarget:
    kind: TargetKind
    target_id: str


@dataclass(slots=True, frozen=True)
class DeliveryResult:
    message_id: str
    reused_asset: bool
    asset_key: str | None = None


@dataclass(slots=True, frozen=True)
class ForwardNodeResult:
    message_id: str
    asset_key: str
    reused_asset: bool


def resolve_delivery_target(event: MessageEvent) -> DeliveryTarget:
    if isinstance(event, GroupMessageEvent):
        return DeliveryTarget(kind="group", target_id=str(event.group_id))
    return DeliveryTarget(kind="private", target_id=str(event.user_id))


def _extract_message_id(result: Any) -> str | None:
    if isinstance(result, dict):
        value = result.get("message_id")
    else:
        value = getattr(result, "message_id", None)
    if value is None:
        return None
    return str(value)


def _normalize_send_result(result: Any) -> DeliveryResult:
    message_id = _extract_message_id(result) or ""
    return DeliveryResult(message_id=message_id, reused_asset=False, asset_key=None)


async def _send_message(
    bot: Bot,
    target: DeliveryTarget,
    message: Message | str,
) -> Any:
    if target.kind == "group":
        return await bot.call_api(
            "send_group_msg",
            group_id=int(target.target_id),
            message=message,
        )
    return await bot.call_api(
        "send_private_msg",
        user_id=int(target.target_id),
        message=message,
    )


async def _try_forward_single_message(
    bot: Bot,
    *,
    target: DeliveryTarget,
    message_id: str,
    origin_message_type: str,
) -> Any:
    candidates: list[tuple[str, dict[str, str | int]]] = []
    if origin_message_type == "group":
        candidates.append(
            (
                "forward_group_single_msg",
                {
                    "message_id": message_id,
                    "group_id": target.target_id,
                }
                if target.kind == "group"
                else {
                    "message_id": message_id,
                    "user_id": target.target_id,
                },
            )
        )
        candidates.append(
            (
                "forward_friend_single_msg",
                {
                    "message_id": message_id,
                    "group_id": target.target_id,
                }
                if target.kind == "group"
                else {
                    "message_id": message_id,
                    "user_id": target.target_id,
                },
            )
        )
    else:
        candidates.append(
            (
                "forward_friend_single_msg",
                {
                    "message_id": message_id,
                    "group_id": target.target_id,
                }
                if target.kind == "group"
                else {
                    "message_id": message_id,
                    "user_id": target.target_id,
                },
            )
        )
        candidates.append(
            (
                "forward_group_single_msg",
                {
                    "message_id": message_id,
                    "group_id": target.target_id,
                }
                if target.kind == "group"
                else {
                    "message_id": message_id,
                    "user_id": target.target_id,
                },
            )
        )

    last_exc: Exception | None = None
    for api, payload in candidates:
        try:
            return await bot.call_api(api, **payload)
        except Exception as exc:  # pragma: no cover - adapter specific
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("no forward single message api candidate")


async def deliver_single_message(
    bot: Bot,
    *,
    target: DeliveryTarget,
    message: Message | str,
    source_kind: str,
    allow_asset_reuse: bool = True,
) -> DeliveryResult:
    descriptor = describe_message_asset(message)
    if allow_asset_reuse and descriptor.reusable_globally:
        asset = await message_asset_repo.get_asset(descriptor.asset_key)
        if asset is not None and asset.message_id:
            try:
                reused = await _try_forward_single_message(
                    bot,
                    target=target,
                    message_id=asset.message_id,
                    origin_message_type=asset.origin_message_type,
                )
                message_id = _extract_message_id(reused) or asset.message_id
                return DeliveryResult(
                    message_id=message_id,
                    reused_asset=True,
                    asset_key=descriptor.asset_key,
                )
            except Exception as exc:
                await message_asset_repo.mark_stale(
                    descriptor.asset_key,
                    last_verify_error=str(exc),
                )

    send_result = await _send_message(bot, target, message)
    normalized = _normalize_send_result(send_result)
    if descriptor.reusable_globally and normalized.message_id:
        await message_asset_repo.upsert_asset(
            asset_key=descriptor.asset_key,
            content_hash=descriptor.content_hash,
            asset_kind="single_message",
            source_kind=source_kind,
            message_id=normalized.message_id,
            sender_bot_id=str(bot.self_id),
            origin_message_type=target.kind,
            origin_target_id=target.target_id,
            message_shape_kind=descriptor.message_shape_kind,
        )
        return DeliveryResult(
            message_id=normalized.message_id,
            reused_asset=False,
            asset_key=descriptor.asset_key,
        )
    return normalized


async def ensure_forward_node(
    bot: Bot,
    *,
    node_message: Message,
    source_kind: str,
    fallback_nickname: str,
) -> ForwardNodeResult:
    descriptor = describe_message_asset(node_message)
    if descriptor.reusable_globally:
        asset = await message_asset_repo.get_asset(descriptor.asset_key)
        if asset is not None and asset.message_id:
            return ForwardNodeResult(
                message_id=asset.message_id,
                asset_key=descriptor.asset_key,
                reused_asset=True,
            )

    staging_target = DeliveryTarget(kind="private", target_id=str(bot.self_id))
    staged = await _send_message(bot, staging_target, node_message)
    message_id = _extract_message_id(staged) or ""
    if not message_id:
        raise RuntimeError("failed to stage forward node message")
    if descriptor.reusable_globally and message_id:
        await message_asset_repo.upsert_asset(
            asset_key=descriptor.asset_key,
            content_hash=descriptor.content_hash,
            asset_kind="forward_node",
            source_kind=source_kind,
            message_id=message_id,
            sender_bot_id=str(bot.self_id),
            origin_message_type="private",
            origin_target_id=str(bot.self_id),
            message_shape_kind=descriptor.message_shape_kind,
        )
    return ForwardNodeResult(
        message_id=message_id,
        asset_key=descriptor.asset_key,
        reused_asset=False,
    )


async def deliver_forward_messages(
    bot: Bot,
    event: MessageEvent,
    messages: Sequence[Message],
    *,
    source_kind: str,
    fallback_nickname: str,
) -> None:
    nodes: list[MessageSegment] = []
    node_results: list[ForwardNodeResult] = []
    try:
        for message in messages:
            node = await ensure_forward_node(
                bot,
                node_message=message,
                source_kind=source_kind,
                fallback_nickname=fallback_nickname,
            )
            node_results.append(node)
            nodes.append(MessageSegment.node(int(node.message_id)))
    except Exception as exc:
        for node in node_results:
            await message_asset_repo.mark_stale(
                node.asset_key,
                last_verify_error=str(exc),
            )
        from src.lib.onebot_forward import send_custom_forward

        await send_custom_forward(
            bot,
            event,
            messages,
            fallback_nickname=fallback_nickname,
        )
        return

    try:
        target = resolve_delivery_target(event)
        if target.kind == "group":
            await bot.call_api(
                "send_group_forward_msg",
                group_id=int(target.target_id),
                messages=nodes,
            )
            return
        await bot.call_api(
            "send_private_forward_msg",
            user_id=int(target.target_id),
            messages=nodes,
        )
    except Exception as exc:
        for node in node_results:
            await message_asset_repo.mark_stale(
                node.asset_key,
                last_verify_error=str(exc),
            )
        from src.lib.onebot_forward import send_custom_forward

        await send_custom_forward(
            bot,
            event,
            messages,
            fallback_nickname=fallback_nickname,
        )
