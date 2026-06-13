"""Run one-shot maintenance for wordbank remote media sync and cache metadata."""

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
    parser = argparse.ArgumentParser(description="Maintain wordbank media storage")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect candidates without uploading remote objects",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="maximum number of records to inspect",
    )
    parser.add_argument(
        "--id-start",
        type=int,
        default=0,
        help="only inspect records whose id is greater than or equal to this value",
    )
    parser.add_argument(
        "--only-unsynced",
        action="store_true",
        help="only process rows missing synced remote metadata",
    )
    parser.add_argument(
        "--verify-remote",
        action="store_true",
        help="verify the uploaded object exists after sync",
    )
    parser.add_argument(
        "--rebuild-cache-metadata",
        action="store_true",
        help="refresh local cache size metadata before syncing",
    )
    parser.add_argument(
        "--report",
        default="./data/db/wordbank-media-maintenance-report.json",
        help="where to write the maintenance report JSON",
    )
    return parser.parse_args()


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def maintain_wordbank_media(
    *,
    dry_run: bool,
    limit: int,
    id_start: int,
    only_unsynced: bool,
    verify_remote: bool,
    rebuild_cache_metadata: bool,
) -> dict[str, Any]:
    _load_wordbank_components()
    rows = await wordbank_repo.list_images_for_remote_sync(
        limit=limit,
        id_start=id_start,
        only_unsynced=only_unsynced,
    )
    report_rows: list[dict[str, Any]] = []
    synced = 0
    failed = 0

    for image in rows:
        row_report: dict[str, Any] = {
            "id": image.id,
            "canonical_image_id": image.canonical_id,
            "md5": image.md5,
            "remote_sync_status_before": image.remote_sync_status,
            "remote_storage_path_before": image.remote_storage_path,
        }
        working_image = image
        if rebuild_cache_metadata:
            working_image = await wordbank_media_service.rebuild_cache_metadata(
                working_image
            )
        if dry_run:
            row_report["action"] = "inspect"
            row_report["remote_sync_status_after"] = working_image.remote_sync_status
            row_report["remote_storage_path_after"] = working_image.remote_storage_path
            report_rows.append(row_report)
            continue

        updated = await wordbank_media_service.sync_image_to_remote(
            working_image,
            verify_remote=verify_remote,
        )
        if updated is None:
            row_report["action"] = "skipped"
            report_rows.append(row_report)
            continue

        row_report["action"] = "sync"
        row_report["remote_sync_status_after"] = updated.remote_sync_status
        row_report["remote_storage_path_after"] = updated.remote_storage_path
        row_report["remote_synced_at"] = updated.remote_synced_at
        if updated.remote_sync_status == "synced":
            synced += 1
        else:
            failed += 1
        report_rows.append(row_report)

    return {
        "generated_at": get_current_time(),
        "dry_run": dry_run,
        "limit": limit,
        "id_start": id_start,
        "only_unsynced": only_unsynced,
        "verify_remote": verify_remote,
        "rebuild_cache_metadata": rebuild_cache_metadata,
        "scanned": len(rows),
        "synced": synced,
        "failed": failed,
        "rows": report_rows,
    }


async def main() -> None:
    nonebot.init()
    _load_wordbank_components()
    args = parse_args()
    await wordbank_repo.init_all_tables()
    report = await maintain_wordbank_media(
        dry_run=args.dry_run,
        limit=args.limit,
        id_start=args.id_start,
        only_unsynced=args.only_unsynced,
        verify_remote=args.verify_remote,
        rebuild_cache_metadata=args.rebuild_cache_metadata,
    )
    report_path = Path(args.report)
    await asyncio.to_thread(write_report, report_path, report)
    logger.success(f"wordbank media maintenance report written: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
