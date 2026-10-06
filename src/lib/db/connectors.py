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
from contextlib import _AsyncGeneratorContextManager, asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import arrow
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase
import zstandard as zstd

from src.lib.consts import GLOBAL_DB_ROOT
from src.lib.db.backup import BackupSource
from src.lib.trace_log import log_trace_event
from src.lib.utils.common import get_current_time
from src.logger import logger

from .manager import db_manager
from .schema import PatchBase, PatchRegistry

MAX_MAP_REDUCE_MONTH_SPAN = 24

# 分片键的业务时区。分片路由（execute_batch_write 与各 plugin writer）一律按
# Asia/Shanghai 切分，因此「当前活跃分片」的判定必须使用同一时区；否则每月 1 号
# 00:00-08:00 CST 期间，当月刚建立的分片会被误判为冷分片并被归档任务删除。
DEFAULT_SEGMENT_TZ = "Asia/Shanghai"

# manifest 落盘去抖间隔（秒）。manifest 只是加速判断的旁路状态，真正的数据来源
# 是分片文件本身，因此允许延迟落盘。
MANIFEST_FLUSH_INTERVAL = 5.0

# 两阶段归档的中间态后缀。归档先把分片 rename 成该后缀，压缩成功后再删除。
ARCHIVE_STAGING_SUFFIX = ".archiving"


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


@dataclass(slots=True)
class SegmentManifest:
    version: int = 1
    segments: dict[str, SegmentManifestEntry] = field(default_factory=dict)

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
            )
            for key, value in payload.get("segments", {}).items()
        }
        return cls(version=int(payload.get("version", 1)), segments=segments)


