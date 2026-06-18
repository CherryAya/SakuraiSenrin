"""Shared helpers for water profile renderers."""

from __future__ import annotations

import arrow
from dataclasses import dataclass
from math import floor, sqrt

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

from ..models import WaterProfileCardData
from ..common import format_rank, short_exp
from ...services.achievement import ACHIEVEMENT_RULES

SEASONAL_ACHIEVEMENT_CAP = 10


def format_profile_rank(rank: int | None, locale: LocaleCode = "zh-CN") -> str:
    return format_rank(rank, locale)


def format_profile_exp(exp: int | str) -> str:
    return short_exp(exp)


def seasonal_total_count(current_unlocked: int = 0) -> int:
    current_defined = sum(
        1 for rule in ACHIEVEMENT_RULES.values() if rule.track_type == "seasonal"
    )
    return max(current_defined, current_unlocked, SEASONAL_ACHIEVEMENT_CAP)


def split_achievement_views(
    achievement_items: list[tuple[str, str, str, int]],
    locale: LocaleCode,
) -> tuple[list[str], list[tuple[str, str]]]:
    latest_season = ""
    seasonal_items = [
        (achievement_id, season_id, unlocked_at)
        for achievement_id, track_type, season_id, unlocked_at in achievement_items
        if track_type == "seasonal" and season_id
    ]
    if seasonal_items:
        latest_season = max(
            seasonal_items,
            key=lambda item: int(item[2]),
        )[1]
    current: list[str] = []
    history_raw: list[tuple[int, str, str]] = []
    for achievement_id, track_type, season_id, unlocked_at in achievement_items:
        rule = ACHIEVEMENT_RULES.get(achievement_id)
        fallback_name = (
            achievement_id.replace("_", " ").strip().title() or achievement_id
        )
        name = rule.name(locale) if rule is not None else fallback_name
        if track_type == "seasonal" and season_id == latest_season:
            current.append(name)
        if track_type == "seasonal":
            title = tr(
                locale,
                "water.profile.fallback.achievement_history.seasonal",
                season_id=season_id,
            )
        else:
            title = "Global"
        history_raw.append((int(unlocked_at), title, name))
    history_raw.sort(key=lambda item: item[0], reverse=True)
    history = [
        (f"{title} · {name}", arrow.get(ts).format("YYYY-MM-DD"))
        for ts, title, name in history_raw
    ]
    return current, history


def build_my_water_text_fallback(
    data: WaterProfileCardData,
    locale: LocaleCode,
) -> str:
    current_achievements, history_achievements = split_achievement_views(
        data.achievement_items,
        locale,
    )
    return "\n".join(
        [
            f"{data.username}",
            f"群组: {data.group_name}",
            f"矩阵: {data.matrix_id}",
            f"全局排行: {format_profile_rank(data.global_rank, locale)}",
            f"群内排行: {format_profile_rank(data.group_user_rank, locale)}",
            f"矩阵排行: {format_profile_rank(data.matrix_user_rank, locale)}",
            f"矩阵总排行: {format_profile_rank(data.matrix_rank, locale)}",
            f"群活跃排行: {format_profile_rank(data.group_rank, locale)}",
            "当前成就: " + ("、".join(current_achievements[:6]) or "-"),
            "历史成就: "
            + (
                "；".join(
                    f"{title}({date_text})"
                    for title, date_text in history_achievements[:5]
                )
                or "-"
            ),
        ]
    )


def build_copyright_text(year: int) -> str:
    return f"© 2020-{year} SakuraiSenrin"


def next_level_target(level: int, base: int) -> int:
    cur = max(1, level)
    return base * (cur + 1) * (cur + 1)


def level_progress(exp: int, level: int, base: int) -> tuple[float, int]:
    next_exp = next_level_target(level, base)
    prev_exp = base * level * level if level > 1 else 0
    span = max(1, next_exp - prev_exp)
    ratio = (exp - prev_exp) / span
    return max(0.0, min(1.0, ratio)), max(0, next_exp - exp)


@dataclass(slots=True, frozen=True)
class ProfileProgressMetrics:
    matrix_lv: int | str
    matrix_exp: int | str
    matrix_season: int | str
    global_lv: int | str
    global_exp: int | str
    global_season: int | str
    matrix_total_exp: int
    sg_ratio: float
    sg_gap: int
    sm_ratio: float
    sm_gap: int
    smt_ratio: float
    smt_gap: int
    gg_ratio: float
    gg_gap: int
    gm_ratio: float
    gm_gap: int
    gmt_ratio: float
    gmt_gap: int


def compute_profile_progress_metrics(
    data: WaterProfileCardData,
) -> ProfileProgressMetrics:
    matrix_lv = data.matrix_level[2] if data.matrix_level is not None else "-"
    matrix_exp = data.matrix_level[0] if data.matrix_level is not None else "-"
    matrix_season = data.matrix_level[1] if data.matrix_level is not None else "-"
    global_lv = data.global_level[2] if data.global_level is not None else "-"
    global_exp = data.global_level[0] if data.global_level is not None else "-"
    global_season = data.global_level[1] if data.global_level is not None else "-"
    personal_exp = data.matrix_level[0] if data.matrix_level is not None else 0
    personal_lv = data.matrix_level[2] if data.matrix_level is not None else 1
    matrix_season_exp = data.matrix_level[1] if data.matrix_level is not None else 0
    global_season_exp = data.global_level[1] if data.global_level is not None else 0
    matrix_total_exp = data.matrix_total_level[0] if data.matrix_total_level else 0
    matrix_total_lv = data.matrix_total_level[2] if data.matrix_total_level else 1
    matrix_total_season_exp = (
        data.matrix_total_level[1] if data.matrix_total_level else 0
    )

    season_global_lv = max(1, floor(sqrt(max(0, global_season_exp) / 100)))
    season_matrix_lv = max(1, floor(sqrt(max(0, matrix_season_exp) / 100)))
    season_matrix_total_lv = max(
        1,
        floor(sqrt(max(0, matrix_total_season_exp) / 2000)),
    )
    sg_ratio, sg_gap = level_progress(global_season_exp, season_global_lv, 100)
    sm_ratio, sm_gap = level_progress(matrix_season_exp, season_matrix_lv, 100)
    smt_ratio, smt_gap = level_progress(
        matrix_total_season_exp,
        season_matrix_total_lv,
        2000,
    )
    gg_ratio, gg_gap = level_progress(
        data.global_level[0] if data.global_level is not None else 0,
        data.global_level[2] if data.global_level is not None else 1,
        100,
    )
    gm_ratio, gm_gap = level_progress(personal_exp, personal_lv, 100)
    gmt_ratio, gmt_gap = level_progress(matrix_total_exp, matrix_total_lv, 2000)
    return ProfileProgressMetrics(
        matrix_lv=matrix_lv,
        matrix_exp=matrix_exp,
        matrix_season=matrix_season,
        global_lv=global_lv,
        global_exp=global_exp,
        global_season=global_season,
        matrix_total_exp=matrix_total_exp,
        sg_ratio=sg_ratio,
        sg_gap=sg_gap,
        sm_ratio=sm_ratio,
        sm_gap=sm_gap,
        smt_ratio=smt_ratio,
        smt_gap=smt_gap,
        gg_ratio=gg_ratio,
        gg_gap=gg_gap,
        gm_ratio=gm_ratio,
        gm_gap=gm_gap,
        gmt_ratio=gmt_ratio,
        gmt_gap=gmt_gap,
    )
