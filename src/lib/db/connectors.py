"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 00:39:22
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-06-12 16:55:00
Description: sharedDB v2 连接器
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
import json
import os
from pathlib import Path
import sqlite3
from typing import cast
import uuid

import arrow
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase
import zstandard as zstd

from src.lib.consts import GLOBAL_DB_ROOT
from src.lib.db.backup import BackupSource
from src.lib.trace_log import log_trace_event
from src.lib.types import JsonObject, JsonValue
from src.lib.utils.time_utils import get_current_time
from src.logger import logger

from .manager import db_manager
from .schema import PatchBase, PatchRegistry

MAX_MAP_REDUCE_MONTH_SPAN = 24

# 分片键业务时区：路由与活跃判定同源
DEFAULT_SEGMENT_TZ = "Asia/Shanghai"

# manifest 去抖落盘间隔（秒），允许延迟
MANIFEST_FLUSH_INTERVAL = 5.0

# 单分片体积上限（MB），对齐 ES ILM
DEFAULT_MAX_SEGMENT_SIZE_MB = 512

# 超阈值分片在巡检时强制 seal 转 COLD
SEGMENT_SIZE_ROLLOVER_MB = 256

# 两阶段归档的中间态后缀
ARCHIVE_STAGING_SUFFIX = ".archiving"

# 业务时间入参：时间戳 / datetime / Arrow / 字符串
type MomentLike = datetime | arrow.Arrow | str | int


class ColdPolicy(StrEnum):
    DENY = "deny"
    SKIP = "skip"
    HYDRATE = "hydrate"


