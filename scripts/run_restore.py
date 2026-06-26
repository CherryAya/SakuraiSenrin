"""Restore a restic snapshot to a target directory."""

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
    parser = argparse.ArgumentParser(description="Restore SakuraiSenrin backup")
    parser.add_argument("snapshot", help="restic snapshot id, or latest")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--target", help="restore target directory")
    mode_group.add_argument(
        "--apply-local",
        action="store_true",
        help="restore snapshot into current local data files and refresh runtime state",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    nonebot.init()

    from src.services.backup import build_backup_service_from_config
    from src.services.startup_sync import restore_remote_snapshot_into_local

    if getattr(args, "apply_local", False):
        await restore_remote_snapshot_into_local(snapshot=args.snapshot)
        logger.success(f"restore completed and applied locally: {args.snapshot}")
        return

    service = build_backup_service_from_config()
    await service.restore(snapshot=args.snapshot, target=Path(args.target))
    logger.success(f"restore completed: {args.snapshot} -> {args.target}")


if __name__ == "__main__":
    asyncio.run(main())
