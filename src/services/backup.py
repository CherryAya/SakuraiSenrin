"""Backup orchestration service."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import uuid

from src.config import config
from src.lib.backup import (
    BackupFailed,
    BackupSkipped,
    BackupStarted,
    BackupSucceeded,
    dispatch_backup_event,
)
from src.lib.db.backup import (
    BackupManifest,
    BackupSource,
    SQLiteSnapshotter,
    new_backup_manifest,
)
from src.lib.db.connectors import BaseDB
from src.lib.utils.common import get_current_time
from src.logger import logger


@dataclass(slots=True, frozen=True)
class BackupRetention:
    keep_daily: int = 7
    keep_weekly: int = 4
    keep_monthly: int = 6


@dataclass(slots=True, frozen=True)
class BackupPlan:
    id: str = "default"
    enabled: bool = False
    cron_hour: int = 3
    cron_minute: int = 20
    include_archives: bool = True
    retention: BackupRetention = BackupRetention()


@dataclass(slots=True, frozen=True)
class ResticConfig:
    repository: str | None
    password: str | None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    require_restic: bool = True


@dataclass(slots=True, frozen=True)
class BackupResult:
    run_id: str
    manifest: BackupManifest
    manifest_path: Path
    restic_snapshot_id: str | None


@dataclass(slots=True, frozen=True)
class ResticSnapshotInfo:
    id: str
    short_id: str | None
    time: str | None
    hostname: str | None
    paths: tuple[str, ...]
    total_files_processed: int | None
    total_bytes_processed: int | None


class BackupService:
    def __init__(
        self,
        *,
        databases: Sequence[BaseDB],
        local_root: Path,
        restic: ResticConfig,
        snapshotter: SQLiteSnapshotter | None = None,
    ) -> None:
        self.databases = tuple(databases)
        self.local_root = local_root
        self.restic = restic
        self.snapshotter = snapshotter or SQLiteSnapshotter()

    async def run(
        self,
        plan: BackupPlan,
        *,
        force: bool = False,
    ) -> BackupResult | None:
        run_id = self._new_run_id(plan.id)
        started_at = get_current_time()
        if not plan.enabled and not force:
            event = BackupSkipped(
                run_id=run_id,
                plan_id=plan.id,
                started_at=started_at,
                finished_at=get_current_time(),
                error="backup plan is disabled",
            )
            await dispatch_backup_event(event)
            return None

        await dispatch_backup_event(
            BackupStarted(run_id=run_id, plan_id=plan.id, started_at=started_at)
        )

        staging_dir = self.local_root / "staging" / run_id
        manifest_dir = self.local_root / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{run_id}.json"
        staging_manifest_path = staging_dir / "manifest.json"
        manifest = new_backup_manifest(run_id)

        try:
            sources = self._collect_sources(include_archives=plan.include_archives)
            for source in sources:
                manifest.files.append(
                    await self.snapshotter.snapshot(source, staging_dir)
                )
            manifest.write_json(manifest_path)
            manifest.write_json(staging_manifest_path)

            snapshot_id = await self._run_restic_backup(staging_dir)
            manifest.restic_snapshot_id = snapshot_id
            manifest.write_json(manifest_path)
            manifest.write_json(staging_manifest_path)

            await self._run_restic_forget(plan.retention)
            shutil.rmtree(staging_dir, ignore_errors=True)

            finished_at = get_current_time()
            await dispatch_backup_event(
                BackupSucceeded(
                    run_id=run_id,
                    plan_id=plan.id,
                    started_at=started_at,
                    finished_at=finished_at,
                    manifest_path=manifest_path,
                    restic_snapshot_id=snapshot_id,
                    files_count=len(manifest.files),
                    bytes_total=manifest.bytes_total,
                )
            )
            return BackupResult(
                run_id=run_id,
                manifest=manifest,
                manifest_path=manifest_path,
                restic_snapshot_id=snapshot_id,
            )
        except Exception as exc:
            manifest.write_json(manifest_path)
            finished_at = get_current_time()
            await dispatch_backup_event(
                BackupFailed(
                    run_id=run_id,
                    plan_id=plan.id,
                    started_at=started_at,
                    finished_at=finished_at,
                    manifest_path=manifest_path,
                    files_count=len(manifest.files),
                    bytes_total=manifest.bytes_total,
                    error=str(exc),
                )
            )
            logger.exception(f"[Backup] run failed: {exc}")
            raise

    def _collect_sources(self, *, include_archives: bool) -> list[BackupSource]:
        sources: list[BackupSource] = []
        for database in self.databases:
            for source in database.iter_backup_sources():
                if source.is_archive and not include_archives:
                    continue
                sources.append(source)
        return sources

    async def _run_restic_backup(self, staging_dir: Path) -> str | None:
        self._validate_restic_config()
        env = self._restic_env()
        process = await asyncio.create_subprocess_exec(
            "restic",
            "backup",
            str(staging_dir),
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="ignore") or stdout.decode(
                "utf-8",
                errors="ignore",
            )
            raise RuntimeError(f"restic backup failed: {message.strip()}")
        return _parse_restic_snapshot_id(stdout.decode("utf-8", errors="ignore"))

    async def _run_restic_forget(self, retention: BackupRetention) -> None:
        env = self._restic_env()
        process = await asyncio.create_subprocess_exec(
            "restic",
            "forget",
            "--prune",
            "--keep-daily",
            str(retention.keep_daily),
            "--keep-weekly",
            str(retention.keep_weekly),
            "--keep-monthly",
            str(retention.keep_monthly),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="ignore") or stdout.decode(
                "utf-8",
                errors="ignore",
            )
            raise RuntimeError(f"restic forget failed: {message.strip()}")

    async def restore(
        self,
        *,
        snapshot: str,
        target: Path,
    ) -> None:
        self._validate_restic_config()
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            "restic",
            "restore",
            snapshot,
            "--target",
            str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._restic_env(),
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="ignore") or stdout.decode(
                "utf-8",
                errors="ignore",
            )
            raise RuntimeError(f"restic restore failed: {message.strip()}")

    async def list_snapshots(self) -> list[ResticSnapshotInfo]:
        self._validate_restic_config()
        process = await asyncio.create_subprocess_exec(
            "restic",
            "snapshots",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._restic_env(),
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="ignore") or stdout.decode(
                "utf-8",
                errors="ignore",
            )
            raise RuntimeError(f"restic snapshots failed: {message.strip()}")
        return _parse_restic_snapshots(stdout.decode("utf-8", errors="ignore"))

    def _validate_restic_config(self) -> None:
        if shutil.which("restic") is None and self.restic.require_restic:
            raise RuntimeError("restic command is not installed")
        if not self.restic.repository:
            raise RuntimeError("BACKUP_RESTIC_REPOSITORY is not configured")
        if not self.restic.password:
            raise RuntimeError("BACKUP_RESTIC_PASSWORD is not configured")

    def _restic_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.restic.repository:
            env["RESTIC_REPOSITORY"] = self.restic.repository
        if self.restic.password:
            env["RESTIC_PASSWORD"] = self.restic.password
        if self.restic.access_key_id:
            env["AWS_ACCESS_KEY_ID"] = self.restic.access_key_id
        if self.restic.secret_access_key:
            env["AWS_SECRET_ACCESS_KEY"] = self.restic.secret_access_key
        return env

    def _new_run_id(self, plan_id: str) -> str:
        return f"{plan_id}-{get_current_time()}-{uuid.uuid4().hex[:8]}"


def _parse_restic_snapshot_id(output: str) -> str | None:
    for line in reversed(output.splitlines()):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        snapshot_id = payload.get("snapshot_id")
        if isinstance(snapshot_id, str) and snapshot_id:
            return snapshot_id
    return None


def _parse_restic_snapshots(output: str) -> list[ResticSnapshotInfo]:
    if not output.strip():
        return []
    payload = json.loads(output)
    if not isinstance(payload, list):
        raise RuntimeError("restic snapshots output is not a JSON array")

    snapshots: list[ResticSnapshotInfo] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        snapshot_id = item.get("id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            continue
        paths = item.get("paths")
        summary = item.get("summary")
        total_files_processed = None
        total_bytes_processed = None
        if isinstance(summary, dict):
            files_value = summary.get("total_files_processed")
            bytes_value = summary.get("total_bytes_processed")
            if isinstance(files_value, int):
                total_files_processed = files_value
            if isinstance(bytes_value, int):
                total_bytes_processed = bytes_value
        snapshots.append(
            ResticSnapshotInfo(
                id=snapshot_id,
                short_id=item.get("short_id")
                if isinstance(item.get("short_id"), str)
                else None,
                time=item.get("time") if isinstance(item.get("time"), str) else None,
                hostname=item.get("hostname")
                if isinstance(item.get("hostname"), str)
                else None,
                paths=tuple(path for path in paths if isinstance(path, str))
                if isinstance(paths, list)
                else (),
                total_files_processed=total_files_processed,
                total_bytes_processed=total_bytes_processed,
            )
        )
    return snapshots


def collect_default_backup_databases() -> tuple[BaseDB, ...]:
    databases: list[BaseDB] = []

    from src.database.instances import core_db, log_db, snapshot_db

    databases.extend([core_db, log_db, snapshot_db])

    try:
        from src.plugins.water.database.instances import (
            water_core_db,
            water_message,
            water_summary,
        )

        databases.extend([water_core_db, water_message, water_summary])
    except Exception as exc:
        logger.debug(f"[Backup] water db instances unavailable: {exc}")

    try:
        from src.plugins.wordbank.database.instances import (
            wordbank_log_db,
            wordbank_main_db,
            wordbank_message_ref_db,
            wordbank_message_route_db,
        )

        databases.extend(
            [
                wordbank_main_db,
                wordbank_log_db,
                wordbank_message_route_db,
                wordbank_message_ref_db,
            ]
        )
    except Exception as exc:
        logger.debug(f"[Backup] wordbank db instances unavailable: {exc}")

    return tuple(databases)


def build_default_backup_plan() -> BackupPlan:
    return BackupPlan(
        id="default",
        enabled=config.BACKUP_ENABLED,
        cron_hour=config.BACKUP_CRON_HOUR,
        cron_minute=config.BACKUP_CRON_MINUTE,
        include_archives=True,
        retention=BackupRetention(
            keep_daily=config.BACKUP_RETENTION_DAILY,
            keep_weekly=config.BACKUP_RETENTION_WEEKLY,
            keep_monthly=config.BACKUP_RETENTION_MONTHLY,
        ),
    )


def build_backup_service_from_config() -> BackupService:
    return BackupService(
        databases=collect_default_backup_databases(),
        local_root=Path(config.BACKUP_LOCAL_ROOT),
        restic=ResticConfig(
            repository=config.BACKUP_RESTIC_REPOSITORY,
            password=config.BACKUP_RESTIC_PASSWORD,
            access_key_id=config.R2_ACCESS_KEY_ID,
            secret_access_key=config.R2_SECRET_ACCESS_KEY,
            require_restic=config.BACKUP_REQUIRE_RESTIC,
        ),
    )
