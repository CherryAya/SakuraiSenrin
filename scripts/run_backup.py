"""Run the configured database backup once."""

from __future__ import annotations

import argparse
import asyncio

import nonebot

from src.logger import logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SakuraiSenrin database backup")
    parser.add_argument("--force", action="store_true", help="run even if disabled")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    nonebot.init()

    from src.services.backup import (
        build_backup_service_from_config,
        build_default_backup_plan,
    )

    service = build_backup_service_from_config()
    plan = build_default_backup_plan()
    result = await service.run(plan, force=args.force)
    if result is None:
        logger.info("backup skipped")
        return
    logger.success(f"backup completed: {result.run_id}")
    logger.info(f"manifest: {result.manifest_path}")
    if result.restic_snapshot_id:
        logger.info(f"restic snapshot: {result.restic_snapshot_id}")


if __name__ == "__main__":
    asyncio.run(main())
