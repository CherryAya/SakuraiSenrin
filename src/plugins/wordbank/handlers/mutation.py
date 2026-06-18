"""Wordbank mutation handlers for review and edit flows."""

from __future__ import annotations

from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import RuleError

from .parsers import (
    MutationActor,
    actor_can_review,
    parse_response_set_args,
    parse_response_weight_args,
    parse_trigger_probability_args,
    parse_trigger_set_args,
)


def build_mutation_actor(event: MessageEvent) -> MutationActor:
    user_id = str(event.user_id)
    group_id = str(getattr(event, "group_id", ""))
    sender = getattr(event, "sender", None)
    role = str(getattr(sender, "role", "") or "")
    can_moderate_group = event.message_type == "group" and role in {"owner", "admin"}
    from src.config import config

    return MutationActor(
        user_id=user_id,
        group_id=group_id,
        can_moderate_group=can_moderate_group,
        is_superuser=user_id in config.SUPERUSERS,
    )


async def handle_approve(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> str:
    if not response_item_id_text.isdigit():
        return tr(locale, "wordbank.error.entry_id_numeric")
    actor = build_mutation_actor(event)
    if not actor_can_review(actor):
        return tr(locale, "wordbank.approval.permission_denied")
    response_item_id = int(response_item_id_text)
    if await service.approve_response_item(
        response_item_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(locale, "wordbank.approval.approved", entry_id=response_item_id)
    return tr(locale, "wordbank.approval.not_found", entry_id=response_item_id)


async def handle_reject(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> str:
    if not response_item_id_text.isdigit():
        return tr(locale, "wordbank.error.entry_id_numeric")
    actor = build_mutation_actor(event)
    if not actor_can_review(actor):
        return tr(locale, "wordbank.approval.permission_denied")
    response_item_id = int(response_item_id_text)
    if await service.reject_response_item(
        response_item_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(locale, "wordbank.approval.rejected", entry_id=response_item_id)
    return tr(locale, "wordbank.approval.not_found", entry_id=response_item_id)


async def handle_delete(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> str:
    if not response_item_id_text.isdigit():
        return tr(locale, "wordbank.error.entry_id_numeric")
    response_item_id = int(response_item_id_text)
    actor = build_mutation_actor(event)
    if await service.delete_response_item(
        response_item_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(locale, "wordbank.delete.success", entry_id=response_item_id)
    return tr(locale, "wordbank.delete.not_found", entry_id=response_item_id)


async def handle_restore(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> str:
    if not response_item_id_text.isdigit():
        return tr(locale, "wordbank.error.entry_id_numeric")
    response_item_id = int(response_item_id_text)
    actor = build_mutation_actor(event)
    if await service.restore_response_item(
        response_item_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(locale, "wordbank.restore.success", entry_id=response_item_id)
    return tr(locale, "wordbank.restore.not_found", entry_id=response_item_id)


async def handle_trigger_probability_update(
    service: WordbankService,
    *,
    event: MessageEvent,
    trigger_group_id: int,
    probability: float,
) -> str:
    actor = build_mutation_actor(event)
    if await service.update_trigger_probability(
        trigger_group_id,
        probability=probability,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return f"trigger group #{trigger_group_id} 的触发概率已更新为 {probability:g}。"
    return f"未找到可修改的 trigger group #{trigger_group_id}，或你没有操作权限。"


async def handle_trigger_content_update(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    trigger_group_id: int,
    text: str,
    raw_message: Message,
) -> str:
    from .commands import (
        build_shape_from_text_and_images as _build_shape_from_text_and_images,
    )

    actor = build_mutation_actor(event)
    trigger_shape = await _build_shape_from_text_and_images(
        media_service,
        text=text,
        message=raw_message,
    )
    if await service.update_trigger_content(
        trigger_group_id,
        trigger_shape=trigger_shape,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return (
            f"trigger group #{trigger_group_id} 的触发词已更新，"
            "该组响应已重新进入待审核。"
        )
    return f"未找到可修改的 trigger group #{trigger_group_id}，或你没有操作权限。"


async def handle_response_weight_update(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id: int,
    weight: int,
) -> str:
    actor = build_mutation_actor(event)
    if await service.update_response_weight(
        response_item_id,
        weight=weight,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return f"词条 #{response_item_id} 的响应权重已更新为 {weight}。"
    return f"未找到可修改的词条 #{response_item_id}，或你没有操作权限。"


async def handle_response_content_update(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    response_item_id: int,
    text: str,
    raw_message: Message,
) -> str:
    from .commands import (
        build_shape_from_text_and_images as _build_shape_from_text_and_images,
    )

    actor = build_mutation_actor(event)
    response_shape = await _build_shape_from_text_and_images(
        media_service,
        text=text,
        message=raw_message,
    )
    if await service.update_response_content(
        response_item_id,
        response_shape=response_shape,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return f"词条 #{response_item_id} 的响应内容已更新，并重新进入待审核。"
    return f"未找到可修改的词条 #{response_item_id}，或你没有操作权限。"


async def handle_trigger_command(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    raw_message: Message | None,
    locale: LocaleCode,
    help_text: str,
) -> str:
    action, _, rest = text.partition(" ")
    action = action.lower()
    if action in {"prob", "probability", "概率"}:
        parsed = parse_trigger_probability_args(rest)
        return await handle_trigger_probability_update(
            service,
            event=event,
            trigger_group_id=parsed.trigger_group_id,
            probability=parsed.probability,
        )
    if action in {"set", "edit", "修改"}:
        if raw_message is None:
            raise RuntimeError("wordbank raw message is required for trigger set")
        parsed = parse_trigger_set_args(rest)
        return await handle_trigger_content_update(
            service,
            media_service,
            event=event,
            trigger_group_id=parsed.trigger_group_id,
            text=parsed.text,
            raw_message=raw_message,
        )
    raise RuleError(
        "trigger 子命令仅支持 prob / set。",
        key="wordbank.error.unknown_subcommand",
        action=f"trigger {action}".strip(),
        help=help_text,
    )


async def handle_response_command(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    raw_message: Message | None,
    locale: LocaleCode,
    help_text: str,
) -> str:
    action, _, rest = text.partition(" ")
    action = action.lower()
    if action in {"weight", "权重"}:
        parsed = parse_response_weight_args(rest)
        return await handle_response_weight_update(
            service,
            event=event,
            response_item_id=parsed.response_item_id,
            weight=parsed.weight,
        )
    if action in {"set", "edit", "修改"}:
        if raw_message is None:
            raise RuntimeError("wordbank raw message is required for response set")
        parsed = parse_response_set_args(rest)
        return await handle_response_content_update(
            service,
            media_service,
            event=event,
            response_item_id=parsed.response_item_id,
            text=parsed.text,
            raw_message=raw_message,
        )
    raise RuleError(
        "response 子命令仅支持 weight / set。",
        key="wordbank.error.unknown_subcommand",
        action=f"response {action}".strip(),
        help=help_text,
    )
