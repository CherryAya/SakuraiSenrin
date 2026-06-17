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


def format_response_summary(
    text: str,
    *,
    shape: MessageShape | None = None,
) -> str:
    if shape is None:
        return text
    return shape_to_summary_text(shape)
