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
    parser.add_argument("--profile", help="backup profile override")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    nonebot.init()

    from src.services.backup import build_backup_service_from_config

    service = build_backup_service_from_config(args.profile)
    snapshots = await service.list_snapshots()

    profile_name = getattr(service, "profile_name", args.profile or "default")
    logger.success(
        "backup healthcheck ok: "
        f"{len(snapshots)} remote snapshots profile={profile_name}"
    )
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
