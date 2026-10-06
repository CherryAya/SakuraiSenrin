"""Wordbank presentation models and text formatters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import arrow

from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.database.types import (
    WordbankRankPeriod,
    WordbankResponseItemDetail,
    WordbankSearchItem,
)
from src.plugins.wordbank.message_model import (
    MessageAtom,
    MessageShape,
    format_event_summary_text,
    format_face_summary_text,
    format_placeholder_summary_text,
    is_response_sender_target,
    shape_to_summary_text,
)

if TYPE_CHECKING:
    from pil_utils import BuildImage


WORDBANK_RANK_PERIOD_LABEL_KEYS: dict[WordbankRankPeriod, MessageKey] = {
    "week": "wordbank.rank.period.week",
    "month": "wordbank.rank.period.month",
    "season": "wordbank.rank.period.season",
    "total": "wordbank.rank.period.total",
}

STATUS_LABEL_KEYS: dict[str, MessageKey] = {
    "pending": "wordbank.status.pending",
    "approved": "wordbank.status.approved",
    "rejected": "wordbank.status.rejected",
}

SCOPE_LABEL_KEYS: dict[str, MessageKey] = {
    "current_group": "wordbank.scope.current_group",
    "all_groups": "wordbank.scope.all_groups",
    "self": "wordbank.scope.self",
    "private_only": "wordbank.scope.private_only",
    "self_in_current_group": "wordbank.scope.self_in_current_group",
}

ROLE_LABEL_KEYS: dict[str, MessageKey] = {
    "owner": "wordbank.rule.role.owner",
    "admin": "wordbank.rule.role.admin",
    "member": "wordbank.rule.role.member",
    "any": "wordbank.rule.role.any",
}

RESPONSE_MODE_LABEL_KEYS: dict[str, tuple[MessageKey, MessageKey]] = {
    "forward_whole": (
        "wordbank.response_mode.forward_whole",
        "wordbank.response_mode.forward_whole_count",
    ),
    "forward_split": (
        "wordbank.response_mode.forward_split",
        "wordbank.response_mode.forward_split_count",
    ),
}

NOTICE_EVENT_LABEL_KEYS: dict[str, MessageKey] = {
    "event:at": "wordbank.event.at",
    "event:mention": "wordbank.event.mention",
    "event:poke": "wordbank.event.poke",
    "event:join": "wordbank.event.join",
    "event:bot_join": "wordbank.event.bot_join",
    "event:member_join": "wordbank.event.member_join",
    "event:group_join": "wordbank.event.group_join",
    "event:group_increase": "wordbank.event.group_increase",
    "event:leave": "wordbank.event.leave",
    "event:bot_leave": "wordbank.event.bot_leave",
    "event:member_leave": "wordbank.event.member_leave",
    "event:group_leave": "wordbank.event.group_leave",
    "event:group_decrease": "wordbank.event.group_decrease",
}


@dataclass(slots=True, frozen=True)
class WordbankAddResult:
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    trigger_text: str
    response_text: str
    scope: str
    probability: float
    weight: int
    status: str = "pending"
    created_group: bool = False
    trigger_shape: MessageShape | None = None
    response_shape: MessageShape | None = None
    created_by: str = ""
    created_at: int = 0
    rule: dict[str, object] | None = None
    response_mode: str = "normal"
    forward_source_message_id: str | None = None
    forward_node_count: int = 0
    reused_existing: bool = False


@dataclass(slots=True, frozen=True)
class WordbankBatchAddItemResult:
    index: int
    ok: bool
    result: WordbankAddResult | None = None
    error: str = ""


@dataclass(slots=True, frozen=True)
class WordbankBatchAddResult:
    total: int
    success: int
    failed: int
    items: tuple[WordbankBatchAddItemResult, ...]


@dataclass(slots=True, frozen=True)
class WordbankDeleteVoteResult:
    vote_id: int
    trigger_group_id: int
    response_item_id: int
    status: str
    support_count: int
    threshold: int
    created: bool
    already_supported: bool
    passed: bool
    response_item_deleted: bool


@dataclass(slots=True)
class WordbankLeaderboardCardItem:
    user_id: str
    display_name: str
    approved_count: int
    score: float
    current_rank: int
    share: float
    latest_created_at: int
    group_count: int
    current_group_count: int
    all_groups_count: int
    self_count: int
    private_only_count: int
    self_in_current_group_count: int
    avatar: BuildImage | None = None


@dataclass(slots=True, frozen=True)
class WordbankLeaderboardCardData:
    title: str
    subtitle: str
    period: WordbankRankPeriod
    badge_text: str
    range_text: str
    generated_at: int
    total_creator_count: int
    total_approved_count: int
    champion_gap: int
    top_share: float
    items: tuple[WordbankLeaderboardCardItem, ...]
    range_start: int
    range_end: int


def rank_period_label(period: WordbankRankPeriod, locale: LocaleCode) -> str:
    return tr(locale, WORDBANK_RANK_PERIOD_LABEL_KEYS[period])


def rank_range_text(
    range_start: int,
    range_end: int,
    *,
    locale: LocaleCode,
) -> str:
    start_text = arrow.get(range_start).to("Asia/Shanghai").format("YYYY-MM-DD")
    end_text = arrow.get(range_end).to("Asia/Shanghai").format("YYYY-MM-DD HH:mm")
    return tr(
        locale,
        "wordbank.rank.range",
        start=start_text,
        end=end_text,
    )


def format_search_items(
    items: list[WordbankSearchItem] | tuple[WordbankSearchItem, ...],
    *,
    locale: LocaleCode,
    page: int = 1,
    limit: int = 10,
    has_more: bool = False,
) -> str:
    if not items:
        return tr(locale, "wordbank.search.empty", page=page)
    lines = [tr(locale, "wordbank.search.title", page=page)]
    for item in items:
        response_preview = " / ".join(item.response_summaries[:3]) or item.response_text
        if item.has_more_responses:
            response_preview = f"{response_preview} (+{item.remaining_response_count})"
        lines.append(
            tr(
                locale,
                "wordbank.search.item",
                entry_id=item.trigger_group_id,
                status=item.status,
                scope=item.scope,
                trigger_text=item.trigger_text,
                response_text=response_preview,
            )
        )
    if has_more:
        lines.append(
            tr(locale, "wordbank.search.more", next_page=page + 1, limit=limit)
        )
    return "\n".join(lines)


def format_pending_items(
    items: list[WordbankSearchItem] | tuple[WordbankSearchItem, ...],
    *,
    locale: LocaleCode,
    page: int = 1,
    limit: int = 10,
    has_more: bool = False,
) -> str:
    if not items:
        return tr(locale, "wordbank.approval.pending_empty", page=page)
    lines = [tr(locale, "wordbank.approval.pending_title", page=page)]
    for item in items:
        response_item_id = (
            item.response_item_ids[0]
            if item.response_item_ids
            else item.trigger_group_id
        )
        lines.append(
            tr(
                locale,
                "wordbank.approval.pending_item",
                entry_id=response_item_id,
                scope=item.scope,
                trigger_text=item.trigger_text,
                response_text=item.response_text,
                created_by=item.created_by,
            )
        )
    if has_more:
        lines.append(
            tr(
                locale,
                "wordbank.approval.pending_more",
                next_page=page + 1,
                limit=limit,
            )
        )
    return "\n".join(lines)


def format_creator_leaderboard(
    data: WordbankLeaderboardCardData,
    *,
    locale: LocaleCode,
) -> str:
    if not data.items:
        return tr(locale, "wordbank.rank.empty")
    lines = [
        tr(
            locale,
            "wordbank.rank.text.title",
            period=tr(locale, WORDBANK_RANK_PERIOD_LABEL_KEYS[data.period]),
            range=data.range_text,
            total_creator_count=data.total_creator_count,
            total_approved_count=data.total_approved_count,
        )
    ]
    for item in data.items:
        lines.append(
            tr(
                locale,
                "wordbank.rank.text.item",
                rank=item.current_rank,
                name=item.display_name,
                approved_count=item.approved_count,
                group_count=item.group_count,
                share=f"{item.share * 100:.1f}%",
            )
        )
    return "\n".join(lines)


def format_add_result(result: WordbankAddResult, *, locale: LocaleCode) -> str:
    if result.reused_existing and result.status == "pending":
        key = "wordbank.add.duplicate_pending"
    elif result.reused_existing and result.status == "approved":
        key = "wordbank.add.duplicate_approved"
    else:
        key = (
            "wordbank.add.pending"
            if result.status == "pending"
            else "wordbank.add.success"
        )
    return tr(
        locale,
        key,
        entry_id=result.response_item_id,
        status=format_status_label(result.status, locale=locale),
        trigger_text=format_notice_content_summary(
            result.trigger_text,
            shape=result.trigger_shape,
            response_mode="normal",
            locale=locale,
        ),
        response_text=format_notice_content_summary(
            result.response_text,
            shape=result.response_shape,
            response_mode=result.response_mode,
            forward_node_count=result.forward_node_count,
            locale=locale,
        ),
        scope=format_scope_label(result, locale=locale),
        probability=f"{result.probability:g}",
        weight=result.weight,
    )


def response_mode_label(result: WordbankAddResult, *, locale: LocaleCode) -> str:
    count = result.forward_node_count or 0
    keys = RESPONSE_MODE_LABEL_KEYS.get(result.response_mode)
    if keys is None:
        return tr(locale, "wordbank.response_mode.normal")
    plain_key, count_key = keys
    return tr(locale, count_key if count > 0 else plain_key, count=count)


def format_status_label(status: str, *, locale: LocaleCode) -> str:
    key = STATUS_LABEL_KEYS.get(status)
    return tr(locale, key) if key else (status or "-")


def format_response_summary(
    text: str,
    *,
    shape: MessageShape | None = None,
) -> str:
    if shape is None:
        return text
    return shape_to_summary_text(shape)


def format_notice_content_summary(
    text: str,
    *,
    shape: MessageShape | None = None,
    response_mode: str = "normal",
    forward_node_count: int = 0,
    locale: LocaleCode = "zh-CN",
) -> str:
    if response_mode == "forward_whole":
        return tr(
            locale,
            "wordbank.response_mode.forward_whole_count"
            if forward_node_count > 0
            else "wordbank.response_mode.forward_whole",
            count=forward_node_count,
        )
    if shape is None or shape.is_empty():
        return text or "-"
    atoms = shape.atoms
    if all(atom.kind == "image" for atom in atoms):
        count = sum(1 for atom in atoms if atom.kind == "image")
        return tr(
            locale,
            "wordbank.notice_content.image_single"
            if count <= 1
            else "wordbank.notice_content.image_multi",
            count=count,
        )
    parts: list[str] = []
    for atom in atoms:
        summary = _format_notice_atom(atom, locale=locale)
        if summary:
            parts.append(summary)
    summary_text = "".join(parts).strip()
    return summary_text or text or "-"


def format_notice_content_raw_text(
    text: str,
    *,
    shape: MessageShape | None = None,
    response_mode: str = "normal",
    forward_node_count: int = 0,
    locale: LocaleCode = "zh-CN",
) -> str:
    if shape is not None and any(atom.kind == "at" for atom in shape.atoms):
        return format_notice_content_summary(
            text,
            shape=shape,
            response_mode=response_mode,
            forward_node_count=forward_node_count,
            locale=locale,
        )
    if text.strip():
        return text
    if response_mode == "forward_whole":
        return tr(
            locale,
            "wordbank.response_mode.forward_whole_count"
            if forward_node_count > 0
            else "wordbank.response_mode.forward_whole",
            count=forward_node_count,
        )
    return "-"


def format_timestamp(timestamp: int) -> str:
    if timestamp <= 0:
        return "-"
    return arrow.get(timestamp).to("Asia/Shanghai").format("YYYY-MM-DD HH:mm")


def format_scope_label(
    detail: WordbankResponseItemDetail | WordbankAddResult | WordbankSearchItem | None,
    *,
    locale: LocaleCode,
) -> str:
    if not detail:
        return "-"
    group_id = (
        detail.group_id
        if isinstance(detail, WordbankResponseItemDetail)
        else detail.trigger_group_id
    )
    if detail.scope not in SCOPE_LABEL_KEYS:
        return "-"
    return tr(
        locale,
        SCOPE_LABEL_KEYS[detail.scope],
        group_id=group_id,
        created_by=detail.created_by,
    )


def format_rule_summary(
    *,
    probability: float,
    rule: dict[str, object] | None = None,
    locale: LocaleCode,
) -> str:
    parts = [
        tr(
            locale,
            "wordbank.rule.probability",
            value=f"{probability:g}",
        )
    ]
    payload = dict(rule or {})
    role = str(payload.get("roles", "") or "").strip()
    if role and role != "any":
        role_key = ROLE_LABEL_KEYS.get(role)
        role_label = tr(locale, role_key) if role_key else role
        parts.append(tr(locale, "wordbank.rule.role", role=role_label))
    call_count = payload.get("call_count")
    if isinstance(call_count, dict):
        window_seconds = int(call_count.get("window_seconds", 0) or 0)
        min_count = int(call_count.get("min", 0) or 0)
        max_count = int(call_count.get("max", 0) or 0)
        if window_seconds > 0:
            parts.append(
                tr(
                    locale,
                    "wordbank.rule.call_count",
                    window=window_seconds,
                    min_count=min_count,
                    max_count=max_count or "inf",
                )
            )
    return " | ".join(parts)


def _format_notice_atom(atom: MessageAtom, *, locale: LocaleCode) -> str:
    if atom.kind == "text" and atom.text:
        return atom.text
    if atom.kind == "image":
        return ""
    if atom.kind == "face" and atom.face_id is not None:
        return format_face_summary_text(atom.face_id)
    if atom.kind == "at":
        return tr(
            locale,
            "wordbank.at.sender"
            if is_response_sender_target(atom.target_id)
            else "wordbank.at.user",
        )
    if atom.kind == "event" and atom.event_name:
        if atom.event_name == "event:poke":
            return format_event_summary_text(atom.event_name, atom.target_id)
        return _format_notice_event_name(atom.event_name, locale=locale)
    if atom.kind == "placeholder" and atom.placeholder_name:
        return format_placeholder_summary_text(atom.placeholder_name)
    return ""


def _format_notice_event_name(event_name: str, *, locale: LocaleCode) -> str:
    key = NOTICE_EVENT_LABEL_KEYS.get(event_name)
    return tr(locale, key) if key else event_name
