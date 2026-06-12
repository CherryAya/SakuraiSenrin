from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import sqlite3
from typing import cast

import arrow
import pytest
from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.lib.db.backup import BackupSource, SQLiteSnapshotter
from src.lib.db.connectors import BaseDB, ColdPolicy, EventStore
from src.services import backup as backup_module
from src.services.backup import (
    BackupPlan,
    BackupRetention,
    BackupService,
    ResticConfig,
    ResticSnapshotInfo,
    build_default_backup_plan,
    collect_default_backup_databases,
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
            staging_dir = Path(str(args[2]))
            assert (staging_dir / "manifest.json").is_file()
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


async def test_backup_service_lists_remote_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (
                b'[{"id":"snap-1","short_id":"snap-1","time":"2026-06-12T21:35:21+08:00","hostname":"host-a","paths":["/tmp/staging"],"summary":{"total_files_processed":75,"total_bytes_processed":1234}}]',
                b"",
            )

    async def _create_subprocess_exec(*args: object, **kwargs: object) -> _Process:
        _ = kwargs
        assert args == ("restic", "snapshots", "--json")
        return _Process()

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

    service = BackupService(
        databases=[],
        local_root=tmp_path / "backup",
        restic=ResticConfig(
            repository="s3:https://example.test/bucket",
            password="secret",
        ),
    )

    snapshots = await service.list_snapshots()

    assert snapshots == [
        ResticSnapshotInfo(
            id="snap-1",
            short_id="snap-1",
            time="2026-06-12T21:35:21+08:00",
            hostname="host-a",
            paths=("/tmp/staging",),
            total_files_processed=75,
            total_bytes_processed=1234,
        )
    ]


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


@pytest.mark.asyncio
async def test_backup_service_collects_segment_archives_and_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-08 12:00:00").int_timestamp,
    )

    class _EventBase(DeclarativeBase):
        pass

    class _EventModel(_EventBase):
        __tablename__ = "sample_event"

        id: Mapped[int] = mapped_column(
            Integer,
            primary_key=True,
            autoincrement=True,
        )
        value: Mapped[str] = mapped_column(String(32), nullable=False)

    db = EventStore(
        namespace="backup_archive",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
        cold_policy=ColdPolicy.HYDRATE,
    )
    await db.init_schema(_EventBase)

    april = arrow.get("2026-04-08").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_EventModel(value="cold"))

    await db.run_archiver_task()

    service = BackupService(
        databases=[db],
        local_root=tmp_path / "backup",
        restic=ResticConfig(repository=None, password=None, require_restic=False),
    )
    sources = service._collect_sources(include_archives=True)

    assert len(sources) == 3
    assert sum(source.is_archive for source in sources) == 2
    assert any(source.path.name == "events_2026_06.db" for source in sources)
    assert any(source.path.name == "events_2026_04.db.zst" for source in sources)
    assert any(source.path.name == "events_manifest.json" for source in sources)


def test_default_backup_plan_reads_cron_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_module.config, "BACKUP_ENABLED", True)
    monkeypatch.setattr(backup_module.config, "BACKUP_CRON_HOUR", 4)
    monkeypatch.setattr(backup_module.config, "BACKUP_CRON_MINUTE", 35)

    plan = build_default_backup_plan()

    assert plan.enabled is True
    assert plan.cron_hour == 4
    assert plan.cron_minute == 35


def test_collect_default_backup_databases_includes_extended_plugin_dbs() -> None:
    from src.plugins.water.database.instances import (
        water_core_db,
        water_message,
        water_summary,
    )
    from src.plugins.wordbank.database.instances import (
        wordbank_log_db,
        wordbank_main_db,
        wordbank_message_ref_db,
        wordbank_message_route_db,
    )

    databases = collect_default_backup_databases()

    assert water_core_db in databases
    assert water_message in databases
    assert water_summary in databases
    assert wordbank_main_db in databases
    assert wordbank_log_db in databases
    assert wordbank_message_route_db in databases
    assert wordbank_message_ref_db in databases
