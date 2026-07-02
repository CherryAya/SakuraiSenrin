from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from typing import Any, Literal

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupMessageEvent,
    MessageEvent,
    NoticeEvent,
)
from nonebot.adapters.onebot.v11.message import Message

from src.lib.message_assets import describe_message_asset, message_asset_repo
from src.logger import logger

TargetKind = Literal["group", "private"]
_MESSAGE_API_HOOK_BYPASS: ContextVar[int] = ContextVar(
    "message_api_hook_bypass",
    default=0,
)


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


@dataclass(slots=True, frozen=True)
class ForwardReusePolicy:
    preserve_node_time_order: bool = True


DEFAULT_FORWARD_REUSE_POLICY = ForwardReusePolicy()


@dataclass(slots=True, frozen=True)
class ForwardBatchDescriptor:
    context_key: str
    node_context_keys: tuple[str, ...]
    node_asset_keys: tuple[str, ...]


def _short_key(value: str, *, length: int = 12) -> str:
    if not value:
        return "-"
    return value[:length]


def _message_segment_count(message: Message | str) -> int:
    return 1 if isinstance(message, str) else len(message)


def should_bypass_message_api_hook() -> bool:
    return _MESSAGE_API_HOOK_BYPASS.get() > 0


@contextmanager
def bypass_message_api_hook() -> Any:
    token = _MESSAGE_API_HOOK_BYPASS.set(_MESSAGE_API_HOOK_BYPASS.get() + 1)
    try:
        yield
    finally:
        _MESSAGE_API_HOOK_BYPASS.reset(token)


def resolve_delivery_target(event: MessageEvent) -> DeliveryTarget:
    if isinstance(event, GroupMessageEvent):
        return DeliveryTarget(kind="group", target_id=str(event.group_id))
    return DeliveryTarget(kind="private", target_id=str(event.user_id))


def resolve_notice_delivery_target(event: NoticeEvent) -> DeliveryTarget:
    group_id = str(getattr(event, "group_id", "") or "")
    if group_id:
        return DeliveryTarget(kind="group", target_id=group_id)
    return DeliveryTarget(
        kind="private",
        target_id=str(getattr(event, "user_id", "")),
    )


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


def build_forward_context_key(
    messages: Sequence[Message],
    *,
    policy: ForwardReusePolicy,
) -> str:
    if not policy.preserve_node_time_order:
        logger.debug(
            "[MessageDelivery] forward context disabled "
            "reason=preserve_node_time_order_false"
        )
        return ""
    asset_keys = [describe_message_asset(message).asset_key for message in messages]
    payload = "|".join(asset_keys)
    context_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    logger.debug(
        "[MessageDelivery] built forward context "
        f"context={_short_key(context_key)} nodes={len(messages)} "
        f"node_hashes={[_short_key(asset_key) for asset_key in asset_keys]}"
    )
    return context_key


def build_forward_batch_descriptor(
    messages: Sequence[Message],
    *,
    policy: ForwardReusePolicy,
) -> ForwardBatchDescriptor:
    context_key = build_forward_context_key(messages, policy=policy)
    node_context_keys: list[str] = []
    node_asset_keys: list[str] = []
    prefix_parts: list[str] = []
    for index, message in enumerate(messages):
        descriptor = describe_message_asset(message)
        if not context_key:
            node_context_keys.append("")
            node_asset_keys.append(descriptor.asset_key)
            logger.debug(
                "[MessageDelivery] forward node descriptor "
                f"index={index} batch_ctx=- node_ctx=- "
                f"asset_key={_short_key(descriptor.asset_key)} "
                f"content_hash={_short_key(descriptor.content_hash)}"
            )
            continue
        prefix_parts.append(descriptor.asset_key)
        node_context_key = hashlib.sha256("|".join(prefix_parts).encode()).hexdigest()
        node_context_keys.append(node_context_key)
        node_asset_key = hashlib.sha256(
            f"{node_context_key}:{descriptor.asset_key}".encode()
        )
        node_asset_digest = node_asset_key.hexdigest()
        node_asset_keys.append(node_asset_digest)
        logger.debug(
            "[MessageDelivery] forward node descriptor "
            f"index={index} batch_ctx={_short_key(context_key)} "
            f"node_ctx={_short_key(node_context_key)} "
            f"asset_key={_short_key(node_asset_digest)} "
            f"content_hash={_short_key(descriptor.content_hash)}"
        )
    logger.debug(
        "[MessageDelivery] built forward batch descriptor "
        f"context={_short_key(context_key)} nodes={len(messages)}"
    )
    return ForwardBatchDescriptor(
        context_key=context_key,
        node_context_keys=tuple(node_context_keys),
        node_asset_keys=tuple(node_asset_keys),
    )


