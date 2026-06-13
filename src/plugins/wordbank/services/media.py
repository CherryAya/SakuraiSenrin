"""Wordbank media hashing, remote storage, and local LRU cache."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import md5
from io import BytesIO
from pathlib import Path
import re
from typing import Final, Protocol, cast
from urllib.parse import urlparse

import imagehash
from PIL import Image, ImageSequence, UnidentifiedImageError
from pybktree import BKTree

from src.lib.object_storage.types import ObjectStorageClient, StorageObject
from src.lib.utils.common import get_current_time
from src.logger import logger
from src.plugins.wordbank.database.types import (
    WordbankImagePayload,
    WordbankImageRecord,
)
from src.plugins.wordbank.debug import log_perf, perf_start
from src.plugins.wordbank.services.errors import WordbankUserError

DEFAULT_MEDIA_ROOT: Final[Path] = Path("./data/wordbank/media")
DEFAULT_MEDIA_CACHE_ROOT: Final[Path] = Path("./data/wordbank/media_cache")
IMAGE_HASH_VERSION: Final[int] = 2
IMAGE_SEARCH_DISTANCE_THRESHOLD: Final[int] = 12
WEBP_CONTENT_TYPE: Final[str] = "image/webp"
WEBP_MAX_DIMENSION: Final[int] = 16383
DEFAULT_MEDIA_EXTENSION: Final[str] = ".bin"
DEFAULT_CACHE_MAX_BYTES: Final[int] = 512 * 1024 * 1024
DEFAULT_CACHE_TRIM_TO_BYTES: Final[int] = 460 * 1024 * 1024
DEFAULT_CACHE_MAX_FILES: Final[int] = 5_000
REMOTE_SYNC_PENDING: Final[str] = "pending"
REMOTE_SYNC_SYNCED: Final[str] = "synced"
REMOTE_SYNC_FAILED: Final[str] = "failed"
EXTENSION_TO_CONTENT_TYPE: Final[dict[str, str]] = {
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": WEBP_CONTENT_TYPE,
}
_HEX_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_HEX_MD5_ANYWHERE_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])")
_URI_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


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


@dataclass(slots=True, frozen=True)
class LocalCacheEntry:
    path: str
    size: int


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
        self,
        payload: WordbankImagePayload,
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
            "/",
            1,
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
            if self.fallback is None:
                return None
            return await self.fallback.load_bytes(storage_path)
        try:
            return await self.object_storage.get_bytes(key)
        except Exception as exc:
            logger.warning(f"[Wordbank] remote media load skipped: {exc}")
            return None

    async def exists(self, storage_path: str) -> bool:
        key = self._key_from_uri(storage_path)
        if key is None:
            return False
        try:
            return await self.object_storage.exists(key)
        except Exception:
            return False

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
        images: Sequence[WordbankImageRecord],
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
        if animated_webp is not None:
            return StoredMedia(
                data=animated_webp,
                extension=".webp",
                content_type=WEBP_CONTENT_TYPE,
            )
        resized_webp = _encode_animated_webp(
            image,
            resize_to_limit=True,
        )
        if resized_webp is not None:
            return StoredMedia(
                data=resized_webp,
                extension=".webp",
                content_type=WEBP_CONTENT_TYPE,
            )
        return _fallback_original_media(image, data)

    normalized = _normalize_static_image(image)
    static_webp = _encode_static_webp(normalized)
    if static_webp is not None:
        return StoredMedia(
            data=static_webp,
            extension=".webp",
            content_type=WEBP_CONTENT_TYPE,
        )
    resized = _resize_image_to_webp_limit(normalized)
    if resized is not normalized:
        resized_webp = _encode_static_webp(resized)
        if resized_webp is not None:
            return StoredMedia(
                data=resized_webp,
                extension=".webp",
                content_type=WEBP_CONTENT_TYPE,
            )
    return _fallback_original_media(image, data)


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


def _encode_static_webp(image: Image.Image) -> bytes | None:
    try:
        buffer = BytesIO()
        image.save(
            buffer,
            format="WEBP",
            quality=82,
            method=4,
        )
        return buffer.getvalue()
    except Exception:
        return None


def _encode_animated_webp(
    image: Image.Image,
    *,
    resize_to_limit: bool = False,
) -> bytes | None:
    try:
        image.seek(0)
        frames = [
            _normalize_static_image(frame.copy())
            for frame in ImageSequence.Iterator(image)
        ]
        if not frames:
            return None
        if resize_to_limit:
            frames = [_resize_image_to_webp_limit(frame) for frame in frames]

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


def _resize_image_to_webp_limit(image: Image.Image) -> Image.Image:
    width, height = image.size
    longest_edge = max(width, height)
    if longest_edge <= WEBP_MAX_DIMENSION:
        return image
    scale = WEBP_MAX_DIMENSION / float(longest_edge)
    resized_width = max(1, int(width * scale))
    resized_height = max(1, int(height * scale))
    return image.resize((resized_width, resized_height), Image.Resampling.LANCZOS)


def _fallback_original_media(image: Image.Image, data: bytes) -> StoredMedia:
    extension = _detect_extension(image)
    content_type = _detect_content_type(image)
    logger.warning(
        "[Wordbank] webp encode failed, fallback to original media format: "
        f"{extension or DEFAULT_MEDIA_EXTENSION}"
    )
    return StoredMedia(
        data=data,
        extension=extension,
        content_type=content_type,
    )


def _detect_extension(image: Image.Image) -> str:
    format_name = str(getattr(image, "format", "") or "").upper()
    if format_name == "GIF":
        return ".gif"
    if format_name == "JPEG":
        return ".jpg"
    if format_name == "PNG":
        return ".png"
    if format_name == "WEBP":
        return ".webp"
    return DEFAULT_MEDIA_EXTENSION


def _detect_content_type(image: Image.Image) -> str:
    return EXTENSION_TO_CONTENT_TYPE.get(
        _detect_extension(image),
        "application/octet-stream",
    )


def _default_cache_root_for(media_root: Path) -> Path:
    if media_root == DEFAULT_MEDIA_ROOT:
        return DEFAULT_MEDIA_CACHE_ROOT
    return media_root.parent / f"{media_root.name}_cache"


def _is_remote_uri(path_value: str) -> bool:
    return bool(path_value and _URI_SCHEME_RE.match(path_value))


def _extension_from_storage_path(path_value: str) -> str:
    if not path_value:
        return DEFAULT_MEDIA_EXTENSION
    parsed = urlparse(path_value)
    target = parsed.path if parsed.scheme or parsed.netloc else path_value
    suffix = Path(target).suffix.lower()
    return suffix or DEFAULT_MEDIA_EXTENSION


def _content_type_from_extension(extension: str) -> str:
    return EXTENSION_TO_CONTENT_TYPE.get(extension.lower(), "application/octet-stream")


class WordbankMediaService:
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
        prewarm_local_cache: bool = True,
        similarity_threshold: int = 8,
        candidate_limit: int = 128,
    ) -> None:
        if (
            storage is not None
            and remote_storage is None
            and hasattr(
                storage,
                "save_prepared_image",
            )
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
            _default_cache_root_for(media_root),
            enabled=cache_enabled,
        )
        self.remote_required = remote_required
        self.remote_provider = remote_provider.strip().lower()
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

    async def rebuild_cache(self) -> None:
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

    def load_cache(self, images: Sequence[WordbankImageRecord]) -> None:
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

    async def ingest_image_bytes(
        self,
        data: bytes,
        *,
        keep_original: bool = False,
    ) -> WordbankImageRecord:
        start = perf_start()
        md5_hex = md5(data).hexdigest()
        existing = await self.repository.get_image_by_md5(md5_hex)
        if existing is not None:
            self._cache_image(existing)
            log_perf(
                "media.ingest_image_bytes.cache_hit",
                start=start,
                md5=md5_hex,
                canonical_id=existing.canonical_id,
                image_id=existing.id,
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

        if self.remote_storage is not None:
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
                        "图片远端存储失败",
                        key="wordbank.error.image_storage_missing",
                    ) from exc
                logger.warning(f"[Wordbank] remote media save fallback to local: {exc}")
                remote_sync_status = REMOTE_SYNC_FAILED
        elif remote_expected:
            if self.remote_required:
                raise MediaError(
                    "图片远端存储未配置",
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
        if (
            self.prewarm_local_cache
            and self.remote_storage is not None
            and image.remote_storage_path
            and self.cache_storage.enabled
        ):
            image = await self._store_cache_bytes(
                image,
                prepared.stored_media.data,
                mark_as_hit=False,
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
            storage="remote" if remote_storage_path else "local",
        )
        return image

    def _cache_image(self, image: WordbankImageRecord) -> None:
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
            and existing.id
            == self._by_canonical_id.get(
                existing.canonical_id,
                existing,
            ).id
            and existing.canonical_id != image.canonical_id
        ):
            self._by_canonical_id.pop(existing.canonical_id, None)

    def resolve_canonical_id(
        self,
        data: bytes,
        *,
        name_hints: Sequence[str] = (),
    ) -> int | None:
        hinted = self.resolve_canonical_id_from_hints(name_hints)
        if hinted is not None:
            return hinted

        raw_md5 = md5(data).hexdigest()
        existing = self._by_md5.get(raw_md5)
        if existing is not None:
            return existing.canonical_id

        fingerprint = fingerprint_image(data)
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

    def resolve_canonical_id_from_hints(self, hints: Sequence[str]) -> int | None:
        for md5_candidate in _iter_md5_candidates(hints):
            existing = self._by_md5.get(md5_candidate)
            if existing is not None:
                return existing.canonical_id
        return None

    async def load_storage_bytes(self, image: WordbankImageRecord) -> bytes | None:
        return await self.load_canonical_storage_bytes(image.canonical_id)

    def _get_remote_load_lock(self, canonical_image_id: int) -> asyncio.Lock:
        if canonical_image_id not in self._remote_load_locks:
            self._remote_load_locks[canonical_image_id] = asyncio.Lock()
        return self._remote_load_locks[canonical_image_id]

    async def load_canonical_storage_bytes(
        self,
        canonical_image_id: int,
    ) -> bytes | None:
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

        cached = await self._load_from_local_cache(image)
        if cached is not None:
            log_perf(
                "media.load_canonical_storage_bytes.hit",
                start=start,
                canonical_image_id=canonical_image_id,
                source="local_cache",
                bytes=len(cached),
            )
            return cached

        async with self._get_remote_load_lock(canonical_image_id):
            refreshed = self._by_canonical_id.get(canonical_image_id, image)
            cached = await self._load_from_local_cache(refreshed)
            if cached is not None:
                log_perf(
                    "media.load_canonical_storage_bytes.hit",
                    start=start,
                    canonical_image_id=canonical_image_id,
                    source="local_cache_after_lock",
                    bytes=len(cached),
                )
                return cached

            remote_bytes = await self._load_from_remote_storage(refreshed)
            if remote_bytes is not None:
                log_perf(
                    "media.load_canonical_storage_bytes.hit",
                    start=start,
                    canonical_image_id=canonical_image_id,
                    source="remote_storage",
                    bytes=len(remote_bytes),
                )
                return remote_bytes

            legacy_bytes = await self._load_from_legacy_storage(refreshed)
            log_perf(
                "media.load_canonical_storage_bytes.hit",
                start=start,
                canonical_image_id=canonical_image_id,
                source="legacy_storage" if legacy_bytes is not None else "miss",
                bytes=len(legacy_bytes) if legacy_bytes is not None else 0,
            )
            return legacy_bytes

    async def _load_from_local_cache(self, image: WordbankImageRecord) -> bytes | None:
        if not image.local_cache_path:
            return None
        cached = await self.cache_storage.load_cached_bytes(image.local_cache_path)
        if cached is None:
            updated = await self.repository.update_image_cache_metadata(
                image.id,
                local_cache_path="",
                cache_file_size=0,
            )
            if updated is not None:
                self._cache_image(updated)
            return None
        await self.cache_storage.touch_cache_entry(image.local_cache_path)
        updated = await self.repository.update_image_cache_metadata(
            image.id,
            local_cache_path=image.local_cache_path,
            cache_file_size=max(image.cache_file_size, len(cached)),
            last_accessed_at=get_current_time(),
            cache_last_hit_at=get_current_time(),
        )
        if updated is not None:
            self._cache_image(updated)
        return cached

    async def _load_from_remote_storage(
        self,
        image: WordbankImageRecord,
    ) -> bytes | None:
        if self.remote_storage is None or not image.remote_storage_path:
            return None
        remote_bytes = await self.remote_storage.load_bytes(image.remote_storage_path)
        if remote_bytes is None:
            await self._mark_remote_sync_failed(image)
            return None
        await self._touch_last_access(image.id)
        updated_image = self._by_id.get(image.id, image)
        if self.cache_storage.enabled:
            updated_image = await self._store_cache_bytes(
                updated_image,
                remote_bytes,
                mark_as_hit=False,
            )
        return remote_bytes

    async def _load_from_legacy_storage(
        self,
        image: WordbankImageRecord,
    ) -> bytes | None:
        if not image.storage_path or _is_remote_uri(image.storage_path):
            return None
        legacy_bytes = await self.legacy_storage.load_bytes(image.storage_path)
        if legacy_bytes is None:
            return None
        updated = await self._touch_last_access(image.id)
        if updated is not None:
            image = updated
        return legacy_bytes

    async def _touch_last_access(self, image_id: int) -> WordbankImageRecord | None:
        current = self._by_id.get(image_id)
        if current is None:
            return None
        updated = await self.repository.update_image_cache_metadata(
            image_id,
            local_cache_path=current.local_cache_path,
            cache_file_size=current.cache_file_size,
            last_accessed_at=get_current_time(),
            cache_last_hit_at=current.cache_last_hit_at,
        )
        if updated is not None:
            self._cache_image(updated)
        return updated

    async def _store_cache_bytes(
        self,
        image: WordbankImageRecord,
        data: bytes,
        *,
        mark_as_hit: bool,
    ) -> WordbankImageRecord:
        cached = await self.cache_storage.store_cached_bytes(
            data,
            md5_hex=image.md5,
            extension=_extension_from_storage_path(
                image.remote_storage_path or image.storage_path
            ),
        )
        if cached is None:
            return image
        now = get_current_time()
        updated = await self.repository.update_image_cache_metadata(
            image.id,
            local_cache_path=cached.path,
            cache_file_size=cached.size,
            last_accessed_at=now,
            cache_last_hit_at=now if mark_as_hit else image.cache_last_hit_at,
        )
        if updated is None:
            return image
        self._cache_image(updated)
        await self.evict_local_cache_if_needed()
        return updated

    async def evict_local_cache_if_needed(self) -> int:
        if not self.cache_storage.enabled:
            return 0
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
            return removed

    async def reconcile_local_cache(self) -> dict[str, int]:
        if not self.cache_storage.enabled:
            return {"cleared_metadata": 0, "removed_orphans": 0, "evicted": 0}
        async with self._cache_maintenance_lock:
            cached_images = await self.repository.list_cached_images()
            cleared_metadata = 0
            known_paths: set[str] = set()
            for image in cached_images:
                if not image.local_cache_path:
                    continue
                path_exists = await asyncio.to_thread(
                    Path(image.local_cache_path).is_file
                )
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
        return {
            "cleared_metadata": cleared_metadata,
            "removed_orphans": removed_orphans,
            "evicted": evicted,
        }

    async def rebuild_cache_metadata(
        self,
        image: WordbankImageRecord,
    ) -> WordbankImageRecord:
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
        self,
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

        extension = _extension_from_storage_path(
            image.remote_storage_path or image.storage_path
        )
        content_type = _content_type_from_extension(extension)
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

        storage_path = (
            image.storage_path
            if image.storage_path and not _is_remote_uri(image.storage_path)
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
        return updated

    async def list_remote_objects_by_key(self) -> dict[str, StorageObject]:
        if self.remote_storage is None:
            return {}
        objects = await self.remote_storage.list_objects()
        return {item.key: item for item in objects}

    def build_expected_remote_key(self, image: WordbankImageRecord) -> str | None:
        if self.remote_storage is None:
            return None
        extension = _extension_from_storage_path(
            image.remote_storage_path or image.storage_path
        )
        return self.remote_storage.build_key(image.md5, extension)

    async def mark_image_remote_synced(
        self,
        image: WordbankImageRecord,
        remote_object: StorageObject,
        *,
        synced_at: int | None = None,
    ) -> WordbankImageRecord | None:
        storage_path = (
            image.storage_path
            if image.storage_path and not _is_remote_uri(image.storage_path)
            else remote_object.uri
        )
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
        self,
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
        return await self.mark_image_remote_synced(
            image,
            remote_object,
            synced_at=synced_at,
        )

    async def retry_remote_sync(
        self,
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
        synced = 0
        failed = 0
        skipped = 0
        for image in images:
            working_image = image
            if rebuild_cache_metadata:
                working_image = await self.rebuild_cache_metadata(working_image)
            updated = await self.sync_image_to_remote(
                working_image,
                verify_remote=verify_remote,
            )
            if updated is None:
                skipped += 1
                continue
            if updated.remote_sync_status == REMOTE_SYNC_SYNCED:
                synced += 1
            else:
                failed += 1
        return {
            "scanned": len(images),
            "synced": synced,
            "failed": failed,
            "skipped": skipped,
        }

    async def run_scheduled_maintenance(
        self,
        *,
        batch_size: int = 200,
    ) -> dict[str, int]:
        cache_report = await self.reconcile_local_cache()
        sync_report = await self.retry_remote_sync(limit=batch_size)
        return {
            **cache_report,
            **sync_report,
        }

    async def _load_source_bytes_for_remote_sync(
        self,
        image: WordbankImageRecord,
    ) -> bytes | None:
        if image.storage_path and not _is_remote_uri(image.storage_path):
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

    def _remote_expected(self) -> bool:
        return self.remote_provider in {"github", "r2"}

    async def _mark_remote_sync_failed(
        self,
        image: WordbankImageRecord,
    ) -> WordbankImageRecord | None:
        updated = await self.repository.update_image_remote_sync(
            image.id,
            remote_storage_path=image.remote_storage_path,
            remote_sync_status=REMOTE_SYNC_FAILED,
            remote_synced_at=image.remote_synced_at,
            remote_etag=image.remote_etag,
            remote_object_size=image.remote_object_size,
        )
        if updated is not None:
            self._cache_image(updated)
        return updated


def _iter_md5_candidates(values: Sequence[str]) -> tuple[str, ...]:
    candidates: list[str] = []
    seen: set[str] = set()
    for value in values:
        for candidate in _extract_md5_candidates(value):
            normalized = candidate.lower()
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(normalized)
    return tuple(candidates)


def _extract_md5_candidates(value: str) -> tuple[str, ...]:
    raw_value = value.strip()
    if not raw_value:
        return ()

    parsed = urlparse(raw_value)
    path_value = parsed.path if parsed.scheme or parsed.netloc else raw_value
    path = Path(path_value)
    raw_values = (
        raw_value,
        path_value,
        path.name.strip(),
        path.stem.strip(),
    )
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        if not raw:
            continue
        for matched in _HEX_MD5_ANYWHERE_RE.findall(raw):
            normalized = matched.lower()
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(normalized)
        compact = re.sub(r"[^0-9a-fA-F]", "", raw)
        if _HEX_MD5_RE.fullmatch(compact):
            normalized = compact.lower()
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(normalized)
    return tuple(candidates)
