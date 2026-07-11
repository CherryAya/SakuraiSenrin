"""Render a water daily report demo image from real DB data."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any

import nonebot
import zstandard as zstd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DB_ROOT = Path("./data/db")
DEFAULT_NAMESPACE = "water_db"
DEFAULT_OUTPUT = Path("./output/water-report-demo.png")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render water report demo image from local DB data",
    )
    parser.add_argument(
        "--db-root",
        default=str(DEFAULT_DB_ROOT),
        help="database root directory",
    )
    parser.add_argument(
        "--namespace",
        default=DEFAULT_NAMESPACE,
        help="water DB namespace directory",
    )
    parser.add_argument(
        "--group-id",
        help="target group id; if omitted, auto-pick a high-activity sample",
    )
    parser.add_argument(
        "--record-date",
        type=int,
        help="target record date in YYYYMMDD; if omitted, auto-pick a sample",
    )
    parser.add_argument(
        "--window",
        choices=("today_live", "yesterday_settled"),
        default="yesterday_settled",
        help="render settled report or today-live report style",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="output PNG path",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=1,
        help="when auto-picking, choose the Nth most active group-day sample",
    )
    return parser.parse_args()


@contextmanager
def _open_sqlite(path: Path) -> Any:
    if path.suffix != ".zst":
        conn = sqlite3.connect(path)
        try:
            yield conn
        finally:
            conn.close()
        return

    with tempfile.TemporaryDirectory(prefix="water-report-demo-") as tmp_dir:
        hydrated_path = Path(tmp_dir) / path.name.removesuffix(".zst")
        dctx = zstd.ZstdDecompressor()
        with path.open("rb") as src, hydrated_path.open("wb") as dst:
            dctx.copy_stream(src, dst)
        conn = sqlite3.connect(hydrated_path)
        try:
            yield conn
        finally:
            conn.close()


def _list_summary_files(namespace_dir: Path) -> list[Path]:
    return sorted(
        [
            *namespace_dir.glob("summary_*.db"),
            *namespace_dir.glob("summary_*.db.zst"),
        ]
    )


def pick_active_sample(
    *,
    db_root: Path,
    namespace: str,
    top_n: int,
) -> tuple[str, int, int]:
    namespace_dir = db_root / namespace
    candidates: list[tuple[int, str, int]] = []
    for db_path in _list_summary_files(namespace_dir):
        with _open_sqlite(db_path) as conn:
            rows = conn.execute(
                """
                SELECT group_id, record_date, SUM(msg_count) AS total_msg_count
                FROM water_daily_summary
                GROUP BY group_id, record_date
                ORDER BY total_msg_count DESC
                LIMIT 10
                """
            ).fetchall()
        for row in rows:
            candidates.append((int(row[2]), str(row[0]), int(row[1])))

    if not candidates:
        raise RuntimeError("No water summary rows found in local DB")

    ordered = sorted(candidates, key=lambda item: (-item[0], item[2], item[1]))
    index = max(0, min(len(ordered) - 1, top_n - 1))
    total_msg_count, group_id, record_date = ordered[index]
    return group_id, record_date, total_msg_count


async def render_demo(args: argparse.Namespace) -> tuple[Path, str]:
    nonebot.init()

    from src.plugins.water.database import water_repo
    from src.plugins.water.renderers.report import build_water_group_report_image
    from src.plugins.water.services.report import water_report_service

    await water_repo.init_all_tables()

    group_id = args.group_id
    record_date = args.record_date
    picked_total = None
    if not group_id or not record_date:
        group_id, record_date, picked_total = pick_active_sample(
            db_root=Path(args.db_root),
            namespace=args.namespace,
            top_n=args.top_n,
        )

    snapshot = await water_repo.get_group_report_summary_snapshot(
        group_id=str(group_id),
        record_date=int(record_date),
    )
    if snapshot is None:
        raise RuntimeError(
            f"No group report snapshot found for group={group_id} date={record_date}"
        )

    data = await water_report_service._build_card_data(
        args.window,
        snapshot,
        "zh-CN",
    )
    image = await build_water_group_report_image(data, "zh-CN")
    if image is None:
        raise RuntimeError(
            "Report renderer returned empty image for "
            f"group={group_id} date={record_date}"
        )

    output_path = Path(args.output)
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output_path.write_bytes, image)
    sample_note = (
        f"group={group_id} date={record_date} total_msg_count={picked_total}"
        if picked_total is not None
        else f"group={group_id} date={record_date}"
    )
    return output_path, sample_note


async def main() -> None:
    args = parse_args()
    output_path, sample_note = await render_demo(args)
    sys.stdout.write(f"rendered: {output_path}\n")
    sys.stdout.write(f"sample: {sample_note}\n")


if __name__ == "__main__":
    asyncio.run(main())
