from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import sqlite3
from typing import cast

import pytest

from src.lib.db.backup import BackupSource, SQLiteSnapshotter
from src.lib.db.connectors import BaseDB
from src.services import backup as backup_module
from src.services.backup import (
    BackupPlan,
    BackupRetention,
    BackupService,
    ResticConfig,
)


def _create_sqlite(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO item (name) VALUES ('ok')")
        conn.commit()


async def test_sqlite_snapshotter_creates_readable_wal_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _create_sqlite(source)

    file_record = await SQLiteSnapshotter().snapshot(
        BackupSource(namespace="core_db", kind="core", path=source),
        tmp_path / "snapshot",
    )

    with sqlite3.connect(file_record.snapshot_path) as conn:
        rows = conn.execute("SELECT name FROM item").fetchall()

    assert rows == [("ok",)]
    assert file_record.sha256
    assert file_record.size > 0


async def test_backup_service_runs_restic_and_dispatches_success_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    _create_sqlite(source)
    events: list[object] = []

    class _DB:
        def iter_backup_sources(self) -> list[BackupSource]:
            return [BackupSource(namespace="core_db", kind="core", path=source)]

    class _Process:
        returncode = 0

        def __init__(self, stdout: bytes) -> None:
            self._stdout = stdout

        async def communicate(self) -> tuple[bytes, bytes]:
            return self._stdout, b""

    async def _create_subprocess_exec(*args: object, **kwargs: object) -> _Process:
        _ = kwargs
        if args[1] == "backup":
            return _Process(b'{"message_type":"summary","snapshot_id":"snap123"}\n')
        return _Process(b"")

    async def _dispatch(event: object) -> None:
        events.append(event)

    monkeypatch.setattr(
        backup_module.shutil,
        "which",
        lambda command: f"/bin/{command}",
    )
    monkeypatch.setattr(
        backup_module.asyncio,
        "create_subprocess_exec",
        _create_subprocess_exec,
    )
    monkeypatch.setattr(backup_module, "dispatch_backup_event", _dispatch)

    service = BackupService(
        databases=cast(Sequence[BaseDB], [_DB()]),
        local_root=tmp_path / "backup",
        restic=ResticConfig(
            repository="s3:https://example.test/bucket",
            password="secret",
        ),
    )

    result = await service.run(
        BackupPlan(
            id="test",
            enabled=True,
            retention=BackupRetention(keep_daily=1, keep_weekly=1, keep_monthly=1),
        )
    )

    assert result is not None
    assert result.restic_snapshot_id == "snap123"
    assert result.manifest_path.is_file()
    assert len(result.manifest.files) == 1
    assert events
    assert events[-1].__class__.__name__ == "BackupSucceeded"


async def test_backup_service_skips_disabled_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []

    async def _dispatch(event: object) -> None:
        events.append(event)

    monkeypatch.setattr(backup_module, "dispatch_backup_event", _dispatch)

    service = BackupService(
        databases=[],
        local_root=tmp_path,
        restic=ResticConfig(repository=None, password=None),
    )

    result = await service.run(BackupPlan(id="disabled", enabled=False))

    assert result is None
    assert events[-1].__class__.__name__ == "BackupSkipped"
