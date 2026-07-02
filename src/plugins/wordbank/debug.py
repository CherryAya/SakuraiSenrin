"""Wordbank performance debug helpers."""

from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter
from typing import Any

from nonebot.adapters.onebot.v11 import Message

from src.logger import logger
from src.plugins.wordbank.message_model import MessageShape, shape_to_summary_text

_DEBUG_TEXT_LIMIT = 48


def perf_start() -> float:
    return perf_counter()


def elapsed_ms(start: float) -> float:
    return (perf_counter() - start) * 1000


def log_perf(stage: str, *, start: float | None = None, **fields: Any) -> None:
    parts: list[str] = []
    if start is not None:
        parts.append(f"elapsed_ms={elapsed_ms(start):.2f}")
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    suffix = f" | {' '.join(parts)}" if parts else ""
    logger.debug(f"[Wordbank][perf] {stage}{suffix}")


def describe_message_segments(message: Message | None) -> str:
    if not isinstance(message, Message):
        return f"type={type(message).__name__}"
    if len(message) == 0:
        return "segments=0"
    parts: list[str] = []
    for index, segment in enumerate(message):
        if segment.type == "text":
            text = _compact_debug_text(str(segment.data.get("text", "")))
            parts.append(f"{index}:text({text})")
            continue
        if segment.type == "image":
            url = "1" if str(segment.data.get("url", "")).strip() else "0"
            file_key = _compact_debug_text(str(segment.data.get("file", "")))
            parts.append(f"{index}:image(url={url},file={file_key})")
            continue
        if segment.type == "at":
            target = _compact_debug_text(str(segment.data.get("qq", "")))
            parts.append(f"{index}:at({target})")
            continue
        if segment.type == "forward":
            source_id = _compact_debug_text(str(segment.data.get("id", "")))
            parts.append(f"{index}:forward({source_id})")
            continue
        parts.append(f"{index}:{segment.type}")
    return "segments=" + ",".join(parts)


def describe_shape(shape: MessageShape | None) -> str:
    if not isinstance(shape, MessageShape):
        return f"type={type(shape).__name__}"
    summary = _compact_debug_text(shape_to_summary_text(shape))
    kinds = ",".join(atom.kind for atom in shape.atoms) or "-"
    return (
        f"atoms={len(shape.atoms)} kinds={kinds} "
        f"empty={shape.is_empty()} summary={summary}"
    )


def describe_batch_errors(
    errors: Sequence[str],
    *,
    limit: int = 5,
) -> str:
    if not errors:
        return "-"
    clipped = [_compact_debug_text(error) for error in errors[: max(1, limit)]]
    suffix = "" if len(errors) <= limit else f"...(+{len(errors) - limit})"
    return " | ".join(clipped) + suffix


def _compact_debug_text(text: str) -> str:
    normalized = text.replace("\n", "\\n").strip()
    if not normalized:
        return "-"
    if len(normalized) <= _DEBUG_TEXT_LIMIT:
        return normalized
    return normalized[: _DEBUG_TEXT_LIMIT - 3] + "..."