async def _send_message(
    bot: Bot,
    target: DeliveryTarget,
    message: Message | str,
) -> Any:
    logger.debug(
        "[MessageDelivery] send message "
        f"target={target.kind}:{target.target_id} "
        f"segments={_message_segment_count(message)}"
    )
    with bypass_message_api_hook():
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
    primary_api = (
        "forward_group_single_msg"
        if target.kind == "group"
        else "forward_friend_single_msg"
    )
    fallback_api = (
        "forward_friend_single_msg"
        if primary_api == "forward_group_single_msg"
        else "forward_group_single_msg"
    )
    payload: dict[str, str | int]
    if target.kind == "group":
        payload = {
            "message_id": message_id,
            "group_id": target.target_id,
        }
    else:
        payload = {
            "message_id": message_id,
            "user_id": target.target_id,
        }
    candidates: list[tuple[str, dict[str, str | int]]] = [
        (primary_api, payload),
        (fallback_api, payload),
    ]

    last_exc: Exception | None = None
    for api, payload in candidates:
        try:
            logger.debug(
                "[MessageDelivery] try forward single message "
                f"api={api} origin={origin_message_type} "
                f"target={target.kind}:{target.target_id} message_id={message_id}"
            )
            return await bot.call_api(api, **payload)
        except Exception as exc:  # pragma: no cover - adapter specific
            logger.debug(
                "[MessageDelivery] forward single message failed "
                f"api={api} message_id={message_id} error={exc}"
            )
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
    reuse_skip_reason = (
        "reuse_disabled"
        if not allow_asset_reuse
        else descriptor.disqualify_reason or "not_reusable"
    )
    logger.debug(
        "[MessageDelivery] deliver single message "
        f"source={source_kind} target={target.kind}:{target.target_id} "
        f"asset_key={_short_key(descriptor.asset_key)} "
        f"content_hash={_short_key(descriptor.content_hash)} "
        f"shape={descriptor.message_shape_kind} "
        f"reusable={descriptor.reusable_globally} "
        f"allow_reuse={allow_asset_reuse} "
        f"reason={descriptor.disqualify_reason or '-'}"
    )
    if allow_asset_reuse and descriptor.reusable_globally:
        asset = await message_asset_repo.get_asset(descriptor.asset_key)
        if asset is not None and asset.message_id:
            try:
                logger.debug(
                    "[MessageDelivery] single message reuse hit "
                    f"asset_key={_short_key(descriptor.asset_key)} "
                    f"cached_message_id={asset.message_id} "
                    "origin="
                    f"{asset.origin_message_type}:{asset.origin_target_id or '-'}"
                )
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
                logger.debug(
                    "[MessageDelivery] single message reuse invalidated "
                    f"asset_key={_short_key(descriptor.asset_key)} error={exc}"
                )
                await message_asset_repo.mark_stale(
                    descriptor.asset_key,
                    last_verify_error=str(exc),
                )
        else:
            miss_reason = (
                "empty_cached_message_id" if asset is not None else "cache_miss"
            )
            logger.debug(
                "[MessageDelivery] single message reuse miss "
                f"asset_key={_short_key(descriptor.asset_key)} "
                f"reason={miss_reason}"
            )
    else:
        logger.debug(
            "[MessageDelivery] single message reuse skipped "
            f"asset_key={_short_key(descriptor.asset_key)} "
            f"reason={reuse_skip_reason}"
        )

    send_result = await _send_message(bot, target, message)
    normalized = _normalize_send_result(send_result)
    store_skip_reason = (
        "empty_message_id"
        if not normalized.message_id
        else descriptor.disqualify_reason or "not_reusable"
    )
    logger.debug(
        "[MessageDelivery] single message sent "
        f"asset_key={_short_key(descriptor.asset_key)} "
        f"message_id={normalized.message_id or '-'}"
    )
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
    logger.debug(
        "[MessageDelivery] single message asset not stored "
        f"asset_key={_short_key(descriptor.asset_key)} "
        f"reason={store_skip_reason}"
    )
    return normalized


