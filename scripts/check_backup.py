"""Check SakuraiSenrin backup health and remote snapshot visibility."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

import nonebot

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.logger import logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check SakuraiSenrin backup health",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="max number of snapshots to show",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    nonebot.init()

    from src.services.backup import build_backup_service_from_config

    service = build_backup_service_from_config()
    snapshots = await service.list_snapshots()

    logger.success(f"backup healthcheck ok: {len(snapshots)} remote snapshots")
    for snapshot in snapshots[: args.limit]:
        logger.info(
            "snapshot: "
            f"{snapshot.short_id or snapshot.id} "
            f"time={snapshot.time or '-'} "
            f"host={snapshot.hostname or '-'} "
            f"files={snapshot.total_files_processed or 0} "
            f"bytes={snapshot.total_bytes_processed or 0}"
        )


if __name__ == "__main__":
    asyncio.run(main())
