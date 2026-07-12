from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import sqlite3
from typing import cast

import arrow
import pytest
from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.lib.backup import (
    ensure_backup_database_registrations_loaded,
    get_registered_backup_databases,
)
from src.lib.backup.registry import reset_backup_database_registry_for_test
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
            profile_name="default",
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


async def test_backup_service_auto_inits_missing_restic_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    _create_sqlite(source)
    called: list[tuple[object, ...]] = []

    class _DB:
        def iter_backup_sources(self) -> list[BackupSource]:
            return [BackupSource(namespace="core_db", kind="core", path=source)]

    class _Process:
        def __init__(
            self,
            *,
            stdout: bytes = b"",
            stderr: bytes = b"",
            returncode: int = 0,
        ) -> None:
            self._stdout = stdout
            self._stderr = stderr
            self.returncode = returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def _create_subprocess_exec(*args: object, **kwargs: object) -> _Process:
        _ = kwargs
        called.append(args)
        if args[1:3] == ("snapshots", "--json"):
            return _Process(
                stderr=(
                    b'{"message_type":"exit_error","code":10,"message":"Fatal: '
                    b'repository does not exist: unable to open config file"}\n'
                ),
                returncode=10,
            )
        if args[1] == "init":
            return _Process(stdout=b"created restic repository\n")
        if args[1] == "backup":
            return _Process(
                stdout=b'{"message_type":"summary","snapshot_id":"snap123"}\n'
            )
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
        databases=cast(Sequence[BaseDB], [_DB()]),
        local_root=tmp_path / "backup",
        restic=ResticConfig(
            profile_name="default",
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
    assert [args[1] for args in called] == ["snapshots", "init", "backup", "forget"]


async def test_backup_service_does_not_auto_init_non_repository_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    _create_sqlite(source)

    class _DB:
        def iter_backup_sources(self) -> list[BackupSource]:
            return [BackupSource(namespace="core_db", kind="core", path=source)]

    class _Process:
        def __init__(
            self,
            *,
            stdout: bytes = b"",
            stderr: bytes = b"",
            returncode: int = 0,
        ) -> None:
            self._stdout = stdout
            self._stderr = stderr
            self.returncode = returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def _create_subprocess_exec(*args: object, **kwargs: object) -> _Process:
        _ = kwargs
        assert args[1:3] == ("snapshots", "--json")
        return _Process(stderr=b"Fatal: wrong password or no key found\n", returncode=1)

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
        databases=cast(Sequence[BaseDB], [_DB()]),
        local_root=tmp_path / "backup",
        restic=ResticConfig(
            profile_name="default",
            repository="s3:https://example.test/bucket",
            password="secret",
        ),
    )

    with pytest.raises(RuntimeError, match="restic repository check failed"):
        await service.run(
            BackupPlan(
                id="test",
                enabled=True,
                retention=BackupRetention(keep_daily=1, keep_weekly=1, keep_monthly=1),
            )
        )


