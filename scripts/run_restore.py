"""Restore a restic snapshot to a target directory."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import nonebot

from src.logger import logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restore SakuraiSenrin backup")
    parser.add_argument("snapshot", help="restic snapshot id, or latest")
    parser.add_argument("--target", required=True, help="restore target directory")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    nonebot.init()

    from src.services.backup import build_backup_service_from_config

    service = build_backup_service_from_config()
    await service.restore(snapshot=args.snapshot, target=Path(args.target))
    logger.success(f"restore completed: {args.snapshot} -> {args.target}")


if __name__ == "__main__":
    asyncio.run(main())
