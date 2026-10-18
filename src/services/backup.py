"""Backup orchestration service."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import uuid

from src.config import config
from src.lib.backup import (
    BackupFailed,
    BackupSkipped,
    BackupStarted,
    BackupSucceeded,
    dispatch_backup_event,
    ensure_backup_database_registrations_loaded,
    get_registered_backup_databases,
)
from src.lib.db.backup import (
    BackupManifest,
    BackupSource,
    SQLiteSnapshotter,
    new_backup_manifest,
)
from src.lib.db.connectors import BaseDB
from src.lib.types import JsonObject, JsonValue, as_object, as_str
from src.lib.utils.time_utils import get_current_time
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
        stream_output: bool = False,
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

        await self._ensure_restic_repository(stream_output=stream_output)

        staging_dir = self.local_root / "staging" / run_id
        manifest_dir = self.local_root / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{run_id}.json"
        staging_manifest_path = staging_dir / "manifest.json"

        manifest = new_backup_manifest(
            run_id,
            hostname=socket.gethostname(),
        )

        try:
            sources = self._collect_sources(include_archives=plan.include_archives)
            for source in sources:
                manifest.files.append(
                    await self.snapshotter.snapshot(source, staging_dir)
                )
            manifest.write_json(manifest_path)
            manifest.write_json(staging_manifest_path)

            snapshot_id = await self._run_restic_backup(
                staging_dir,
                stream_output=stream_output,
            )
            manifest.restic_snapshot_id = snapshot_id
            manifest.write_json(manifest_path)
            manifest.write_json(staging_manifest_path)

            await self._run_restic_forget(
                plan.retention,
                stream_output=stream_output,
            )
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

    async def _run_restic_backup(
        self,
        staging_dir: Path,
        *,
        stream_output: bool = False,
    ) -> str | None:
        self.validate_config()
        stdout, stderr, returncode = await self._run_restic_process(
            "restic",
            "backup",
            str(staging_dir),
            "--json",
            stream_output=stream_output,
        )
        if returncode != 0:
            message = stderr or stdout
            raise RuntimeError(f"restic backup failed: {message.strip()}")
        return _parse_restic_snapshot_id(stdout)

    async def _run_restic_forget(
        self,
        retention: BackupRetention,
        *,
        stream_output: bool = False,
    ) -> None:
        stdout, stderr, returncode = await self._run_restic_process(
            "restic",
            "forget",
            "--prune",
            "--keep-daily",
            str(retention.keep_daily),
            "--keep-weekly",
            str(retention.keep_weekly),
            "--keep-monthly",
            str(retention.keep_monthly),
            stream_output=stream_output,
        )
        if returncode != 0:
            message = stderr or stdout
            raise RuntimeError(f"restic forget failed: {message.strip()}")

    async def _ensure_restic_repository(self, *, stream_output: bool = False) -> None:
        self.validate_config()
        stdout, stderr, returncode = await self._run_restic_process(
            "restic",
            "snapshots",
            "--json",
        )
        if returncode == 0:
            return

        message = stderr or stdout
        if not _is_missing_restic_repository_message(message):
            raise RuntimeError(f"restic repository check failed: {message.strip()}")

        logger.warning("[Backup] restic repository missing, initializing it now")
        init_stdout, init_stderr, init_returncode = await self._run_restic_process(
            "restic",
            "init",
            stream_output=stream_output,
        )
        if init_returncode != 0:
            init_message = init_stderr or init_stdout
            raise RuntimeError(f"restic init failed: {init_message.strip()}")
        logger.info("[Backup] restic repository initialized")

    async def restore(
        self,
        *,
        snapshot: str,
        target: Path,
    ) -> None:
        self.validate_config()
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
        self.validate_config()
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

    def validate_config(self) -> None:
        """在创建暂存文件或调用 restic 前校验当前配置。"""
        if shutil.which("restic") is None and self.restic.require_restic:
            raise RuntimeError("restic command is not installed")
        if not self.restic.repository:
            raise RuntimeError("backup repository is not configured")
        if not self.restic.password:
            raise RuntimeError("backup password is not configured")

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

    async def _run_restic_process(
        self,
        *args: str,
        stream_output: bool = False,
    ) -> tuple[str, str, int]:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._restic_env(),
        )
        if not stream_output:
            stdout, stderr = await process.communicate()
            returncode = process.returncode
            assert returncode is not None
            return (
                stdout.decode("utf-8", errors="ignore"),
                stderr.decode("utf-8", errors="ignore"),
                returncode,
            )

        assert process.stdout is not None
        assert process.stderr is not None
        renderer = _TerminalProgressRenderer()
        stdout_task = asyncio.create_task(
            self._read_restic_stream(
                process.stdout,
                is_error=False,
                renderer=renderer,
            )
        )
        stderr_task = asyncio.create_task(
            self._read_restic_stream(
                process.stderr,
                is_error=True,
                renderer=renderer,
            )
        )
        returncode = await process.wait()
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        renderer.finish()
        return stdout, stderr, returncode

    async def _read_restic_stream(
        self,
        stream: asyncio.StreamReader,
        *,
        is_error: bool,
        renderer: "_TerminalProgressRenderer | None" = None,
    ) -> str:
        chunks: list[str] = []
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="ignore")
            chunks.append(text)
            event_kind, rendered = _parse_restic_output_event(text)
            if not rendered:
                continue
            if event_kind == "status" and renderer is not None and not is_error:
                renderer.show_status(f"[Backup] {rendered}")
                continue
            if renderer is not None:
                renderer.before_log_line()
            if is_error:
                logger.warning(f"[Backup] {rendered}")
            else:
                logger.info(f"[Backup] {rendered}")
        return "".join(chunks)


def _parse_restic_snapshot_id(output: str) -> str | None:
    for line in reversed(output.splitlines()):
        if not line.strip():
            continue
        try:
            payload: JsonValue = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        snapshot_id = as_str(payload.get("snapshot_id"))
        if snapshot_id:
            return snapshot_id
    return None


def _is_missing_restic_repository_message(message: str) -> bool:
    normalized = message.casefold()
    return (
        "repository does not exist" in normalized
        or "unable to open config file" in normalized
        or "is there a repository at the following location?" in normalized
    )


class _TerminalProgressRenderer:
    def __init__(self) -> None:
        self._enabled = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._active = False
        self._last_status = ""

    def show_status(self, message: str) -> None:
        if message == self._last_status:
            return
        self._last_status = message
        if not self._enabled:
            return
        sys.stdout.write(f"\r\033[2K{message}")
        sys.stdout.flush()
        self._active = True

    def before_log_line(self) -> None:
        if not self._enabled or not self._active:
            return
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._active = False

    def finish(self) -> None:
        self.before_log_line()


def _parse_restic_output_event(raw_line: str) -> tuple[str, str]:
    line = raw_line.strip()
    if not line:
        return "empty", ""
    try:
        payload: JsonValue = json.loads(line)
    except json.JSONDecodeError:
        return "text", line
    if not isinstance(payload, dict):
        return "text", line

    message_type = as_str(payload.get("message_type"))
    if message_type == "status":
        parts = ["restic status"]
        percent_done = payload.get("percent_done")
        if isinstance(percent_done, (int, float)):
            parts.append(f"progress={percent_done * 100:.1f}%")
        files_done = payload.get("files_done")
        total_files = payload.get("total_files")
        if isinstance(files_done, int) and isinstance(total_files, int):
            parts.append(f"files={files_done}/{total_files}")
        bytes_done = payload.get("bytes_done")
        total_bytes = payload.get("total_bytes")
        if isinstance(bytes_done, int) and isinstance(total_bytes, int):
            parts.append(
                f"bytes={_format_bytes(bytes_done)}/{_format_bytes(total_bytes)}"
            )
        current_files = payload.get("current_files")
        if isinstance(current_files, list) and current_files:
            current = current_files[0]
            if isinstance(current, str) and current:
                parts.append(f"current={current}")
        return "status", " ".join(parts)

    if message_type == "summary":
        parts = ["restic summary"]
        snapshot_id = payload.get("snapshot_id")
        if isinstance(snapshot_id, str) and snapshot_id:
            parts.append(f"snapshot={snapshot_id}")
        files_total = payload.get("total_files_processed")
        if isinstance(files_total, int):
            parts.append(f"files={files_total}")
        bytes_total = payload.get("total_bytes_processed")
        if isinstance(bytes_total, int):
            parts.append(f"bytes={_format_bytes(bytes_total)}")
        return "summary", " ".join(parts)

    if message_type == "error":
        error = payload.get("error")
        if isinstance(error, str) and error:
            return "error", f"restic error: {error}"

    return "text", line


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def _fetch_restic_object(item: JsonValue, key: str) -> JsonObject | None:
    """从 restic 对象条目中取出嵌套对象；非对象（含缺失）统一返回 None。"""
    parent = as_object(item)
    if parent is None:
        return None
    return as_object(parent.get(key))


def _parse_restic_snapshots(output: str) -> list[ResticSnapshotInfo]:
    if not output.strip():
        return []
    payload: JsonValue = json.loads(output)
    if not isinstance(payload, list):
        raise RuntimeError("restic snapshots output is not a JSON array")

    snapshots: list[ResticSnapshotInfo] = []
    for raw_item in payload:
        item = as_object(raw_item)
        if item is None:
            continue
        snapshot_id = as_str(item.get("id"))
        if not snapshot_id:
            continue
        total_files_processed: int | None = None
        total_bytes_processed: int | None = None
        summary = _fetch_restic_object(raw_item, "summary")
        if summary is not None:
            files_value = summary.get("total_files_processed")
            bytes_value = summary.get("total_bytes_processed")
            if isinstance(files_value, int):
                total_files_processed = files_value
            if isinstance(bytes_value, int):
                total_bytes_processed = bytes_value
        paths = item.get("paths")
        path_values = paths if isinstance(paths, list) else []
        snapshots.append(
            ResticSnapshotInfo(
                id=snapshot_id,
                short_id=_as_optional_str(item.get("short_id")),
                time=_as_optional_str(item.get("time")),
                hostname=_as_optional_str(item.get("hostname")),
                paths=tuple(path for path in path_values if isinstance(path, str)),
                total_files_processed=total_files_processed,
                total_bytes_processed=total_bytes_processed,
            )
        )
    return snapshots


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
    ensure_backup_database_registrations_loaded()
    return BackupService(
        databases=get_registered_backup_databases(),
        local_root=Path(config.BACKUP_LOCAL_ROOT),
        restic=ResticConfig(
            repository=config.BACKUP_RESTIC_REPOSITORY,
            password=config.BACKUP_RESTIC_PASSWORD,
            access_key_id=config.R2_ACCESS_KEY_ID,
            secret_access_key=config.R2_SECRET_ACCESS_KEY,
            require_restic=config.BACKUP_REQUIRE_RESTIC,
        ),
    )


def _as_optional_str(value: object) -> str | None:
    """把运行期取值收窄成 ``str | None``；非字符串回落 None。"""
    return value if isinstance(value, str) else None
