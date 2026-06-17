"""Runtime cache/load/sync mixin for the wordbank media service."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pybktree import BKTree

from src.lib.object_storage.types import StorageObject
from src.lib.utils.common import get_current_time
from src.logger import logger
from src.plugins.wordbank.database.types import WordbankImageRecord
from src.plugins.wordbank.debug import elapsed_ms, log_perf, perf_start

from .media_models import (
    CanonicalImageMatch,
    IMAGE_HASH_VERSION,
    IMAGE_SEARCH_DISTANCE_THRESHOLD,
    REMOTE_SYNC_FAILED,
    REMOTE_SYNC_SYNCED,
    content_type_from_extension,
    extension_from_storage_path,
    fingerprint_image,
    hamming_distance,
    is_remote_uri,
    iter_md5_candidates,
)
from .media_storage import (
    LocalLruCacheWordbankMediaStorage,
    MediaRepository,
    ObjectStorageWordbankMediaStorage,
    WordbankMediaStorage,
)


def image_log_fields(
    image: WordbankImageRecord | None,
    *,
    canonical_image_id: int | None = None,
) -> dict[str, Any]:
    if image is None:
        return {
            "image_id": "-",
            "canonical_image_id": canonical_image_id or "-",
            "local_cache_path": "-",
            "remote_storage_path": "-",
            "legacy_storage_path": "-",
        }
    return {
        "image_id": image.id,
        "canonical_image_id": canonical_image_id or image.canonical_id,
        "local_cache_path": image.local_cache_path or "-",
        "remote_storage_path": image.remote_storage_path or "-",
        "legacy_storage_path": image.storage_path or "-",
    }


class WordbankMediaRuntimeMixin:
    async def rebuild_cache(self: _MediaRuntimeHost) -> None:
        start = perf_start()
        images = await self.repository.list_images()
        self.load_cache(images)
        log_perf(
            "media.rebuild_cache.done",
            start=start,
            images=len(images),
            canonical_ids=len(self._by_canonical_id),
            dhash_nodes=len(self._canonical_ids_by_dhash),
        )

    def load_cache(self: _MediaRuntimeHost, images: Sequence[WordbankImageRecord]) -> None:
        by_id: dict[int, WordbankImageRecord] = {}
        by_prefix: dict[str, list[WordbankImageRecord]] = defaultdict(list)
        by_md5: dict[str, WordbankImageRecord] = {}
        by_canonical_id: dict[int, WordbankImageRecord] = {}
        canonical_hash_image: dict[int, WordbankImageRecord] = {}
        canonical_ids_by_dhash: dict[str, set[int]] = defaultdict(set)
        for image in images:
            by_id[image.id] = image
            by_md5[image.md5] = image
            by_prefix[image.dhash[:4]].append(image)
            by_canonical_id.setdefault(image.canonical_id, image)
            current = canonical_hash_image.get(image.canonical_id)
            if current is None or image.hash_version > current.hash_version:
                canonical_hash_image[image.canonical_id] = image
        for canonical_id, image in canonical_hash_image.items():
            canonical_ids_by_dhash[image.dhash].add(canonical_id)
        self._by_id = by_id
        self._by_md5 = by_md5
        self._by_dhash_prefix = dict(by_prefix)
        self._by_canonical_id = by_canonical_id
        self._canonical_hash_image = canonical_hash_image
        self._canonical_ids_by_dhash = {
            dhash: tuple(sorted(canonical_ids))
            for dhash, canonical_ids in canonical_ids_by_dhash.items()
        }
        self._dhash_tree = (
            BKTree(hamming_distance, self._canonical_ids_by_dhash.keys())
            if self._canonical_ids_by_dhash
            else None
        )

    def _cache_image(self: _MediaRuntimeHost, image: WordbankImageRecord) -> None:
        existing = self._by_id.get(image.id)
        self._by_id[image.id] = image
        self._by_md5[image.md5] = image
        prefix = image.dhash[:4]
        bucket = self._by_dhash_prefix.setdefault(prefix, [])
        for index, current in enumerate(bucket):
            if current.id == image.id:
                bucket[index] = image
                break
        else:
            bucket.append(image)
        current_canonical = self._by_canonical_id.get(image.canonical_id)
        if current_canonical is None or current_canonical.id == image.id:
            self._by_canonical_id[image.canonical_id] = image
        current_hash = self._canonical_hash_image.get(image.canonical_id)
        if (
            current_hash is None
            or current_hash.id == image.id
            or image.hash_version > current_hash.hash_version
        ):
            self._canonical_hash_image[image.canonical_id] = image
            known_dhash = image.dhash in self._canonical_ids_by_dhash
            canonical_ids = set(self._canonical_ids_by_dhash.get(image.dhash, ()))
            canonical_ids.add(image.canonical_id)
            self._canonical_ids_by_dhash[image.dhash] = tuple(sorted(canonical_ids))
            if self._dhash_tree is None:
                self._dhash_tree = BKTree(hamming_distance, [image.dhash])
            elif not known_dhash:
                self._dhash_tree.add(image.dhash)
        if (
            existing is not None
            and existing.id == self._by_canonical_id.get(existing.canonical_id, existing).id
            and existing.canonical_id != image.canonical_id
        ):
            self._by_canonical_id.pop(existing.canonical_id, None)

    def resolve_canonical_id(
        self: _MediaRuntimeHost,
        data: bytes,
        *,
        name_hints: Sequence[str] = (),
    ) -> int | None:
        hinted = self.resolve_canonical_id_from_hints(name_hints)
        if hinted is not None:
            return hinted
        existing = self._by_md5.get(self._md5_hex(data))
        if existing is not None:
            return existing.canonical_id
        fingerprint = fingerprint_image(data)
        for candidate in self._by_dhash_prefix.get(fingerprint.dhash[:4], [])[: self.candidate_limit]:
            if hamming_distance(fingerprint.dhash, candidate.dhash) <= self.similarity_threshold:
                return candidate.canonical_id
        return None

    def search_similar_images(
        self: _MediaRuntimeHost,
        data: bytes,
        *,
        limit: int = 32,
        distance_threshold: int = IMAGE_SEARCH_DISTANCE_THRESHOLD,
    ) -> list[CanonicalImageMatch]:
        start = perf_start()
        fingerprint = fingerprint_image(data)
        existing = self._by_md5.get(fingerprint.md5)
        if existing is not None:
            log_perf(
                "media.search_similar_images.exact_hit",
                start=start,
                canonical_id=existing.canonical_id,
                limit=limit,
                distance_threshold=distance_threshold,
            )
            return [
                CanonicalImageMatch(
                    canonical_id=existing.canonical_id,
                    score=1.0,
                    dhash_distance=0,
                    phash_distance=0,
                )
            ]
        if self._dhash_tree is None:
            log_perf(
                "media.search_similar_images.empty_cache",
                start=start,
                limit=limit,
                distance_threshold=distance_threshold,
            )
            return []
        matches: list[CanonicalImageMatch] = []
        for dhash_distance, matched_dhash in self._dhash_tree.find(fingerprint.dhash, distance_threshold):
            for canonical_id in self._canonical_ids_by_dhash.get(matched_dhash, ()):
                image = self._canonical_hash_image.get(canonical_id)
                if image is None:
                    continue
                score = max(0.0, 1.0 - (dhash_distance / max(distance_threshold, 1)))
                phash_distance: int | None = None
                if image.hash_version >= IMAGE_HASH_VERSION:
                    phash_distance = hamming_distance(fingerprint.phash, image.phash)
                    score = min(
                        1.0,
                        score
                        + max(0.0, 1.0 - (phash_distance / max(distance_threshold * 2, 1))) * 0.25,
                    )
                matches.append(
                    CanonicalImageMatch(
                        canonical_id=canonical_id,
                        score=score,
                        dhash_distance=dhash_distance,
                        phash_distance=phash_distance,
                    )
                )
        matches.sort(
            key=lambda item: (item.score, -(item.phash_distance or 0), -item.dhash_distance, -item.canonical_id),
            reverse=True,
        )
        limited = matches[:limit]
        log_perf(
            "media.search_similar_images.done",
            start=start,
            limit=limit,
            distance_threshold=distance_threshold,
            candidates=len(matches),
            returned=len(limited),
        )
        return limited

    def resolve_canonical_id_from_hints(self: _MediaRuntimeHost, hints: Sequence[str]) -> int | None:
        for md5_candidate in iter_md5_candidates(hints):
            existing = self._by_md5.get(md5_candidate)
            if existing is not None:
                return existing.canonical_id
        return None

    async def load_storage_bytes(self: _MediaRuntimeHost, image: WordbankImageRecord) -> bytes | None:
        return await self.load_canonical_storage_bytes(image.canonical_id)

    def _get_remote_load_lock(self: _MediaRuntimeHost, canonical_image_id: int) -> asyncio.Lock:
        if canonical_image_id not in self._remote_load_locks:
            self._remote_load_locks[canonical_image_id] = asyncio.Lock()
        return self._remote_load_locks[canonical_image_id]

    async def load_canonical_storage_bytes(self: _MediaRuntimeHost, canonical_image_id: int) -> bytes | None:
        start = perf_start()
        image = self._by_canonical_id.get(canonical_image_id)
        if image is None:
            log_perf(
                "media.load_canonical_storage_bytes.miss",
                start=start,
                canonical_image_id=canonical_image_id,
                reason="unknown_canonical_id",
            )
            return None
        base_fields = image_log_fields(image, canonical_image_id=canonical_image_id)
        log_perf(
            "media.load_canonical_storage_bytes.begin",
            canonical_image_id=canonical_image_id,
            has_local_cache_path=bool(image.local_cache_path),
            has_remote_storage_path=bool(image.remote_storage_path),
            has_legacy_storage_path=bool(image.storage_path),
            cache_enabled=self.cache_storage.enabled,
            remote_enabled=self.remote_storage is not None,
        )
        local_probe_start = perf_start()
        cached = await self._load_from_local_cache(image)
        log_perf(
            "media.load_canonical_storage_bytes.local_cache_probe",
            start=local_probe_start,
            phase="initial",
            hit=cached is not None,
            cache_enabled=self.cache_storage.enabled,
            **base_fields,
        )
        if cached is not None:
            log_perf(
                "media.load_canonical_storage_bytes.end",
                start=start,
                canonical_image_id=canonical_image_id,
                source="local_cache",
                bytes=len(cached),
            )
            return cached
        lock = self._get_remote_load_lock(canonical_image_id)
        wait_start = perf_start()
        await lock.acquire()
        log_perf(
            "media.load_canonical_storage_bytes.remote_lock_wait",
            canonical_image_id=canonical_image_id,
            wait_ms=f"{elapsed_ms(wait_start):.2f}",
        )
        try:
            refreshed = self._by_canonical_id.get(canonical_image_id, image)
            refreshed_fields = image_log_fields(refreshed, canonical_image_id=canonical_image_id)
            local_probe_start = perf_start()
            cached = await self._load_from_local_cache(refreshed)
            log_perf(
                "media.load_canonical_storage_bytes.local_cache_probe",
                start=local_probe_start,
                phase="after_lock",
                hit=cached is not None,
                cache_enabled=self.cache_storage.enabled,
                **refreshed_fields,
            )
            if cached is not None:
                log_perf(
                    "media.load_canonical_storage_bytes.end",
                    start=start,
                    canonical_image_id=canonical_image_id,
                    source="local_cache_after_lock",
                    bytes=len(cached),
                )
                return cached
            legacy_bytes = await self._load_from_legacy_storage(refreshed)
            if legacy_bytes is not None:
                log_perf(
                    "media.load_canonical_storage_bytes.end",
                    start=start,
                    canonical_image_id=canonical_image_id,
                    source="legacy_storage",
                    bytes=len(legacy_bytes),
                )
                return legacy_bytes
            remote_bytes = await self._load_from_remote_storage(refreshed)
            if remote_bytes is not None:
                log_perf(
                    "media.load_canonical_storage_bytes.end",
                    start=start,
                    canonical_image_id=canonical_image_id,
                    source="remote_storage",
                    bytes=len(remote_bytes),
                )
                return remote_bytes
            log_perf(
                "media.load_canonical_storage_bytes.end",
                start=start,
                canonical_image_id=canonical_image_id,
                source="miss",
                bytes=0,
            )
            return None
        finally:
            lock.release()

    async def _load_from_local_cache(self: _MediaRuntimeHost, image: WordbankImageRecord) -> bytes | None:
        fields = image_log_fields(image)
        if not self.cache_storage.enabled:
            log_perf("media.local_cache.read", hit=False, cache_enabled=False, reason="cache_disabled", **fields)
            return None
        if not image.local_cache_path:
            log_perf("media.local_cache.read", hit=False, cache_enabled=True, reason="empty_cache_path", **fields)
            return None
        read_start = perf_start()
        cached = await self.cache_storage.load_cached_bytes(image.local_cache_path)
        log_perf(
            "media.local_cache.read",
            start=read_start,
            hit=cached is not None,
            cache_enabled=True,
            bytes=len(cached) if cached is not None else 0,
            **fields,
        )
        if cached is None:
            log_perf("media.local_cache.path_missing", cache_enabled=True, reason="cache_file_missing", **fields)
            metadata_start = perf_start()
            updated = await self.repository.update_image_cache_metadata(image.id, local_cache_path="", cache_file_size=0)
            log_perf("media.local_cache.stale_metadata_cleared", start=metadata_start, updated=updated is not None, **fields)
            if updated is not None:
                self._cache_image(updated)
            return None
        touch_start = perf_start()
        await self.cache_storage.touch_cache_entry(image.local_cache_path)
        log_perf("media.local_cache.touch", start=touch_start, **fields)
        metadata_start = perf_start()
        now = get_current_time()
        updated = await self.repository.update_image_cache_metadata(
            image.id,
            local_cache_path=image.local_cache_path,
            cache_file_size=max(image.cache_file_size, len(cached)),
            last_accessed_at=now,
            cache_last_hit_at=now,
        )
        log_perf(
            "media.local_cache.metadata_update",
            start=metadata_start,
            updated=updated is not None,
            cache_file_size=max(image.cache_file_size, len(cached)),
            **fields,
        )
        if updated is not None:
            self._cache_image(updated)
        return cached

    async def _load_from_remote_storage(self: _MediaRuntimeHost, image: WordbankImageRecord) -> bytes | None:
        if self.remote_storage is None or not image.remote_storage_path:
            return None
        fetch_start = perf_start()
        remote_bytes = await self.remote_storage.load_bytes(image.remote_storage_path)
        log_perf(
            "media.load_canonical_storage_bytes.remote_fetch",
            start=fetch_start,
            provider=(
                self.remote_storage.object_storage.provider
                if isinstance(self.remote_storage, ObjectStorageWordbankMediaStorage)
                else "-"
            ),
            uri=image.remote_storage_path,
            bytes=len(remote_bytes) if remote_bytes is not None else 0,
            hit=remote_bytes is not None,
            **image_log_fields(image),
        )
        if remote_bytes is None:
            await self._mark_remote_sync_failed(image)
            return None
        await self._touch_last_access(image.id)
        updated_image = self._by_id.get(image.id, image)
        if self.cache_storage.enabled:
            cache_store_start = perf_start()
            updated_image = await self._store_cache_bytes(updated_image, remote_bytes, mark_as_hit=False)
            log_perf(
                "media.load_canonical_storage_bytes.cache_store_after_remote",
                start=cache_store_start,
                success=updated_image.local_cache_path != "",
                bytes=len(remote_bytes),
                **image_log_fields(updated_image),
            )
        else:
            log_perf(
                "media.load_canonical_storage_bytes.cache_store_after_remote",
                success=False,
                bytes=len(remote_bytes),
                cache_enabled=False,
                reason="cache_disabled",
                **image_log_fields(updated_image),
            )
        return remote_bytes

    async def _load_from_legacy_storage(self: _MediaRuntimeHost, image: WordbankImageRecord) -> bytes | None:
        if not image.storage_path or is_remote_uri(image.storage_path):
            return None
        legacy_start = perf_start()
        legacy_bytes = await self.legacy_storage.load_bytes(image.storage_path)
        log_perf(
            "media.load_canonical_storage_bytes.legacy_fetch",
            start=legacy_start,
            bytes=len(legacy_bytes) if legacy_bytes is not None else 0,
            hit=legacy_bytes is not None,
            **image_log_fields(image),
        )
        if legacy_bytes is None:
            return None
        updated = await self._touch_last_access(image.id)
        return updated and legacy_bytes or legacy_bytes

    async def _touch_last_access(self: _MediaRuntimeHost, image_id: int) -> WordbankImageRecord | None:
        current = self._by_id.get(image_id)
        if current is None:
            return None
        start = perf_start()
        updated = await self.repository.update_image_cache_metadata(
            image_id,
            local_cache_path=current.local_cache_path,
            cache_file_size=current.cache_file_size,
            last_accessed_at=get_current_time(),
            cache_last_hit_at=current.cache_last_hit_at,
        )
        log_perf("media.image_last_access_update", start=start, updated=updated is not None, **image_log_fields(current))
        if updated is not None:
            self._cache_image(updated)
        return updated

    async def _store_cache_bytes(
        self: _MediaRuntimeHost,
        image: WordbankImageRecord,
        data: bytes,
        *,
        mark_as_hit: bool,
    ) -> WordbankImageRecord:
        if not self.cache_storage.enabled:
            log_perf(
                "media.local_cache.store",
                success=False,
                cache_enabled=False,
                reason="cache_disabled",
                bytes=len(data),
                **image_log_fields(image),
            )
            return image
        store_start = perf_start()
        cached = await self.cache_storage.store_cached_bytes(
            data,
            md5_hex=image.md5,
            extension=extension_from_storage_path(image.remote_storage_path or image.storage_path),
        )
        log_perf(
            "media.local_cache.store",
            start=store_start,
            success=cached is not None,
            cache_enabled=True,
            bytes=len(data),
            cache_path=cached.path if cached is not None else "-",
            **image_log_fields(image),
        )
        if cached is None:
            return image
        now = get_current_time()
        metadata_start = perf_start()
        updated = await self.repository.update_image_cache_metadata(
            image.id,
            local_cache_path=cached.path,
            cache_file_size=cached.size,
            last_accessed_at=now,
            cache_last_hit_at=now if mark_as_hit else image.cache_last_hit_at,
        )
        log_perf(
            "media.local_cache.store_metadata_update",
            start=metadata_start,
            updated=updated is not None,
            cache_path=cached.path,
            cache_file_size=cached.size,
            **image_log_fields(image),
        )
        if updated is None:
            return image
        self._cache_image(updated)
        await self.evict_local_cache_if_needed()
        return updated

    async def evict_local_cache_if_needed(self: _MediaRuntimeHost) -> int:
        if not self.cache_storage.enabled:
            log_perf("media.local_cache.evict_if_needed", removed=0, cache_enabled=False)
            return 0
        start = perf_start()
        async with self._cache_maintenance_lock:
            cached_images = await self.repository.list_cached_images()
            evicted = await self.cache_storage.evict_if_needed(cached_images)
            removed = 0
            for image in evicted:
                if image.local_cache_path:
                    await self.cache_storage.remove_cache_entry(image.local_cache_path)
                updated = await self.repository.update_image_cache_metadata(
                    image.id,
                    local_cache_path="",
                    cache_file_size=0,
                    last_accessed_at=image.last_accessed_at,
                    cache_last_hit_at=0,
                )
                if updated is not None:
                    self._cache_image(updated)
                removed += 1
        log_perf(
            "media.local_cache.evict_if_needed",
            start=start,
            removed=removed,
            candidate_count=len(cached_images),
            evicted_count=len(evicted),
            cache_enabled=True,
        )
        return removed

    async def reconcile_local_cache(self: _MediaRuntimeHost) -> dict[str, int]:
        if not self.cache_storage.enabled:
            return {"cleared_metadata": 0, "removed_orphans": 0, "evicted": 0}
        async with self._cache_maintenance_lock:
            cached_images = await self.repository.list_cached_images()
            cleared_metadata = 0
            known_paths: set[str] = set()
            for image in cached_images:
                if not image.local_cache_path:
                    continue
                path_exists = await asyncio.to_thread(Path(image.local_cache_path).is_file)
                if path_exists:
                    known_paths.add(image.local_cache_path)
                    continue
                updated = await self.repository.update_image_cache_metadata(
                    image.id,
                    local_cache_path="",
                    cache_file_size=0,
                    last_accessed_at=image.last_accessed_at,
                    cache_last_hit_at=0,
                )
                if updated is not None:
                    self._cache_image(updated)
                cleared_metadata += 1
            removed_orphans = len(await self.cache_storage.prune_orphans(known_paths))
        evicted = await self.evict_local_cache_if_needed()
        return {"cleared_metadata": cleared_metadata, "removed_orphans": removed_orphans, "evicted": evicted}

    async def backfill_local_cache_metadata(
        self: _MediaRuntimeHost,
        *,
        dry_run: bool = False,
        limit: int = 0,
        id_start: int = 0,
        only_missing: bool = True,
    ) -> dict[str, Any]:
        images = await self.repository.list_images()
        scanned = updated = unchanged = skipped_existing = missing_files = failed = 0
        rows: list[dict[str, Any]] = []
        remaining = max(limit, 0)
        for image in sorted(images, key=lambda item: item.id):
            if image.id < id_start:
                continue
            if remaining > 0 and scanned >= remaining:
                break
            scanned += 1
            extension = extension_from_storage_path(image.remote_storage_path or image.storage_path)
            expected_path = self.cache_storage.cache_root / f"{image.md5}{extension}"
            row: dict[str, Any] = {
                "id": image.id,
                "canonical_image_id": image.canonical_id,
                "md5": image.md5,
                "expected_cache_path": str(expected_path),
                "local_cache_path_before": image.local_cache_path,
                "cache_file_size_before": image.cache_file_size,
            }
            if only_missing and image.local_cache_path:
                skipped_existing += 1
                row["action"] = "skip_existing"
                rows.append(row)
                continue
            exists = await asyncio.to_thread(expected_path.is_file)
            if not exists:
                missing_files += 1
                row["action"] = "missing_file"
                rows.append(row)
                continue
            file_size = await asyncio.to_thread(lambda: expected_path.stat().st_size)
            normalized_path = str(expected_path)
            row["cache_file_size_detected"] = file_size
            if image.local_cache_path == normalized_path and image.cache_file_size == file_size:
                unchanged += 1
                row["action"] = "unchanged"
                rows.append(row)
                continue
            if dry_run:
                updated += 1
                row["action"] = "would_update"
                row["local_cache_path_after"] = normalized_path
                row["cache_file_size_after"] = file_size
                rows.append(row)
                continue
            refreshed = await self.repository.update_image_cache_metadata(
                image.id,
                local_cache_path=normalized_path,
                cache_file_size=file_size,
            )
            if refreshed is None:
                failed += 1
                row["action"] = "update_failed"
                rows.append(row)
                continue
            self._cache_image(refreshed)
            updated += 1
            row["action"] = "updated"
            row["local_cache_path_after"] = refreshed.local_cache_path
            row["cache_file_size_after"] = refreshed.cache_file_size
            rows.append(row)
        report = {
            "dry_run": dry_run,
            "limit": limit,
            "id_start": id_start,
            "only_missing": only_missing,
            "scanned": scanned,
            "updated": updated,
            "unchanged": unchanged,
            "skipped_existing": skipped_existing,
            "missing_files": missing_files,
            "failed": failed,
            "rows": rows,
        }
        log_perf(
            "media.backfill_local_cache_metadata.done",
            dry_run=dry_run,
            scanned=scanned,
            updated=updated,
            unchanged=unchanged,
            skipped_existing=skipped_existing,
            missing_files=missing_files,
            failed=failed,
        )
        return report

    async def rebuild_cache_metadata(self: _MediaRuntimeHost, image: WordbankImageRecord) -> WordbankImageRecord:
        if not image.local_cache_path:
            return image
        path = Path(image.local_cache_path)
        path_exists = await asyncio.to_thread(path.is_file)
        if not path_exists:
            updated = await self.repository.update_image_cache_metadata(
                image.id,
                local_cache_path="",
                cache_file_size=0,
                last_accessed_at=image.last_accessed_at,
                cache_last_hit_at=0,
            )
            if updated is not None:
                self._cache_image(updated)
                return updated
            return image
        cache_file_size = await asyncio.to_thread(lambda: path.stat().st_size)
        updated = await self.repository.update_image_cache_metadata(
            image.id,
            local_cache_path=image.local_cache_path,
            cache_file_size=cache_file_size,
            last_accessed_at=image.last_accessed_at,
            cache_last_hit_at=image.cache_last_hit_at,
        )
        if updated is not None:
            self._cache_image(updated)
            return updated
        return image

    async def sync_image_to_remote(
        self: _MediaRuntimeHost,
        image: WordbankImageRecord,
        *,
        verify_remote: bool = False,
    ) -> WordbankImageRecord | None:
        if self.remote_storage is None:
            return None
        source_bytes = await self._load_source_bytes_for_remote_sync(image)
        if source_bytes is None:
            updated = await self._mark_remote_sync_failed(image)
            if updated is not None:
                self._cache_image(updated)
            return updated
        extension = extension_from_storage_path(image.remote_storage_path or image.storage_path)
        content_type = content_type_from_extension(extension)
        try:
            stored = await self.remote_storage.save_bytes(
                source_bytes,
                md5_hex=image.md5,
                extension=extension,
                content_type=content_type,
            )
            if verify_remote and not await self.remote_storage.exists(stored.uri):
                raise RuntimeError("remote object verification failed")
        except Exception as exc:
            logger.warning(f"[Wordbank] remote media sync failed: {exc}")
            updated = await self._mark_remote_sync_failed(image)
            if updated is not None:
                self._cache_image(updated)
            return updated
        storage_path = image.storage_path if image.storage_path and not is_remote_uri(image.storage_path) else stored.uri
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
        return updated

    async def list_remote_objects_by_key(self: _MediaRuntimeHost) -> dict[str, StorageObject]:
        if self.remote_storage is None:
            return {}
        objects = await self.remote_storage.list_objects()
        return {item.key: item for item in objects}

    def build_expected_remote_key(self: _MediaRuntimeHost, image: WordbankImageRecord) -> str | None:
        if self.remote_storage is None:
            return None
        extension = extension_from_storage_path(image.remote_storage_path or image.storage_path)
        return self.remote_storage.build_key(image.md5, extension)

    async def mark_image_remote_synced(
        self: _MediaRuntimeHost,
        image: WordbankImageRecord,
        remote_object: StorageObject,
        *,
        synced_at: int | None = None,
    ) -> WordbankImageRecord | None:
        storage_path = image.storage_path if image.storage_path and not is_remote_uri(image.storage_path) else remote_object.uri
        updated = await self.repository.update_image_remote_sync(
            image.id,
            remote_storage_path=remote_object.uri,
            remote_sync_status=REMOTE_SYNC_SYNCED,
            remote_synced_at=synced_at or get_current_time(),
            remote_etag=remote_object.etag or "",
            remote_object_size=remote_object.size,
            storage_path=storage_path,
        )
        if updated is not None:
            self._cache_image(updated)
        return updated

    async def reconcile_image_with_remote_inventory(
        self: _MediaRuntimeHost,
        image: WordbankImageRecord,
        remote_objects_by_key: Mapping[str, StorageObject],
        *,
        synced_at: int | None = None,
    ) -> WordbankImageRecord | None:
        expected_key = self.build_expected_remote_key(image)
        if not expected_key:
            return None
        remote_object = remote_objects_by_key.get(expected_key)
        if remote_object is None:
            return None
        return await self.mark_image_remote_synced(image, remote_object, synced_at=synced_at)

    async def retry_remote_sync(
        self: _MediaRuntimeHost,
        *,
        limit: int = 200,
        id_start: int = 0,
        only_unsynced: bool = True,
        verify_remote: bool = False,
        rebuild_cache_metadata: bool = False,
    ) -> dict[str, int]:
        images = await self.repository.list_images_for_remote_sync(
            limit=limit,
            id_start=id_start,
            only_unsynced=only_unsynced,
        )
        synced = failed = skipped = 0
        for image in images:
            working_image = await self.rebuild_cache_metadata(image) if rebuild_cache_metadata else image
            updated = await self.sync_image_to_remote(working_image, verify_remote=verify_remote)
            if updated is None:
                skipped += 1
                continue
            if updated.remote_sync_status == REMOTE_SYNC_SYNCED:
                synced += 1
            else:
                failed += 1
        return {"scanned": len(images), "synced": synced, "failed": failed, "skipped": skipped}

    async def run_scheduled_maintenance(self: _MediaRuntimeHost, *, batch_size: int = 200) -> dict[str, int]:
        cache_report = await self.reconcile_local_cache()
        sync_report = await self.retry_remote_sync(limit=batch_size)
        return {**cache_report, **sync_report}

    async def _load_source_bytes_for_remote_sync(
        self: _MediaRuntimeHost,
        image: WordbankImageRecord,
    ) -> bytes | None:
        if image.storage_path and not is_remote_uri(image.storage_path):
            legacy_bytes = await self.legacy_storage.load_bytes(image.storage_path)
            if legacy_bytes is not None:
                return legacy_bytes
        if image.local_cache_path:
            cached = await self.cache_storage.load_cached_bytes(image.local_cache_path)
            if cached is not None:
                return cached
        if self.remote_storage is not None and image.remote_storage_path:
            return await self.remote_storage.load_bytes(image.remote_storage_path)
        return None

    def _remote_expected(self: _MediaRuntimeHost) -> bool:
        return self.remote_provider in {"github", "r2"}

    async def _mark_remote_sync_failed(
        self: _MediaRuntimeHost,
        image: WordbankImageRecord,
    ) -> WordbankImageRecord | None:
        start = perf_start()
        updated = await self.repository.update_image_remote_sync(
            image.id,
            remote_storage_path=image.remote_storage_path,
            remote_sync_status=REMOTE_SYNC_FAILED,
            remote_synced_at=image.remote_synced_at,
            remote_etag=image.remote_etag,
            remote_object_size=image.remote_object_size,
        )
        log_perf(
            "media.remote_sync.mark_failed",
            start=start,
            updated=updated is not None,
            reason="remote_load_or_sync_failed",
            **image_log_fields(image),
        )
        if updated is not None:
            self._cache_image(updated)
        return updated

    @staticmethod
    def _md5_hex(data: bytes) -> str:
        from hashlib import md5

        return md5(data).hexdigest()


class _MediaRuntimeHost(Protocol):
    repository: MediaRepository
    cache_storage: LocalLruCacheWordbankMediaStorage
    remote_storage: ObjectStorageWordbankMediaStorage | None
    legacy_storage: WordbankMediaStorage
    remote_provider: str
    similarity_threshold: int
    candidate_limit: int
    _by_id: dict[int, WordbankImageRecord]
    _by_md5: dict[str, WordbankImageRecord]
    _by_dhash_prefix: dict[str, list[WordbankImageRecord]]
    _by_canonical_id: dict[int, WordbankImageRecord]
    _canonical_ids_by_dhash: dict[str, tuple[int, ...]]
    _canonical_hash_image: dict[int, WordbankImageRecord]
    _dhash_tree: BKTree | None
    _remote_load_locks: dict[int, asyncio.Lock]
    _cache_maintenance_lock: asyncio.Lock

    def _cache_image(self, image: WordbankImageRecord) -> None: ...
    def load_cache(self, images: Sequence[WordbankImageRecord]) -> None: ...
    def resolve_canonical_id_from_hints(self, hints: Sequence[str]) -> int | None: ...
    async def load_canonical_storage_bytes(self, canonical_image_id: int) -> bytes | None: ...
    def _get_remote_load_lock(self, canonical_image_id: int) -> asyncio.Lock: ...
    async def evict_local_cache_if_needed(self) -> int: ...
    async def reconcile_local_cache(self) -> dict[str, int]: ...
    async def rebuild_cache_metadata(self, image: WordbankImageRecord) -> WordbankImageRecord: ...
    async def retry_remote_sync(
        self,
        *,
        limit: int = 200,
        id_start: int = 0,
        only_unsynced: bool = True,
        verify_remote: bool = False,
        rebuild_cache_metadata: bool = False,
    ) -> dict[str, int]: ...
    async def sync_image_to_remote(
        self,
        image: WordbankImageRecord,
        *,
        verify_remote: bool = False,
    ) -> WordbankImageRecord | None: ...
    def build_expected_remote_key(self, image: WordbankImageRecord) -> str | None: ...
    async def mark_image_remote_synced(
        self,
        image: WordbankImageRecord,
        remote_object: StorageObject,
        *,
        synced_at: int | None = None,
    ) -> WordbankImageRecord | None: ...
    async def _load_from_local_cache(self, image: WordbankImageRecord) -> bytes | None: ...
    async def _load_from_legacy_storage(self, image: WordbankImageRecord) -> bytes | None: ...
    async def _load_from_remote_storage(self, image: WordbankImageRecord) -> bytes | None: ...
    async def _load_source_bytes_for_remote_sync(self, image: WordbankImageRecord) -> bytes | None: ...
    async def _touch_last_access(self, image_id: int) -> WordbankImageRecord | None: ...
    async def _store_cache_bytes(
        self,
        image: WordbankImageRecord,
        data: bytes,
        *,
        mark_as_hit: bool,
    ) -> WordbankImageRecord: ...
    async def _mark_remote_sync_failed(
        self,
        image: WordbankImageRecord,
    ) -> WordbankImageRecord | None: ...
    def _remote_expected(self) -> bool: ...
    @staticmethod
    def _md5_hex(data: bytes) -> str: ...
