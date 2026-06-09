from io import BytesIO
from pathlib import Path

from PIL import Image

from src.plugins.wordbank.database.types import (
    WordbankImagePayload,
    WordbankImageRecord,
)
from src.plugins.wordbank.services.media import (
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
