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
from PIL import Image, ImageSequence, UnidentifiedImageError
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
WEBP_CONTENT_TYPE: Final[str] = "image/webp"
DEFAULT_MEDIA_EXTENSION: Final[str] = ".bin"
EXTENSION_TO_CONTENT_TYPE: Final[dict[str, str]] = {
    ".gif": "image/gif",
    ".png": "image/png",
    ".webp": WEBP_CONTENT_TYPE,
}


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


@dataclass(slots=True, frozen=True)
class StoredMedia:
    data: bytes
    extension: str
    content_type: str


@dataclass(slots=True, frozen=True)
class PreparedImage:
    original_data: bytes
    fingerprint: ImageFingerprint
    stored_media: StoredMedia


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
        fallback: WordbankMediaStorage,
        key_prefix: str = "wordbank/media",
    ) -> None:
        self.object_storage = object_storage
        self.fallback = fallback
        self.key_prefix = key_prefix.strip("/")

    async def save_image(
        self,
        prepared: PreparedImage,
        *,
        md5_hex: str,
        keep_original: bool,
    ) -> str:
        key = f"{self.key_prefix}/{md5_hex}{prepared.stored_media.extension}"
        try:
            stored = await self.object_storage.put_bytes(
                key,
                prepared.stored_media.data,
                content_type=prepared.stored_media.content_type,
            )
            if keep_original:
                await self.object_storage.put_bytes(
                    f"{self.key_prefix}/{md5_hex}.source",
                    prepared.original_data,
                    content_type="application/octet-stream",
                )
            return stored.uri
        except Exception as exc:
            logger.warning(f"[Wordbank] remote media save fallback to local: {exc}")
            return await self.fallback.save_image(
                prepared,
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


R2WordbankMediaStorage = ObjectStorageWordbankMediaStorage


def hamming_distance(left: str, right: str) -> int:
    return (int(left or "0", 16) ^ int(right or "0", 16)).bit_count()


def fingerprint_image(data: bytes) -> ImageFingerprint:
    try:
        return prepare_image_fingerprint(data)
    except UnidentifiedImageError as exc:
        raise MediaError(
            "无法识别图片内容",
            key="wordbank.error.image_unrecognized",
        ) from exc


def prepare_image_fingerprint(data: bytes) -> ImageFingerprint:
    with Image.open(BytesIO(data)) as image:
        return _build_fingerprint(image, data)


def prepare_image_bytes(data: bytes) -> PreparedImage:
    with Image.open(BytesIO(data)) as image:
        fingerprint = _build_fingerprint(image, data)
        stored_media = _build_stored_media(image, data)
        return PreparedImage(
            original_data=data,
            fingerprint=fingerprint,
            stored_media=stored_media,
        )


def _build_fingerprint(image: Image.Image, data: bytes) -> ImageFingerprint:
    representative = _extract_representative_frame(image)
    width, height = representative.size
    return ImageFingerprint(
        md5=md5(data).hexdigest(),
        dhash=str(imagehash.dhash(representative, hash_size=8)),
        phash=str(imagehash.phash(representative, hash_size=8)),
        width=width,
        height=height,
        file_size=len(data),
    )


def _build_stored_media(image: Image.Image, data: bytes) -> StoredMedia:
    if _is_animated(image):
        animated_webp = _encode_animated_webp(image)
        if animated_webp is None:
            raise MediaError(
                "无法转码为 animated webp",
                key="wordbank.error.image_prepare_failed",
                reason="无法转码为 animated webp",
            )
        return StoredMedia(
            data=animated_webp,
            extension=".webp",
            content_type=WEBP_CONTENT_TYPE,
        )

    buffer = BytesIO()
    _normalize_static_image(image).save(
        buffer,
        format="WEBP",
        quality=82,
        method=4,
    )
    return StoredMedia(
        data=buffer.getvalue(),
        extension=".webp",
        content_type=WEBP_CONTENT_TYPE,
    )


def _extract_representative_frame(image: Image.Image) -> Image.Image:
    if _is_animated(image):
        image.seek(0)
    return _normalize_static_image(image)


def _normalize_static_image(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"}:
        return image.convert("RGBA")
    if image.mode == "P":
        if "transparency" in image.info:
            return image.convert("RGBA")
        return image.convert("RGB")
    if image.mode == "RGB":
        return image.copy()
    return image.convert("RGB")


def _encode_animated_webp(image: Image.Image) -> bytes | None:
    try:
        image.seek(0)
        frames = [
            _normalize_static_image(frame.copy())
            for frame in ImageSequence.Iterator(image)
        ]
        if not frames:
            return None

        durations = _collect_frame_durations(image, len(frames))
        loop = int(image.info.get("loop", 0) or 0)
        buffer = BytesIO()
        first, *rest = frames
        first.save(
            buffer,
            format="WEBP",
            save_all=True,
            append_images=rest,
            duration=durations,
            loop=loop,
            quality=82,
            method=4,
        )
        return buffer.getvalue()
    except Exception:
        return None


def _collect_frame_durations(image: Image.Image, frame_count: int) -> list[int]:
    durations: list[int] = []
    for frame in ImageSequence.Iterator(image):
        duration = int(
            frame.info.get("duration", image.info.get("duration", 100)) or 100
        )
        durations.append(max(duration, 1))
    if len(durations) < frame_count:
        durations.extend([100] * (frame_count - len(durations)))
    return durations[:frame_count]


def _is_animated(image: Image.Image) -> bool:
    return bool(
        getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1
    )


def _detect_extension(image: Image.Image) -> str:
    format_name = str(getattr(image, "format", "") or "").upper()
    if format_name == "GIF":
        return ".gif"
    if format_name == "PNG":
        return ".png"
    if format_name == "WEBP":
        return ".webp"
    return DEFAULT_MEDIA_EXTENSION


def _detect_content_type(image: Image.Image) -> str:
    return EXTENSION_TO_CONTENT_TYPE.get(
        _detect_extension(image), "application/octet-stream"
    )


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
        md5_hex = md5(data).hexdigest()
        existing = await self.repository.get_image_by_md5(md5_hex)
        if existing is not None:
            self._cache_image(existing)
            return existing

        prepared = prepare_image_bytes(data)
        fingerprint = prepared.fingerprint
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
            prepared,
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
