"""Wordbank mutation handlers for review and edit flows."""

from __future__ import annotations

from dataclasses import dataclass
import re

from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.database.types import (
    WordbankGroupDetail,
    WordbankReviewHistoryEntry,
)
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.presentation import (
    format_scope_label,
    format_status_label,
    format_timestamp,
)
from src.plugins.wordbank.services.rules import RuleError
from src.plugins.wordbank.text_parsing import normalize_cq_plain_text

from .parsers import (
    MutationActor,
    actor_can_review,
    parse_response_set_args,
    parse_response_weight_args,
    parse_trigger_probability_args,
    parse_trigger_set_args,
)


@dataclass(slots=True, frozen=True)
class ApprovalMutationOutcome:
    message: str
    completed: bool = False
    action: str = ""
    response_item_id: int = 0


APPROVAL_OVERRIDE_TOKENS = {"continue", "override", "继续", "覆盖"}
_APPROVAL_OVERRIDE_PATTERN = "|".join(
    sorted(
        (re.escape(token) for token in APPROVAL_OVERRIDE_TOKENS),
        key=len,
        reverse=True,
    )
)
_APPROVAL_TARGET_RE = re.compile(
    rf"^(?:(?P<prefix>{_APPROVAL_OVERRIDE_PATTERN})\s*)?"
    rf"(?P<id>\d+)"
    rf"(?:\s*(?P<suffix>{_APPROVAL_OVERRIDE_PATTERN}))?$",
    re.IGNORECASE,
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


def parse_approval_target(text: str) -> tuple[int, bool] | None:
    normalized = " ".join(
        normalize_cq_plain_text(text, strip_leading_at=True).strip().split()
    )
    if not normalized:
        return None
    match = _APPROVAL_TARGET_RE.fullmatch(normalized)
    if match is None:
        return None
    return int(match.group("id")), bool(match.group("prefix") or match.group("suffix"))


def format_entry_history(
    detail: WordbankGroupDetail,
    *,
    locale: LocaleCode,
) -> str:
    selected = detail.selected_response
    assert selected is not None
    lines = [
        tr(
            locale,
            "wordbank.reply.history",
            entry_id=selected.response_item_id,
            status=format_status_label(selected.status),
            enabled=_format_enabled(selected.enabled, locale),
            deleted_at=_format_deleted_at(selected.deleted_at),
            scope=format_scope_label(selected.scope),
            probability=f"{detail.probability:g}",
            weight=selected.weight,
        ),
        "审批历史:",
        *_format_review_history_lines(selected.review_history),
    ]
    if selected.approved_by:
        lines.append(f"当前审批人: {selected.approved_by}")
    return "\n".join(lines)


def _format_review_history_lines(
    review_history: tuple[WordbankReviewHistoryEntry, ...],
) -> tuple[str, ...]:
    if not review_history:
        return ("- 暂无审批历史记录。",)
    lines: list[str] = []
    for index, entry in enumerate(review_history, start=1):
        action_label = "通过" if entry.action == "approve" else "拒绝"
        line = (
            f"{index}. {format_timestamp(entry.created_at)} "
            f"{entry.actor_user_id or '管理员'} {action_label}"
        )
        if entry.overwritten and entry.previous_status:
            line += f"（覆盖此前{format_status_label(entry.previous_status)}）"
        lines.append(line)
    return tuple(lines)


async def build_repeat_review_prompt(
    service: WordbankService,
    *,
    response_item_id: int,
    locale: LocaleCode,
    requested_action: str,
    continue_hint: str,
    alternative_hint: str,
) -> str | None:
    response_item = await service.get_response_item_record(
        response_item_id,
        include_deleted=True,
    )
    if response_item is None:
        return None
    detail = await service.get_group_detail(
        response_item.trigger_group_id,
        response_item_id=response_item_id,
    )
    if detail is None or detail.selected_response is None:
        return None
    selected = detail.selected_response
    if selected.deleted_at != 0 or selected.status not in {"approved", "rejected"}:
        return None
    requested_label = "通过" if requested_action == "approve" else "拒绝"
    return "\n".join(
        (
            f"词条 #{selected.response_item_id} 已经被审批过。",
            format_entry_history(detail, locale=locale),
            "",
            "继续审批将覆盖此前结果。",
            f"如需继续{requested_label}，请继续发送：{continue_hint}",
            f"如需改为另一结果，请继续发送：{alternative_hint}",
        )
    )


async def handle_approve(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> str:
    outcome = await handle_approve_result(
        service,
        event=event,
        response_item_id_text=response_item_id_text,
        locale=locale,
    )
    return outcome.message


async def handle_approve_result(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> ApprovalMutationOutcome:
    parsed_target = parse_approval_target(response_item_id_text)
    if parsed_target is None:
        return ApprovalMutationOutcome(tr(locale, "wordbank.error.entry_id_numeric"))
    actor = build_mutation_actor(event)
    if not actor_can_review(actor):
        return ApprovalMutationOutcome(
            tr(locale, "wordbank.approval.permission_denied")
        )
    response_item_id, allow_overwrite = parsed_target
    if await service.approve_response_item(
        response_item_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
        allow_overwrite=allow_overwrite,
    ):
        return ApprovalMutationOutcome(
            tr(locale, "wordbank.approval.approved", entry_id=response_item_id),
            completed=True,
            action="approve",
            response_item_id=response_item_id,
        )
    if not allow_overwrite:
        prompt = await build_repeat_review_prompt(
            service,
            response_item_id=response_item_id,
            locale=locale,
            requested_action="approve",
            continue_hint=f"通过词条 {response_item_id} 覆盖",
            alternative_hint=f"拒绝词条 {response_item_id} 覆盖",
        )
        if prompt is not None:
            return ApprovalMutationOutcome(
                prompt,
                action="approve",
                response_item_id=response_item_id,
            )
    return ApprovalMutationOutcome(
        tr(locale, "wordbank.approval.not_found", entry_id=response_item_id),
        action="approve",
        response_item_id=response_item_id,
    )


async def handle_reject(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> str:
    outcome = await handle_reject_result(
        service,
        event=event,
        response_item_id_text=response_item_id_text,
        locale=locale,
    )
    return outcome.message


async def handle_reject_result(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> ApprovalMutationOutcome:
    parsed_target = parse_approval_target(response_item_id_text)
    if parsed_target is None:
        return ApprovalMutationOutcome(tr(locale, "wordbank.error.entry_id_numeric"))
    actor = build_mutation_actor(event)
    if not actor_can_review(actor):
        return ApprovalMutationOutcome(
            tr(locale, "wordbank.approval.permission_denied")
        )
    response_item_id, allow_overwrite = parsed_target
    if await service.reject_response_item(
        response_item_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
        allow_overwrite=allow_overwrite,
    ):
        return ApprovalMutationOutcome(
            tr(locale, "wordbank.approval.rejected", entry_id=response_item_id),
            completed=True,
            action="reject",
            response_item_id=response_item_id,
        )
    if not allow_overwrite:
        prompt = await build_repeat_review_prompt(
            service,
            response_item_id=response_item_id,
            locale=locale,
            requested_action="reject",
            continue_hint=f"拒绝词条 {response_item_id} 覆盖",
            alternative_hint=f"通过词条 {response_item_id} 覆盖",
        )
        if prompt is not None:
            return ApprovalMutationOutcome(
                prompt,
                action="reject",
                response_item_id=response_item_id,
            )
    return ApprovalMutationOutcome(
        tr(locale, "wordbank.approval.not_found", entry_id=response_item_id),
        action="reject",
        response_item_id=response_item_id,
    )


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
    locale: LocaleCode,
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
        return tr(
            locale,
            "wordbank.mutation.trigger_probability_updated",
            group_id=trigger_group_id,
            probability=f"{probability:g}",
        )
    return tr(locale, "wordbank.mutation.trigger_not_found", group_id=trigger_group_id)


def _format_enabled(enabled: int, locale: LocaleCode = "zh-CN") -> str:
    return tr(
        locale,
        "wordbank.state.enabled" if enabled else "wordbank.state.disabled",
    )


def _format_deleted_at(deleted_at: int) -> str:
    return str(deleted_at) if deleted_at else "0"


async def handle_trigger_content_update(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    trigger_group_id: int,
    text: str,
    raw_message: Message,
    locale: LocaleCode,
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
        return tr(
            locale,
            "wordbank.mutation.trigger_content_updated",
            group_id=trigger_group_id,
        )
    return tr(locale, "wordbank.mutation.trigger_not_found", group_id=trigger_group_id)


async def handle_response_weight_update(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id: int,
    weight: int,
    locale: LocaleCode,
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
        return tr(
            locale,
            "wordbank.mutation.response_weight_updated",
            entry_id=response_item_id,
            weight=weight,
        )
    return tr(locale, "wordbank.mutation.response_not_found", entry_id=response_item_id)


async def handle_response_content_update(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    response_item_id: int,
    text: str,
    raw_message: Message,
    locale: LocaleCode,
) -> str:
    from .media_helpers import (
        build_response_shape_from_message,
        extract_message_suffix_by_plain_text,
    )

    actor = build_mutation_actor(event)
    response_shape = await build_response_shape_from_message(
        media_service,
        extract_message_suffix_by_plain_text(raw_message, text),
    )
    if await service.update_response_content(
        response_item_id,
        response_shape=response_shape,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(
            locale,
            "wordbank.mutation.response_content_updated",
            entry_id=response_item_id,
        )
    return tr(locale, "wordbank.mutation.response_not_found", entry_id=response_item_id)


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
            locale=locale,
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
            locale=locale,
        )
    raise RuleError(
        tr(
            "zh-CN",
            "wordbank.error.unknown_subcommand",
            action=f"trigger {action}".strip(),
            help=help_text,
        ),
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
            locale=locale,
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
            locale=locale,
        )
    raise RuleError(
        tr(
            "zh-CN",
            "wordbank.error.unknown_subcommand",
            action=f"response {action}".strip(),
            help=help_text,
        ),
        key="wordbank.error.unknown_subcommand",
        action=f"response {action}".strip(),
        help=help_text,
    )
