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


def test_fingerprint_and_hamming_distance() -> None:
    first = fingerprint_image(_png((255, 0, 0)))
    second = fingerprint_image(_png((255, 0, 0)))

    assert first.md5 == second.md5
    assert first.width == 16
    assert first.height == 16
    assert hamming_distance(first.dhash, second.dhash) == 0


async def test_media_ingest_dedupes_md5_and_uses_cache(tmp_path: Path) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _png((0, 255, 0))

    first = await service.ingest_image_bytes(data)
    second = await service.ingest_image_bytes(data)

    assert first.id == second.id
    assert len(repo.images) == 1
    assert service.resolve_canonical_id(data) == first.canonical_id
    assert (tmp_path / f"{first.md5}.webp").is_file()


async def test_media_search_similar_images_returns_ranked_matches(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    source = _png((64, 128, 255))

    first = await service.ingest_image_bytes(source)
    await service.rebuild_cache()
    matches = service.search_similar_images(source)

    assert matches
    assert matches[0].canonical_id == first.canonical_id
    assert matches[0].score == 1.0


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


async def test_r2_media_storage_saves_and_loads_remote_image(tmp_path: Path) -> None:
    storage = _ObjectStorage()
    media_storage = R2WordbankMediaStorage(
        storage,
        fallback=LocalWordbankMediaStorage(tmp_path),
    )
    data = _png((0, 0, 255))
    fingerprint = fingerprint_image(data)

    storage_path = await media_storage.save_image(
        prepare_image_bytes(data),
        md5_hex=fingerprint.md5,
        keep_original=False,
    )
    loaded = await media_storage.load_bytes(storage_path)

    assert storage_path == f"r2://bucket/wordbank/media/{fingerprint.md5}.webp"
    assert loaded == storage.objects[f"wordbank/media/{fingerprint.md5}.webp"]


async def test_object_media_storage_saves_and_loads_github_uri(tmp_path: Path) -> None:
    storage = _ObjectStorage(provider="github")
    media_storage = ObjectStorageWordbankMediaStorage(
        storage,
        fallback=LocalWordbankMediaStorage(tmp_path),
    )
    data = _png((128, 128, 255))
    fingerprint = fingerprint_image(data)

    storage_path = await media_storage.save_image(
        prepare_image_bytes(data),
        md5_hex=fingerprint.md5,
        keep_original=False,
    )
    loaded = await media_storage.load_bytes(storage_path)

    assert storage_path == f"github://bucket/wordbank/media/{fingerprint.md5}.webp"
    assert loaded == storage.objects[f"wordbank/media/{fingerprint.md5}.webp"]


async def test_r2_media_storage_falls_back_to_local_on_upload_error(
    tmp_path: Path,
) -> None:
    media_storage = R2WordbankMediaStorage(
        _ObjectStorage(fail=True),
        fallback=LocalWordbankMediaStorage(tmp_path),
    )
    data = _png((255, 255, 0))
    fingerprint = fingerprint_image(data)

    storage_path = await media_storage.save_image(
        prepare_image_bytes(data),
        md5_hex=fingerprint.md5,
        keep_original=False,
    )

    assert storage_path == str(tmp_path / f"{fingerprint.md5}.webp")
    assert (tmp_path / f"{fingerprint.md5}.webp").is_file()


def test_fingerprint_uses_representative_gif_frame() -> None:
    first_frame = _png((255, 0, 0))
    animated = _gif([(255, 0, 0), (0, 0, 255)])

    still_fingerprint = fingerprint_image(first_frame)
    animated_fingerprint = fingerprint_image(animated)

    assert animated_fingerprint.md5 != still_fingerprint.md5
    assert animated_fingerprint.dhash == still_fingerprint.dhash
    assert animated_fingerprint.phash == still_fingerprint.phash
    assert animated_fingerprint.width == 16
    assert animated_fingerprint.height == 16


async def test_media_ingest_preserves_animation_bytes_for_gif(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _gif([(255, 0, 0), (0, 255, 0)])

    image = await service.ingest_image_bytes(data)
    stored_path = Path(image.storage_path)
    stored_bytes = await asyncio.to_thread(stored_path.read_bytes)

    assert stored_path.suffix == ".webp"
    with Image.open(BytesIO(stored_bytes)) as stored_image:
        assert getattr(stored_image, "n_frames", 1) > 1


async def test_media_ingest_detects_gif_by_header_even_when_suffix_is_jpg(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    fake_jpg_path = tmp_path / "fake-animation.jpg"
    fake_jpg_path.write_bytes(_gif([(255, 0, 0), (0, 255, 0)]))

    image = await service.ingest_image_bytes(fake_jpg_path.read_bytes())
    stored_path = Path(image.storage_path)
    stored_bytes = await asyncio.to_thread(stored_path.read_bytes)

    assert stored_path.suffix == ".webp"
    with Image.open(BytesIO(stored_bytes)) as stored_image:
        assert str(getattr(stored_image, "format", "")).upper() == "WEBP"
        assert getattr(stored_image, "n_frames", 1) > 1


async def test_media_ingest_dedupes_gif_by_md5_before_similarity(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _gif([(255, 0, 0), (0, 255, 0)])

    first = await service.ingest_image_bytes(data)
    second = await service.ingest_image_bytes(data)

    assert first.id == second.id
    assert len(repo.images) == 1


async def test_media_ingest_short_circuits_on_md5_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _gif([(255, 0, 0), (0, 255, 0)])

    first = await service.ingest_image_bytes(data)

    def _fail_prepare(_data: bytes) -> Any:
        raise AssertionError("prepare_image_bytes should not run after md5 hit")

    monkeypatch.setattr(media_module, "prepare_image_bytes", _fail_prepare)

    second = await service.ingest_image_bytes(data)

    assert second.id == first.id


async def test_resolve_canonical_id_short_circuits_on_name_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _png((10, 20, 30))

    first = await service.ingest_image_bytes(data)

    def _fail_fingerprint(_data: bytes) -> Any:
        raise AssertionError("fingerprint_image should not run after md5 hint hit")

    monkeypatch.setattr(media_module, "fingerprint_image", _fail_fingerprint)

    resolved = service.resolve_canonical_id(
        b"not-an-image",
        name_hints=[f"https://example.test/path/{first.md5.upper()}.PNG?download=1"],
    )

    assert resolved == first.canonical_id


async def test_resolve_canonical_id_short_circuits_on_raw_bytes_md5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _gif([(255, 0, 0), (0, 255, 0)])

    first = await service.ingest_image_bytes(data)

    def _fail_fingerprint(_data: bytes) -> Any:
        raise AssertionError("fingerprint_image should not run after raw md5 hit")

    monkeypatch.setattr(media_module, "fingerprint_image", _fail_fingerprint)

    resolved = service.resolve_canonical_id(data)

    assert resolved == first.canonical_id


async def test_r2_media_storage_saves_gif_as_animated_media(tmp_path: Path) -> None:
    storage = _ObjectStorage()
    media_storage = R2WordbankMediaStorage(
        storage,
        fallback=LocalWordbankMediaStorage(tmp_path),
    )
    data = _gif([(0, 0, 255), (255, 255, 0)])
    fingerprint = fingerprint_image(data)

    storage_path = await media_storage.save_image(
        prepare_image_bytes(data),
        md5_hex=fingerprint.md5,
        keep_original=False,
    )
    key = storage_path.removeprefix("r2://bucket/")
    loaded = await media_storage.load_bytes(storage_path)

    assert loaded is not None
    assert loaded == storage.objects[key]
    assert Path(key).suffix == ".webp"
    assert storage.content_types[key] == "image/webp"
    with Image.open(BytesIO(loaded)) as stored_image:
        assert getattr(stored_image, "n_frames", 1) > 1


def test_prepare_image_bytes_falls_back_to_original_gif_when_webp_encode_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _gif([(255, 0, 255), (0, 255, 255)])

    monkeypatch.setattr(
        media_module,
        "_encode_animated_webp",
        lambda _image, resize_to_limit=False: None,
    )

    prepared = prepare_image_bytes(data)

    assert prepared.stored_media.extension == ".gif"
    assert prepared.stored_media.content_type == "image/gif"
    assert prepared.stored_media.data == data


def test_prepare_image_bytes_falls_back_to_original_jpeg_when_static_webp_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _jpeg((12, 34, 56))

    monkeypatch.setattr(media_module, "_encode_static_webp", lambda _image: None)

    prepared = prepare_image_bytes(data)

    assert prepared.stored_media.extension == ".jpg"
    assert prepared.stored_media.content_type == "image/jpeg"
    assert prepared.stored_media.data == data


async def test_media_ingest_persists_remote_storage_path_for_new_images(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
        remote_sync_mode="sync",
    )

    image = await service.ingest_image_bytes(_png((1, 2, 3)))

    assert image.remote_storage_path == f"r2://bucket/wordbank/media/{image.md5}.webp"
    assert image.remote_sync_status == "synced"
    assert image.storage_path == image.remote_storage_path
    assert await asyncio.to_thread(Path(repo.images[0].local_cache_path).is_file)


async def test_media_ingest_defers_remote_sync_and_keeps_local_storage_available(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        legacy_storage=LocalWordbankMediaStorage(tmp_path / "legacy"),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
        remote_sync_mode="deferred",
    )

    image = await service.ingest_image_bytes(_png((14, 15, 16)))
    await asyncio.sleep(0)

    assert image.remote_storage_path == ""
    assert image.remote_sync_status == "pending"
    assert await asyncio.to_thread(Path(image.storage_path).is_file)
    assert storage.objects[f"wordbank/media/{image.md5}.webp"]
    assert repo.images[0].remote_sync_status == "synced"


async def test_media_ingest_marks_failed_when_expected_remote_is_unavailable(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=None,
        legacy_storage=LocalWordbankMediaStorage(tmp_path / "legacy"),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
        remote_provider="r2",
    )

    image = await service.ingest_image_bytes(_png((9, 9, 9)))

    assert image.remote_sync_status == "failed"
    assert image.remote_storage_path == ""
    assert await asyncio.to_thread(Path(image.storage_path).is_file)


async def test_media_ingest_raises_when_remote_required_but_provider_is_unavailable(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=None,
        legacy_storage=LocalWordbankMediaStorage(tmp_path / "legacy"),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
        remote_provider="github",
        remote_required=True,
    )

    with pytest.raises(media_module.MediaError):
        await service.ingest_image_bytes(_png((11, 11, 11)))


async def test_media_load_prefers_local_lru_cache(tmp_path: Path) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    cached_bytes = _png((4, 5, 6))
    cache_path = cache_root / "cached.png"
    cache_path.write_bytes(cached_bytes)
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(cache_root),
    )
    await repo.create_image(
        {
            "md5": "1" * 32,
            "dhash": "0" * 16,
            "phash": "0" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(cached_bytes),
            "hash_version": 2,
            "storage_path": "r2://bucket/wordbank/media/test.png",
            "remote_storage_path": "r2://bucket/wordbank/media/test.png",
            "local_cache_path": str(cache_path),
            "cache_file_size": len(cached_bytes),
            "remote_sync_status": "synced",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()
    storage.get_bytes = AsyncMock(side_effect=AssertionError("remote should not run"))

    loaded = await service.load_canonical_storage_bytes(1)

    assert loaded == cached_bytes
    assert repo.images[0].cache_last_hit_at > 0


async def test_media_load_remote_miss_backfills_local_cache_and_updates_access_time(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    data = _png((7, 8, 9))
    storage.objects["wordbank/media/test.png"] = data
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
    )
    await repo.create_image(
        {
            "md5": "2" * 32,
            "dhash": "1" * 16,
            "phash": "1" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(data),
            "hash_version": 2,
            "storage_path": "r2://bucket/wordbank/media/test.png",
            "remote_storage_path": "r2://bucket/wordbank/media/test.png",
            "remote_sync_status": "synced",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    loaded = await service.load_canonical_storage_bytes(1)

    assert loaded == data
    assert repo.images[0].local_cache_path
    assert await asyncio.to_thread(Path(repo.images[0].local_cache_path).is_file)
    assert repo.images[0].last_accessed_at > 0


async def test_media_cache_evicts_least_recently_used_when_size_exceeded(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    first_bytes = _png((10, 20, 30))
    second_bytes = _png((40, 50, 60))
    storage.objects["wordbank/media/first.png"] = first_bytes
    storage.objects["wordbank/media/second.png"] = second_bytes
    cache_storage = LocalLruCacheWordbankMediaStorage(
        tmp_path / "cache",
        max_bytes=len(first_bytes) + 8,
        trim_to_bytes=len(first_bytes),
        max_files=10,
    )
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=cache_storage,
    )
    await repo.create_image(
        {
            "md5": "3" * 32,
            "dhash": "2" * 16,
            "phash": "2" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(first_bytes),
            "hash_version": 2,
            "storage_path": "r2://bucket/wordbank/media/first.png",
            "remote_storage_path": "r2://bucket/wordbank/media/first.png",
            "remote_sync_status": "synced",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await repo.create_image(
        {
            "md5": "4" * 32,
            "dhash": "3" * 16,
            "phash": "3" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(second_bytes),
            "hash_version": 2,
            "storage_path": "r2://bucket/wordbank/media/second.png",
            "remote_storage_path": "r2://bucket/wordbank/media/second.png",
            "remote_sync_status": "synced",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    assert await service.load_canonical_storage_bytes(1) == first_bytes
    assert await service.load_canonical_storage_bytes(2) == second_bytes

    assert repo.images[0].local_cache_path == ""
    assert repo.images[1].local_cache_path != ""
    first_cache_exists = await asyncio.to_thread(
        Path(tmp_path / "cache" / f"{'3' * 32}.png").exists
    )
    assert not first_cache_exists


async def test_media_cache_singleflight_prevents_duplicate_remote_download(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    data = _png((70, 80, 90))

    class _SlowObjectStorage(_ObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0
            self.objects["wordbank/media/test.png"] = data

        async def get_bytes(self, key: str) -> bytes:
            self.calls += 1
            await asyncio.sleep(0.05)
            return await super().get_bytes(key)

    storage = _SlowObjectStorage()
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
    )
    await repo.create_image(
        {
            "md5": "5" * 32,
            "dhash": "4" * 16,
            "phash": "4" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(data),
            "hash_version": 2,
            "storage_path": "r2://bucket/wordbank/media/test.png",
            "remote_storage_path": "r2://bucket/wordbank/media/test.png",
            "remote_sync_status": "synced",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    first, second = await asyncio.gather(
        service.load_canonical_storage_bytes(1),
        service.load_canonical_storage_bytes(1),
    )

    assert first == data
    assert second == data
    assert storage.calls == 1


async def test_media_prefers_legacy_local_storage_before_remote(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    storage.get_bytes = AsyncMock(side_effect=AssertionError("remote should not run"))
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy_path = legacy_root / "legacy.png"
    legacy_bytes = _png((100, 110, 120))
    legacy_path.write_bytes(legacy_bytes)
    service = WordbankMediaService(
        repo,
        media_root=legacy_root,
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
    )
    await repo.create_image(
        {
            "md5": "6" * 32,
            "dhash": "5" * 16,
            "phash": "5" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(legacy_bytes),
            "hash_version": 2,
            "storage_path": str(legacy_path),
            "remote_storage_path": "r2://bucket/wordbank/media/missing.png",
            "remote_sync_status": "failed",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    loaded = await service.load_canonical_storage_bytes(1)

    assert loaded == legacy_bytes
    assert repo.images[0].remote_sync_status == "failed"
    storage.get_bytes.assert_not_awaited()


async def test_media_logs_legacy_stages_when_local_file_is_used_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    storage.get_bytes = AsyncMock(side_effect=AssertionError("remote should not run"))
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy_path = legacy_root / "legacy.png"
    legacy_bytes = _png((101, 102, 103))
    legacy_path.write_bytes(legacy_bytes)
    service = WordbankMediaService(
        repo,
        media_root=legacy_root,
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
    )
    await repo.create_image(
        {
            "md5": "9" * 32,
            "dhash": "7" * 16,
            "phash": "7" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(legacy_bytes),
            "hash_version": 2,
            "storage_path": str(legacy_path),
            "remote_storage_path": "r2://bucket/wordbank/media/missing.png",
            "remote_sync_status": "failed",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    events: list[str] = []

    def _capture(stage: str, *, start: float | None = None, **fields: Any) -> None:
        _ = start, fields
        events.append(stage)

    monkeypatch.setattr(media_module, "log_perf", _capture)

    loaded = await service.load_canonical_storage_bytes(1)

    assert loaded == legacy_bytes
    assert "media.load_canonical_storage_bytes.legacy_fetch" in events
    assert "media.load_canonical_storage_bytes.end" in events
    assert "media.load_canonical_storage_bytes.remote_fetch" not in events


async def test_media_backfills_local_cache_metadata_from_existing_cache_files(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    image_bytes = _png((12, 34, 56))
    cache_path = cache_root / f"{'a' * 32}.webp"
    cache_path.write_bytes(image_bytes)
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        cache_storage=LocalLruCacheWordbankMediaStorage(cache_root),
    )
    await repo.create_image(
        {
            "md5": "a" * 32,
            "dhash": "1" * 16,
            "phash": "1" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(image_bytes),
            "hash_version": 2,
            "storage_path": str(tmp_path / "legacy" / "a.webp"),
            "remote_storage_path": "r2://bucket/wordbank/media/test.webp",
            "local_cache_path": "",
            "cache_file_size": 0,
            "created_at": 1,
            "updated_at": 1,
        }
    )

    report = await service.backfill_local_cache_metadata()

    assert report["scanned"] == 1
    assert report["updated"] == 1
    assert report["missing_files"] == 0
    assert repo.images[0].local_cache_path == str(cache_path)
    assert repo.images[0].cache_file_size == len(image_bytes)


async def test_media_backfill_dry_run_reports_updates_without_writing(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    image_bytes = _png((65, 43, 21))
    cache_path = cache_root / f"{'b' * 32}.webp"
    cache_path.write_bytes(image_bytes)
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        cache_storage=LocalLruCacheWordbankMediaStorage(cache_root),
    )
    await repo.create_image(
        {
            "md5": "b" * 32,
            "dhash": "2" * 16,
            "phash": "2" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(image_bytes),
            "hash_version": 2,
            "storage_path": str(tmp_path / "legacy" / "b.webp"),
            "remote_storage_path": "r2://bucket/wordbank/media/test2.webp",
            "local_cache_path": "",
            "cache_file_size": 0,
            "created_at": 1,
            "updated_at": 1,
        }
    )

    report = await service.backfill_local_cache_metadata(dry_run=True)

    assert report["updated"] == 1
    assert report["rows"][0]["action"] == "would_update"
    assert repo.images[0].local_cache_path == ""
    assert repo.images[0].cache_file_size == 0


async def test_media_syncs_legacy_local_image_to_remote_and_keeps_it_readable(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy_path = legacy_root / "legacy.webp"
    legacy_bytes = _png((123, 45, 67))
    legacy_path.write_bytes(legacy_bytes)
    service = WordbankMediaService(
        repo,
        media_root=legacy_root,
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        legacy_storage=LocalWordbankMediaStorage(legacy_root),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
        remote_provider="r2",
    )
    await repo.create_image(
        {
            "md5": "7" * 32,
            "dhash": "6" * 16,
            "phash": "6" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(legacy_bytes),
            "hash_version": 2,
            "storage_path": str(legacy_path),
            "remote_storage_path": "",
            "remote_sync_status": "pending",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    updated = await service.sync_image_to_remote(repo.images[0], verify_remote=True)
    assert updated is not None
    assert updated.remote_sync_status == "synced"
    assert updated.remote_storage_path == f"r2://bucket/wordbank/media/{'7' * 32}.webp"

    legacy_path.unlink()
    reloaded = await service.load_canonical_storage_bytes(1)

    assert reloaded == legacy_bytes