class SegmentState(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class ArchiveCodec(StrEnum):
    ZSTD = "zstd"


class SegmentArchiveOutcome(StrEnum):
    """单个分片归档结果：区分跳过与失败。"""

    ARCHIVED = "archived"
    SKIPPED = "skipped"
    FAILED = "failed"
    CONFLICT = "conflict"


@dataclass(slots=True, frozen=True)
class SegmentArchiveFailure:
    """归档失败/冲突记录：分片与原因如实传给 job 层。"""

    shard_key: str
    outcome: SegmentArchiveOutcome
    reason: str


@dataclass(slots=True)
class SegmentArchiveReport:
    """一次归档巡检的汇总结果。"""

    archived: list[str] = field(default_factory=list[str])
    skipped: list[str] = field(default_factory=list[str])
    failures: list[SegmentArchiveFailure] = field(
        default_factory=list[SegmentArchiveFailure]
    )
    healed: list[str] = field(default_factory=list[str])

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        return (
            f"archived={len(self.archived)} skipped={len(self.skipped)} "
            f"healed={len(self.healed)} failed={len(self.failures)}"
        )


class ArchiveStagingConflictError(RuntimeError):
    """在线分片与归档残留并存：拒绝覆盖，交运维裁决。"""


class ArchiveIntegrityError(RuntimeError):
    """归档损坏（截断 / 校验和失配）：隔离留证，拒绝当库存。"""


@dataclass(slots=True)
class SegmentManifestEntry:
    segment_id: str
    state: SegmentState
    path: str
    archive_path: str | None
    row_count: int = 0
    size_bytes: int = 0
    last_access_at: int = 0
    hydrated_at: int = 0
    updated_at: int = 0
    archived_at: int = 0


@dataclass(slots=True)
class SegmentManifest:
    version: int = 1
    segments: dict[str, SegmentManifestEntry] = field(
        default_factory=dict[str, SegmentManifestEntry]
    )

    def to_json(self) -> str:
        payload = {
            "version": self.version,
            "segments": {
                key: {
                    **asdict(entry),
                    "state": entry.state.value,
                }
                for key, entry in self.segments.items()
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> SegmentManifest:
        payload = json.loads(raw)
        segments = {
            key: SegmentManifestEntry(
                segment_id=value["segment_id"],
                state=SegmentState(value["state"]),
                path=value["path"],
                archive_path=value.get("archive_path"),
                row_count=int(value.get("row_count", 0)),
                size_bytes=int(value.get("size_bytes", 0)),
                last_access_at=int(value.get("last_access_at", 0)),
                hydrated_at=int(value.get("hydrated_at", 0)),
                updated_at=int(value.get("updated_at", 0)),
                archived_at=int(value.get("archived_at", 0)),
            )
            for key, value in payload.get("segments", {}).items()
        }
        return cls(version=int(payload.get("version", 1)), segments=segments)


@dataclass(slots=True)
class SegmentConfig:
    """分片生命周期策略，对应 ES 的 ILM policy。"""

    granularity: str = "month"
    hot_window: int = 2
    warm_ttl_seconds: int = 24 * 60 * 60
    warm_budget_mb: int = 512
    cold_policy: ColdPolicy = ColdPolicy.DENY
    archive_codec: ArchiveCodec = ArchiveCodec.ZSTD
    map_reduce_concurrency: int = 4
    max_segment_size_mb: int = DEFAULT_MAX_SEGMENT_SIZE_MB
    retention_months: int = 0


@dataclass
class BaseDB(ABC):
    namespace: str

    patch_registry: PatchRegistry = field(default_factory=PatchRegistry, init=False)
    _schema_base: type[DeclarativeBase] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    @property
    def base_dir(self) -> Path:
        d = GLOBAL_DB_ROOT / self.namespace
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        """单文件库物理路径；分片库此处报错。"""
        filename = getattr(self, "filename", None)
        if filename is None:
            raise TypeError(
                f"{type(self).__name__} 是分片库，没有单一 db 文件",
            )
        return self.base_dir / filename

    @abstractmethod
    def read_session(
        self,
        time_ctx: datetime | None = None,
        cold_policy: ColdPolicy | None = None,
    ) -> AbstractAsyncContextManager[AsyncSession]:
        pass

    @abstractmethod
    def write_session(
        self,
        time_ctx: datetime | None = None,
    ) -> AbstractAsyncContextManager[AsyncSession]:
        pass

    def session(
        self,
        commit: bool = True,
        *,
        time_ctx: datetime | None = None,
        cold_policy: ColdPolicy | None = None,
    ) -> AbstractAsyncContextManager[AsyncSession]:
        # 具名参数如实转发：分片库据此路由，单文件库忽略
        if commit:
            return self.write_session(time_ctx=time_ctx)
        return self.read_session(time_ctx=time_ctx, cold_policy=cold_policy)

    async def init(self, base: type[DeclarativeBase]) -> None:
        await self.init_schema(base)

    def reset_runtime_state(self) -> None:
        """清空进程内派生状态；备份恢复后由公共层统一调用。"""

    def iter_backup_sources(self) -> list[BackupSource]:
        return []

    async def init_schema(self, base: type[DeclarativeBase]) -> None:
        self._schema_base = base
        async with self.write_session() as session:
            engine = session.bind
            assert isinstance(engine, AsyncEngine)
            async with engine.begin() as conn:
                await conn.run_sync(base.metadata.create_all)
                await conn.run_sync(PatchBase.metadata.create_all)
            await self.patch_registry.apply_all(session, get_current_time())


@dataclass
class StateStore(BaseDB):
    filename: str

    def read_session(
        self,
        *args: object,
        **kwargs: object,
    ) -> AbstractAsyncContextManager[AsyncSession]:
        path = self.base_dir / self.filename
        return db_manager.open(str(path), commit=False)

    def write_session(
        self,
        *args: object,
        **kwargs: object,
    ) -> AbstractAsyncContextManager[AsyncSession]:
        path = self.base_dir / self.filename
        return db_manager.open(str(path), commit=True)

    def iter_backup_sources(self) -> list[BackupSource]:
        path = self.base_dir / self.filename
        if not path.exists():
            return []
        return [
            BackupSource(
                namespace=self.namespace,
                kind=Path(self.filename).stem,
                path=path,
            )
        ]


def _safe_size(path: Path | None) -> int:
    if path is None:
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


class _ReentrantShardLock:
    """按 task 可重入的分片锁。"""

    __slots__ = ("_depth", "_lock", "_owner")

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._depth = 0
        self._owner: asyncio.Task[object] | None = None

    async def __aenter__(self) -> None:
        task = asyncio.current_task()
        if self._owner is task and self._depth > 0:
            self._depth += 1
            return
        await self._lock.acquire()
        self._owner = task
        self._depth = 1

    async def __aexit__(self, *_exc_info: object) -> None:
        self._depth -= 1
        if self._depth <= 0:
            self._depth = 0
            self._owner = None
            self._lock.release()

    def is_busy(self) -> bool:
        """锁被任一 task 持有时为真，供 WARM 逐出做跳过判断。"""
        return self._depth > 0


@dataclass
class SegmentStore(BaseDB):
    prefix: str
    fmt: str = "%Y_%m"
    active_window_months: int = 2
    cold_policy: ColdPolicy = ColdPolicy.DENY
    map_reduce_concurrency: int = 4
    warm_ttl_seconds: int = 24 * 60 * 60
    warm_budget_mb: int = 512
    archive_codec: ArchiveCodec = ArchiveCodec.ZSTD
    tz: str = DEFAULT_SEGMENT_TZ
    max_segment_size_mb: int = DEFAULT_MAX_SEGMENT_SIZE_MB
    retention_months: int = 0
    _locks: dict[str, _ReentrantShardLock] = field(
        default_factory=dict[str, _ReentrantShardLock]
    )
    _initialized_shards: dict[str, tuple[int, int]] = field(
        default_factory=dict[str, tuple[int, int]]
    )
    _manifest: SegmentManifest | None = field(default=None, init=False, repr=False)
    _manifest_dirty: bool = field(default=False, init=False, repr=False)
    _manifest_flush_task: asyncio.Task[None] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _manifest_loop: asyncio.AbstractEventLoop | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _manifest_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def manifest_path(self) -> Path:
        return self.base_dir / f"{self.prefix}_manifest.json"

    def reset_runtime_state(self) -> None:
        """清空分片内存态；备份恢复后由公共层统一调用。"""
        self._initialized_shards.clear()
        self._manifest = None
        self._manifest_dirty = False
        self._manifest_flush_task = None

    @property
    def hydrate_dir(self) -> Path:
        path = self.base_dir / "_hydrate_cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _tz_now(self) -> arrow.Arrow:
        """按本 store 的业务时区取「当前时刻」。"""
        return arrow.get(get_current_time()).to(self.tz)

    def _to_local(self, moment: datetime | arrow.Arrow) -> arrow.Arrow:
        """归一化到本 store 业务时区：naive 视为该时区墙钟。"""
        if isinstance(moment, arrow.Arrow):
            return moment.to(self.tz)
        if moment.tzinfo is None:
            return arrow.get(moment, tzinfo=self.tz)
        return arrow.get(moment).to(self.tz)

    def _lock_key(self, shard_key: str) -> str:
        return f"shard:{shard_key}"

    def _get_lock(self, shard_key: str) -> _ReentrantShardLock:
        if shard_key not in self._locks:
            self._locks[shard_key] = _ReentrantShardLock()
        return self._locks[shard_key]

    def _shard_lock(self, shard_key: str) -> _ReentrantShardLock:
        """分片级互斥锁：归档、唤醒、schema 初始化共用。"""
        return self._get_lock(self._lock_key(shard_key))

    @staticmethod
    def _shard_identity(db_path: Path) -> tuple[int, int] | None:
        try:
            stat = db_path.stat()
        except OSError:
            return None
        return stat.st_dev, stat.st_ino

    def _safe_resolve(self, target_path: Path) -> Path:
        resolved_target = target_path.resolve()
        resolved_root = self.base_dir.resolve()
        if not resolved_target.is_relative_to(resolved_root):
            raise PermissionError("Access Denied: Path traversal attempt detected.")
        return resolved_target

    def _get_shard_key(self, dt: datetime) -> str:
        return dt.strftime(self.fmt)

    def _get_file_paths(self, shard_key: str) -> tuple[Path, Path]:
        base = self.base_dir / f"{self.prefix}_{shard_key}"
        return base.with_suffix(".db"), base.with_suffix(".db.zst")

    def _get_staging_path(self, shard_key: str) -> Path:
        db_path, _ = self._get_file_paths(shard_key)
        return db_path.with_suffix(ARCHIVE_STAGING_SUFFIX)

    def _is_active_shard(self, shard_key: str) -> bool:
        now = self._tz_now().floor("month")
        active_keys = {
            now.shift(months=-offset).strftime(self.fmt)
            for offset in range(self.active_window_months)
        }
        return shard_key in active_keys

    def shard_file_for(self, moment: MomentLike) -> Path:
        """解析业务时间对应的物理分片文件路径。"""
        local = self._to_local(
            moment if isinstance(moment, arrow.Arrow) else arrow.get(moment)
        )
        shard_key = self._get_shard_key(
            local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).datetime,
        )
        return self._get_file_paths(shard_key)[0]

    @asynccontextmanager
    async def hold_online_for_attach(
        self,
        moment: MomentLike,
    ) -> AsyncGenerator[Path, None]:
        """持分片锁确保分片在线，锁覆盖整个 ATTACH 会话。"""
        local = self._to_local(
            moment if isinstance(moment, arrow.Arrow) else arrow.get(moment)
        )
        shard_key = self._get_shard_key(
            local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).datetime,
        )
        async with self._shard_lock(shard_key):
            await self._ensure_shard_online(
                shard_key,
                cold_policy=ColdPolicy.HYDRATE,
                create_if_missing=True,
            )
            await self._initialize_shard_schema(shard_key)
            db_path, _ = self._get_file_paths(shard_key)
            yield db_path

    def _manifest_entry(
        self,
        shard_key: str,
        db_path: Path,
        archive_path: Path,
    ) -> SegmentManifestEntry:
        manifest = self._load_manifest()
        entry = manifest.segments.get(shard_key)
        if entry is None:
            entry = SegmentManifestEntry(
                segment_id=shard_key,
                state=SegmentState.HOT if db_path.exists() else SegmentState.COLD,
                path=str(db_path),
                archive_path=str(archive_path),
                updated_at=get_current_time(),
            )
            manifest.segments[shard_key] = entry
        return entry

    def _load_manifest(self) -> SegmentManifest:
        if self._manifest is not None:
            return self._manifest
        if self.manifest_path.exists():
            self._manifest = SegmentManifest.from_json(
                self.manifest_path.read_text(encoding="utf-8")
            )
        else:
            self._manifest = SegmentManifest()
        return self._manifest

    async def flush_manifest(self) -> None:
        """强制把 manifest 落盘；失败只记日志并保持 dirty 待重试。"""
        task = self._manifest_flush_task
        if task is not None and not task.done():
            if self._manifest_loop is asyncio.get_running_loop():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            self._manifest_flush_task = None
        self._manifest_dirty = False
        async with self._manifest_lock:
            try:
                await asyncio.to_thread(self._write_manifest_atomic)
            except Exception as exc:
                self._manifest_dirty = True
                logger.warning(f"manifest 落盘失败（不影响数据写入）: {exc}")
                log_trace_event(
                    event_name="manifest_flush_failed",
                    source_kind="segment_manifest",
                    component=f"{self.namespace}.{self.prefix}",
                    status="failed",
                    summary="Manifest flush failed; kept dirty for retry.",
                    level="WARNING",
                    payload_json={"error": repr(exc)},
                )

    def _write_manifest_atomic(self) -> None:
        manifest = self._load_manifest()
        target = self.manifest_path
        payload = manifest.to_json()
        # 临时名取 uuid：主进程与 cron worker 并发落盘时避免互踩
        tmp_path = target.with_suffix(f".json.{uuid.uuid4().hex}.tmp")
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, target)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _save_manifest(self) -> None:
        """标记 manifest 脏并安排一次去抖落盘。"""
        self._manifest_dirty = True
        self._schedule_manifest_flush()

    def _schedule_manifest_flush(self) -> None:
        task = self._manifest_flush_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        # 残留任务属于别的 loop 时直接丢弃并同步落盘
        if task is not None and self._manifest_loop is not loop:
            self._manifest_flush_task = None
            self._write_manifest_atomic()
            self._manifest_dirty = False
            return
        self._manifest_loop = loop
        self._manifest_flush_task = loop.create_task(self._manifest_flush_loop())

    async def _manifest_flush_loop(self) -> None:
        await asyncio.sleep(MANIFEST_FLUSH_INTERVAL)
        if not self._manifest_dirty:
            return
        self._manifest_dirty = False
        try:
            async with self._manifest_lock:
                await asyncio.to_thread(self._write_manifest_atomic)
        except Exception as exc:
            logger.warning(f"manifest 落盘失败: {exc}")
            self._manifest_dirty = True

    async def _initialize_shard_schema(self, shard_key: str) -> None:
        if self._schema_base is None:
            return
        db_path, _ = self._get_file_paths(shard_key)
        shard_marker = str(db_path.resolve())
        identity = await asyncio.to_thread(self._shard_identity, db_path)
        if (
            identity is not None
            and self._initialized_shards.get(shard_marker) == identity
        ):
            return
        async with self._shard_lock(shard_key):
            identity = await asyncio.to_thread(self._shard_identity, db_path)
            if (
                identity is not None
                and self._initialized_shards.get(shard_marker) == identity
            ):
                return
            trace_id = log_trace_event(
                event_name="initialize_shard_schema",
                source_kind="segment_schema",
                component=f"{self.namespace}.{self.prefix}",
                status="started",
                summary=f"Initializing shard schema for {shard_key}.",
                shard_key=shard_key,
            )
            # 分片文件身份变化时先释放旧引擎：连接池可能仍握着失效 inode
            await db_manager.dispose(str(db_path))
            async with db_manager.open(str(db_path), commit=True) as session:
                engine = session.bind
                assert isinstance(engine, AsyncEngine)
                async with engine.begin() as conn:
                    await conn.run_sync(self._schema_base.metadata.create_all)
                    await conn.run_sync(PatchBase.metadata.create_all)
                await self.patch_registry.apply_all(session, get_current_time())
            new_identity = await asyncio.to_thread(self._shard_identity, db_path)
            if new_identity is None:
                self._initialized_shards.pop(shard_marker, None)
            else:
                self._initialized_shards[shard_marker] = new_identity
            log_trace_event(
                event_name="initialize_shard_schema",
                source_kind="segment_schema",
                component=f"{self.namespace}.{self.prefix}",
                status="success",
                summary=f"Initialized shard schema for {shard_key}.",
                trace_id=trace_id,
                shard_key=shard_key,
            )

    def _compress_file(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Windows：连接句柄会钉住 .db 与 sidecar，先显式关闭再 rename
        conn = sqlite3.connect(source)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        # 先写 tmp 再原子改名，压缩中断只留下 .zst.tmp
        # write_checksum：供解压侧识别截断与位翻转
        tmp_path = destination.with_suffix(f"{destination.suffix}.tmp")
        cctx = zstd.ZstdCompressor(level=10, write_checksum=True)
        try:
            with source.open("rb") as src, tmp_path.open("wb") as dst:
                cctx.copy_stream(src, dst)
            os.replace(tmp_path, destination)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _cleanup_sqlite_sidecars(self, db_path: Path) -> None:
        # -journal 是回滚日志模式下的残留，一并清理
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(f"{db_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()

    def _decompress_file(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # tmp + 原子改名；用 decompressobj 识别截断与校验和失配
        tmp_path = destination.with_suffix(f"{destination.suffix}.tmp")
        dctx = zstd.ZstdDecompressor()
        decompressor = dctx.decompressobj()
        try:
            with source.open("rb") as src, tmp_path.open("wb") as dst:
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    dst.write(decompressor.decompress(chunk))
                dst.write(decompressor.flush())
            if not decompressor.eof:
                raise ArchiveIntegrityError(
                    f"archive is truncated (zstd frame not terminated): {source.name}"
                )
            os.replace(tmp_path, destination)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    async def _touch_segment(
        self,
        shard_key: str,
        *,
        state: SegmentState | None = None,
        durable: bool = False,
    ) -> None:
        db_path, archive_path = self._get_file_paths(shard_key)
        entry = self._manifest_entry(shard_key, db_path, archive_path)
        entry.last_access_at = get_current_time()
        entry.updated_at = get_current_time()
        if state is not None:
            entry.state = state
            if state == SegmentState.WARM:
                entry.hydrated_at = entry.last_access_at
        entry.path = str(db_path)
        entry.archive_path = str(archive_path)
        if await asyncio.to_thread(db_path.exists):
            entry.size_bytes = await asyncio.to_thread(lambda: db_path.stat().st_size)
        if durable:
            await self.flush_manifest()
        else:
            self._save_manifest()

    async def _ensure_budget(self) -> None:
        manifest = self._load_manifest()
        warm_entries = [
            entry
            for entry in manifest.segments.values()
            if entry.state == SegmentState.WARM
        ]
        warm_entries.sort(key=lambda item: item.last_access_at)
        limit_bytes = self.warm_budget_mb * 1024 * 1024
        current_bytes = sum(entry.size_bytes for entry in warm_entries)
        now_ts = get_current_time()
        for entry in warm_entries:
            if (
                current_bytes <= limit_bytes
                and now_ts - entry.last_access_at <= self.warm_ttl_seconds
            ):
                continue
            shard_key = self._shard_key_for_path(Path(entry.path))
            if shard_key is None:
                continue
            # 逐出即删在线文件：仅归档在场时可做
            archive_path = Path(entry.archive_path) if entry.archive_path else None
            if archive_path is None or not await asyncio.to_thread(archive_path.exists):
                logger.warning(
                    f"[{self.namespace}/{self.prefix}] 分片 {shard_key} 无归档可回灌，"
                    f"跳过逐出以免删除唯一副本"
                )
                continue
            # 锁忙时跳过：跨分片等待会形成 AB-BA 死锁
            lock = self._shard_lock(shard_key)
            if lock.is_busy():
                logger.debug(
                    f"[{self.namespace}/{self.prefix}] 分片 {shard_key} 正被占用，"
                    f"跳过本次逐出"
                )
                continue
            async with lock:
                # 状态复检：期间写路径可能把该分片提升回 HOT
                if entry.state != SegmentState.WARM:
                    continue
                db_path = Path(entry.path)
                if await asyncio.to_thread(db_path.exists):
                    try:
                        staging = await self._retire_shard_file(shard_key, db_path)
                    except ArchiveStagingConflictError as exc:
                        # 归档残留阻塞逐出：保留两者并跳过本次
                        logger.error(
                            f"[{self.namespace}/{self.prefix}] 逐出分片 {shard_key} "
                            f"被归档残留阻塞，已保留待人工裁决: {exc}"
                        )
                        log_trace_event(
                            event_name="staging_conflict",
                            source_kind="segment_lifecycle",
                            component=f"{self.namespace}.{self.prefix}",
                            status="conflict",
                            summary=(
                                f"Eviction of {shard_key} blocked by staging residue."
                            ),
                            level="ERROR",
                            shard_key=shard_key,
                        )
                        continue
                    with suppress(OSError):
                        await asyncio.to_thread(os.remove, staging)
            entry.state = SegmentState.COLD
            entry.updated_at = now_ts
            current_bytes -= entry.size_bytes
            entry.size_bytes = 0
        await self.flush_manifest()

    async def _mark_archived(self, shard_key: str) -> None:
        """记录归档完成时间，供 retention 判定。"""
        entry = self._load_manifest().segments.get(shard_key)
        if entry is None:
            return
        entry.archived_at = get_current_time()
        entry.updated_at = entry.archived_at

    def shard_health(self) -> list[dict[str, JsonValue]]:
        """分片健康快照，对应 ES 的 _cat/indices；只读。"""
        manifest = self._load_manifest()
        rows: list[dict[str, JsonValue]] = []
        for shard_key, entry in sorted(manifest.segments.items()):
            db_path = Path(entry.path)
            archive_path = Path(entry.archive_path) if entry.archive_path else None
            rows.append(
                {
                    "shard": shard_key,
                    "state": entry.state.value,
                    "is_active": self._is_active_shard(shard_key),
                    "online": db_path.exists(),
                    "archived": bool(archive_path and archive_path.exists()),
                    "size_bytes": (
                        entry.size_bytes
                        if db_path.exists()
                        else _safe_size(archive_path)
                    ),
                    "last_access_at": entry.last_access_at,
                    "archived_at": entry.archived_at,
                },
            )
        return rows

    def _shard_key_for_path(self, db_path: Path) -> str | None:
        name = db_path.name
        marker = f"{self.prefix}_"
        if not name.startswith(marker) or not name.endswith(".db"):
            return None
        return name[len(marker) : -len(".db")]

    async def _retire_shard_file(self, shard_key: str, db_path: Path) -> Path:
        """把分片文件原子挪走并释放引擎，返回 staging 路径。

        staging 已存在时拒绝覆盖并抛冲突异常。
        """
        staging_path = self._get_staging_path(shard_key)
        if await asyncio.to_thread(staging_path.exists):
            raise ArchiveStagingConflictError(
                f"staging residue exists for shard {shard_key}: {staging_path.name}"
            )
        await db_manager.dispose(str(db_path))
        # 先回放 WAL 已提交帧、再删 sidecar，固定此顺序
        await asyncio.to_thread(self._checkpoint_wal, db_path)
        await asyncio.to_thread(self._cleanup_sqlite_sidecars, db_path)
        await asyncio.to_thread(os.replace, db_path, staging_path)
        return staging_path

    @staticmethod
    def _checkpoint_wal(db_path: Path) -> None:
        """把 WAL 已提交帧合并进主库；-wal 在场时才执行。"""
        if not Path(f"{db_path}-wal").exists():
            return
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()

    async def _restore_staged_shard(self, shard_key: str) -> bool:
        """在线分片缺失而 .archiving 残留在场时把残留归位，返回 True 表示已归位。"""
        staging_path = self._get_staging_path(shard_key)
        if not await asyncio.to_thread(staging_path.exists):
            return False
        db_path, _ = self._get_file_paths(shard_key)
        if await asyncio.to_thread(db_path.exists):
            return False
        # 与 hydrate 同锁：归位与解压/新建并发会写出损坏库
        async with self._shard_lock(shard_key):
            if await asyncio.to_thread(db_path.exists):
                return False
            if not await asyncio.to_thread(staging_path.exists):
                return False
            await db_manager.dispose(str(db_path))
            try:
                await asyncio.to_thread(os.replace, staging_path, db_path)
            except FileNotFoundError:
                # 并发路径已完成归位
                return False
        log_trace_event(
            event_name="recover_staged_shard",
            source_kind="segment_online",
            component=f"{self.namespace}.{self.prefix}",
            status="success",
            summary=f"Restored staged shard residue for {shard_key}.",
            shard_key=shard_key,
        )
        return True

    async def _quarantine_archive(
        self,
        shard_key: str,
        error: BaseException,
    ) -> Path:
        """把损坏归档改名隔离，返回隔离路径。

        改名后 hydrate 路径随之避开它，该分片显式表现为离线/损坏。
        """
        archive_path = self._get_file_paths(shard_key)[1]
        quarantine = archive_path.with_suffix(
            f"{archive_path.suffix}.corrupt.{int(get_current_time())}"
        )
        try:
            await asyncio.to_thread(os.replace, archive_path, quarantine)
        except OSError as exc:
            logger.error(
                f"[{self.namespace}/{self.prefix}] 隔离损坏归档失败 "
                f"{archive_path.name}: {exc}"
            )
            return archive_path
        logger.error(
            f"[{self.namespace}/{self.prefix}] 归档 {archive_path.name} 已损坏"
            f"并隔离为 {quarantine.name}（{error}）；该分片需要人工处置",
        )
        return quarantine

    async def _ensure_shard_online(
        self,
        shard_key: str,
        *,
        cold_policy: ColdPolicy | None = None,
        create_if_missing: bool = False,
    ) -> bool:
        policy = cold_policy or self.cold_policy
        db_path, archive_path = self._get_file_paths(shard_key)
        entry = self._manifest_entry(shard_key, db_path, archive_path)
        # 归档残留在场时先归位，避免下方重建空库
        if not await asyncio.to_thread(db_path.exists):
            restored = await self._restore_staged_shard(shard_key)
            if restored:
                entry.state = SegmentState.HOT
                entry.updated_at = get_current_time()
        if await asyncio.to_thread(db_path.exists):
            await self._touch_segment(
                shard_key,
                state=(
                    SegmentState.HOT
                    if self._is_active_shard(shard_key)
                    else entry.state
                ),
            )
            return True
        if not await asyncio.to_thread(archive_path.exists):
            if create_if_missing:
                entry.state = SegmentState.HOT
                entry.updated_at = get_current_time()
                await self.flush_manifest()
                log_trace_event(
                    event_name="create_shard_placeholder",
                    source_kind="segment_online",
                    component=f"{self.namespace}.{self.prefix}",
                    status="success",
                    summary=f"Created online shard placeholder for {shard_key}.",
                    shard_key=shard_key,
                )
            return create_if_missing
        if policy == ColdPolicy.DENY:
            raise FileNotFoundError(f"Cold shard is offline: {archive_path.name}")
        if policy == ColdPolicy.SKIP:
            logger.warning(f"冷库跳过: {archive_path.name}")
            return False

        safe_archive = self._safe_resolve(archive_path)
        safe_db = self._safe_resolve(db_path)
        logger.info(f"唤醒冷库: 正在解压 {safe_archive.name}")
        trace_id = log_trace_event(
            event_name="hydrate_shard",
            source_kind="segment_online",
            component=f"{self.namespace}.{self.prefix}",
            status="started",
            summary=f"Hydrating cold shard {shard_key}.",
            shard_key=shard_key,
        )
        async with self._shard_lock(shard_key):
            if await asyncio.to_thread(db_path.exists):
                await self._touch_segment(shard_key, state=SegmentState.WARM)
                return True
            try:
                await asyncio.to_thread(self._decompress_file, safe_archive, safe_db)
            except ArchiveIntegrityError as exc:
                # 损坏归档：隔离留证并把该分片显式报错
                quarantine = await self._quarantine_archive(shard_key, exc)
                log_trace_event(
                    event_name="hydrate_shard",
                    source_kind="segment_online",
                    component=f"{self.namespace}.{self.prefix}",
                    status="failed",
                    summary=f"Hydrating cold shard {shard_key} detected corruption.",
                    level="ERROR",
                    trace_id=trace_id,
                    shard_key=shard_key,
                    payload_json={
                        "error": repr(exc),
                        "quarantined": quarantine.name,
                    },
                )
                raise
            await self._touch_segment(shard_key, state=SegmentState.WARM)
        # 放分片锁之外执行，避免嵌套加锁
        await self._ensure_budget()
        logger.success(f"冷库解压完成: {safe_db.name}")
        log_trace_event(
            event_name="hydrate_shard",
            source_kind="segment_online",
            component=f"{self.namespace}.{self.prefix}",
            status="success",
            summary=f"Hydrated cold shard {shard_key}.",
            trace_id=trace_id,
            shard_key=shard_key,
        )
        return True

    @asynccontextmanager
    async def read_session(
        self,
        *args: object,
        time_ctx: datetime | None = None,
        cold_policy: ColdPolicy | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[AsyncSession, None]:
        _ = (args, kwargs)
        if time_ctx is None:
            time_ctx = self._tz_now().datetime
        shard_key = self._get_shard_key(time_ctx)
        async with self._shard_lock(shard_key):
            is_online = await self._ensure_shard_online(
                shard_key,
                cold_policy=cold_policy,
                create_if_missing=self._is_active_shard(shard_key),
            )
            if not is_online:
                raise FileNotFoundError(f"Shard is not online: {shard_key}")
            await self._initialize_shard_schema(shard_key)
            db_path, _ = self._get_file_paths(shard_key)
            async with db_manager.open(str(db_path), commit=False) as sess:
                yield sess

    @asynccontextmanager
    async def write_session(
        self,
        *args: object,
        time_ctx: datetime | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[AsyncSession, None]:
        _ = (args, kwargs)
        if time_ctx is None:
            time_ctx = self._tz_now().datetime
        shard_key = self._get_shard_key(time_ctx)
        # 全程持分片锁：归档须等在途写事务结束后才能动文件
        async with self._shard_lock(shard_key):
            await self._ensure_shard_online(
                shard_key,
                cold_policy=ColdPolicy.HYDRATE,
                create_if_missing=True,
            )
            await self._initialize_shard_schema(shard_key)
            await self._touch_segment(shard_key, state=SegmentState.HOT)
            db_path, _ = self._get_file_paths(shard_key)
            async with db_manager.open(str(db_path), commit=True) as sess:
                yield sess
            await self._touch_segment(shard_key, state=SegmentState.HOT)

    async def map_reduce[T](
        self,
        start_time: datetime,
        end_time: datetime,
        query_func: Callable[[AsyncSession], Awaitable[T]],
        *,
        cold_policy: ColdPolicy | None = None,
    ) -> list[T]:
        # 窗口边界先归一到 self.tz 再取月，与分片切分口径一致
        start_local = self._to_local(start_time)
        end_local = self._to_local(end_time)
        curr = start_local.floor("month")
        end = end_local.floor("month")
        months_span = (
            (end_local.year - start_local.year) * 12
            + end_local.month
            - start_local.month
        )
        # 通用分片扫描的库级护栏，业务侧应各自收窄窗口
        if months_span > MAX_MAP_REDUCE_MONTH_SPAN:
            from src.lib.i18n.catalog import tr

            raise ValueError(
                tr(
                    "zh-CN",
                    "db.map_reduce.month_span_exceeded",
                    max_months=MAX_MAP_REDUCE_MONTH_SPAN,
                )
            )
        keys: list[str] = []
        while curr <= end:
            keys.append(curr.strftime(self.fmt))
            curr = curr.shift(months=1)
        shard_keys = list(dict.fromkeys(keys))
        log_trace_event(
            event_name="map_reduce",
            source_kind="segment_scan",
            component=f"{self.namespace}.{self.prefix}",
            status="started",
            summary=(
                f"Scanning {len(shard_keys)} shard(s) from "
                f"{start_local.strftime('%Y-%m')} to {end_local.strftime('%Y-%m')}."
            ),
            batch_size=len(shard_keys),
            # list[str] 赋给 JsonValue 需显式 cast
            payload_json=cast("JsonObject", {"shard_keys": list(shard_keys)}),
        )
        semaphore = asyncio.Semaphore(self.map_reduce_concurrency)

        async def _run(key: str) -> T | None:
            policy = cold_policy or self.cold_policy
            async with semaphore:
                # 全程持分片锁：锁内检查与 open 之间是 TOCTOU 窗口
                async with self._shard_lock(key):
                    try:
                        is_online = await self._ensure_shard_online(
                            key,
                            cold_policy=policy,
                            create_if_missing=False,
                        )
                    except FileNotFoundError:
                        if policy == ColdPolicy.SKIP:
                            return None
                        raise
                    if not is_online:
                        return None
                    await self._initialize_shard_schema(key)
                    db_path, _ = self._get_file_paths(key)
                    if not db_path.exists():
                        return None
                    async with db_manager.open(str(db_path), commit=False) as sess:
                        return await query_func(sess)

        results = await asyncio.gather(*(_run(key) for key in shard_keys))
        return [result for result in results if result is not None]

    async def run_archiver_task(self) -> SegmentArchiveReport:
        """ILM 巡检：残留自愈 -> hot -> warm -> cold，并按保留期回收。

        返回结构化报告：调用方据此如实上报失败/冲突。
        """
        report = SegmentArchiveReport()
        now = self._tz_now()
        active_keys = {
            now.shift(months=-offset).strftime(self.fmt)
            for offset in range(self.active_window_months)
        }
        # 阶段 0：上次归档中断留下的 .archiving 残留自愈
        conflicted: set[str] = set()
        residue_pattern = f"{self.prefix}_*{ARCHIVE_STAGING_SUFFIX}"
        for residue in sorted(self.base_dir.glob(residue_pattern)):
            shard_key = residue.name.removeprefix(f"{self.prefix}_").removesuffix(
                ARCHIVE_STAGING_SUFFIX
            )
            if not shard_key:
                continue
            db_path, _ = self._get_file_paths(shard_key)
            async with self._shard_lock(shard_key):
                if await asyncio.to_thread(db_path.exists):
                    conflicted.add(shard_key)
                    report.failures.append(
                        SegmentArchiveFailure(
                            shard_key=shard_key,
                            outcome=SegmentArchiveOutcome.CONFLICT,
                            reason=(
                                f"staging residue coexists with online shard: "
                                f"{residue.name}"
                            ),
                        )
                    )
                    logger.error(
                        f"[{self.namespace}/{self.prefix}] 分片 {shard_key} 同时存在"
                        f"在线库与归档残留 {residue.name}，已保留两者待人工裁决"
                    )
                    log_trace_event(
                        event_name="staging_conflict",
                        source_kind="segment_lifecycle",
                        component=f"{self.namespace}.{self.prefix}",
                        status="conflict",
                        summary=(
                            f"Staging residue coexists with online shard "
                            f"{shard_key}; both preserved."
                        ),
                        level="ERROR",
                        shard_key=shard_key,
                    )
                    continue
                if await self._restore_staged_shard(shard_key):
                    report.healed.append(shard_key)
        for db_file in self.base_dir.glob(f"{self.prefix}_*.db"):
            file_key = db_file.stem.removeprefix(f"{self.prefix}_")
            if not file_key:
                continue
            # 阶段 0 已判定冲突的分片留在该集合里，此处跳过
            if file_key in conflicted:
                continue
            size_mb = await asyncio.to_thread(
                lambda: db_file.stat().st_size / (1024 * 1024)
            )
            # 窗口外或体积超阈值的分片都走 seal
            oversized = size_mb >= SEGMENT_SIZE_ROLLOVER_MB
            if file_key in active_keys and not oversized:
                await self._touch_segment(file_key, state=SegmentState.HOT)
                continue
            if file_key in active_keys and oversized:
                logger.warning(
                    f"分片 {file_key} 已达 {size_mb:.1f}MB，超过 "
                    f"{SEGMENT_SIZE_ROLLOVER_MB}MB 阈值，将被 seal",
                )
            outcome, reason = await self._archive_shard(file_key, force=oversized)
            if outcome == SegmentArchiveOutcome.ARCHIVED:
                report.archived.append(file_key)
            elif outcome == SegmentArchiveOutcome.SKIPPED:
                report.skipped.append(file_key)
            else:
                report.failures.append(
                    SegmentArchiveFailure(
                        shard_key=file_key,
                        outcome=outcome,
                        reason=reason,
                    )
                )
        await self._ensure_budget()
        if self.retention_months > 0:
            await self._enforce_retention(now)
        await self.flush_manifest()
        log_trace_event(
            event_name="archive_sweep",
            source_kind="segment_lifecycle",
            component=f"{self.namespace}.{self.prefix}",
            status="success" if report.ok else "partial",
            summary=f"Archive sweep finished: {report.summary()}",
            level="INFO" if report.ok else "ERROR",
            payload_json={
                "archived": list(report.archived),
                "skipped": list(report.skipped),
                "healed": list(report.healed),
                "failures": [
                    {
                        "shard_key": f.shard_key,
                        "outcome": f.outcome.value,
                        "reason": f.reason,
                    }
                    for f in report.failures
                ],
            },
        )
        return report

    async def _enforce_retention(self, now: arrow.Arrow) -> None:
        """ILM delete 阶段：删除超保留期的 .db.zst 归档。

        确认分片已出活跃窗口后才动手，在线 .db 一概保留。
        """
        manifest = self._load_manifest()
        cutoff = now.shift(months=-self.retention_months).strftime(self.fmt)
        removed = 0
        freed = 0
        for shard_key, entry in list(manifest.segments.items()):
            if entry.state == SegmentState.HOT:
                continue
            if shard_key >= cutoff:
                continue
            archive_path = entry.archive_path
            if not archive_path:
                continue
            path = Path(archive_path)
            if not await asyncio.to_thread(path.exists):
                manifest.segments.pop(shard_key, None)
                continue
            size = await asyncio.to_thread(lambda: path.stat().st_size)
            async with self._shard_lock(shard_key):
                with suppress(OSError):
                    await asyncio.to_thread(os.remove, path)
                    removed += 1
                    freed += size
            manifest.segments.pop(shard_key, None)
            logger.info(f"保留期回收: {path.name} ({size / 1024 / 1024:.1f}MB)")
        if removed:
            logger.success(
                f"保留期回收完成: 删除 {removed} 个归档，释放 "
                f"{freed / 1024 / 1024:.1f}MB",
            )
            log_trace_event(
                event_name="retention_sweep",
                source_kind="segment_lifecycle",
                component=f"{self.namespace}.{self.prefix}",
                status="success",
                summary=f"Removed {removed} archived segment(s) past retention.",
                payload_json={
                    "removed": removed,
                    "freed_bytes": freed,
                    "retention_months": self.retention_months,
                },
            )

    async def _archive_shard(
        self,
        shard_key: str,
        *,
        force: bool = False,
    ) -> tuple[SegmentArchiveOutcome, str]:
        """两阶段归档单个分片，返回 (结果, 原因)；force 跳过活跃窗口保护。
        阶段一（持锁）释放引擎并 rename 成 staging，阶段二（松锁）压缩后删除。
        """
        db_path, archive_path = self._get_file_paths(shard_key)
        trace_id = log_trace_event(
            event_name="archive_shard",
            source_kind="segment_archive",
            component=f"{self.namespace}.{self.prefix}",
            status="started",
            summary=f"Archiving shard {shard_key}.",
            shard_key=shard_key,
        )
        try:
            async with self._shard_lock(shard_key):
                # 归档期间可能正好跨月，复检活跃窗口；force 跳过该保护
                if not force and self._is_active_shard(shard_key):
                    await self._touch_segment(shard_key, state=SegmentState.HOT)
                    return SegmentArchiveOutcome.SKIPPED, "active window"
                if not await asyncio.to_thread(db_path.exists):
                    return SegmentArchiveOutcome.SKIPPED, "shard offline"
                safe_db = self._safe_resolve(db_path)
                safe_archive = self._safe_resolve(archive_path)
                logger.info(f"归档冷库: {safe_db.name} -> {safe_archive.name}")
                retired = await self._retire_shard_file(shard_key, safe_db)
                safe_staging = self._safe_resolve(retired)
            try:
                await asyncio.to_thread(self._compress_file, safe_staging, safe_archive)
            except Exception as exc:
                async with self._shard_lock(shard_key):
                    with suppress(OSError):
                        if await asyncio.to_thread(safe_staging.exists):
                            await asyncio.to_thread(os.replace, safe_staging, safe_db)
                logger.error(f"归档失败，已回滚分片 {safe_db.name}: {exc}")
                log_trace_event(
                    event_name="archive_shard",
                    source_kind="segment_archive",
                    component=f"{self.namespace}.{self.prefix}",
                    status="failed",
                    summary=f"Archiving shard {shard_key} failed, rolled back.",
                    level="ERROR",
                    trace_id=trace_id,
                    shard_key=shard_key,
                    payload_json={"error": repr(exc)},
                )
                return SegmentArchiveOutcome.FAILED, repr(exc)
            with suppress(OSError):
                await asyncio.to_thread(os.remove, safe_staging)
            async with self._shard_lock(shard_key):
                db_path, _ = self._get_file_paths(shard_key)
                if await asyncio.to_thread(db_path.exists):
                    await self._touch_segment(
                        shard_key,
                        state=SegmentState.HOT,
                    )
                else:
                    await self._touch_segment(
                        shard_key,
                        state=SegmentState.COLD,
                        durable=True,
                    )
                    await self._mark_archived(shard_key)
            logger.success(f"归档完成，已释放原始磁盘占用: {safe_db.name}")
            log_trace_event(
                event_name="archive_shard",
                source_kind="segment_archive",
                component=f"{self.namespace}.{self.prefix}",
                status="success",
                summary=f"Archived shard {shard_key}.",
                trace_id=trace_id,
                shard_key=shard_key,
            )
            return SegmentArchiveOutcome.ARCHIVED, ""
        except ArchiveStagingConflictError as exc:
            logger.error(
                f"归档分片 {shard_key} 冲突：存在未压缩的 staging 残留，"
                f"拒绝覆盖以免丢数据: {exc}"
            )
            log_trace_event(
                event_name="archive_shard",
                source_kind="segment_archive",
                component=f"{self.namespace}.{self.prefix}",
                status="conflict",
                summary=f"Archiving shard {shard_key} refused: staging residue.",
                level="ERROR",
                trace_id=trace_id,
                shard_key=shard_key,
                payload_json={"error": repr(exc)},
            )
            return SegmentArchiveOutcome.CONFLICT, repr(exc)
        except Exception as exc:
            logger.error(f"归档分片 {shard_key} 异常: {exc}")
            log_trace_event(
                event_name="archive_shard",
                source_kind="segment_archive",
                component=f"{self.namespace}.{self.prefix}",
                status="failed",
                summary=f"Archiving shard {shard_key} failed.",
                level="ERROR",
                trace_id=trace_id,
                shard_key=shard_key,
                payload_json={"error": repr(exc)},
            )
            return SegmentArchiveOutcome.FAILED, repr(exc)

    def iter_backup_sources(self) -> list[BackupSource]:
        sources: list[BackupSource] = []
        for db_file in sorted(self.base_dir.glob(f"{self.prefix}_*.db")):
            shard_key = db_file.stem.removeprefix(f"{self.prefix}_")
            sources.append(
                BackupSource(
                    namespace=self.namespace,
                    kind=self.prefix,
                    path=db_file,
                    shard_key=shard_key,
                    is_active=self._is_active_shard(shard_key),
                )
            )
        for archive_file in sorted(self.base_dir.glob(f"{self.prefix}_*.db.zst")):
            shard_key = archive_file.name.removeprefix(f"{self.prefix}_").removesuffix(
                ".db.zst"
            )
            sources.append(
                BackupSource(
                    namespace=self.namespace,
                    kind=self.prefix,
                    path=archive_file,
                    shard_key=shard_key,
                    is_active=False,
                    is_archive=True,
                )
            )
        manifest = self.manifest_path
        if manifest.exists():
            sources.append(
                BackupSource(
                    namespace=self.namespace,
                    kind=f"{self.prefix}_manifest",
                    path=manifest,
                    is_active=True,
                    is_archive=True,
                )
            )
        return sources


@dataclass
class CounterStore(SegmentStore):
    """计数型分段存储。"""


@dataclass
class EventStore(SegmentStore):
    """事件型分段存储。"""


__all__ = [
    "ArchiveCodec",
    "ArchiveStagingConflictError",
    "BaseDB",
    "ColdPolicy",
    "CounterStore",
    "EventStore",
    "MomentLike",
    "SegmentArchiveFailure",
    "SegmentArchiveOutcome",
    "SegmentArchiveReport",
    "SegmentConfig",
    "SegmentManifest",
    "SegmentManifestEntry",
    "SegmentState",
    "SegmentStore",
    "StateStore",
]
