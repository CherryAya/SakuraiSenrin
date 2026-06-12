"""Operational entrypoint for archiving sharded event databases."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_pkg(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(path)]  # type: ignore[attr-defined]
    sys.modules[name] = pkg


_ensure_pkg("src.plugins.wordbank", ROOT / "src" / "plugins" / "wordbank")
_ensure_pkg("src.plugins.water", ROOT / "src" / "plugins" / "water")

from src.logger import logger
from src.plugins.water.database.instances import water_message, water_summary
from src.plugins.wordbank.database.instances import (
    wordbank_log_db,
    wordbank_message_ref_db,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive sharded event databases")
    parser.add_argument(
        "--target",
        choices=("wordbank", "water", "all"),
        default="all",
        help="which database group to archive",
    )
    parser.add_argument(
        "--include-water-summary",
        action="store_true",
        help="include water summary shards when target includes water",
    )
    return parser.parse_args()


async def archive_targets(
    *,
    target: str,
    include_water_summary: bool = False,
) -> list[str]:
    completed: list[str] = []
    if target in {"wordbank", "all"}:
        await wordbank_log_db.run_archiver_task()
        completed.append("wordbank_logs")
        await wordbank_message_ref_db.run_archiver_task()
        completed.append("wordbank_message_ref")
    if target in {"water", "all"}:
        await water_message.run_archiver_task()
        completed.append("water_message")
        if include_water_summary:
            await water_summary.run_archiver_task()
            completed.append("water_summary")
    return completed


async def main() -> None:
    args = parse_args()
    logger.info(
        "[archive-event-shards] starting "
        f"target={args.target} "
        f"include_water_summary={args.include_water_summary}"
    )
    completed = await archive_targets(
        target=args.target,
        include_water_summary=args.include_water_summary,
    )
    logger.success(
        "[archive-event-shards] completed " + ",".join(completed or ["none"])
    )


if __name__ == "__main__":
    asyncio.run(main())
