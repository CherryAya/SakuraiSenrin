"""Wordbank media constants, models, and image helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import md5
from io import BytesIO
from pathlib import Path
import re
from typing import Final
from urllib.parse import urlparse

import imagehash
from PIL import Image, ImageSequence, UnidentifiedImageError

from src.lib.i18n.runtime import tr
from src.logger import logger
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


def hamming_distance(left: str, right: str) -> int:
    return (int(left or "0", 16) ^ int(right or "0", 16)).bit_count()


def fingerprint_image(data: bytes) -> ImageFingerprint:
    try:
        return prepare_image_fingerprint(data)
    except UnidentifiedImageError as exc:
        raise MediaError(
            tr("zh-CN", "wordbank.error.image_unrecognized"),
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
        resized_webp = _encode_animated_webp(image, resize_to_limit=True)
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
        image.save(buffer, format="WEBP", quality=82, method=4)
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
        frames = [_normalize_static_image(frame.copy()) for frame in ImageSequence.Iterator(image)]
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
        duration = int(frame.info.get("duration", image.info.get("duration", 100)) or 100)
        durations.append(max(duration, 1))
    if len(durations) < frame_count:
        durations.extend([100] * (frame_count - len(durations)))
    return durations[:frame_count]


def _is_animated(image: Image.Image) -> bool:
    return bool(getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1)


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
    return StoredMedia(data=data, extension=extension, content_type=content_type)


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
    return EXTENSION_TO_CONTENT_TYPE.get(_detect_extension(image), "application/octet-stream")


def default_cache_root_for(media_root: Path) -> Path:
    if media_root == DEFAULT_MEDIA_ROOT:
        return DEFAULT_MEDIA_CACHE_ROOT
    return media_root.parent / f"{media_root.name}_cache"


def is_remote_uri(path_value: str) -> bool:
    return bool(path_value and _URI_SCHEME_RE.match(path_value))


def extension_from_storage_path(path_value: str) -> str:
    if not path_value:
        return DEFAULT_MEDIA_EXTENSION
    parsed = urlparse(path_value)
    target = parsed.path if parsed.scheme or parsed.netloc else path_value
    suffix = Path(target).suffix.lower()
    return suffix or DEFAULT_MEDIA_EXTENSION


def content_type_from_extension(extension: str) -> str:
    return EXTENSION_TO_CONTENT_TYPE.get(extension.lower(), "application/octet-stream")


def iter_md5_candidates(values: Sequence[str]) -> tuple[str, ...]:
    candidates: list[str] = []
    seen: set[str] = set()
    for value in values:
        for candidate in extract_md5_candidates(value):
            normalized = candidate.lower()
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(normalized)
    return tuple(candidates)


def extract_md5_candidates(value: str) -> tuple[str, ...]:
    raw_value = value.strip()
    if not raw_value:
        return ()

    parsed = urlparse(raw_value)
    path_value = parsed.path if parsed.scheme or parsed.netloc else raw_value
    path = Path(path_value)
    raw_values = (raw_value, path_value, path.name.strip(), path.stem.strip())
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
