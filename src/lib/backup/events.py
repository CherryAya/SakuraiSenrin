"""Typed backup lifecycle events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import inspect
from pathlib import Path

from src.logger import logger


@dataclass(slots=True, frozen=True)
class BackupEvent:
    run_id: str
    plan_id: str
    started_at: int
    finished_at: int | None = None
    manifest_path: Path | None = None
    restic_snapshot_id: str | None = None
    files_count: int = 0
    bytes_total: int = 0
    error: str | None = None


class BackupStarted(BackupEvent):
    pass


class BackupSucceeded(BackupEvent):
    pass


class BackupFailed(BackupEvent):
    pass


class BackupSkipped(BackupEvent):
    pass


BackupCallback = Callable[[BackupEvent], Awaitable[None] | None]

_callbacks: list[BackupCallback] = []


def register_backup_callback(callback: BackupCallback) -> None:
    if callback not in _callbacks:
        _callbacks.append(callback)


async def dispatch_backup_event(event: BackupEvent) -> None:
    for callback in tuple(_callbacks):
        try:
            result = callback(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.warning(f"[Backup] callback failed: {exc}")
