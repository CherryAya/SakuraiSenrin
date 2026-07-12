"""Backfill wordbank local cache metadata from files already present in media_cache."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

import nonebot

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lib.utils.common import get_current_time
from src.logger import logger

wordbank_repo: Any = None
wordbank_media_service: Any = None
DEFAULT_REPORT_PATH = "./data/db/wordbank-media-cache-backfill-report.json"


def _load_wordbank_components() -> None:
    global wordbank_repo
    global wordbank_media_service
    if wordbank_repo is not None and wordbank_media_service is not None:
        return

    from src.plugins.wordbank.database import wordbank_repo as loaded_wordbank_repo
    from src.plugins.wordbank.services import (
        wordbank_media_service as loaded_wordbank_media_service,
    )

    wordbank_repo = loaded_wordbank_repo
    wordbank_media_service = loaded_wordbank_media_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill wordbank local cache metadata from media_cache files"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect rows without writing local_cache_path metadata",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="maximum number of image rows to inspect, 0 means no limit",
    )
    parser.add_argument(
        "--id-start",
        type=int,
        default=0,
        help="only inspect image rows whose id is greater than or equal to this value",
    )
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="also refresh rows that already have a local_cache_path value",
    )
    parser.add_argument(
        "--report",
        default=DEFAULT_REPORT_PATH,
        help="where to write the backfill report JSON",
    )
    return parser.parse_args()


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def backfill_wordbank_media_cache(
    *,
    dry_run: bool,
    limit: int,
    id_start: int,
    include_existing: bool,
) -> dict[str, Any]:
    _load_wordbank_components()
    report = await wordbank_media_service.backfill_local_cache_metadata(
        dry_run=dry_run,
        limit=limit,
        id_start=id_start,
        only_missing=not include_existing,
    )
    return {
        "generated_at": get_current_time(),
        "dry_run": dry_run,
        "limit": limit,
        "id_start": id_start,
        "include_existing": include_existing,
        **report,
    }


async def main() -> None:
    nonebot.init()
    _load_wordbank_components()
    args = parse_args()
    await wordbank_repo.init_all_tables()
    report = await backfill_wordbank_media_cache(
        dry_run=args.dry_run,
        limit=args.limit,
        id_start=args.id_start,
        include_existing=args.include_existing,
    )
    report_path = await asyncio.to_thread(lambda: Path(args.report).resolve())
    await asyncio.to_thread(write_report, report_path, report)
    logger.info(
        "[Wordbank] local cache metadata backfill done: "
        f"dry_run={report['dry_run']} scanned={report['scanned']} "
        f"updated={report['updated']} unchanged={report['unchanged']} "
        f"skipped_existing={report['skipped_existing']} "
        f"missing_files={report['missing_files']} failed={report['failed']} "
        f"report={report_path}"
    )


if __name__ == "__main__":
    asyncio.run(main())