@dataclass(slots=True)
class SegmentConfig:
    granularity: str = "month"
    hot_window: int = 2
    warm_ttl_seconds: int = 24 * 60 * 60
    warm_budget_mb: int = 512
    cold_policy: ColdPolicy = ColdPolicy.DENY
    archive_codec: ArchiveCodec = ArchiveCodec.ZSTD
    map_reduce_concurrency: int = 4


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

    @abstractmethod
    def read_session(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> _AsyncGeneratorContextManager[AsyncSession, None]:
        pass

    @abstractmethod
    def write_session(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> _AsyncGeneratorContextManager[AsyncSession, None]:
        pass

    def session(
        self,
        commit: bool = True,
        *args: Any,
        **kwargs: Any,
    ) -> _AsyncGeneratorContextManager[AsyncSession, None]:
        if commit:
            return self.write_session(*args, **kwargs)
        return self.read_session(*args, **kwargs)

    async def init(self, base: type[DeclarativeBase]) -> None:
        await self.init_schema(base)

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

    def read_session(self) -> _AsyncGeneratorContextManager[AsyncSession, None]:
        path = self.base_dir / self.filename
        return db_manager.open(str(path), commit=False)

    def write_session(self) -> _AsyncGeneratorContextManager[AsyncSession, None]:
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


class _ReentrantShardLock:
    """按 task 可重入的分片锁。

    write_session 会在整个会话期间持锁（否则归档任务会在写事务飞行途中
    dispose 引擎并 rename 文件，导致写事务重连到一个新建的空库）。而该持锁
    路径内部又会调用 _ensure_shard_online / _initialize_shard_schema，这两者
    也需要同一把锁，因此这里做按 task 的可重入处理，避免自死锁。
    """

    __slots__ = ("_depth", "_lock", "_owner")

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._depth = 0
        self._owner: asyncio.Task[Any] | None = None

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
    _locks: dict[str, _ReentrantShardLock] = field(default_factory=dict)
    _initialized_shards: dict[str, tuple[int, int]] = field(default_factory=dict)
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

    @property
    def hydrate_dir(self) -> Path:
        path = self.base_dir / "_hydrate_cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _tz_now(self) -> arrow.Arrow:
        """按本 store 的业务时区取「当前时刻」。"""
        return arrow.get(get_current_time()).to(self.tz)

    def _lock_key(self, shard_key: str) -> str:
        return f"shard:{shard_key}"

    def _get_lock(self, shard_key: str) -> _ReentrantShardLock:
        if shard_key not in self._locks:
            self._locks[shard_key] = _ReentrantShardLock()
        return self._locks[shard_key]

    def _shard_lock(self, shard_key: str) -> _ReentrantShardLock:
        """分片级互斥锁。归档、冷库唤醒、schema 初始化共用同一把锁。"""
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
        """强制把 manifest 落盘。"""
        task = self._manifest_flush_task
        if task is not None and not task.done():
            if self._manifest_loop is asyncio.get_running_loop():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            self._manifest_flush_task = None
        self._manifest_dirty = False
        async with self._manifest_lock:
            await asyncio.to_thread(self._write_manifest_atomic)

    def _write_manifest_atomic(self) -> None:
        manifest = self._load_manifest()
        target = self.manifest_path
        tmp_path = target.with_suffix(".json.tmp")
        tmp_path.write_text(manifest.to_json(), encoding="utf-8")
        os.replace(tmp_path, target)

    def _save_manifest(self) -> None:
        """标记 manifest 脏，并安排一次去抖落盘。

        manifest 只是分片状态的旁路缓存，真实数据在分片文件本身，因此允许延迟
        落盘；这里只保证「进程不会被截断的 JSON 打死」——真正落盘走 tmp +
        os.replace 原子替换。
        """
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
        # 后台任务不能跨事件循环存活（测试里每个用例各有一个 loop）。若残留任务
        # 属于别的 loop，直接丢弃并同步落盘，避免 "attached to a different loop"。
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
            # 分片文件已被删除/重建（identity 变化或为 None）时，旧引擎的连接池里
            # 仍握着那个已失效的 inode；必须先释放，否则后续写入会静默落到被删除
            # 的文件上，数据看似写成功实则全部丢失。
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
        with sqlite3.connect(source) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        cctx = zstd.ZstdCompressor(level=10)
        with source.open("rb") as src, destination.open("wb") as dst:
            cctx.copy_stream(src, dst)

    def _cleanup_sqlite_sidecars(self, db_path: Path) -> None:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{db_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()

    def _decompress_file(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        dctx = zstd.ZstdDecompressor()
        with source.open("rb") as src, destination.open("wb") as dst:
            dctx.copy_stream(src, dst)

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
            async with self._shard_lock(shard_key):
                db_path = Path(entry.path)
                if await asyncio.to_thread(db_path.exists):
                    staging = await self._retire_shard_file(shard_key, db_path)
                    with suppress(OSError):
                        await asyncio.to_thread(os.remove, staging)
            entry.state = SegmentState.COLD
            entry.updated_at = now_ts
            current_bytes -= entry.size_bytes
            entry.size_bytes = 0
        await self.flush_manifest()

    def _shard_key_for_path(self, db_path: Path) -> str | None:
        name = db_path.name
        marker = f"{self.prefix}_"
        if not name.startswith(marker) or not name.endswith(".db"):
            return None
        return name[len(marker) : -len(".db")]

    async def _retire_shard_file(self, shard_key: str, db_path: Path) -> Path:
        """把分片文件原子挪走并释放引擎，返回 staging 路径。

        用 rename 而不是 unlink：rename 之后原路径立即消失，后续写入方看到的是
        「分片不存在」，会走新建/唤醒路径，绝不会写进正在被压缩的文件；而已经在
        飞行中的写事务仍然写在这个 inode 上，其数据会随 staging 一起进入归档，
        不会丢。
        """
        staging_path = self._get_staging_path(shard_key)
        await db_manager.dispose(str(db_path))
        await asyncio.to_thread(self._cleanup_sqlite_sidecars, db_path)
        await asyncio.to_thread(os.replace, db_path, staging_path)
        return staging_path

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
            await asyncio.to_thread(self._decompress_file, safe_archive, safe_db)
            await self._touch_segment(shard_key, state=SegmentState.WARM)
        # 放在分片锁之外：_ensure_budget 会逐个淘汰其它分片，嵌套加锁会带来死锁风险。
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
        time_ctx: datetime | None = None,
        cold_policy: ColdPolicy | None = None,
    ) -> AsyncGenerator[AsyncSession, None]:
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
        time_ctx: datetime | None = None,
    ) -> AsyncGenerator[AsyncSession, None]:
        if time_ctx is None:
            time_ctx = self._tz_now().datetime
        shard_key = self._get_shard_key(time_ctx)
        # 全程持分片锁：归档任务必须等在途写事务结束后才能 rename/删除分片，
        # 否则 db_manager.dispose 会把在途 session 重新接到一个新建的空库上。
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
        curr = arrow.get(start_time).floor("month")
        end = arrow.get(end_time).floor("month")
        months_span = (
            (end_time.year - start_time.year) * 12 + end_time.month - start_time.month
        )
        # Store-level safety guard for generic shard scans. Business modules should
        # enforce their own narrower windows before reaching this layer.
        if months_span > MAX_MAP_REDUCE_MONTH_SPAN:
            from src.lib.i18n.runtime import tr

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
                f"{start_time.strftime('%Y-%m')} to {end_time.strftime('%Y-%m')}."
            ),
            batch_size=len(shard_keys),
            payload_json={"shard_keys": shard_keys},
        )
        semaphore = asyncio.Semaphore(self.map_reduce_concurrency)

        async def _run(key: str) -> T | None:
            policy = cold_policy or self.cold_policy
            async with semaphore:
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

    async def run_archiver_task(self) -> None:
        # 归档窗口与分片路由共用同一时区，避免月初 UTC/CST 错位把当月分片误归档。
        now = self._tz_now()
        active_keys = [
            now.shift(months=-offset).strftime(self.fmt)
            for offset in range(self.active_window_months)
        ]
        for db_file in self.base_dir.glob(f"{self.prefix}_*.db"):
            file_key = db_file.stem.removeprefix(f"{self.prefix}_")
            if not file_key:
                continue
            if file_key in active_keys or self._is_active_shard(file_key):
                await self._touch_segment(file_key, state=SegmentState.HOT)
                continue
            await self._archive_shard(file_key)
        await self._ensure_budget()
        await self.flush_manifest()

    async def _archive_shard(self, shard_key: str) -> bool:
        """两阶段归档单个分片。

        阶段一（持锁）：释放引擎并把分片原子 rename 成 staging。
        阶段二（不持锁）：压缩 staging 到 .db.zst，成功后删除，失败则改回原名。

        之所以用 rename 而不是直接 unlink，是因为 unlink 之后已经在飞行中的写事务
        仍会写进那个已被删除的 inode，数据静默丢失；rename 之后写入方看到的是
        「分片不存在」，会走新建/唤醒路径，而旧 inode 上的数据随 staging 进入归档。
        """
        db_path, archive_path = self._get_file_paths(shard_key)
        staging_path = self._get_staging_path(shard_key)
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
                # 归档期间可能正好跨月，重新确认一次，避免删掉刚转成活跃的分片。
                if self._is_active_shard(shard_key):
                    await self._touch_segment(shard_key, state=SegmentState.HOT)
                    return False
                if not await asyncio.to_thread(db_path.exists):
                    return False
                safe_db = self._safe_resolve(db_path)
                safe_archive = self._safe_resolve(archive_path)
                safe_staging = self._safe_resolve(staging_path)
                logger.info(f"归档冷库: {safe_db.name} -> {safe_archive.name}")
                retired = await self._retire_shard_file(shard_key, safe_db)
                safe_staging = self._safe_resolve(retired)
            try:
                await asyncio.to_thread(self._compress_file, safe_staging, safe_archive)
            except Exception as exc:
                async with self._shard_lock(shard_key):
                    with suppress(OSError):
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
                return False
            with suppress(OSError):
                await asyncio.to_thread(os.remove, safe_staging)
            async with self._shard_lock(shard_key):
                await self._touch_segment(
                    shard_key,
                    state=(
                        SegmentState.HOT
                        if self._is_active_shard(shard_key)
                        else SegmentState.COLD
                    ),
                    durable=True,
                )
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
            return True
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
            return False

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
    "BaseDB",
    "ColdPolicy",
    "CounterStore",
    "EventStore",
    "SegmentConfig",
    "SegmentManifest",
    "SegmentManifestEntry",
    "SegmentState",
    "SegmentStore",
    "StateStore",
]
