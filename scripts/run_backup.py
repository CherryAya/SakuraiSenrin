"""Run the configured database backup once."""

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
    parser = argparse.ArgumentParser(description="Run SakuraiSenrin database backup")
    parser.add_argument("--force", action="store_true", help="run even if disabled")
    parser.add_argument("--profile", help="backup profile override")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    nonebot.init()

    from src.services.backup import (
        build_backup_service_from_config,
        build_default_backup_plan,
    )

    service = build_backup_service_from_config(args.profile)
    plan = build_default_backup_plan()
    result = await service.run(plan, force=args.force, stream_output=True)
    if result is None:
        logger.info("backup skipped")
        return
    profile_name = getattr(service, "profile_name", args.profile or "default")
    logger.success(
        f"backup completed: {result.run_id} profile={profile_name}"
    )
    logger.info(f"manifest: {result.manifest_path}")
    if result.restic_snapshot_id:
        logger.info(f"restic snapshot: {result.restic_snapshot_id}")


if __name__ == "__main__":
    asyncio.run(main())
