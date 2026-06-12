"""Migrate legacy PostgreSQL water records into the current water plugin."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import TYPE_CHECKING

import nonebot

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.system_migration import LegacyPgConfig, load_legacy_pg_config
from src.logger import logger

if TYPE_CHECKING:
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy water data")
    parser.add_argument(
        "--old-repo",
        default="../sakuraisenrin-old",
        help="path to the legacy repository root",
    )
    parser.add_argument("--pg-host", help="legacy PostgreSQL host override")
    parser.add_argument("--pg-port", type=int, help="legacy PostgreSQL port override")
    parser.add_argument("--pg-user", help="legacy PostgreSQL user override")
    parser.add_argument("--pg-password", help="legacy PostgreSQL password override")
    parser.add_argument(
        "--pg-database",
        default="senrin_water",
        help="legacy PostgreSQL database name",
    )
    parser.add_argument(
        "--report",
        default="./data/db/water-migration-report.json",
        help="where to write the migration report JSON",
    )
    parser.add_argument(
        "--no-reset-target",
        action="store_true",
        help="do not clear the target water runtime before importing",
    )
    parser.add_argument(
        "--from-date",
        type=_parse_day_arg,
        help="inclusive lower bound in YYYYMMDD",
    )
    parser.add_argument(
        "--to-date",
        type=_parse_day_arg,
        help="inclusive upper bound in YYYYMMDD",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=2000,
        help="message batch size for importing water_message",
    )
    return parser.parse_args()


def build_pg_config(args: argparse.Namespace) -> LegacyPgConfig:
    defaults = load_legacy_pg_config(Path(args.old_repo))
    return LegacyPgConfig(
        host=args.pg_host or defaults.host,
        port=args.pg_port or defaults.port,
        user=args.pg_user or defaults.user,
        password=args.pg_password or defaults.password,
        database=args.pg_database or defaults.database,
    )


async def main() -> None:
    nonebot.init()
    args = parse_args()
    if args.from_date is not None and args.to_date is not None:
        if args.from_date > args.to_date:
            raise ValueError("--from-date cannot be greater than --to-date")

    from src.plugins.water.migration import (
        build_legacy_water_rows,
        fetch_legacy_water_rows,
        migrate_legacy_water,
        write_report,
    )

    raw_rows = await fetch_legacy_water_rows(
        build_pg_config(args),
        from_date=args.from_date,
        to_date=args.to_date,
    )
    rows = build_legacy_water_rows(raw_rows)
    report = await migrate_legacy_water(
        rows,
        reset_target=not args.no_reset_target,
        chunk_size=args.chunk_size,
    )

    report_path = Path(args.report)
    await asyncio.to_thread(write_report, report_path, report)
    logger.success(
        "water migration completed: "
        f"report={report_path} "
        f"source_rows={report.source_rows} "
        f"imported_messages={report.imported_messages} "
        f"settled_days={report.settled_days} "
        f"failed_days={len(report.failed_days)}"
    )


def _parse_day_arg(raw: str) -> int:
    text = raw.strip()
    if len(text) != 8 or not text.isdigit():
        raise argparse.ArgumentTypeError("expected YYYYMMDD")
    return int(text)


if __name__ == "__main__":
    asyncio.run(main())
