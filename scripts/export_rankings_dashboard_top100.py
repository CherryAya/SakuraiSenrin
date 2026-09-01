"""Export rankings dashboard data bundle for the offline HTML page."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import json
from pathlib import Path
import sys
from typing import Any

import arrow
import nonebot
from nonebot import logger
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lib.utils.common import get_current_time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100, help="top-N rows per board")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output" / "rankings-dashboard-top100" / "index.json",
        help="output bundle manifest path",
    )
    parser.add_argument(
        "--locale",
        default="zh-CN",
        help="locale used for wordbank display names",
    )
    parser.add_argument(
        "--wordbank-period",
        choices=("week", "month", "season", "total"),
        default="total",
        help="wordbank leaderboard period",
    )
    return parser.parse_args()


async def _fetch_global_rows(limit: int) -> list[dict[str, Any]]:
    from src.database.core.tables import CoreBase, Group, User
    from src.database.instances import core_db
    from src.plugins.water.database import water_repo
    from src.plugins.water.database.instances import water_core_db
    from src.plugins.water.database.tables import WaterGlobalLevel, WaterGroupUserTotal

    await core_db.init(CoreBase)
    await water_repo.init_all_tables()

    async with core_db.session(commit=False) as session:
        users = {
            str(user_id): str(user_name)
            for user_id, user_name in (
                await session.execute(select(User.user_id, User.user_name))
            ).all()
        }
        groups = {
            str(group_id): str(group_name)
            for group_id, group_name in (
                await session.execute(select(Group.group_id, Group.group_name))
            ).all()
        }

    async with water_core_db.session(commit=False) as session:
        levels = (
            await session.execute(
                select(
                    WaterGlobalLevel.user_id,
                    WaterGlobalLevel.level,
                    WaterGlobalLevel.exp,
                    WaterGlobalLevel.season_exp,
                ).order_by(
                    WaterGlobalLevel.level.desc(),
                    WaterGlobalLevel.exp.desc(),
                    WaterGlobalLevel.user_id.asc(),
                ).limit(max(1, limit))
            )
        ).all()
        user_ids = [str(row.user_id) for row in levels]
        totals = (
            await session.execute(
                select(
                    WaterGroupUserTotal.user_id,
                    WaterGroupUserTotal.group_id,
                    WaterGroupUserTotal.msg_count,
                    WaterGroupUserTotal.active_days,
                    WaterGroupUserTotal.active_hours,
                )
                .where(WaterGroupUserTotal.user_id.in_(user_ids))
                .order_by(
                    WaterGroupUserTotal.user_id.asc(),
                    WaterGroupUserTotal.msg_count.desc(),
                    WaterGroupUserTotal.active_days.desc(),
                    WaterGroupUserTotal.active_hours.desc(),
                    WaterGroupUserTotal.group_id.asc(),
                )
            )
        ).all()

    groups_by_user: dict[str, list[dict[str, int | str]]] = {}
    total_msg_by_user: dict[str, int] = defaultdict(int)
    for row in totals:
        user_id = str(row.user_id)
        total_msg_by_user[user_id] += int(row.msg_count)
        groups_by_user.setdefault(user_id, []).append(
            {
                "group_id": str(row.group_id),
                "group_name": groups.get(str(row.group_id), str(row.group_id)),
                "msg_count": int(row.msg_count),
                "active_days": int(row.active_days),
                "active_hours": int(row.active_hours),
            }
        )

    return [
        {
            "rank": rank,
            "user_id": str(row.user_id),
            "user_name": users.get(str(row.user_id), str(row.user_id)),
            "level": int(row.level),
            "exp": int(row.exp),
            "season_exp": int(row.season_exp),
            "total_msg_count": int(total_msg_by_user.get(str(row.user_id), 0)),
            "active_group_count": len(groups_by_user.get(str(row.user_id), [])),
            "active_groups": groups_by_user.get(str(row.user_id), []),
        }
        for rank, row in enumerate(levels, start=1)
    ]


async def _fetch_feedback_rows(limit: int) -> list[dict[str, Any]]:
    from scripts.export_feedback_group_water_levels import (
        recompute_feedback_group_water_levels,
    )

    return await recompute_feedback_group_water_levels(limit=max(1, limit))


async def _load_group_members(
    group_ids: list[str],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    from src.database.core.tables import Group, Member, User
    from src.database.instances import core_db

    if not group_ids:
        return {}, {}

    async with core_db.session(commit=False) as session:
        group_rows = (
            await session.execute(
                select(Group.group_id, Group.group_name).where(
                    Group.group_id.in_(group_ids)
                )
            )
        ).all()
        member_rows = (
            await session.execute(
                select(
                    Member.user_id,
                    Member.group_id,
                    Member.group_card,
                    User.user_name,
                )
                .join(User, User.user_id == Member.user_id)
                .where(Member.group_id.in_(group_ids))
                .order_by(Member.user_id.asc(), Member.group_id.asc())
            )
        ).all()

    group_map = {
        str(group_id).strip(): str(group_name).strip()
        for group_id, group_name in group_rows
        if str(group_id).strip()
    }
    if not member_rows:
        return group_map, {}

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
        if group_id_text in group_map:
            entry["groups"].append(
                f"{group_id_text}|{group_map.get(group_id_text, group_id_text)}"
            )
    return group_map, members_by_user


async def _build_user_profiles(
    *,
    user_ids: list[str],
    group_ids: list[str],
) -> dict[str, Any]:
    from src.plugins.water.database import water_repo
    from src.plugins.water.database.instances import water_core_db
    from src.plugins.water.database.ops_summary import WaterSummaryOps

    group_map, members_by_user = await _load_group_members(group_ids)
    if not group_map or not members_by_user:
        return {"groups": [], "profiles": {}}

    target_ids = [user_id for user_id in user_ids if user_id in members_by_user]
    if not target_ids:
        return {
            "groups": [
                {"group_id": gid, "group_name": gname}
                for gid, gname in group_map.items()
            ],
            "profiles": {},
        }

    hot_start_date = water_repo._hot_summary_start_date()
    first_archived_date = await water_repo._get_archived_first_summary_record_date(
        end_date=water_repo._previous_date(hot_start_date),
        group_ids=list(group_map.keys()),
    )
    if first_archived_date is not None:
        start_date = first_archived_date
    else:
        async with water_core_db.session(commit=False) as session:
            start_date = await WaterSummaryOps(session).get_first_summary_record_date(
                group_ids=list(group_map.keys())
            )
    if start_date is None:
        return {
            "groups": [
                {"group_id": gid, "group_name": gname}
                for gid, gname in group_map.items()
            ],
            "profiles": {},
        }

    summaries = await water_repo.get_summaries_in_window(
        start_date=start_date,
        end_date=int(
            arrow.get(get_current_time()).to("Asia/Shanghai").format("YYYYMMDD")
        ),
        group_ids=list(group_map.keys()),
    )
    summaries_by_user: dict[str, list[Any]] = defaultdict(list)
    for row in summaries:
        if row.user_id in target_ids:
            summaries_by_user[row.user_id].append(row)

    def _compress_hours(hours: set[int]) -> list[dict[str, int]]:
        if not hours:
            return []
        ordered = sorted(hours)
        ranges: list[dict[str, int]] = []
        start = prev = ordered[0]
        for hour in ordered[1:]:
            if hour == prev + 1:
                prev = hour
                continue
            ranges.append({"start": start, "end": prev})
            start = prev = hour
        ranges.append({"start": start, "end": prev})
        return ranges

    profiles: dict[str, Any] = {}
    for user_id in target_ids:
        rows = summaries_by_user.get(user_id, [])
        if not rows:
            continue
        rows.sort(key=lambda item: (item.record_date, item.group_id))
        group_totals: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "msg_count": 0,
                "active_days": set(),
                "active_hours": set(),
                "hourly_counts": [0] * 24,
            }
        )
        daily_totals: dict[int, dict[str, Any]] = defaultdict(
            lambda: {"msg_count": 0, "group_breakdown": {}, "hourly_counts": [0] * 24}
        )
        for row in rows:
            group_id = str(row.group_id)
            record_date = int(row.record_date)
            msg_count = int(row.msg_count)
            hourly_counts = list(row.hourly_counts or [0] * 24)[:24]
            if len(hourly_counts) < 24:
                hourly_counts.extend([0] * (24 - len(hourly_counts)))
            group_totals[group_id]["msg_count"] += msg_count
            group_totals[group_id]["active_days"].add(record_date)
            group_totals[group_id]["active_hours"].update(
                idx for idx, count in enumerate(hourly_counts) if count > 0
            )
            for idx, count in enumerate(hourly_counts):
                group_totals[group_id]["hourly_counts"][idx] += int(count)
            daily_totals[record_date]["msg_count"] += msg_count
            daily_totals[record_date]["group_breakdown"][group_id] = msg_count
            for idx, count in enumerate(hourly_counts):
                daily_totals[record_date]["hourly_counts"][idx] += int(count)

        profiles[user_id] = {
            "user_id": user_id,
            "display_name": members_by_user[user_id]["display_name"],
            "groups": [
                {
                    "group_id": group_id,
                    "group_name": group_map.get(group_id, group_id),
                    "msg_count": int(info["msg_count"]),
                    "active_days": len(info["active_days"]),
                    "active_hours": len(info["active_hours"]),
                    "hourly_counts": info["hourly_counts"],
                }
                for group_id, info in sorted(
                    group_totals.items(),
                    key=lambda item: (-int(item[1]["msg_count"]), item[0]),
                )
            ],
            "daily": [
                {
                    "record_date": record_date,
                    "msg_count": int(info["msg_count"]),
                    "group_breakdown": info["group_breakdown"],
                    "hourly_counts": info["hourly_counts"],
                }
                for record_date, info in sorted(daily_totals.items())
            ],
            "date_range": {
                "start": min(daily_totals) if daily_totals else None,
                "end": max(daily_totals) if daily_totals else None,
            },
        }
        all_hours = set()
        for info in group_totals.values():
            all_hours.update(info["active_hours"])
        profiles[user_id]["active_hours_ranges"] = _compress_hours(all_hours)

    return {
        "groups": [
            {"group_id": gid, "group_name": gname}
            for gid, gname in group_map.items()
        ],
        "profiles": profiles,
    }

async def _fetch_wordbank_rows(
    *,
    limit: int,
    locale: str,
    period: str,
) -> list[dict[str, Any]]:
    from src.plugins.wordbank.database import wordbank_repo
    from src.plugins.wordbank.services import wordbank_service

    await wordbank_repo.init_all_tables()
    data = await wordbank_service.build_creator_leaderboard(
        period=period,
        locale=locale,
        limit=max(1, limit),
    )
    return [
        {
            "rank": int(item.current_rank),
            "user_id": str(item.user_id),
            "display_name": str(item.display_name),
            "approved_count": int(item.approved_count),
            "score": float(item.score),
            "share": float(item.share),
            "latest_created_at": int(item.latest_created_at),
            "group_count": int(item.group_count),
            "current_group_count": int(item.current_group_count),
            "all_groups_count": int(item.all_groups_count),
            "self_count": int(item.self_count),
            "private_only_count": int(item.private_only_count),
            "self_in_current_group_count": int(item.self_in_current_group_count),
        }
        for item in data.items
    ]


async def main() -> None:
    args = parse_args()
    nonebot.init()

    global_rows, feedback_rows, wordbank_rows = await asyncio.gather(
        _fetch_global_rows(args.limit),
        _fetch_feedback_rows(args.limit),
        _fetch_wordbank_rows(
            limit=args.limit,
            locale=args.locale,
            period=args.wordbank_period,
        ),
    )
    target_user_ids = list(
        dict.fromkeys(
            [row["user_id"] for row in global_rows]
            + [row["user_id"] for row in feedback_rows]
            + [row["user_id"] for row in wordbank_rows]
        )
    )

    from src.database.core.tables import Group
    from src.database.instances import core_db
    from src.lib.plugin_docs.meta import resolve_support_groups

    async with core_db.session(commit=False) as session:
        all_group_ids = [
            str(group_id)
            for group_id, in (
                await session.execute(select(Group.group_id))
            ).all()
        ]

    support_group_ids = []
    for group in resolve_support_groups():
        group_id = str(group.group_id).strip()
        if group_id:
            support_group_ids.append(group_id)

    global_profiles = await _build_user_profiles(
        user_ids=target_user_ids,
        group_ids=all_group_ids,
    )
    feedback_profiles = await _build_user_profiles(
        user_ids=target_user_ids,
        group_ids=support_group_ids,
    )

    now = arrow.get(get_current_time()).to("Asia/Shanghai")
    bundle_dir = args.output.parent
    global_profile_dir = bundle_dir / "profiles" / "global"
    feedback_profile_dir = bundle_dir / "profiles" / "feedback"
    payload = {
        "generated_at": int(now.timestamp()),
        "generated_date": now.strftime("%Y-%m-%d"),
        "limit": int(args.limit),
        "formula": {
            "exp_latex": r"E = \left\lfloor 10\sqrt{M} + 5H \right\rfloor",
            "level_latex": (
                r"L = \max\left(1, \left\lfloor \sqrt{\frac{E}{100}} \right"
                r"\rfloor\right)"
            ),
            "variables": {
                "M": "消息数",
                "H": "活跃小时数",
                "E": "经验值",
                "L": "等级",
            },
        },
        "sources": {
            "global": (
                "water_global_level + water_group_user_total + "
                "core_db.biz_user/biz_group"
            ),
            "feedback": (
                "scripts.export_feedback_group_water_levels."
                "recompute_feedback_group_water_levels"
            ),
            "wordbank": "wordbank_service.build_creator_leaderboard(total)",
        },
        "profile_roots": {
            "global": "profiles/global",
            "feedback": "profiles/feedback",
        },
        "global": global_rows,
        "feedback": feedback_rows,
        "wordbank": wordbank_rows,
    }

    bundle_dir.mkdir(parents=True, exist_ok=True)
    global_profile_dir.mkdir(parents=True, exist_ok=True)
    feedback_profile_dir.mkdir(parents=True, exist_ok=True)

    for user_id, profile in global_profiles.get("profiles", {}).items():
        (global_profile_dir / f"{user_id}.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    for user_id, profile in feedback_profiles.get("profiles", {}).items():
        (feedback_profile_dir / f"{user_id}.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        json.dumps(
            {
                "output": str(args.output),
                "global_profiles": str(global_profile_dir),
                "feedback_profiles": str(feedback_profile_dir),
                "generated_date": payload["generated_date"],
                "global_rows": len(global_rows),
                "feedback_rows": len(feedback_rows),
                "wordbank_rows": len(wordbank_rows),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
