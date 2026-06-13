"""Water 自然榜单类型定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

WaterRankSubject = Literal["user", "group", "matrix"]
WaterRankScope = Literal["group", "matrix", "global"]
WaterRankPeriod = Literal["day", "week", "month", "season", "year", "total"]
VisibleWaterRankPeriod = Literal["day", "week", "month", "season", "year", "total"]

SUBJECT_LABELS: dict[WaterRankSubject, str] = {
    "user": "用户榜",
    "group": "群聊榜",
    "matrix": "矩阵榜",
}

SCOPE_LABELS: dict[WaterRankScope, str] = {
    "group": "本群",
    "matrix": "本矩阵",
    "global": "全局",
}

PERIOD_LABELS: dict[WaterRankPeriod, str] = {
    "day": "日榜",
    "week": "周榜",
    "month": "月榜",
    "season": "季榜",
    "year": "年榜",
    "total": "总榜",
}

SUBJECT_TOKENS: dict[str, WaterRankSubject] = {
    "用户榜": "user",
    "群聊榜": "group",
    "矩阵榜": "matrix",
}

SCOPE_TOKENS: dict[str, WaterRankScope] = {
    "本群": "group",
    "本矩阵": "matrix",
    "全局": "global",
}

PERIOD_TOKENS: dict[str, WaterRankPeriod] = {
    "日榜": "day",
    "周榜": "week",
    "月榜": "month",
    "季榜": "season",
    "年榜": "year",
    "总榜": "total",
}

VALID_SCOPES_BY_SUBJECT: dict[WaterRankSubject, tuple[WaterRankScope, ...]] = {
    "user": ("group", "matrix", "global"),
    "group": ("matrix", "global"),
    "matrix": ("global",),
}

RESTRICTED_RANK_PERIODS: tuple[WaterRankPeriod, ...] = ("year", "total")
DEFAULT_VISIBLE_RANK_PERIODS: tuple[WaterRankPeriod, ...] = (
    "day",
    "week",
    "month",
    "season",
)
SUPERUSER_VISIBLE_RANK_PERIODS: tuple[WaterRankPeriod, ...] = (
    "day",
    "week",
    "month",
    "season",
    "year",
    "total",
)


@dataclass(frozen=True)
class WaterRankQuerySpec:
    subject: WaterRankSubject
    scope: WaterRankScope
    period: WaterRankPeriod
    errors: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        return (
            f"{SUBJECT_LABELS[self.subject]} · "
            f"{SCOPE_LABELS[self.scope]}{PERIOD_LABELS[self.period]}"
        )

    @property
    def normalized_command(self) -> str:
        return (
            f"#水王 {SUBJECT_LABELS[self.subject]} "
            f"{SCOPE_LABELS[self.scope]} {PERIOD_LABELS[self.period]}"
        )


def is_valid_rank_combo(subject: WaterRankSubject, scope: WaterRankScope) -> bool:
    return scope in VALID_SCOPES_BY_SUBJECT[subject]


def suggest_scope_for_subject(subject: WaterRankSubject) -> WaterRankScope:
    if subject == "group":
        return "matrix"
    if subject == "matrix":
        return "global"
    return "group"


def visible_rank_periods(*, is_superuser: bool) -> tuple[WaterRankPeriod, ...]:
    return (
        SUPERUSER_VISIBLE_RANK_PERIODS if is_superuser else DEFAULT_VISIBLE_RANK_PERIODS
    )


def is_rank_period_allowed(period: WaterRankPeriod, *, is_superuser: bool) -> bool:
    return is_superuser or period not in RESTRICTED_RANK_PERIODS