async def ensure_forward_node(
    bot: Bot,
    *,
    node_message: Message,
    node_asset_key: str,
    source_kind: str,
    fallback_nickname: str,
    forward_context_key: str = "",
    forward_sort_key: int = 0,
    allow_asset_reuse: bool = True,
) -> ForwardNodeResult:
    descriptor = describe_message_asset(node_message)
    reuse_skip_reason = (
        "reuse_disabled"
        if not allow_asset_reuse
        else descriptor.disqualify_reason or "not_reusable"
    )
    logger.debug(
        "[MessageDelivery] ensure forward node "
        f"source={source_kind} asset_key={_short_key(node_asset_key)} "
        f"content_hash={_short_key(descriptor.content_hash)} "
        f"forward_ctx={_short_key(forward_context_key)} "
        f"sort={forward_sort_key} reusable={descriptor.reusable_globally} "
        f"allow_reuse={allow_asset_reuse} "
        f"reason={descriptor.disqualify_reason or '-'}"
    )
    if descriptor.reusable_globally and allow_asset_reuse:
        asset = await message_asset_repo.get_forward_node_asset(node_asset_key)
        if (
            asset is not None
            and asset.message_id
            and asset.forward_context_key == forward_context_key
        ):
            logger.debug(
                "[MessageDelivery] forward node reuse hit "
                f"asset_key={_short_key(node_asset_key)} message_id={asset.message_id}"
            )
            return ForwardNodeResult(
                message_id=asset.message_id,
                asset_key=node_asset_key,
                reused_asset=True,
            )
        reason = "cache_miss"
        if asset is not None and not asset.message_id:
            reason = "empty_cached_message_id"
        elif (
            asset is not None
            and asset.message_id
            and asset.forward_context_key != forward_context_key
        ):
            reason = (
                "forward_context_mismatch:"
                f"{_short_key(asset.forward_context_key)}!={_short_key(forward_context_key)}"
            )
        logger.debug(
            "[MessageDelivery] forward node reuse miss "
            f"asset_key={_short_key(node_asset_key)} reason={reason}"
        )
    else:
        logger.debug(
            "[MessageDelivery] forward node reuse skipped "
            f"asset_key={_short_key(node_asset_key)} "
            f"reason={reuse_skip_reason}"
        )

    staging_target = DeliveryTarget(kind="private", target_id=str(bot.self_id))
    logger.debug(
        "[MessageDelivery] stage forward node "
        f"asset_key={_short_key(node_asset_key)} staging_target=private:{bot.self_id}"
    )
    try:
        staged = await _send_message(bot, staging_target, node_message)
    except Exception as exc:
        await message_asset_repo.mark_stale(
            node_asset_key,
            last_verify_error=str(exc),
        )
        raise
    message_id = _extract_message_id(staged) or ""
    if not message_id:
        raise RuntimeError("failed to stage forward node message")
    logger.debug(
        "[MessageDelivery] staged forward node "
        f"asset_key={_short_key(node_asset_key)} message_id={message_id}"
    )
    if descriptor.reusable_globally and message_id:
        await message_asset_repo.upsert_asset(
            asset_key=node_asset_key,
            content_hash=descriptor.content_hash,
            asset_kind="forward_node",
            source_kind=source_kind,
            message_id=message_id,
            sender_bot_id=str(bot.self_id),
            origin_message_type="private",
            origin_target_id=str(bot.self_id),
            message_shape_kind=descriptor.message_shape_kind,
            forward_context_key=forward_context_key,
            forward_sort_key=forward_sort_key,
        )
    else:
        logger.debug(
            "[MessageDelivery] forward node asset not stored "
            f"asset_key={_short_key(node_asset_key)} "
            f"reason={descriptor.disqualify_reason or 'not_reusable'}"
        )
    return ForwardNodeResult(
        message_id=message_id,
        asset_key=node_asset_key,
        reused_asset=False,
    )


async def _resolve_reusable_forward_prefix_length(
    batch: ForwardBatchDescriptor,
) -> int:
    if not batch.node_asset_keys:
        logger.debug("[MessageDelivery] forward prefix reuse empty batch")
        return 0
    previous_sort_tuple: tuple[int, int, int] | None = None
    reusable_count = 0
    for index, node_asset_key in enumerate(batch.node_asset_keys):
        asset = await message_asset_repo.get_forward_node_asset(node_asset_key)
        if asset is None:
            logger.debug(
                "[MessageDelivery] forward prefix stop "
                f"index={index} asset_key={_short_key(node_asset_key)} "
                "reason=cache_miss"
            )
            break
        if not asset.message_id:
            logger.debug(
                "[MessageDelivery] forward prefix stop "
                f"index={index} asset_key={_short_key(node_asset_key)} "
                "reason=empty_cached_message_id"
            )
            break
        if asset.forward_context_key != batch.node_context_keys[index]:
            logger.debug(
                "[MessageDelivery] forward prefix stop "
                f"index={index} asset_key={_short_key(node_asset_key)} "
                "reason=forward_context_mismatch "
                f"cached={_short_key(asset.forward_context_key)} "
                f"expected={_short_key(batch.node_context_keys[index])}"
            )
            break
        current_sort_tuple = (
            asset.created_at,
            asset.updated_at,
            asset.forward_sort_key,
        )
        if previous_sort_tuple is not None and current_sort_tuple < previous_sort_tuple:
            logger.debug(
                "[MessageDelivery] forward prefix stop "
                f"index={index} asset_key={_short_key(node_asset_key)} "
                f"reason=non_monotonic_sort current={current_sort_tuple} "
                f"previous={previous_sort_tuple}"
            )
            break
        previous_sort_tuple = current_sort_tuple
        reusable_count += 1
        logger.debug(
            "[MessageDelivery] forward prefix reusable "
            f"index={index} asset_key={_short_key(node_asset_key)} "
            f"message_id={asset.message_id} sort={current_sort_tuple}"
        )
    logger.debug(
        "[MessageDelivery] forward prefix resolved "
        f"reusable_count={reusable_count} total={len(batch.node_asset_keys)} "
        f"context={_short_key(batch.context_key)}"
    )
    return reusable_count


