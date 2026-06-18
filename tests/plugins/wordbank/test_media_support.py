import asyncio
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from PIL import Image
import pytest

from src.lib.object_storage import StorageObject
from src.plugins.wordbank.database.types import (
    WordbankImagePayload,
    WordbankImageRecord,
)
from src.plugins.wordbank.services import media as media_module
from src.plugins.wordbank.services import media_models as media_models_module
from src.plugins.wordbank.services import media_runtime as media_runtime_module
from src.plugins.wordbank.services.media import (
    LocalLruCacheWordbankMediaStorage,
    LocalWordbankMediaStorage,
    ObjectStorageWordbankMediaStorage,
    R2WordbankMediaStorage,
    WordbankMediaService,
    fingerprint_image,
    hamming_distance,
    prepare_image_bytes,
)


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _gif(colors: list[tuple[int, int, int]]) -> bytes:
    buffer = BytesIO()
    frames = [Image.new("RGB", (16, 16), color) for color in colors]
    first, *rest = frames
    first.save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=rest,
        duration=[80] * len(frames),
        loop=0,
    )
    return buffer.getvalue()


class _ImageRepo:
    def __init__(self) -> None:
        self.images: list[WordbankImageRecord] = []

    async def list_images(self) -> list[WordbankImageRecord]:
        return list(self.images)

    async def get_image_by_id(self, image_id: int) -> WordbankImageRecord | None:
        for image in self.images:
            if image.id == image_id:
                return image
        return None

    async def get_image_by_md5(self, md5: str) -> WordbankImageRecord | None:
        for image in self.images:
            if image.md5 == md5:
                return image
        return None

    async def get_image_candidates(
        self,
        dhash_prefix: str,
        *,
        limit: int = 128,
    ) -> list[WordbankImageRecord]:
        return [image for image in self.images if image.dhash.startswith(dhash_prefix)][
            :limit
        ]

    async def create_image(
        self,
        payload: WordbankImagePayload,
    ) -> WordbankImageRecord:
        image_id = len(self.images) + 1
        image = WordbankImageRecord(
            id=image_id,
            canonical_image_id=payload.get("canonical_image_id") or image_id,
            md5=payload["md5"],
            dhash=payload["dhash"],
            phash=payload["phash"],
            width=payload["width"],
            height=payload["height"],
            file_size=payload["file_size"],
            hash_version=payload["hash_version"],
            storage_path=payload["storage_path"],
            remote_storage_path=payload.get("remote_storage_path", ""),
            local_cache_path=payload.get("local_cache_path", ""),
            cache_file_size=payload.get("cache_file_size", 0),
            last_accessed_at=payload.get("last_accessed_at", 0),
            cache_last_hit_at=payload.get("cache_last_hit_at", 0),
            remote_sync_status=payload.get("remote_sync_status", "pending"),
            remote_synced_at=payload.get("remote_synced_at", 0),
            remote_etag=payload.get("remote_etag", ""),
            remote_object_size=payload.get("remote_object_size", 0),
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )
        self.images.append(image)
        return image

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
    ) -> WordbankImageRecord | None:
        for index, image in enumerate(self.images):
            if image.id != image_id:
                continue
            updated = replace(
                image,
                remote_storage_path=remote_storage_path,
                remote_sync_status=remote_sync_status,
                remote_synced_at=remote_synced_at,
                remote_etag=remote_etag,
                remote_object_size=remote_object_size,
                storage_path=(
                    storage_path if storage_path is not None else image.storage_path
                ),
                updated_at=updated_at or image.updated_at,
            )
            self.images[index] = updated
            return updated
        return None

    async def update_image_cache_metadata(
        self,
        image_id: int,
        *,
        local_cache_path: str,
        cache_file_size: int,
        last_accessed_at: int | None = None,
        cache_last_hit_at: int | None = None,
        updated_at: int | None = None,
    ) -> WordbankImageRecord | None:
        for index, image in enumerate(self.images):
            if image.id != image_id:
                continue
            updated = replace(
                image,
                local_cache_path=local_cache_path,
                cache_file_size=cache_file_size,
                last_accessed_at=(
                    image.last_accessed_at
                    if last_accessed_at is None
                    else last_accessed_at
                ),
                cache_last_hit_at=(
                    image.cache_last_hit_at
                    if cache_last_hit_at is None
                    else cache_last_hit_at
                ),
                updated_at=updated_at or image.updated_at,
            )
            self.images[index] = updated
            return updated
        return None

    async def list_cached_images(self) -> list[WordbankImageRecord]:
        return [image for image in self.images if image.local_cache_path]

    async def list_images_for_remote_sync(
        self,
        *,
        limit: int = 200,
        id_start: int = 0,
        only_unsynced: bool = True,
    ) -> list[WordbankImageRecord]:
        images = [image for image in self.images if image.id >= id_start]
        if only_unsynced:
            images = [
                image
                for image in images
                if not image.remote_storage_path or image.remote_sync_status != "synced"
            ]
        return images[:limit]


__all__ = [
    "Any",
    "AsyncMock",
    "BytesIO",
    "Image",
    "LocalLruCacheWordbankMediaStorage",
    "LocalWordbankMediaStorage",
    "ObjectStorageWordbankMediaStorage",
    "Path",
    "R2WordbankMediaStorage",
    "StorageObject",
    "WordbankImagePayload",
    "WordbankImageRecord",
    "WordbankMediaService",
    "_ImageRepo",
    "_ObjectStorage",
    "_gif",
    "_jpeg",
    "_png",
    "asyncio",
    "fingerprint_image",
    "hamming_distance",
    "media_models_module",
    "media_module",
    "media_runtime_module",
    "prepare_image_bytes",
    "pytest",
]


class _ObjectStorage:
    available = True

    def __init__(self, *, provider: str = "r2", fail: bool = False) -> None:
        self.provider = provider
        self.bucket = "bucket"
        self.fail = fail
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str | None] = {}

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> StorageObject:
        _ = content_type
        if self.fail:
            raise RuntimeError("upload failed")
        self.objects[key] = data
        self.content_types[key] = content_type
        return StorageObject(
            provider=self.provider,
            bucket="bucket",
            key=key,
            uri=f"{self.provider}://bucket/{key}",
            public_url=None,
            etag="etag",
            size=len(data),
        )

    async def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    async def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def list_objects(self, prefix: str) -> list[StorageObject]:
        normalized_prefix = prefix.strip("/")
        return [
            StorageObject(
                provider=self.provider,
                bucket=self.bucket,
                key=key,
                uri=f"{self.provider}://{self.bucket}/{key}",
                public_url=None,
                etag="etag",
                size=len(data),
            )
            for key, data in self.objects.items()
            if key.startswith(normalized_prefix)
        ]

    async def presign_get_url(self, key: str, *, expires_in: int = 3600) -> str:
        _ = expires_in
        return f"https://example.test/{key}"
