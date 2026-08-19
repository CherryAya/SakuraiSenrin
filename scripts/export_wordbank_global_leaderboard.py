"""Export and render offline wordbank global creator leaderboard."""

from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any

import nonebot

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_OUTPUT_DIR = ROOT / "output"
DEFAULT_PERIOD = "total"
DEFAULT_LOCALE = "zh-CN"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export wordbank global creator leaderboard from offline DB",
    )
    parser.add_argument(
        "--period",
        choices=("week", "month", "season", "total"),
        default=DEFAULT_PERIOD,
        help="leaderboard period",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="top-N rows to export",
    )
    parser.add_argument(
        "--locale",
        default=DEFAULT_LOCALE,
        help="locale for display names and card rendering",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="output directory",
    )
    parser.add_argument(
        "--prefix",
        default="wordbank-global-leaderboard",
        help="output filename prefix",
    )
    parser.add_argument(
        "--render-image",
        action="store_true",
        help="also render leaderboard PNG",
    )
    return parser.parse_args()


async def _init_runtime() -> dict[str, Any]:
    nonebot.init()

    from src.lib.utils.img import QQAvatar
    from src.plugins.wordbank.database import wordbank_repo
    from src.plugins.wordbank.handlers.leaderboard_cards import (
        render_wordbank_leaderboard_card_bytes,
    )
    from src.plugins.wordbank.services import wordbank_service

    await wordbank_repo.init_all_tables()
    return {
        "QQAvatar": QQAvatar,
        "wordbank_repo": wordbank_repo,
        "wordbank_service": wordbank_service,
        "render_wordbank_leaderboard_card_bytes": (
            render_wordbank_leaderboard_card_bytes
        ),
    }


def _json_path(output_dir: Path, prefix: str, period: str) -> Path:
    return output_dir / f"{prefix}-{period}.json"


def _csv_path(output_dir: Path, prefix: str, period: str) -> Path:
    return output_dir / f"{prefix}-{period}.csv"


def _png_path(output_dir: Path, prefix: str, period: str) -> Path:
    return output_dir / f"{prefix}-{period}.png"


def _row_from_item(item: Any) -> dict[str, Any]:
    return {
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


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "rank",
        "user_id",
        "display_name",
        "approved_count",
        "score",
        "share",
        "latest_created_at",
        "group_count",
        "current_group_count",
        "all_groups_count",
        "self_count",
        "private_only_count",
        "self_in_current_group_count",
    ]
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


async def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)

    runtime = await _init_runtime()
    QQAvatar = runtime["QQAvatar"]
    wordbank_service = runtime["wordbank_service"]
    render_wordbank_leaderboard_card_bytes = runtime[
        "render_wordbank_leaderboard_card_bytes"
    ]

    data = await wordbank_service.build_creator_leaderboard(
        period=args.period,
        locale=args.locale,
        limit=max(1, int(args.limit)),
    )
    rows = [_row_from_item(item) for item in data.items]

    payload = {
        "title": data.title,
        "subtitle": data.subtitle,
        "period": data.period,
        "badge_text": data.badge_text,
        "range_text": data.range_text,
        "generated_at": int(data.generated_at),
        "total_creator_count": int(data.total_creator_count),
        "total_approved_count": int(data.total_approved_count),
        "champion_gap": int(data.champion_gap),
        "top_share": float(data.top_share),
        "items": rows,
    }

    json_path = _json_path(output_dir, args.prefix, args.period)
    csv_path = _csv_path(output_dir, args.prefix, args.period)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_csv(csv_path, rows)

    image_path: Path | None = None
    if args.render_image:
        render_data = replace(
            data,
            items=tuple(item for item in data.items if float(item.score) > 20.0),
        )
        if render_data.items:
            avatars = await asyncio.gather(
                *(
                    QQAvatar.fetch_user(item.user_id, size=160)
                    for item in render_data.items
                ),
                return_exceptions=True,
            )
            render_data = replace(
                render_data,
                items=tuple(
                    replace(
                        item,
                        avatar=avatar if not isinstance(avatar, Exception) else None,
                    )
                    for item, avatar in zip(render_data.items, avatars, strict=False)
                ),
            )
        image_bytes = await asyncio.to_thread(
            render_wordbank_leaderboard_card_bytes,
            data=render_data,
            locale=args.locale,
        )
        image_path = _png_path(output_dir, args.prefix, args.period)
        image_path.write_bytes(image_bytes)

    result = {
        "json": str(json_path),
        "csv": str(csv_path),
        "png": str(image_path) if image_path is not None else "",
        "rows": len(rows),
        "render_rows": sum(1 for item in data.items if float(item.score) > 20.0),
        "period": args.period,
    }
    sys.stdout.write(f"{json.dumps(result, ensure_ascii=False, indent=2)}\n")


if __name__ == "__main__":
    asyncio.run(main())