async def deliver_forward_messages(
    bot: Bot,
    event: MessageEvent,
    messages: Sequence[Message],
    *,
    source_kind: str,
    fallback_nickname: str,
    reuse_policy: ForwardReusePolicy = DEFAULT_FORWARD_REUSE_POLICY,
) -> DeliveryResult:
    batch = build_forward_batch_descriptor(messages, policy=reuse_policy)
    target = resolve_delivery_target(event)
    bundle_asset_key = batch.context_key
    logger.debug(
        "[MessageDelivery] deliver forward messages "
        f"source={source_kind} event={event.message_type} "
        f"target={target.kind}:{target.target_id} "
        f"nodes={len(messages)} batch_ctx={_short_key(batch.context_key)}"
    )
    from src.lib.onebot_forward import send_custom_forward

    if bundle_asset_key:
        bundle_asset = await message_asset_repo.get_forward_bundle_asset(
            bundle_asset_key
        )
        if bundle_asset is not None and bundle_asset.message_id:
            try:
                bundle_origin = (
                    f"{bundle_asset.origin_message_type}:"
                    f"{bundle_asset.origin_target_id or '-'}"
                )
                logger.debug(
                    "[MessageDelivery] forward bundle reuse hit "
                    f"asset_key={_short_key(bundle_asset_key)} "
                    f"message_id={bundle_asset.message_id} "
                    f"origin={bundle_origin}"
                )
                await _try_forward_single_message(
                    bot,
                    target=target,
                    message_id=bundle_asset.message_id,
                    origin_message_type=bundle_asset.origin_message_type,
                )
                return DeliveryResult(
                    message_id=bundle_asset.message_id,
                    reused_asset=True,
                    asset_key=bundle_asset_key,
                )
            except Exception as exc:
                logger.debug(
                    "[MessageDelivery] forward bundle reuse invalidated "
                    f"asset_key={_short_key(bundle_asset_key)} error={exc}"
                )
                await message_asset_repo.mark_stale(
                    bundle_asset_key,
                    last_verify_error=str(exc),
                )

    reusable_prefix_length = await _resolve_reusable_forward_prefix_length(batch)
    reuse_mode = "prefix_hit" if reusable_prefix_length > 0 else "rebuild_all"
    logger.debug(
        "[MessageDelivery] merged forward path "
        f"mode=node_custom_direct reuse_mode={reuse_mode} "
        f"reusable_prefix={reusable_prefix_length}/{len(messages)} "
        "reason=napcat_send_group_forward_msg_requires_custom_nodes"
    )
    for index in range(reusable_prefix_length, len(messages)):
        await ensure_forward_node(
            bot,
            node_message=messages[index],
            node_asset_key=batch.node_asset_keys[index],
            source_kind=source_kind,
            fallback_nickname=fallback_nickname,
            forward_context_key=batch.node_context_keys[index],
            forward_sort_key=index,
            allow_asset_reuse=False,
        )

    send_result = await send_custom_forward(
        bot,
        event,
        messages,
        fallback_nickname=fallback_nickname,
        bundle_asset_key=bundle_asset_key,
        reuse_mode=reuse_mode,
    )
    message_id = _extract_message_id(send_result) or ""
    if bundle_asset_key and message_id:
        await message_asset_repo.upsert_forward_bundle_asset(
            asset_key=bundle_asset_key,
            content_hash=bundle_asset_key,
            source_kind=source_kind,
            message_id=message_id,
            sender_bot_id=str(bot.self_id),
            origin_message_type=target.kind,
            origin_target_id=target.target_id,
            forward_context_key=batch.context_key,
        )
    elif bundle_asset_key:
        logger.debug(
            "[MessageDelivery] forward bundle asset not stored "
            f"asset_key={_short_key(bundle_asset_key)} reason=empty_message_id"
        )
    return DeliveryResult(
        message_id=message_id,
        reused_asset=False,
        asset_key=bundle_asset_key or None,
    )
