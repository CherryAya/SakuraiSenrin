from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import cast

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.matcher import Matcher

from src.lib.message_delivery import (
    DeliveryResult,
    DeliveryTarget,
    deliver_forward_messages,
    deliver_single_message,
    resolve_delivery_target,
)
from src.lib.messages import empty_message


@dataclass(slots=True, frozen=True)
class TextBlock:
    text: str


@dataclass(slots=True, frozen=True)
class ImageBytesBlock:
    image_bytes: bytes


@dataclass(slots=True, frozen=True)
class AtRefBlock:
    target_id: str


@dataclass(slots=True, frozen=True)
class ReplyRefBlock:
    message_id: str


@dataclass(slots=True, frozen=True)
class RawMessageBlock:
    message: Message


type MessagePlanBlock = (
    TextBlock | ImageBytesBlock | AtRefBlock | ReplyRefBlock | RawMessageBlock
)


@dataclass(slots=True, frozen=True)
class MessagePlanEntry:
    blocks: tuple[MessagePlanBlock, ...]

    @classmethod
    def from_message(cls, message: Message | str) -> MessagePlanEntry:
        if isinstance(message, str):
            return cls(blocks=(TextBlock(message),))
        return cls(blocks=(RawMessageBlock(message),))


type MessagePlanInput = Message | str | MessagePlanEntry


@dataclass(slots=True, frozen=True)
class DeliveryPlan:
    messages: tuple[MessagePlanInput, ...]
    source_kind: str = ""
    fallback_nickname: str = ""
    wait_message: MessagePlanInput | None = None
    allow_asset_reuse: bool = True
    force_forward: bool | None = None

    @property
    def should_forward(self) -> bool:
        if self.force_forward is not None:
            return self.force_forward
        return len(self.messages) > 1


@dataclass(slots=True, frozen=True)
class DeliveryPlanResult:
    wait_result: DeliveryResult | None
    results: tuple[DeliveryResult, ...]
    used_forward: bool


def with_delivery_defaults(
    plan: DeliveryPlan,
    *,
    source_kind: str,
    fallback_nickname: str = "",
    wait_message: MessagePlanInput | None = None,
) -> DeliveryPlan:
    updated = plan
    if not updated.source_kind:
        updated = replace(updated, source_kind=source_kind)
    if fallback_nickname and not updated.fallback_nickname:
        updated = replace(updated, fallback_nickname=fallback_nickname)
    if wait_message is not None and updated.wait_message is None:
        updated = replace(updated, wait_message=wait_message)
    return updated


def normalize_message_plan_entry(entry: MessagePlanInput) -> MessagePlanEntry:
    if isinstance(entry, MessagePlanEntry):
        return entry
    return MessagePlanEntry.from_message(entry)


def build_text_plan_entry(text: str) -> MessagePlanEntry:
    return MessagePlanEntry(blocks=(TextBlock(text),))


def build_image_plan_entry(image_bytes: bytes) -> MessagePlanEntry:
    return MessagePlanEntry(blocks=(ImageBytesBlock(image_bytes),))


def append_image_plan_entry(
    entry: MessagePlanEntry,
    image_bytes: bytes,
) -> MessagePlanEntry:
    return MessagePlanEntry(blocks=(*entry.blocks, ImageBytesBlock(image_bytes)))


async def build_image_or_text_plan_entry(
    *,
    image_bytes: bytes | None,
    fallback_text: str | Awaitable[str],
) -> MessagePlanEntry:
    if image_bytes is not None:
        return build_image_plan_entry(image_bytes)
    if isinstance(fallback_text, Awaitable):
        fallback_text = await fallback_text
    return build_text_plan_entry(fallback_text)


async def build_preferred_message_plan[TPlanResult](
    *,
    preferred_builder: Callable[[], Awaitable[TPlanResult]],
    alternative_builder: Callable[[], Awaitable[TPlanResult] | TPlanResult],
    on_preferred_error: Callable[[Exception], None] | None = None,
) -> TPlanResult:
    try:
        return await preferred_builder()
    except Exception as exc:
        if on_preferred_error is not None:
            on_preferred_error(exc)

    alternative = alternative_builder()
    if isinstance(alternative, Awaitable):
        return await alternative
    return alternative


