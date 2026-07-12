"""SQLite database backup primitives."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3

from src.lib.utils.common import get_current_time


@dataclass(slots=True, frozen=True)
class BackupSource:
    namespace: str
    kind: str
    path: Path
    shard_key: str | None = None
    is_active: bool = True
    is_archive: bool = False

    @property
    def manifest_name(self) -> str:
        suffix = "".join(self.path.suffixes).lstrip(".") or (
            "db" if not self.is_archive else "bin"
        )
        if self.shard_key:
            return f"{self.namespace}-{self.kind}-{self.shard_key}.{suffix}"
        return f"{self.namespace}-{self.kind}.{suffix}"


@dataclass(slots=True)
class BackupManifestFile:
    namespace: str
    kind: str
    source_path: str
    snapshot_path: str
    sha256: str
    size: int
    shard_key: str | None
    is_active: bool
    is_archive: bool


@dataclass(slots=True)
class BackupManifest:
    run_id: str
    created_at: int
    app_env: str | None = None
    backup_profile: str | None = None
    hostname: str | None = None
    files: list[BackupManifestFile] = field(default_factory=list)
    restic_snapshot_id: str | None = None

    @property
    def bytes_total(self) -> int:
        return sum(file.size for file in self.files)

    def write_json(self, path: Path) -> None:
        payload = {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "app_env": self.app_env,
            "backup_profile": self.backup_profile,
            "hostname": self.hostname,
            "restic_snapshot_id": self.restic_snapshot_id,
            "bytes_total": self.bytes_total,
            "files": [asdict(file) for file in self.files],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_sqlite_db(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)


class SQLiteSnapshotter:
    async def snapshot(
        self,
        source: BackupSource,
        destination_dir: Path,
    ) -> BackupManifestFile:
        destination = destination_dir / source.manifest_name
        if source.is_archive:
            destination.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, source.path, destination)
        else:
            await asyncio.to_thread(snapshot_sqlite_db, source.path, destination)
        digest = await asyncio.to_thread(hash_file, destination)
        stat = destination.stat()
        return BackupManifestFile(
            namespace=source.namespace,
            kind=source.kind,
            source_path=str(source.path),
            snapshot_path=str(destination),
            sha256=digest,
            size=stat.st_size,
            shard_key=source.shard_key,
            is_active=source.is_active,
            is_archive=source.is_archive,
        )


def new_backup_manifest(
    run_id: str,
    *,
    app_env: str | None = None,
    backup_profile: str | None = None,
    hostname: str | None = None,
) -> BackupManifest:
    return BackupManifest(
        run_id=run_id,
        created_at=get_current_time(),
        app_env=app_env,
        backup_profile=backup_profile,
        hostname=hostname,
    )
