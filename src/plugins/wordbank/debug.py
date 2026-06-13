"""Wordbank performance debug helpers."""

from __future__ import annotations

from time import perf_counter
from typing import Any

from src.logger import logger


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
