from io import BytesIO
from pathlib import Path

from PIL import Image

from src.lib.object_storage import StorageObject
from src.plugins.wordbank.database.types import (
    WordbankImagePayload,
    WordbankImageRecord,
)
from src.plugins.wordbank.services.media import (
    LocalWordbankMediaStorage,
    R2WordbankMediaStorage,
    WordbankMediaService,
    fingerprint_image,
    hamming_distance,
)


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
    return buffer.getvalue()


class _ImageRepo:
    def __init__(self) -> None:
        self.images: list[WordbankImageRecord] = []

    async def list_images(self) -> list[WordbankImageRecord]:
        return list(self.images)

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
            storage_path=payload["storage_path"],
        )
        self.images.append(image)
        return image


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


class _ObjectStorage:
    provider = "r2"
    available = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.objects: dict[str, bytes] = {}

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
        return StorageObject(
            provider="r2",
            bucket="bucket",
            key=key,
            uri=f"r2://bucket/{key}",
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
        data,
        md5_hex=fingerprint.md5,
        keep_original=False,
    )
    loaded = await media_storage.load_bytes(storage_path)

    assert storage_path == f"r2://bucket/wordbank/media/{fingerprint.md5}.webp"
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
        data,
        md5_hex=fingerprint.md5,
        keep_original=False,
    )

    assert storage_path == str(tmp_path / f"{fingerprint.md5}.webp")
    assert (tmp_path / f"{fingerprint.md5}.webp").is_file()
