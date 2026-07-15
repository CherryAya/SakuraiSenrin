"""Wordbank media service and compatibility exports."""

from __future__ import annotations

import asyncio
from hashlib import md5
from pathlib import Path
from typing import Any, cast

from pybktree import BKTree

from src.lib.i18n.runtime import tr
from src.lib.utils.common import get_current_time
from src.logger import logger
from src.plugins.wordbank.database.types import WordbankImageRecord
from src.plugins.wordbank.debug import log_perf, perf_start

from .media_models import (
    DEFAULT_CACHE_MAX_BYTES,
    DEFAULT_CACHE_MAX_FILES,
    DEFAULT_CACHE_TRIM_TO_BYTES,
    DEFAULT_MEDIA_CACHE_ROOT,
    DEFAULT_MEDIA_EXTENSION,
    DEFAULT_MEDIA_ROOT,
    IMAGE_HASH_VERSION,
    IMAGE_SEARCH_DISTANCE_THRESHOLD,
    REMOTE_SYNC_FAILED,
    REMOTE_SYNC_PENDING,
    REMOTE_SYNC_SYNCED,
    CanonicalImageMatch,
    MediaError,
    PreparedImage,
    content_type_from_extension,
    default_cache_root_for,
    extension_from_storage_path,
    fingerprint_image,
    hamming_distance,
    is_remote_uri,
    prepare_image_bytes,
)
from .media_runtime import WordbankMediaRuntimeMixin
from .media_storage import (
    LocalCacheEntry,
    LocalLruCacheWordbankMediaStorage,
    LocalWordbankMediaStorage,
    MediaRepository,
    ObjectStorageWordbankMediaStorage,
    R2WordbankMediaStorage,
    WordbankMediaStorage,
)


