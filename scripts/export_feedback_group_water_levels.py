"""List water global levels for users inside configured support groups."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

import nonebot
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.core.tables import Member, User
from src.database.instances import core_db
from src.lib.plugin_docs.meta import resolve_support_groups

if TYPE_CHECKING:
    from src.lib.db.connectors import StateStore
    from src.plugins.water.database.tables import WaterGlobalLevel

water_core_db: "StateStore" | None = None
WaterGlobalLevel: type["WaterGlobalLevel"] | None = None


def _load_water_components() -> None:
    global water_core_db
    global WaterGlobalLevel
    if water_core_db is not None and WaterGlobalLevel is not None:
        return

    from src.plugins.water.database.instances import (
        water_core_db as loaded_water_core_db,
    )
    from src.plugins.water.database.tables import (
        WaterGlobalLevel as loaded_water_global_level,
    )

    water_core_db = loaded_water_core_db
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
    return parser.parse_args()


def _normalize_limit(value: int) -> int | None:
    if value <= 0:
        return None
    return value


def _format_group_label(group_id: str, title: str) -> str:
    return f"{group_id}|{title}" if title else group_id


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
                str(row.get(header, "")).ljust(widths[header])
                for header in headers
            )
        )
    return "\n".join(lines)


async def fetch_feedback_group_water_levels(
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    _load_water_components()
    support_groups = resolve_support_groups()
    support_group_map = {
        str(group.group_id).strip(): str(group.title).strip()
        for group in support_groups
    }
    support_group_ids = [group_id for group_id in support_group_map if group_id]
    if not support_group_ids:
        return []

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
        return []

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

    result: list[dict[str, Any]] = []
    for index, (user_id, level, exp, season_exp) in enumerate(level_rows, start=1):
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


async def main() -> None:
    nonebot.init()
    args = parse_args()
    rows = await fetch_feedback_group_water_levels(
        limit=_normalize_limit(args.limit),
    )
    if args.json:
        sys.stdout.write(f"{json.dumps(rows, ensure_ascii=False, indent=2)}\n")
        return
    sys.stdout.write(f"{_render_table(rows)}\n")


if __name__ == "__main__":
    asyncio.run(main())
