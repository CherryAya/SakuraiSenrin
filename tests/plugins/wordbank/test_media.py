import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
import pytest

from src.lib.object_storage import StorageObject
from src.plugins.wordbank.database.types import (
    WordbankImagePayload,
    WordbankImageRecord,
)
from src.plugins.wordbank.services import media as media_module
from src.plugins.wordbank.services.media import (
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