class WordbankMediaService(WordbankMediaRuntimeMixin):
    def __init__(
        self,
        repository: MediaRepository,
        *,
        media_root: Path = DEFAULT_MEDIA_ROOT,
        storage: WordbankMediaStorage | None = None,
        remote_storage: ObjectStorageWordbankMediaStorage | None = None,
        legacy_storage: WordbankMediaStorage | None = None,
        cache_storage: LocalLruCacheWordbankMediaStorage | None = None,
        cache_enabled: bool = True,
        remote_required: bool = False,
        remote_provider: str = "local",
        remote_sync_mode: str = "deferred",
        prewarm_local_cache: bool = True,
        similarity_threshold: int = 8,
        candidate_limit: int = 128,
    ) -> None:
        if (
            storage is not None
            and remote_storage is None
            and hasattr(storage, "save_prepared_image")
        ):
            remote_storage = cast(ObjectStorageWordbankMediaStorage, storage)
            fallback = getattr(storage, "fallback", None)
            if legacy_storage is None and fallback is not None:
                legacy_storage = cast(WordbankMediaStorage, fallback)
        if legacy_storage is None:
            legacy_storage = storage or LocalWordbankMediaStorage(media_root)
        self.repository = repository
        self.media_root = media_root
        self.remote_storage = remote_storage
        self.legacy_storage = legacy_storage
        self.cache_storage = cache_storage or LocalLruCacheWordbankMediaStorage(
            default_cache_root_for(media_root),
            enabled=cache_enabled,
        )
        self.remote_required = remote_required
        self.remote_provider = remote_provider.strip().lower()
        self.remote_sync_mode = remote_sync_mode.strip().lower()
        self.prewarm_local_cache = prewarm_local_cache
        self.similarity_threshold = similarity_threshold
        self.candidate_limit = candidate_limit
        self._by_id: dict[int, WordbankImageRecord] = {}
        self._by_md5: dict[str, WordbankImageRecord] = {}
        self._by_dhash_prefix: dict[str, list[WordbankImageRecord]] = {}
        self._by_canonical_id: dict[int, WordbankImageRecord] = {}
        self._canonical_ids_by_dhash: dict[str, tuple[int, ...]] = {}
        self._canonical_hash_image: dict[int, WordbankImageRecord] = {}
        self._dhash_tree: BKTree | None = None
        self._remote_load_locks: dict[int, asyncio.Lock] = {}
        self._cache_maintenance_lock = asyncio.Lock()
        self._background_remote_sync_tasks: set[asyncio.Task[None]] = set()

    def describe_canonical_image_state(
        self,
        canonical_image_id: int,
    ) -> dict[str, Any]:
        image = self._by_canonical_id.get(canonical_image_id)
        if image is None:
            return {
                "canonical_image_id": canonical_image_id,
                "image_id": "-",
                "found": False,
                "remote_provider": self.remote_provider or "-",
                "remote_enabled": self.remote_storage is not None,
                "cache_enabled": self.cache_storage.enabled,
                "storage_path": "-",
                "remote_storage_path": "-",
                "local_cache_path": "-",
                "remote_sync_status": "-",
                "remote_synced_at": 0,
                "cache_file_size": 0,
                "file_size": 0,
            }
        return {
            "canonical_image_id": canonical_image_id,
            "image_id": image.id,
            "found": True,
            "remote_provider": self.remote_provider or "-",
            "remote_enabled": self.remote_storage is not None,
            "cache_enabled": self.cache_storage.enabled,
            "storage_path": image.storage_path or "-",
            "remote_storage_path": image.remote_storage_path or "-",
            "local_cache_path": image.local_cache_path or "-",
            "remote_sync_status": image.remote_sync_status or "-",
            "remote_synced_at": image.remote_synced_at,
            "cache_file_size": image.cache_file_size,
            "file_size": image.file_size,
        }

    async def ingest_image_bytes(
        self,
        data: bytes,
        *,
        keep_original: bool = False,
    ) -> WordbankImageRecord:
        start = perf_start()
        md5_hex = md5(data).hexdigest()
        existing = self._by_md5.get(md5_hex)
        if existing is not None:
            log_perf(
                "media.ingest_image_bytes.cache_hit",
                start=start,
                md5=md5_hex,
                canonical_id=existing.canonical_id,
                image_id=existing.id,
                source="memory",
            )
            return existing
        existing = await self.repository.get_image_by_md5(md5_hex)
        if existing is not None:
            self._cache_image(existing)
            log_perf(
                "media.ingest_image_bytes.cache_hit",
                start=start,
                md5=md5_hex,
                canonical_id=existing.canonical_id,
                image_id=existing.id,
                source="repo",
            )
            return existing

        prepare_start = perf_start()
        prepared = prepare_image_bytes(data)
        prepare_ms = perf_start() - prepare_start
        fingerprint = prepared.fingerprint
        canonical_id: int | None = None
        candidate_lookup_start = perf_start()
        candidates = await self.repository.get_image_candidates(
            fingerprint.dhash[:4],
            limit=self.candidate_limit,
        )
        candidate_lookup_ms = perf_start() - candidate_lookup_start
        for candidate in candidates:
            if (
                hamming_distance(fingerprint.dhash, candidate.dhash)
                <= self.similarity_threshold
            ):
                canonical_id = candidate.canonical_id
                break

        now = get_current_time()
        remote_storage_path = ""
        remote_sync_status = REMOTE_SYNC_PENDING
        remote_synced_at = 0
        remote_etag = ""
        remote_object_size = 0
        storage_path = ""
        remote_expected = self._remote_expected()
        remote_sync_deferred = (
            self.remote_storage is not None
            and not self.remote_required
            and self.remote_sync_mode != "sync"
        )

        if self.remote_storage is not None and not remote_sync_deferred:
            try:
                remote_save_start = perf_start()
                stored = await self.remote_storage.save_prepared_image(
                    prepared,
                    md5_hex=fingerprint.md5,
                    keep_original=keep_original,
                )
                remote_storage_path = stored.uri
                remote_sync_status = REMOTE_SYNC_SYNCED
                remote_synced_at = now
                remote_etag = stored.etag or ""
                remote_object_size = stored.size
                storage_path = stored.uri
                remote_save_ms = (perf_start() - remote_save_start) * 1000
                log_perf(
                    "media.ingest_image_bytes.remote_saved",
                    md5=fingerprint.md5,
                    canonical_id=canonical_id or "new",
                    remote_save_ms=f"{remote_save_ms:.2f}",
                    remote_size=stored.size,
                )
            except Exception as exc:
                if self.remote_required:
                    raise MediaError(
                        tr("zh-CN", "wordbank.error.image_storage_missing"),
                        key="wordbank.error.image_storage_missing",
                    ) from exc
                logger.warning(f"[Wordbank] remote media save fallback to local: {exc}")
                remote_sync_status = REMOTE_SYNC_FAILED
        elif remote_sync_deferred:
            log_perf(
                "media.ingest_image_bytes.remote_deferred",
                md5=fingerprint.md5,
                canonical_id=canonical_id or "new",
                remote_provider=self.remote_provider or "unknown",
            )
        elif remote_expected:
            if self.remote_required:
                raise MediaError(
                    tr("zh-CN", "wordbank.error.image_storage_missing"),
                    key="wordbank.error.image_storage_missing",
                )
            remote_sync_status = REMOTE_SYNC_FAILED

        if not storage_path:
            legacy_save_start = perf_start()
            storage_path = await self.legacy_storage.save_image(
                prepared,
                md5_hex=fingerprint.md5,
                keep_original=keep_original,
            )
            log_perf(
                "media.ingest_image_bytes.local_saved",
                md5=fingerprint.md5,
                legacy_save_ms=f"{(perf_start() - legacy_save_start) * 1000:.2f}",
            )

        create_start = perf_start()
        image = await self.repository.create_image(
            {
                "canonical_image_id": canonical_id,
                "md5": fingerprint.md5,
                "dhash": fingerprint.dhash,
                "phash": fingerprint.phash,
                "width": fingerprint.width,
                "height": fingerprint.height,
                "file_size": fingerprint.file_size,
                "hash_version": fingerprint.hash_version,
                "storage_path": storage_path,
                "remote_storage_path": remote_storage_path,
                "local_cache_path": "",
                "cache_file_size": 0,
                "last_accessed_at": 0,
                "cache_last_hit_at": 0,
                "remote_sync_status": remote_sync_status,
                "remote_synced_at": remote_synced_at,
                "remote_etag": remote_etag,
                "remote_object_size": remote_object_size,
                "created_at": now,
                "updated_at": now,
            }
        )
        create_ms = (perf_start() - create_start) * 1000
        self._cache_image(image)
        if self.prewarm_local_cache and self.cache_storage.enabled:
            image = await self._store_cache_bytes(
                image, prepared.stored_media.data, mark_as_hit=False
            )
        if remote_sync_deferred:
            self._schedule_background_remote_sync(
                image, prepared=prepared, keep_original=keep_original
            )
        log_perf(
            "media.ingest_image_bytes.done",
            start=start,
            md5=fingerprint.md5,
            canonical_id=image.canonical_id,
            image_id=image.id,
            keep_original=keep_original,
            candidate_count=len(candidates),
            prepare_ms=f"{prepare_ms * 1000:.2f}",
            candidate_lookup_ms=f"{candidate_lookup_ms * 1000:.2f}",
            create_ms=f"{create_ms:.2f}",
            remote_sync_deferred=remote_sync_deferred,
            storage=(
                "remote"
                if remote_storage_path
                else "local_deferred_remote"
                if remote_sync_deferred
                else "local"
            ),
        )
        return image

    def _schedule_background_remote_sync(
        self,
        image: WordbankImageRecord,
        *,
        prepared: PreparedImage,
        keep_original: bool,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            self._run_background_remote_sync(
                image.id,
                prepared=prepared,
                keep_original=keep_original,
            )
        )
        self._background_remote_sync_tasks.add(task)
        task.add_done_callback(self._background_remote_sync_tasks.discard)

    async def _run_background_remote_sync(
        self,
        image_id: int,
        *,
        prepared: PreparedImage,
        keep_original: bool,
    ) -> None:
        if self.remote_storage is None:
            return
        image = self._by_id.get(image_id)
        if image is None:
            return
        start = perf_start()
        log_perf(
            "media.remote_sync.background.begin",
            image_id=image.id,
            canonical_image_id=image.canonical_id,
            md5=image.md5,
            keep_original=keep_original,
        )
        try:
            stored = await self.remote_storage.save_prepared_image(
                prepared,
                md5_hex=image.md5,
                keep_original=keep_original,
            )
        except Exception as exc:
            logger.warning(f"[Wordbank] background remote media sync failed: {exc}")
            updated = await self._mark_remote_sync_failed(image)
            log_perf(
                "media.remote_sync.background.failed",
                start=start,
                updated=updated is not None,
                image_id=image.id,
                canonical_image_id=image.canonical_id,
                md5=image.md5,
            )
            return

        storage_path = (
            image.storage_path
            if image.storage_path and not is_remote_uri(image.storage_path)
            else stored.uri
        )
        updated = await self.repository.update_image_remote_sync(
            image.id,
            remote_storage_path=stored.uri,
            remote_sync_status=REMOTE_SYNC_SYNCED,
            remote_synced_at=get_current_time(),
            remote_etag=stored.etag or "",
            remote_object_size=stored.size,
            storage_path=storage_path,
        )
        if updated is not None:
            self._cache_image(updated)
        log_perf(
            "media.remote_sync.background.done",
            start=start,
            updated=updated is not None,
            image_id=image.id,
            canonical_image_id=image.canonical_id,
            md5=image.md5,
            remote_size=stored.size,
            remote_storage_path=stored.uri,
        )


__all__ = [
    "DEFAULT_CACHE_MAX_BYTES",
    "DEFAULT_CACHE_MAX_FILES",
    "DEFAULT_CACHE_TRIM_TO_BYTES",
    "DEFAULT_MEDIA_CACHE_ROOT",
    "DEFAULT_MEDIA_EXTENSION",
    "DEFAULT_MEDIA_ROOT",
    "IMAGE_HASH_VERSION",
    "IMAGE_SEARCH_DISTANCE_THRESHOLD",
    "CanonicalImageMatch",
    "LocalCacheEntry",
    "LocalLruCacheWordbankMediaStorage",
    "LocalWordbankMediaStorage",
    "MediaError",
    "MediaRepository",
    "ObjectStorageWordbankMediaStorage",
    "PreparedImage",
    "R2WordbankMediaStorage",
    "WordbankMediaService",
    "WordbankMediaStorage",
    "content_type_from_extension",
    "default_cache_root_for",
    "extension_from_storage_path",
    "fingerprint_image",
    "hamming_distance",
    "is_remote_uri",
    "prepare_image_bytes",
]
