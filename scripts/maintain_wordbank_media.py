"""Run one-shot maintenance for wordbank remote media sync and cache metadata."""

from __future__ import annotations

import argparse
import asyncio
from io import BytesIO
import json
from pathlib import Path
import sys
from typing import Any

import nonebot
from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lib.utils.common import get_current_time
from src.logger import logger

wordbank_repo: Any = None
wordbank_media_service: Any = None
DEFAULT_BATCH_SIZE = 200
DEFAULT_CONCURRENCY = 8


def _is_animated_image(image: Image.Image) -> bool:
    return bool(
        getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1
    )


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
        "--migrate-animated-gif",
        action="store_true",
        help="rewrite animated media to gif before regular maintenance",
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
    migrate_animated_gif: bool,
) -> dict[str, Any]:
    _load_wordbank_components()
    report_rows: list[dict[str, Any]] = []
    remote_inventory: dict[str, Any] = {}
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

    async def inspect_gif_migration(image: Any) -> dict[str, Any] | None:
        source_bytes = await wordbank_media_service.load_canonical_storage_bytes(
            image.canonical_id
        )
        if source_bytes is None:
            return {
                "candidate": False,
                "reason": "missing_source_bytes",
            }
        try:
            with Image.open(BytesIO(source_bytes)) as source_image:
                source_format = str(getattr(source_image, "format", "") or "").upper()
                source_is_animated = _is_animated_image(source_image)
        except UnidentifiedImageError:
            return {
                "candidate": False,
                "reason": "unrecognized_image",
            }
        return {
            "candidate": source_is_animated and source_format != "GIF",
            "reason": "eligible"
            if source_is_animated and source_format != "GIF"
            else "",
            "source_format": source_format or "UNKNOWN",
            "source_is_animated": source_is_animated,
        }

    async def migrate_image_to_gif(image: Any) -> Any | None:
        from src.plugins.wordbank.services.media import (
            is_remote_uri,
            prepare_image_bytes,
        )
        from src.plugins.wordbank.services.media_models import REMOTE_SYNC_FAILED

        source_bytes = await wordbank_media_service.load_canonical_storage_bytes(
            image.canonical_id
        )
        if source_bytes is None:
            return None
        prepared = prepare_image_bytes(source_bytes)
        if prepared.stored_media.extension != ".gif":
            return None

        old_storage_path = image.storage_path
        old_remote_storage_path = image.remote_storage_path
        old_local_cache_path = image.local_cache_path
        local_storage_path = await wordbank_media_service.legacy_storage.save_image(
            prepared,
            md5_hex=image.md5,
            keep_original=False,
        )

        remote_storage_path = ""
        remote_sync_status = image.remote_sync_status
        remote_synced_at = image.remote_synced_at
        remote_etag = ""
        remote_object_size = 0
        remote_storage = getattr(wordbank_media_service, "remote_storage", None)
        if remote_storage is not None:
            try:
                stored = await remote_storage.save_prepared_image(
                    prepared,
                    md5_hex=image.md5,
                    keep_original=False,
                )
                remote_storage_path = stored.uri
                remote_sync_status = "synced"
                remote_synced_at = get_current_time()
                remote_etag = stored.etag or ""
                remote_object_size = stored.size
            except Exception as exc:
                logger.warning(f"[Wordbank] animated media gif migration failed: {exc}")
                remote_sync_status = REMOTE_SYNC_FAILED
        elif old_remote_storage_path:
            remote_sync_status = REMOTE_SYNC_FAILED

        updated = await wordbank_repo.update_image_remote_sync(
            image.id,
            remote_storage_path=remote_storage_path,
            remote_sync_status=remote_sync_status,
            remote_synced_at=remote_synced_at,
            remote_etag=remote_etag,
            remote_object_size=remote_object_size,
            storage_path=local_storage_path,
        )
        if updated is None:
            return None

        if old_local_cache_path:
            await wordbank_media_service.cache_storage.remove_cache_entry(
                old_local_cache_path
            )
        cached = await wordbank_media_service.cache_storage.store_cached_bytes(
            prepared.stored_media.data,
            md5_hex=image.md5,
            extension=prepared.stored_media.extension,
        )
        updated = (
            await wordbank_repo.update_image_cache_metadata(
                updated.id,
                local_cache_path=cached.path if cached is not None else "",
                cache_file_size=cached.size if cached is not None else 0,
                last_accessed_at=updated.last_accessed_at,
                cache_last_hit_at=0,
            )
            or updated
        )

        if (
            old_storage_path
            and not is_remote_uri(old_storage_path)
            and old_storage_path != updated.storage_path
        ):
            await wordbank_media_service.legacy_storage.delete_image(old_storage_path)
        if (
            old_storage_path
            and is_remote_uri(old_storage_path)
            and old_storage_path != updated.remote_storage_path
            and remote_storage is not None
        ):
            await remote_storage.delete_image(old_storage_path)
        if (
            old_remote_storage_path
            and old_remote_storage_path != old_storage_path
            and old_remote_storage_path != updated.remote_storage_path
            and remote_storage is not None
        ):
            await remote_storage.delete_image(old_remote_storage_path)

        return updated

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
            if migrate_animated_gif:
                migration_info = await inspect_gif_migration(working_image)
                if migration_info is not None:
                    row_report["migration_source_format"] = migration_info.get(
                        "source_format", "UNKNOWN"
                    )
                    row_report["migration_source_is_animated"] = migration_info.get(
                        "source_is_animated", False
                    )
                    row_report["migration_candidate"] = migration_info["candidate"]
                    if migration_info.get("reason"):
                        row_report["migration_reason"] = migration_info["reason"]
                if migration_info is not None and migration_info["candidate"]:
                    row_report["migration_target_extension"] = ".gif"
                    if dry_run:
                        row_report["action"] = "migrate_inspect"
                        row_report["remote_sync_status_after"] = (
                            working_image.remote_sync_status
                        )
                        row_report["remote_storage_path_after"] = (
                            working_image.remote_storage_path
                        )
                        row_report["storage_path_after"] = working_image.storage_path
                        return row_report, "inspect"
                    migrated = await migrate_image_to_gif(working_image)
                    if migrated is None:
                        row_report["action"] = "migrate_failed"
                        return row_report, "failed"
                    row_report["action"] = "migrate"
                    row_report["remote_sync_status_after"] = migrated.remote_sync_status
                    row_report["remote_storage_path_after"] = (
                        migrated.remote_storage_path
                    )
                    row_report["storage_path_after"] = migrated.storage_path
                    row_report["remote_synced_at"] = migrated.remote_synced_at
                    if migrated.remote_sync_status == "failed" and (
                        bool(working_image.remote_storage_path)
                        or getattr(wordbank_media_service, "remote_storage", None)
                        is not None
                    ):
                        return row_report, "failed"
                    return row_report, "synced"
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
        "migrate_animated_gif": migrate_animated_gif,
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
        migrate_animated_gif=args.migrate_animated_gif,
    )
    report_path = Path(args.report)
    await asyncio.to_thread(write_report, report_path, report)
    logger.success(f"wordbank media maintenance report written: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
