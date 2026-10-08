"""Backup orchestration service."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import socket
import sys
from typing import cast
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
from src.lib.types import JsonObject, JsonValue, as_object, as_str, as_str_tuple
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
    profile_name: str
    repository: str | None
    password: str | None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    allowed_app_envs_for_backup: tuple[str, ...] = ()
    allowed_app_envs_for_restore: tuple[str, ...] = ()
    allow_backup: bool = True
    allow_restore: bool = True
    require_restic: bool = True


@dataclass(slots=True, frozen=True)
class BackupRemoteProfile:
    name: str
    repository: str
    password: str
    access_key_id: str | None = None
    secret_access_key: str | None = None
    allowed_app_envs_for_backup: tuple[str, ...] = ()
    allowed_app_envs_for_restore: tuple[str, ...] = ()
    allow_backup: bool = True
    allow_restore: bool = True


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

    @property
    def profile_name(self) -> str:
        return self.restic.profile_name

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

        # 授权判定必须排在 _ensure_restic_repository 之前：后者会探测 restic
        # 可执行文件并对远端仓库执行 init/snapshots，属于有副作用的环境操作。
        # 先做「这个 profile 是否允许在本环境备份」的纯策略判定，越权请求就不会
        # 先碰到远端仓库；同时也避免环境缺少 restic 时把授权错误掩盖成环境错误。
        app_env = resolve_app_env()
        if not self.restic.allow_backup:
            raise RuntimeError(
                f"backup profile {self.profile_name!r} does not allow backup"
            )
        if (
            self.restic.allowed_app_envs_for_backup
            and app_env not in self.restic.allowed_app_envs_for_backup
        ):
            raise RuntimeError(
                f"app_env={app_env} cannot backup to profile {self.profile_name}"
            )

        await self._ensure_restic_repository(stream_output=stream_output)

        staging_dir = self.local_root / "staging" / run_id
        manifest_dir = self.local_root / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{run_id}.json"
        staging_manifest_path = staging_dir / "manifest.json"

        manifest = new_backup_manifest(
            run_id,
            app_env=app_env,
            backup_profile=self.profile_name,
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
        self._validate_restic_config()
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
        self._validate_restic_config()
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
        self._validate_restic_config()
        if not self.restic.allow_restore:
            raise RuntimeError(
                f"backup profile {self.profile_name!r} does not allow restore"
            )
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
            raise RuntimeError(
                f"backup repository is not configured for profile {self.profile_name}"
            )
        if not self.restic.password:
            raise RuntimeError(
                f"backup password is not configured for profile {self.profile_name}"
            )

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


def build_backup_service_from_config(
    profile_name: str | None = None,
) -> BackupService:
    ensure_backup_database_registrations_loaded()
    profile = resolve_backup_profile(profile_name)
    return BackupService(
        databases=get_registered_backup_databases(),
        local_root=Path(config.BACKUP_LOCAL_ROOT),
        restic=ResticConfig(
            profile_name=profile.name,
            repository=profile.repository,
            password=profile.password,
            access_key_id=profile.access_key_id,
            secret_access_key=profile.secret_access_key,
            allowed_app_envs_for_backup=profile.allowed_app_envs_for_backup,
            allowed_app_envs_for_restore=profile.allowed_app_envs_for_restore,
            allow_backup=profile.allow_backup,
            allow_restore=profile.allow_restore,
            require_restic=config.BACKUP_REQUIRE_RESTIC,
        ),
    )


def resolve_default_backup_profile_name() -> str:
    configured = (getattr(config, "BACKUP_REMOTE_PROFILE", None) or "").strip()
    if configured:
        return configured
    profiles = _load_backup_profiles()
    if len(profiles) == 1:
        return next(iter(profiles))
    app_env = resolve_app_env()
    if app_env == "production":
        return "prod"
    if app_env == "development":
        return "dev"
    raise RuntimeError("BACKUP_REMOTE_PROFILE is not configured")


def resolve_backup_profile(
    profile_name: str | None = None,
) -> BackupRemoteProfile:
    profiles = _load_backup_profiles()
    if not profiles:
        raise RuntimeError("no backup profile is configured")
    resolved_name = (profile_name or resolve_default_backup_profile_name()).strip()
    profile = profiles.get(resolved_name)
    if profile is None:
        raise RuntimeError(f"backup profile not found: {resolved_name}")
    return profile


def _as_optional_str(value: object) -> str | None:
    """把运行期取值收窄成 ``str | None``；非字符串一律视为未配置。"""
    return value if isinstance(value, str) else None


def _profile_payload(profile: object) -> dict[str, object]:
    """把配置里的 profile 对象摊平成 ``dict[str, object]``。

    兼容 pydantic 模型的 ``model_dump()``、普通对象的 ``__dict__``，以及本就是
    ``Mapping`` 的字典。字段级收窄由下游 ``_coerce_backup_profile`` 负责。
    """
    dump = getattr(profile, "model_dump", None)
    if callable(dump):
        dumped = dump()
        if isinstance(dumped, Mapping):
            return {
                str(key): value
                for key, value in cast("Mapping[object, object]", dumped).items()
            }
        raise RuntimeError("backup profile model_dump() must return a mapping")
    dunder = getattr(profile, "__dict__", None)
    if isinstance(dunder, Mapping):
        return {
            str(key): value
            for key, value in cast("Mapping[object, object]", dunder).items()
        }
    if isinstance(profile, Mapping):
        return {
            str(key): value
            for key, value in cast("Mapping[object, object]", profile).items()
        }
    raise RuntimeError(f"unsupported backup profile: {type(profile)!r}")


def _profile_str(payload: Mapping[str, object], key: str, default: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else default


def _profile_bool(payload: Mapping[str, object], key: str, default: bool) -> bool:
    value = payload.get(key)
    return value if isinstance(value, bool) else default


def _coerce_backup_profile(name: str, profile: object) -> BackupRemoteProfile:
    """把配置里的备份 profile 收窄成 ``BackupRemoteProfile``。

    字段级校验在运行期逐条收口：必填的 ``repository`` / ``password`` 缺失或非
    字符串时抛 ``RuntimeError``（与旧实现的 pydantic 校验保持一致），可选字段
    则回落默认值。
    """
    if isinstance(profile, BackupRemoteProfile):
        return profile
    payload = _profile_payload(profile)
    repository = _as_optional_str(payload.get("repository"))
    password = _as_optional_str(payload.get("password"))
    if not repository or not password:
        raise RuntimeError(
            f"invalid backup profile {name!r}: repository/password are required",
        )
    return BackupRemoteProfile(
        name=_profile_str(payload, "name", name) or name,
        repository=repository,
        password=password,
        access_key_id=_as_optional_str(payload.get("access_key_id")),
        secret_access_key=_as_optional_str(payload.get("secret_access_key")),
        allowed_app_envs_for_backup=as_str_tuple(
            payload.get("allowed_app_envs_for_backup")
        ),
        allowed_app_envs_for_restore=as_str_tuple(
            payload.get("allowed_app_envs_for_restore")
        ),
        allow_backup=_profile_bool(payload, "allow_backup", True),
        allow_restore=_profile_bool(payload, "allow_restore", True),
    )


def _load_backup_profiles() -> dict[str, BackupRemoteProfile]:
    backup_profiles = getattr(config, "backup_profiles", None)
    if callable(backup_profiles):
        profiles = backup_profiles()
        if not isinstance(profiles, Mapping):
            raise RuntimeError("backup_profiles() must return a mapping")
        normalized: dict[str, BackupRemoteProfile] = {}
        for name, profile in cast("Mapping[object, object]", profiles).items():
            if not isinstance(name, str) or not name:
                raise RuntimeError("backup profile name must be a non-empty string")
            normalized[name] = _coerce_backup_profile(name, profile)
        return normalized

    repository = _as_optional_str(getattr(config, "BACKUP_RESTIC_REPOSITORY", None))
    password = _as_optional_str(getattr(config, "BACKUP_RESTIC_PASSWORD", None))
    if repository and password:
        profile_name = resolve_legacy_backup_profile_name()
        return {
            profile_name: BackupRemoteProfile(
                name=profile_name,
                repository=repository,
                password=password,
                access_key_id=_as_optional_str(
                    getattr(config, "R2_ACCESS_KEY_ID", None)
                ),
                secret_access_key=_as_optional_str(
                    getattr(config, "R2_SECRET_ACCESS_KEY", None)
                ),
                allowed_app_envs_for_backup=(resolve_app_env(),),
                allowed_app_envs_for_restore=(resolve_app_env(),),
                allow_backup=True,
                allow_restore=True,
            )
        }
    return {}


def resolve_legacy_backup_profile_name() -> str:
    configured = (getattr(config, "BACKUP_REMOTE_PROFILE", None) or "").strip()
    return configured or "default"


def resolve_app_env() -> str:
    return str(getattr(config, "APP_ENV", "development"))
