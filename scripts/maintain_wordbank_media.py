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

from src.lib.object_storage.types import StorageObject
from src.lib.utils.common import get_current_time
from src.logger import logger

wordbank_repo: Any = None
wordbank_media_service: Any = None
DEFAULT_BATCH_SIZE = 200
DEFAULT_CONCURRENCY = 8


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
        default=0,
        help="maximum total records to inspect, 0 means no limit",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="how many candidate rows to fetch per database page",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="maximum number of concurrent remote sync tasks per batch",
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
    batch_size: int,
    concurrency: int,
    id_start: int,
    only_unsynced: bool,
    verify_remote: bool,
    rebuild_cache_metadata: bool,
) -> dict[str, Any]:
    _load_wordbank_components()
    report_rows: list[dict[str, Any]] = []
    remote_inventory: dict[str, StorageObject] = {}
    use_remote_inventory = False
    scanned = 0
    synced = 0
    failed = 0
    skipped = 0
    cursor = id_start
    page = 0
    total_limit = max(limit, 0)
    batch_size = max(batch_size, 1)
    concurrency = max(concurrency, 1)
    semaphore = asyncio.Semaphore(concurrency)

    if (
        verify_remote
        and getattr(wordbank_media_service, "remote_storage", None) is not None
    ):
        try:
            remote_inventory = await wordbank_media_service.list_remote_objects_by_key()
            use_remote_inventory = True
            logger.info(
                f"[Wordbank] loaded remote inventory "
                f"with {len(remote_inventory)} objects"
            )
        except Exception as exc:
            logger.warning(
                f"[Wordbank] remote inventory listing failed, "
                f"fallback to per-object verification: {exc}"
            )

    async def process_image(image: Any) -> tuple[dict[str, Any], str]:
        row_report: dict[str, Any] = {
            "id": image.id,
            "canonical_image_id": image.canonical_id,
            "md5": image.md5,
            "remote_sync_status_before": image.remote_sync_status,
            "remote_storage_path_before": image.remote_storage_path,
        }
        async with semaphore:
            working_image = image
            if rebuild_cache_metadata:
                working_image = await wordbank_media_service.rebuild_cache_metadata(
                    working_image
                )
            if dry_run:
                row_report["action"] = "inspect"
                row_report["remote_sync_status_after"] = (
                    working_image.remote_sync_status
                )
                row_report["remote_storage_path_after"] = (
                    working_image.remote_storage_path
                )
                return row_report, "inspect"

            if use_remote_inventory:
                inventory_synced = (
                    await wordbank_media_service.reconcile_image_with_remote_inventory(
                        working_image,
                        remote_inventory,
                    )
                )
                if inventory_synced is not None:
                    row_report["action"] = "verify"
                    row_report["remote_sync_status_after"] = (
                        inventory_synced.remote_sync_status
                    )
                    row_report["remote_storage_path_after"] = (
                        inventory_synced.remote_storage_path
                    )
                    row_report["remote_synced_at"] = inventory_synced.remote_synced_at
                    return row_report, "synced"

            updated = await wordbank_media_service.sync_image_to_remote(
                working_image,
                verify_remote=verify_remote and not use_remote_inventory,
            )
            if updated is None:
                row_report["action"] = "skipped"
                return row_report, "skipped"

            row_report["action"] = "sync"
            row_report["remote_sync_status_after"] = updated.remote_sync_status
            row_report["remote_storage_path_after"] = updated.remote_storage_path
            row_report["remote_synced_at"] = updated.remote_synced_at
            if updated.remote_sync_status == "synced":
                return row_report, "synced"
            return row_report, "failed"

    while True:
        remaining = total_limit - scanned if total_limit > 0 else batch_size
        if total_limit > 0 and remaining <= 0:
            break
        fetch_limit = min(batch_size, remaining) if total_limit > 0 else batch_size
        rows = await wordbank_repo.list_images_for_remote_sync(
            limit=fetch_limit,
            id_start=cursor,
            only_unsynced=only_unsynced,
        )
        if not rows:
            break
        page += 1
        logger.info(
            f"[Wordbank] media maintenance page {page} "
            f"fetched {len(rows)} rows from id >= {cursor}"
        )
        results = await asyncio.gather(*(process_image(image) for image in rows))
        for row_report, status in results:
            scanned += 1
            report_rows.append(row_report)
            if status == "synced":
                synced += 1
            elif status == "failed":
                failed += 1
            elif status == "skipped":
                skipped += 1
        logger.info(
            f"[Wordbank] media maintenance progress page={page} processed={scanned} "
            f"synced={synced} failed={failed} skipped={skipped}"
        )
        cursor = rows[-1].id + 1

    return {
        "generated_at": get_current_time(),
        "dry_run": dry_run,
        "limit": limit,
        "batch_size": batch_size,
        "concurrency": concurrency,
        "id_start": id_start,
        "only_unsynced": only_unsynced,
        "verify_remote": verify_remote,
        "rebuild_cache_metadata": rebuild_cache_metadata,
        "scanned": scanned,
        "synced": synced,
        "failed": failed,
        "skipped": skipped,
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
        batch_size=args.batch_size,
        concurrency=args.concurrency,
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
