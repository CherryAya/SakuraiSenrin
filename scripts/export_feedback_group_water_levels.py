"""List or recompute water levels for users inside configured support groups."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import json
from math import floor, sqrt
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any, cast

import arrow
import nonebot
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.core.tables import Member, User
from src.database.instances import core_db
from src.lib.plugin_docs.meta import resolve_support_groups
from src.lib.utils.common import get_current_time

if TYPE_CHECKING:
    from src.plugins.water.database.ops import WaterSummaryOps
    from src.plugins.water.database.tables import WaterGlobalLevel
    from src.plugins.water.database.types import WaterSummaryRecord

water_core_db: Any = None
water_repo: Any = None
WaterSummaryOps: Any = None
WaterGlobalLevel: Any = None


def _load_water_components() -> None:
    global water_core_db
    global water_repo
    global WaterSummaryOps
    global WaterGlobalLevel
    if (
        water_core_db is not None
        and water_repo is not None
        and WaterSummaryOps is not None
        and WaterGlobalLevel is not None
    ):
        return

    from src.plugins.water.database import water_repo as loaded_water_repo
    from src.plugins.water.database.instances import (
        water_core_db as loaded_water_core_db,
    )
    from src.plugins.water.database.ops import (
        WaterSummaryOps as loaded_water_summary_ops,
    )
    from src.plugins.water.database.tables import (
        WaterGlobalLevel as loaded_water_global_level,
    )

    water_core_db = loaded_water_core_db
    water_repo = loaded_water_repo
    WaterSummaryOps = loaded_water_summary_ops
    WaterGlobalLevel = loaded_water_global_level


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export water global levels for support-group members"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="limit number of exported users; 0 means no limit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print JSON instead of a plain-text table",
    )
    parser.add_argument(
        "--mode",
        choices=("stored", "recompute"),
        default="stored",
        help="stored reads water_global_level; recompute recalculates from summaries",
    )
    parser.add_argument(
        "--start-date",
        type=int,
        default=0,
        help="recompute mode only; inclusive YYYYMMDD start date, default auto-detect",
    )
    parser.add_argument(
        "--end-date",
        type=int,
        default=0,
        help="recompute mode only; inclusive YYYYMMDD end date, default today",
    )
    return parser.parse_args()


def _normalize_limit(value: int) -> int | None:
    if value <= 0:
        return None
    return value


def _format_group_label(group_id: str, title: str) -> str:
    return f"{group_id}|{title}" if title else group_id


def _calc_personal_delta_exp(msg_count: int, active_hours: int) -> int:
    return floor(10 * sqrt(msg_count) + 5 * active_hours)


def _calc_level(exp: int) -> int:
    return max(1, floor(sqrt(max(0, exp) / 100)))


def _normalize_date(value: int) -> int | None:
    if value <= 0:
        return None
    return value


def _render_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No matching users found."

    headers = [
        "rank",
        "user_id",
        "display_name",
        "level",
        "exp",
        "season_exp",
        "groups",
    ]
    widths = {
        header: max(
            len(header),
            *(len(str(row.get(header, ""))) for row in rows),
        )
        for header in headers
    }
    lines = [
        "  ".join(header.ljust(widths[header]) for header in headers),
        "  ".join("-" * widths[header] for header in headers),
    ]
    for row in rows:
        lines.append(
            "  ".join(
                str(row.get(header, "")).ljust(widths[header]) for header in headers
            )
        )
    return "\n".join(lines)


async def _load_feedback_group_members() -> tuple[
    dict[str, str], dict[str, dict[str, Any]]
]:
    support_groups = resolve_support_groups()
    support_group_map = {
        str(group.group_id).strip(): str(group.title).strip()
        for group in support_groups
    }
    support_group_ids = [group_id for group_id in support_group_map if group_id]
    if not support_group_ids:
        return {}, {}

    async with core_db.session(commit=False) as session:
        member_rows = (
            await session.execute(
                select(
                    Member.user_id,
                    Member.group_id,
                    Member.group_card,
                    User.user_name,
                )
                .join(User, User.user_id == Member.user_id)
                .where(Member.group_id.in_(support_group_ids))
                .order_by(Member.user_id.asc(), Member.group_id.asc())
            )
        ).all()

    if not member_rows:
        return {}, {}

    members_by_user: dict[str, dict[str, Any]] = {}
    for user_id, group_id, group_card, user_name in member_rows:
        key = str(user_id)
        group_id_text = str(group_id)
        entry = members_by_user.setdefault(
            key,
            {
                "display_name": str(group_card or user_name or "").strip() or key,
                "groups": [],
            },
        )
        if not entry["display_name"]:
            entry["display_name"] = str(user_name or "").strip() or key
        entry["groups"].append(
            _format_group_label(group_id_text, support_group_map.get(group_id_text, ""))
        )
    return support_group_map, members_by_user


def _build_result_rows(
    rows: list[tuple[str, int, int, int]],
    *,
    members_by_user: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, (user_id, level, exp, season_exp) in enumerate(rows, start=1):
        member_info = members_by_user[str(user_id)]
        result.append(
            {
                "rank": index,
                "user_id": str(user_id),
                "display_name": member_info["display_name"],
                "level": int(level),
                "exp": int(exp),
                "season_exp": int(season_exp),
                "groups": ", ".join(member_info["groups"]),
            }
        )
    return result


async def fetch_feedback_group_water_levels(
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    _load_water_components()
    _, members_by_user = await _load_feedback_group_members()
    if not members_by_user:
        return []

    user_ids = list(members_by_user.keys())
    assert water_core_db is not None
    assert WaterGlobalLevel is not None
    async with water_core_db.session(commit=False) as session:
        stmt = (
            select(
                WaterGlobalLevel.user_id,
                WaterGlobalLevel.level,
                WaterGlobalLevel.exp,
                WaterGlobalLevel.season_exp,
            )
            .where(WaterGlobalLevel.user_id.in_(user_ids))
            .order_by(
                WaterGlobalLevel.level.desc(),
                WaterGlobalLevel.exp.desc(),
                WaterGlobalLevel.season_exp.desc(),
                WaterGlobalLevel.user_id.asc(),
            )
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        level_rows = (await session.execute(stmt)).all()

    normalized_rows = [
        (str(user_id), int(level), int(exp), int(season_exp))
        for user_id, level, exp, season_exp in level_rows
    ]
    return _build_result_rows(normalized_rows, members_by_user=members_by_user)


def _aggregate_recomputed_rows(
    summaries: list["WaterSummaryRecord"],
    *,
    members_by_user: dict[str, dict[str, Any]],
    limit: int | None,
) -> list[dict[str, Any]]:
    exp_by_user: dict[str, int] = defaultdict(int)
    season_exp_by_user: dict[str, int] = defaultdict(int)
    for row in summaries:
        if row.user_id not in members_by_user:
            continue
        delta = _calc_personal_delta_exp(int(row.msg_count), int(row.active_hours))
        if row.msg_count > 1000 and row.active_hours <= 2:
            continue
        exp_by_user[row.user_id] += delta
        season_exp_by_user[row.user_id] += delta

    computed_rows = [
        (
            user_id,
            _calc_level(exp),
            exp,
            season_exp_by_user[user_id],
        )
        for user_id, exp in exp_by_user.items()
    ]
    computed_rows.sort(key=lambda item: (-item[1], -item[2], -item[3], item[0]))
    if limit is not None:
        computed_rows = computed_rows[:limit]
    return _build_result_rows(computed_rows, members_by_user=members_by_user)


async def recompute_feedback_group_water_levels(
    *,
    limit: int | None = None,
    start_date: int | None = None,
    end_date: int | None = None,
) -> list[dict[str, Any]]:
    _load_water_components()
    support_group_map, members_by_user = await _load_feedback_group_members()
    if not support_group_map or not members_by_user:
        return []

    assert water_repo is not None
    assert water_core_db is not None
    assert WaterSummaryOps is not None
    resolved_end_date = end_date or int(
        arrow.get(get_current_time())
        .to("Asia/Shanghai")
        .floor("day")
        .format("YYYYMMDD")
    )
    resolved_start_date = start_date
    if resolved_start_date is None:
        hot_start_date = water_repo._hot_summary_start_date()
        first_archived_date = await water_repo._get_archived_first_summary_record_date(
            end_date=water_repo._previous_date(hot_start_date),
            group_ids=list(support_group_map.keys()),
        )
        if first_archived_date is not None:
            resolved_start_date = first_archived_date
        else:
            async with water_core_db.session(commit=False) as session:
                resolved_start_date = await WaterSummaryOps(
                    session
                ).get_first_summary_record_date(
                    group_ids=list(support_group_map.keys())
                )
    if resolved_start_date is None or resolved_start_date > resolved_end_date:
        return []

    summaries = cast(
        list["WaterSummaryRecord"],
        await water_repo.get_summaries_in_window(
            start_date=resolved_start_date,
            end_date=resolved_end_date,
            group_ids=list(support_group_map.keys()),
        ),
    )
    return _aggregate_recomputed_rows(
        summaries,
        members_by_user=members_by_user,
        limit=limit,
    )


async def main() -> None:
    nonebot.init()
    args = parse_args()
    limit = _normalize_limit(args.limit)
    if args.mode == "recompute":
        rows = await recompute_feedback_group_water_levels(
            limit=limit,
            start_date=_normalize_date(args.start_date),
            end_date=_normalize_date(args.end_date),
        )
    else:
        rows = await fetch_feedback_group_water_levels(limit=limit)
    if args.json:
        sys.stdout.write(f"{json.dumps(rows, ensure_ascii=False, indent=2)}\n")
        return
    sys.stdout.write(f"{_render_table(rows)}\n")


if __name__ == "__main__":
    asyncio.run(main())