def render_message_plan_entry(entry: MessagePlanEntry) -> Message:
    message = empty_message()
    for block in entry.blocks:
        if isinstance(block, TextBlock):
            if block.text:
                message += MessageSegment.text(block.text)
            continue
        if isinstance(block, ImageBytesBlock):
            message += MessageSegment.image(block.image_bytes)
            continue
        if isinstance(block, AtRefBlock):
            if block.target_id:
                message += MessageSegment.at(block.target_id)
            continue
        if isinstance(block, ReplyRefBlock):
            if block.message_id.isdigit():
                message += MessageSegment.reply(int(block.message_id))
            continue
        message += block.message
    return message


def render_message_plan_input(entry: MessagePlanInput) -> Message:
    return render_message_plan_entry(normalize_message_plan_entry(entry))


def render_delivery_plan_messages(plan: DeliveryPlan) -> tuple[Message, ...]:
    return tuple(render_message_plan_input(entry) for entry in plan.messages)


def _render_matcher_message_input(message: MessagePlanInput) -> Message | str:
    if isinstance(message, MessagePlanEntry):
        return render_message_plan_entry(message)
    return message


async def deliver_message_plan(
    bot: Bot,
    *,
    plan: DeliveryPlan,
    event: MessageEvent | None = None,
    target: DeliveryTarget | None = None,
) -> DeliveryPlanResult:
    delivery_target = target
    if delivery_target is None:
        if event is None:
            raise ValueError("event or target is required for delivery")
        delivery_target = resolve_delivery_target(event)

    wait_result: DeliveryResult | None = None
    if plan.wait_message is not None:
        wait_result = await deliver_single_message(
            bot,
            target=delivery_target,
            message=render_message_plan_input(plan.wait_message),
            source_kind=plan.source_kind,
            allow_asset_reuse=False,
        )

    rendered_messages = render_delivery_plan_messages(plan)
    if plan.should_forward:
        if event is None:
            raise ValueError("forward delivery requires event")
        forward_result = await deliver_forward_messages(
            bot,
            event,
            rendered_messages,
            source_kind=plan.source_kind,
            fallback_nickname=plan.fallback_nickname,
        )
        return DeliveryPlanResult(
            wait_result=wait_result,
            results=(forward_result,),
            used_forward=True,
        )

    results = []
    for rendered in rendered_messages:
        results.append(
            await deliver_single_message(
                bot,
                target=delivery_target,
                message=rendered,
                source_kind=plan.source_kind,
                allow_asset_reuse=plan.allow_asset_reuse,
            )
        )
    return DeliveryPlanResult(
        wait_result=wait_result,
        results=tuple(results),
        used_forward=False,
    )


async def finish_with_delivery_plan(
    bot: Bot,
    matcher: Matcher,
    *,
    plan: DeliveryPlan,
    event: MessageEvent | None = None,
    target: DeliveryTarget | None = None,
) -> None:
    await deliver_message_plan(
        bot,
        plan=plan,
        event=event,
        target=target,
    )
    await matcher.finish()


async def finish_with_message(
    bot: Bot | None,
    matcher: Matcher,
    *,
    message: MessagePlanInput,
    source_kind: str,
    event: MessageEvent | None = None,
    target: DeliveryTarget | None = None,
    fallback_nickname: str = "",
    allow_asset_reuse: bool = True,
    force_forward: bool | None = None,
) -> None:
    rendered_message = _render_matcher_message_input(message)
    if target is None and force_forward is not True and not fallback_nickname:
        await matcher.finish(rendered_message)
        return
    delivery_capable_bot = isinstance(bot, Bot)
    if not delivery_capable_bot or (event is None and target is None):
        await matcher.finish(rendered_message)
        return
    await finish_with_delivery_plan(
        cast(Bot, bot),
        matcher,
        plan=DeliveryPlan(
            messages=(message,),
            source_kind=source_kind,
            fallback_nickname=fallback_nickname,
            allow_asset_reuse=allow_asset_reuse,
            force_forward=force_forward,
        ),
        event=event,
        target=target,
    )


async def reject_with_message(
    matcher: Matcher,
    *,
    message: MessagePlanInput,
) -> None:
    await matcher.reject(_render_matcher_message_input(message))


async def send_with_message(
    matcher: Matcher,
    *,
    message: MessagePlanInput,
) -> None:
    await matcher.send(_render_matcher_message_input(message))


async def pause_with_message(
    matcher: Matcher,
    *,
    message: MessagePlanInput,
) -> None:
    await matcher.pause(_render_matcher_message_input(message))