async def test_backup_service_streams_restic_output_in_real_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    _create_sqlite(source)
    stdout_writes: list[str] = []
    info_logs: list[str] = []
    warning_logs: list[str] = []

    class _DB:
        def iter_backup_sources(self) -> list[BackupSource]:
            return [BackupSource(namespace="core_db", kind="core", path=source)]

    class _Reader:
        def __init__(self, lines: list[bytes]) -> None:
            self._lines = list(lines)

        async def readline(self) -> bytes:
            if not self._lines:
                return b""
            return self._lines.pop(0)

    class _Process:
        def __init__(
            self,
            *,
            stdout_lines: list[bytes],
            stderr_lines: list[bytes],
            returncode: int = 0,
        ) -> None:
            self._stdout_lines = list(stdout_lines)
            self._stderr_lines = list(stderr_lines)
            self.stdout = _Reader(list(stdout_lines))
            self.stderr = _Reader(list(stderr_lines))
            self.returncode = returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"".join(self._stdout_lines), b"".join(self._stderr_lines)

        async def wait(self) -> int:
            return self.returncode

    async def _create_subprocess_exec(*args: object, **kwargs: object) -> _Process:
        _ = kwargs
        if args[1:3] == ("snapshots", "--json"):
            return _Process(
                stdout_lines=[b"[]"],
                stderr_lines=[],
            )
        if args[1] == "backup":
            return _Process(
                stdout_lines=[
                    (
                        b'{"message_type":"status","percent_done":0.5,'
                        b'"files_done":5,"total_files":10,'
                        b'"bytes_done":512,"total_bytes":1024}\n'
                    ),
                    (
                        b'{"message_type":"status","percent_done":0.5,'
                        b'"files_done":5,"total_files":10,'
                        b'"bytes_done":512,"total_bytes":1024}\n'
                    ),
                    b'{"message_type":"summary","snapshot_id":"snap123"}\n',
                ],
                stderr_lines=[],
            )
        return _Process(
            stdout_lines=[],
            stderr_lines=[b"pruning old snapshots\n"],
        )

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

    class _Stdout:
        def isatty(self) -> bool:
            return True

        def write(self, text: str) -> int:
            stdout_writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    monkeypatch.setattr(backup_module.logger, "info", info_logs.append)
    monkeypatch.setattr(backup_module.logger, "warning", warning_logs.append)
    monkeypatch.setattr(backup_module.sys, "stdout", _Stdout())

    service = BackupService(
        databases=cast(Sequence[BaseDB], [_DB()]),
        local_root=tmp_path / "backup",
        restic=ResticConfig(
            profile_name="default",
            repository="s3:https://example.test/bucket",
            password="secret",
        ),
    )

    result = await service.run(
        BackupPlan(
            id="test",
            enabled=True,
            retention=BackupRetention(keep_daily=1, keep_weekly=1, keep_monthly=1),
        ),
        stream_output=True,
    )

    assert result is not None
    assert result.restic_snapshot_id == "snap123"
    assert sum("progress=50.0%" in chunk for chunk in stdout_writes) == 1
    assert any(
        chunk.startswith("\r\033[2K[Backup] restic status") for chunk in stdout_writes
    )
    assert any("snapshot=snap123" in message for message in info_logs)
    assert warning_logs == ["[Backup] pruning old snapshots"]


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
            profile_name="default",
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
        restic=ResticConfig(
            profile_name="default",
            repository=None,
            password=None,
        ),
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
        restic=ResticConfig(
            profile_name="default",
            repository=None,
            password=None,
            require_restic=False,
        ),
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


def test_backup_registration_loader_discovers_core_and_plugin_databases() -> None:
    reset_backup_database_registry_for_test()

    ensure_backup_database_registrations_loaded()

    from src.database.instances import core_db, log_db, snapshot_db
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

    databases = get_registered_backup_databases()

    assert len(databases) >= 10
    assert databases[:3] == (core_db, log_db, snapshot_db)
    assert water_core_db in databases
    assert water_message in databases
    assert water_summary in databases
    assert wordbank_main_db in databases
    assert wordbank_log_db in databases
    assert wordbank_message_route_db in databases
    assert wordbank_message_ref_db in databases


def test_backup_registration_loader_is_idempotent() -> None:
    reset_backup_database_registry_for_test()

    ensure_backup_database_registrations_loaded()
    first = get_registered_backup_databases()

    ensure_backup_database_registrations_loaded()
    second = get_registered_backup_databases()

    assert second == first
    assert len({id(db) for db in second}) == len(second)


def test_build_backup_service_from_config_uses_registered_databases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_backup_database_registry_for_test()
    monkeypatch.setattr(backup_module.config, "BACKUP_LOCAL_ROOT", "./data/test-backup")
    monkeypatch.setattr(
        backup_module.config,
        "BACKUP_RESTIC_REPOSITORY",
        "s3:https://example.test/bucket",
    )
    monkeypatch.setattr(backup_module.config, "BACKUP_RESTIC_PASSWORD", "secret")

    service = backup_module.build_backup_service_from_config()
    databases = service.databases

    assert len(databases) >= 10
    assert databases == get_registered_backup_databases()
