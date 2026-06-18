"""Wordbank media storage backends and repository protocols."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from src.lib.object_storage.types import ObjectStorageClient, StorageObject
from src.logger import logger
from src.plugins.wordbank.database.types import (
    WordbankImagePayload,
    WordbankImageRecord,
)
from src.plugins.wordbank.debug import log_perf, perf_start

from .media_models import (
    DEFAULT_CACHE_MAX_BYTES,
    DEFAULT_CACHE_MAX_FILES,
    DEFAULT_CACHE_TRIM_TO_BYTES,
    DEFAULT_MEDIA_CACHE_ROOT,
    DEFAULT_MEDIA_EXTENSION,
    DEFAULT_MEDIA_ROOT,
    LocalCacheEntry,
    PreparedImage,
)


class MediaRepository(Protocol):
    async def list_images(self) -> list[WordbankImageRecord]: ...
    async def get_image_by_id(self, image_id: int) -> WordbankImageRecord | None: ...
    async def get_image_by_md5(self, md5: str) -> WordbankImageRecord | None: ...
    async def get_image_candidates(
        self,
        dhash_prefix: str,
        *,
        limit: int = 128,
    ) -> list[WordbankImageRecord]: ...
    async def create_image(
        self, payload: WordbankImagePayload
    ) -> WordbankImageRecord: ...
    async def update_image_remote_sync(
        self,
        image_id: int,
        *,
        remote_storage_path: str,
        remote_sync_status: str,
        remote_synced_at: int,
        remote_etag: str = "",
        remote_object_size: int = 0,
        storage_path: str | None = None,
        updated_at: int | None = None,
    ) -> WordbankImageRecord | None: ...
    async def update_image_cache_metadata(
        self,
        image_id: int,
        *,
        local_cache_path: str,
        cache_file_size: int,
        last_accessed_at: int | None = None,
        cache_last_hit_at: int | None = None,
        updated_at: int | None = None,
    ) -> WordbankImageRecord | None: ...
    async def list_cached_images(self) -> list[WordbankImageRecord]: ...
    async def list_images_for_remote_sync(
        self,
        *,
        limit: int = 200,
        id_start: int = 0,
        only_unsynced: bool = True,
    ) -> list[WordbankImageRecord]: ...


class WordbankMediaStorage(Protocol):
    async def save_image(
        self,
        prepared: PreparedImage,
        *,
        md5_hex: str,
        keep_original: bool,
    ) -> str: ...

    async def load_bytes(self, storage_path: str) -> bytes | None: ...


class LocalWordbankMediaStorage:
    def __init__(self, media_root: Path = DEFAULT_MEDIA_ROOT) -> None:
        self.media_root = media_root

    async def save_image(
        self,
        prepared: PreparedImage,
        *,
        md5_hex: str,
        keep_original: bool,
    ) -> str:
        return await asyncio.to_thread(
            self._save_image,
            prepared,
            md5_hex=md5_hex,
            keep_original=keep_original,
        )

    def _save_image(
        self,
        prepared: PreparedImage,
        *,
        md5_hex: str,
        keep_original: bool,
    ) -> str:
        self.media_root.mkdir(parents=True, exist_ok=True)
        media_path = self.media_root / f"{md5_hex}{prepared.stored_media.extension}"
        media_path.write_bytes(prepared.stored_media.data)
        if keep_original:
            original_path = self.media_root / f"{md5_hex}.source"
            original_path.write_bytes(prepared.original_data)
        return str(media_path)

    async def load_bytes(self, storage_path: str) -> bytes | None:
        return await asyncio.to_thread(self._load_bytes, storage_path)

    def _load_bytes(self, storage_path: str) -> bytes | None:
        path = Path(storage_path)
        if not path.is_file():
            return None
        return path.read_bytes()


class ObjectStorageWordbankMediaStorage:
    def __init__(
        self,
        object_storage: ObjectStorageClient,
        *,
        fallback: WordbankMediaStorage | None = None,
        key_prefix: str = "wordbank/media",
    ) -> None:
        self.object_storage = object_storage
        self.fallback = fallback
        self.key_prefix = key_prefix.strip("/")
        self.bucket = str(getattr(object_storage, "bucket", "") or "")

    def _build_key(self, md5_hex: str, extension: str) -> str:
        return f"{self.key_prefix}/{md5_hex}{extension}"

    def build_key(self, md5_hex: str, extension: str) -> str:
        return self._build_key(md5_hex, extension)

    def build_uri(self, md5_hex: str, extension: str) -> str:
        key = self._build_key(md5_hex, extension)
        bucket = self.bucket.strip("/")
        if bucket:
            return f"{self.object_storage.provider}://{bucket}/{key}"
        return f"{self.object_storage.provider}://{key}"

    def _key_from_uri(self, storage_path: str) -> str | None:
        if not storage_path.startswith(f"{self.object_storage.provider}://"):
            return None
        if self.bucket:
            prefix = f"{self.object_storage.provider}://{self.bucket}/"
            if storage_path.startswith(prefix):
                return storage_path.removeprefix(prefix)
        return storage_path.removeprefix(f"{self.object_storage.provider}://").split(
            "/", 1
        )[-1]

    async def save_prepared_image(
        self,
        prepared: PreparedImage,
        *,
        md5_hex: str,
        keep_original: bool,
    ) -> StorageObject:
        stored = await self.save_bytes(
            prepared.stored_media.data,
            md5_hex=md5_hex,
            extension=prepared.stored_media.extension,
            content_type=prepared.stored_media.content_type,
        )
        if keep_original:
            await self.object_storage.put_bytes(
                f"{self.key_prefix}/{md5_hex}.source",
                prepared.original_data,
                content_type="application/octet-stream",
            )
        return stored

    async def save_bytes(
        self,
        data: bytes,
        *,
        md5_hex: str,
        extension: str,
        content_type: str | None = None,
    ) -> StorageObject:
        return await self.object_storage.put_bytes(
            self._build_key(md5_hex, extension),
            data,
            content_type=content_type,
        )

    async def save_image(
        self,
        prepared: PreparedImage,
        *,
        md5_hex: str,
        keep_original: bool,
    ) -> str:
        try:
            stored = await self.save_prepared_image(
                prepared,
                md5_hex=md5_hex,
                keep_original=keep_original,
            )
            return stored.uri
        except Exception as exc:
            if self.fallback is None:
                raise
            logger.warning(f"[Wordbank] remote media save fallback to local: {exc}")
            return await self.fallback.save_image(
                prepared,
                md5_hex=md5_hex,
                keep_original=keep_original,
            )

    async def load_bytes(self, storage_path: str) -> bytes | None:
        key = self._key_from_uri(storage_path)
        if key is None:
            log_perf(
                "media.remote_storage.uri_unhandled_fallback",
                provider=self.object_storage.provider,
                bucket=self.bucket or "-",
                uri=storage_path,
                has_fallback=self.fallback is not None,
                reason="uri_not_owned_by_provider",
            )
            if self.fallback is None:
                return None
            start = perf_start()
            loaded = await self.fallback.load_bytes(storage_path)
            log_perf(
                "media.remote_storage.load.done",
                start=start,
                provider=self.object_storage.provider,
                bucket=self.bucket or "-",
                key="-",
                uri=storage_path,
                bytes=len(loaded) if loaded is not None else 0,
                hit=loaded is not None,
                fallback_used=True,
            )
            return loaded
        log_perf(
            "media.remote_storage.load.begin",
            provider=self.object_storage.provider,
            bucket=self.bucket or "-",
            key=key,
            uri=storage_path,
            fallback_used=False,
        )
        start = perf_start()
        try:
            loaded = await self.object_storage.get_bytes(key)
        except Exception as exc:
            log_perf(
                "media.remote_storage.load.error",
                start=start,
                provider=self.object_storage.provider,
                bucket=self.bucket or "-",
                key=key,
                uri=storage_path,
                fallback_used=False,
                error=type(exc).__name__,
            )
            logger.warning(f"[Wordbank] remote media load skipped: {exc}")
            return None
        log_perf(
            "media.remote_storage.load.done",
            start=start,
            provider=self.object_storage.provider,
            bucket=self.bucket or "-",
            key=key,
            uri=storage_path,
            bytes=len(loaded) if loaded is not None else 0,
            hit=loaded is not None,
            fallback_used=False,
        )
        return loaded

    async def exists(self, storage_path: str) -> bool:
        key = self._key_from_uri(storage_path)
        if key is None:
            log_perf(
                "media.remote_storage.exists.error",
                provider=self.object_storage.provider,
                bucket=self.bucket or "-",
                key="-",
                uri=storage_path,
                reason="uri_not_owned_by_provider",
            )
            return False
        log_perf(
            "media.remote_storage.exists.begin",
            provider=self.object_storage.provider,
            bucket=self.bucket or "-",
            key=key,
            uri=storage_path,
        )
        start = perf_start()
        try:
            exists = await self.object_storage.exists(key)
        except Exception as exc:
            log_perf(
                "media.remote_storage.exists.error",
                start=start,
                provider=self.object_storage.provider,
                bucket=self.bucket or "-",
                key=key,
                uri=storage_path,
                error=type(exc).__name__,
            )
            return False
        log_perf(
            "media.remote_storage.exists.done",
            start=start,
            provider=self.object_storage.provider,
            bucket=self.bucket or "-",
            key=key,
            uri=storage_path,
            exists=exists,
        )
        return exists

    async def list_objects(self) -> list[StorageObject]:
        return await self.object_storage.list_objects(f"{self.key_prefix}/")


R2WordbankMediaStorage = ObjectStorageWordbankMediaStorage


class LocalLruCacheWordbankMediaStorage:
    def __init__(
        self,
        cache_root: Path = DEFAULT_MEDIA_CACHE_ROOT,
        *,
        enabled: bool = True,
        max_bytes: int = DEFAULT_CACHE_MAX_BYTES,
        trim_to_bytes: int = DEFAULT_CACHE_TRIM_TO_BYTES,
        max_files: int = DEFAULT_CACHE_MAX_FILES,
    ) -> None:
        self.cache_root = cache_root
        self.enabled = enabled
        self.max_bytes = max(int(max_bytes), 0)
        self.trim_to_bytes = max(int(trim_to_bytes), 0)
        self.max_files = max(int(max_files), 0)

    async def load_cached_bytes(self, local_cache_path: str) -> bytes | None:
        if not self.enabled or not local_cache_path:
            return None
        return await asyncio.to_thread(self._load_cached_bytes, local_cache_path)

    def _load_cached_bytes(self, local_cache_path: str) -> bytes | None:
        path = Path(local_cache_path)
        if not path.is_file():
            return None
        return path.read_bytes()

    async def store_cached_bytes(
        self,
        data: bytes,
        *,
        md5_hex: str,
        extension: str,
    ) -> LocalCacheEntry | None:
        if not self.enabled:
            return None
        return await asyncio.to_thread(
            self._store_cached_bytes,
            data,
            md5_hex=md5_hex,
            extension=extension,
        )

    def _store_cached_bytes(
        self,
        data: bytes,
        *,
        md5_hex: str,
        extension: str,
    ) -> LocalCacheEntry:
        normalized_extension = extension or DEFAULT_MEDIA_EXTENSION
        self.cache_root.mkdir(parents=True, exist_ok=True)
        cache_path = self.cache_root / f"{md5_hex}{normalized_extension}"
        cache_path.write_bytes(data)
        return LocalCacheEntry(path=str(cache_path), size=cache_path.stat().st_size)

    async def touch_cache_entry(self, local_cache_path: str) -> None:
        if not self.enabled or not local_cache_path:
            return
        await asyncio.to_thread(self._touch_cache_entry, local_cache_path)

    def _touch_cache_entry(self, local_cache_path: str) -> None:
        path = Path(local_cache_path)
        if path.is_file():
            path.touch()

    async def evict_if_needed(
        self,
        images: list[WordbankImageRecord] | tuple[WordbankImageRecord, ...],
    ) -> tuple[WordbankImageRecord, ...]:
        if not self.enabled:
            return ()
        candidates = [
            image
            for image in images
            if image.local_cache_path and image.cache_file_size > 0
        ]
        current_bytes = sum(image.cache_file_size for image in candidates)
        current_files = len(candidates)
        if current_bytes <= self.max_bytes and current_files <= self.max_files:
            return ()

        trim_target = self.trim_to_bytes or self.max_bytes
        trim_target = min(trim_target, self.max_bytes) if self.max_bytes else 0
        ordered = sorted(
            candidates,
            key=lambda image: (
                image.cache_last_hit_at or image.last_accessed_at or image.updated_at,
                image.id,
            ),
        )
        evicted: list[WordbankImageRecord] = []
        for image in ordered:
            if (
                current_bytes <= trim_target
                and current_files <= self.max_files
                and self.max_bytes > 0
                and self.max_files > 0
            ):
                break
            evicted.append(image)
            current_bytes -= image.cache_file_size
            current_files -= 1
        if self.max_bytes <= 0 or self.max_files <= 0:
            return tuple(ordered)
        return tuple(evicted)

    async def remove_cache_entry(self, local_cache_path: str) -> None:
        if not local_cache_path:
            return
        await asyncio.to_thread(self._remove_cache_entry, local_cache_path)

    def _remove_cache_entry(self, local_cache_path: str) -> None:
        path = Path(local_cache_path)
        if path.exists():
            path.unlink()

    async def prune_orphans(self, known_paths: set[str]) -> list[str]:
        if not self.enabled:
            return []
        return await asyncio.to_thread(self._prune_orphans, known_paths)

    def _prune_orphans(self, known_paths: set[str]) -> list[str]:
        if not self.cache_root.exists():
            return []
        removed: list[str] = []
        for path in self.cache_root.rglob("*"):
            if not path.is_file():
                continue
            normalized = str(path)
            if normalized in known_paths:
                continue
            path.unlink()
            removed.append(normalized)
        return removed
