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

SHORTCUT_PERIOD_PREFIXES: dict[WaterRankPeriod, str] = {
    "day": "今日",
    "week": "本周",
    "month": "本月",
    "season": "本季",
    "year": "本年",
    "total": "总",
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
class WaterRankShortcut:
    aliases: tuple[str, ...]
    subject: WaterRankSubject
    scope: WaterRankScope
    period: WaterRankPeriod

    @property
    def primary_alias(self) -> str:
        return self.aliases[0]

    @property
    def query_spec(self) -> "WaterRankQuerySpec":
        return WaterRankQuerySpec(
            subject=self.subject,
            scope=self.scope,
            period=self.period,
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


def _build_rank_shortcuts() -> tuple[WaterRankShortcut, ...]:
    visible_periods: tuple[WaterRankPeriod, ...] = DEFAULT_VISIBLE_RANK_PERIODS
    shortcuts: list[WaterRankShortcut] = []

    for period in visible_periods:
        prefix = SHORTCUT_PERIOD_PREFIXES[period]
        shortcuts.extend(
            (
                WaterRankShortcut((f"{prefix}水王",), "user", "group", period),
                WaterRankShortcut((f"{prefix}矩阵水王",), "user", "matrix", period),
                WaterRankShortcut((f"{prefix}全局水王",), "user", "global", period),
                WaterRankShortcut(
                    (f"{prefix}矩阵群榜", f"{prefix}矩阵群聊榜"),
                    "group",
                    "matrix",
                    period,
                ),
                WaterRankShortcut(
                    (f"{prefix}群榜", f"{prefix}群聊榜"),
                    "group",
                    "global",
                    period,
                ),
                WaterRankShortcut((f"{prefix}矩阵榜",), "matrix", "global", period),
            )
        )

    return tuple(shortcuts)


RANK_SHORTCUTS: tuple[WaterRankShortcut, ...] = _build_rank_shortcuts()

RANK_SHORTCUT_ALIAS_MAP: dict[str, WaterRankShortcut] = {
    alias: shortcut for shortcut in RANK_SHORTCUTS for alias in shortcut.aliases
}

RANK_SHORTCUT_ALIASES: frozenset[str] = frozenset(RANK_SHORTCUT_ALIAS_MAP)


def get_rank_shortcut(alias: str) -> WaterRankShortcut | None:
    return RANK_SHORTCUT_ALIAS_MAP.get(alias.strip())


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
