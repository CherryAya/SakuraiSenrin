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
    WordbankSearchItem,
)
from src.plugins.wordbank.message_model import MessageShape, shape_to_summary_text

if TYPE_CHECKING:
    from pil_utils import BuildImage


WORDBANK_RANK_PERIOD_LABEL_KEYS: dict[WordbankRankPeriod, MessageKey] = {
    "week": "wordbank.rank.period.week",
    "month": "wordbank.rank.period.month",
    "season": "wordbank.rank.period.season",
    "total": "wordbank.rank.period.total",
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
    current_rank: int
    share: float
    latest_created_at: int
    group_count: int
    current_group_count: int
    all_groups_count: int
    self_count: int
    private_only_count: int
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
    key = (
        "wordbank.add.pending" if result.status == "pending" else "wordbank.add.success"
    )
    return tr(
        locale,
        key,
        entry_id=result.response_item_id,
        status=result.status,
        trigger_text=result.trigger_text,
        response_text=result.response_text,
        scope=result.scope,
        probability=f"{result.probability:g}",
        weight=result.weight,
    )


def response_mode_label(result: WordbankAddResult) -> str:
    if result.response_mode == "forward_whole":
        count = result.forward_node_count or 0
        return f"合并转发整体（{count} 条）" if count > 0 else "合并转发整体"
    if result.response_mode == "forward_split":
        count = result.forward_node_count or 0
        return f"合并转发拆分（{count} 条）" if count > 0 else "合并转发拆分"
    return "普通响应"


def format_response_summary(
    text: str,
    *,
    shape: MessageShape | None = None,
) -> str:
    if shape is None:
        return text
    return shape_to_summary_text(shape)


def format_timestamp(timestamp: int) -> str:
    if timestamp <= 0:
        return "-"
    return arrow.get(timestamp).to("Asia/Shanghai").format("YYYY-MM-DD HH:mm")


def format_scope_label(scope: str) -> str:
    return {
        "current_group": "当前群",
        "all_groups": "所有群",
        "self": "仅自己",
        "private_only": "仅私聊",
        "self_in_current_group": "自己+当前群",
    }.get(scope, scope or "-")


def format_rule_summary(
    *,
    probability: float,
    rule: dict[str, object] | None = None,
) -> str:
    parts = [f"概率 {probability:g}"]
    payload = dict(rule or {})
    role = str(payload.get("roles", "") or "").strip()
    if role:
        role_label = {
            "owner": "群主",
            "admin": "管理",
            "member": "成员",
            "any": "不限",
        }.get(role, role)
        if role_label != "不限":
            parts.append(f"角色 {role_label}")
    call_count = payload.get("call_count")
    if isinstance(call_count, dict):
        window_seconds = int(call_count.get("window_seconds", 0) or 0)
        min_count = int(call_count.get("min", 0) or 0)
        max_count = int(call_count.get("max", 0) or 0)
        if window_seconds > 0:
            parts.append(f"频率 {window_seconds}s/{min_count}-{max_count or 'inf'}")
    return " | ".join(parts)
