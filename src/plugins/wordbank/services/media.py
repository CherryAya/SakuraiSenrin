"""Wordbank media hashing and local storage."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import md5
from io import BytesIO
from pathlib import Path
from typing import Final, Protocol

import imagehash
from PIL import Image, UnidentifiedImageError
from pybktree import BKTree

from src.lib.object_storage import ObjectStorageClient
from src.lib.utils.common import get_current_time
from src.logger import logger
from src.plugins.wordbank.database.types import (
    WordbankImagePayload,
    WordbankImageRecord,
)
from src.plugins.wordbank.services.errors import WordbankUserError

DEFAULT_MEDIA_ROOT: Final[Path] = Path("./data/wordbank/media")
IMAGE_HASH_VERSION: Final[int] = 2
IMAGE_SEARCH_DISTANCE_THRESHOLD: Final[int] = 12


class MediaError(WordbankUserError):
    """Raised when an image cannot be processed for wordbank media storage."""


@dataclass(slots=True, frozen=True)
class ImageFingerprint:
    md5: str
    dhash: str
    phash: str
    width: int
    height: int
    file_size: int
    hash_version: int = IMAGE_HASH_VERSION


@dataclass(slots=True, frozen=True)
class CanonicalImageMatch:
    canonical_id: int
    score: float
    dhash_distance: int
    phash_distance: int | None = None


class MediaRepository(Protocol):
    async def list_images(self) -> list[WordbankImageRecord]: ...

    async def get_image_by_md5(self, md5: str) -> WordbankImageRecord | None: ...

    async def get_image_candidates(
        self,
        dhash_prefix: str,
        *,
        limit: int = 128,
    ) -> list[WordbankImageRecord]: ...

    async def create_image(
        self,
        payload: WordbankImagePayload,
    ) -> WordbankImageRecord: ...


class WordbankMediaStorage(Protocol):
    async def save_image(
        self,
        data: bytes,
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
        data: bytes,
        *,
        md5_hex: str,
        keep_original: bool,
    ) -> str:
        return await asyncio.to_thread(
            self._save_image,
            data,
            md5_hex=md5_hex,
            keep_original=keep_original,
        )

    def _save_image(
        self,
        data: bytes,
        *,
        md5_hex: str,
        keep_original: bool,
    ) -> str:
        self.media_root.mkdir(parents=True, exist_ok=True)
        webp_path = self.media_root / f"{md5_hex}.webp"
        with Image.open(BytesIO(data)) as image:
            image.save(webp_path, format="WEBP", quality=82, method=4)
        if keep_original:
            original_path = self.media_root / f"{md5_hex}.source"
            original_path.write_bytes(data)
        return str(webp_path)

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
        fallback: WordbankMediaStorage,
        key_prefix: str = "wordbank/media",
    ) -> None:
        self.object_storage = object_storage
        self.fallback = fallback
        self.key_prefix = key_prefix.strip("/")

    async def save_image(
        self,
        data: bytes,
        *,
        md5_hex: str,
        keep_original: bool,
    ) -> str:
        key = f"{self.key_prefix}/{md5_hex}.webp"
        try:
            stored = await self.object_storage.put_bytes(
                key,
                self._to_webp(data),
                content_type="image/webp",
            )
            if keep_original:
                await self.object_storage.put_bytes(
                    f"{self.key_prefix}/{md5_hex}.source",
                    data,
                    content_type="application/octet-stream",
                )
            return stored.uri
        except Exception as exc:
            logger.warning(f"[Wordbank] remote media save fallback to local: {exc}")
            return await self.fallback.save_image(
                data,
                md5_hex=md5_hex,
                keep_original=keep_original,
            )

    async def load_bytes(self, storage_path: str) -> bytes | None:
        scheme = f"{self.object_storage.provider}://"
        if not storage_path.startswith(scheme):
            return await self.fallback.load_bytes(storage_path)
        try:
            key = storage_path.removeprefix(scheme).split("/", 1)[1]
            return await self.object_storage.get_bytes(key)
        except Exception as exc:
            logger.warning(f"[Wordbank] remote media load skipped: {exc}")
            return None

    def _to_webp(self, data: bytes) -> bytes:
        buffer = BytesIO()
        with Image.open(BytesIO(data)) as image:
            image.save(buffer, format="WEBP", quality=82, method=4)
        return buffer.getvalue()


R2WordbankMediaStorage = ObjectStorageWordbankMediaStorage


def hamming_distance(left: str, right: str) -> int:
    return (int(left or "0", 16) ^ int(right or "0", 16)).bit_count()


def fingerprint_image(data: bytes) -> ImageFingerprint:
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            width, height = image.size
            return ImageFingerprint(
                md5=md5(data).hexdigest(),
                dhash=str(imagehash.dhash(image, hash_size=8)),
                phash=str(imagehash.phash(image, hash_size=8)),
                width=width,
                height=height,
                file_size=len(data),
            )
    except UnidentifiedImageError as exc:
        raise MediaError(
            "无法识别图片内容",
            key="wordbank.error.image_unrecognized",
        ) from exc


class WordbankMediaService:
    def __init__(
        self,
        repository: MediaRepository,
        *,
        media_root: Path = DEFAULT_MEDIA_ROOT,
        storage: WordbankMediaStorage | None = None,
        similarity_threshold: int = 8,
        candidate_limit: int = 128,
    ) -> None:
        self.repository = repository
        self.media_root = media_root
        self.storage = storage or LocalWordbankMediaStorage(media_root)
        self.similarity_threshold = similarity_threshold
        self.candidate_limit = candidate_limit
        self._by_md5: dict[str, WordbankImageRecord] = {}
        self._by_dhash_prefix: dict[str, list[WordbankImageRecord]] = {}
        self._by_canonical_id: dict[int, WordbankImageRecord] = {}
        self._canonical_ids_by_dhash: dict[str, tuple[int, ...]] = {}
        self._canonical_hash_image: dict[int, WordbankImageRecord] = {}
        self._dhash_tree: BKTree | None = None

    async def rebuild_cache(self) -> None:
        images = await self.repository.list_images()
        self.load_cache(images)

    def load_cache(self, images: Sequence[WordbankImageRecord]) -> None:
        by_prefix: dict[str, list[WordbankImageRecord]] = defaultdict(list)
        by_md5: dict[str, WordbankImageRecord] = {}
        by_canonical_id: dict[int, WordbankImageRecord] = {}
        canonical_hash_image: dict[int, WordbankImageRecord] = {}
        canonical_ids_by_dhash: dict[str, set[int]] = defaultdict(set)
        for image in images:
            by_md5[image.md5] = image
            by_prefix[image.dhash[:4]].append(image)
            by_canonical_id.setdefault(image.canonical_id, image)
            current = canonical_hash_image.get(image.canonical_id)
            if current is None or image.hash_version > current.hash_version:
                canonical_hash_image[image.canonical_id] = image
        for canonical_id, image in canonical_hash_image.items():
            canonical_ids_by_dhash[image.dhash].add(canonical_id)
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

    async def ingest_image_bytes(
        self,
        data: bytes,
        *,
        keep_original: bool = False,
    ) -> WordbankImageRecord:
        fingerprint = fingerprint_image(data)
        existing = await self.repository.get_image_by_md5(fingerprint.md5)
        if existing is not None:
            self._cache_image(existing)
            return existing

        canonical_id: int | None = None
        for candidate in await self.repository.get_image_candidates(
            fingerprint.dhash[:4],
            limit=self.candidate_limit,
        ):
            if (
                hamming_distance(fingerprint.dhash, candidate.dhash)
                <= self.similarity_threshold
            ):
                canonical_id = candidate.canonical_id
                break

        storage_path = await self.storage.save_image(
            data,
            md5_hex=fingerprint.md5,
            keep_original=keep_original,
        )
        now = get_current_time()
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
                "created_at": now,
                "updated_at": now,
            }
        )
        self._cache_image(image)
        return image

    def _cache_image(self, image: WordbankImageRecord) -> None:
        self._by_md5[image.md5] = image
        bucket = self._by_dhash_prefix.setdefault(image.dhash[:4], [])
        if all(item.id != image.id for item in bucket):
            bucket.append(image)
        self._by_canonical_id.setdefault(image.canonical_id, image)
        current = self._canonical_hash_image.get(image.canonical_id)
        if current is None or image.hash_version > current.hash_version:
            self._canonical_hash_image[image.canonical_id] = image
            known_dhash = image.dhash in self._canonical_ids_by_dhash
            canonical_ids = set(self._canonical_ids_by_dhash.get(image.dhash, ()))
            canonical_ids.add(image.canonical_id)
            self._canonical_ids_by_dhash[image.dhash] = tuple(sorted(canonical_ids))
            if self._dhash_tree is None:
                self._dhash_tree = BKTree(hamming_distance, [image.dhash])
            elif not known_dhash:
                self._dhash_tree.add(image.dhash)

    def resolve_canonical_id(self, data: bytes) -> int | None:
        fingerprint = fingerprint_image(data)
        existing = self._by_md5.get(fingerprint.md5)
        if existing is not None:
            return existing.canonical_id

        for candidate in self._by_dhash_prefix.get(fingerprint.dhash[:4], [])[
            : self.candidate_limit
        ]:
            if (
                hamming_distance(fingerprint.dhash, candidate.dhash)
                <= self.similarity_threshold
            ):
                return candidate.canonical_id
        return None

    def search_similar_images(
        self,
        data: bytes,
        *,
        limit: int = 32,
        distance_threshold: int = IMAGE_SEARCH_DISTANCE_THRESHOLD,
    ) -> list[CanonicalImageMatch]:
        fingerprint = fingerprint_image(data)
        existing = self._by_md5.get(fingerprint.md5)
        if existing is not None:
            return [
                CanonicalImageMatch(
                    canonical_id=existing.canonical_id,
                    score=1.0,
                    dhash_distance=0,
                    phash_distance=0,
                )
            ]
        if self._dhash_tree is None:
            return []

        matches: list[CanonicalImageMatch] = []
        for dhash_distance, matched_dhash in self._dhash_tree.find(
            fingerprint.dhash,
            distance_threshold,
        ):
            for canonical_id in self._canonical_ids_by_dhash.get(matched_dhash, ()):
                image = self._canonical_hash_image.get(canonical_id)
                if image is None:
                    continue
                score = max(
                    0.0,
                    1.0 - (dhash_distance / max(distance_threshold, 1)),
                )
                phash_distance: int | None = None
                if image.hash_version >= IMAGE_HASH_VERSION:
                    phash_distance = hamming_distance(fingerprint.phash, image.phash)
                    score = min(
                        1.0,
                        score
                        + max(
                            0.0,
                            1.0 - (phash_distance / max(distance_threshold * 2, 1)),
                        )
                        * 0.25,
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
            key=lambda item: (
                item.score,
                -(item.phash_distance or 0),
                -item.dhash_distance,
                -item.canonical_id,
            ),
            reverse=True,
        )
        return matches[:limit]

    async def load_storage_bytes(self, image: WordbankImageRecord) -> bytes | None:
        return await self.storage.load_bytes(image.storage_path)

    async def load_canonical_storage_bytes(
        self,
        canonical_image_id: int,
    ) -> bytes | None:
        image = self._by_canonical_id.get(canonical_image_id)
        if image is None:
            return None
        return await self.load_storage_bytes(image)
