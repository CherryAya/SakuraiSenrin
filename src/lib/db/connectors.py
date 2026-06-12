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
from contextlib import _AsyncGeneratorContextManager, asynccontextmanager
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
from src.lib.utils.common import get_current_time
from src.logger import logger

from .manager import db_manager
from .schema import PatchBase, PatchRegistry


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
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _initialized_shards: set[str] = field(default_factory=set)
    _manifest: SegmentManifest | None = field(default=None, init=False, repr=False)

    @property
    def manifest_path(self) -> Path:
        return self.base_dir / f"{self.prefix}_manifest.json"

    @property
    def hydrate_dir(self) -> Path:
        path = self.base_dir / "_hydrate_cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _get_lock(self, shard_key: str) -> asyncio.Lock:
        if shard_key not in self._locks:
            self._locks[shard_key] = asyncio.Lock()
        return self._locks[shard_key]

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

    def _is_active_shard(self, shard_key: str) -> bool:
        now = arrow.get(get_current_time()).floor("month")
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

    def _save_manifest(self) -> None:
        manifest = self._load_manifest()
        self.manifest_path.write_text(manifest.to_json(), encoding="utf-8")

    async def _initialize_shard_schema(self, shard_key: str) -> None:
        if self._schema_base is None:
            return
        db_path, _ = self._get_file_paths(shard_key)
        shard_marker = str(db_path.resolve())
        if shard_marker in self._initialized_shards:
            return
        async with self._get_lock(f"schema:{shard_marker}"):
            if shard_marker in self._initialized_shards:
                return
            async with db_manager.open(str(db_path), commit=True) as session:
                engine = session.bind
                assert isinstance(engine, AsyncEngine)
                async with engine.begin() as conn:
                    await conn.run_sync(self._schema_base.metadata.create_all)
                    await conn.run_sync(PatchBase.metadata.create_all)
                await self.patch_registry.apply_all(session, get_current_time())
            self._initialized_shards.add(shard_marker)

    def _compress_file(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        cctx = zstd.ZstdCompressor(level=10)
        with source.open("rb") as src, destination.open("wb") as dst:
            cctx.copy_stream(src, dst)

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
        if db_path.exists():
            entry.size_bytes = db_path.stat().st_size
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
            db_path = Path(entry.path)
            if await asyncio.to_thread(db_path.exists):
                await db_manager.dispose(str(db_path))
                await asyncio.to_thread(os.remove, db_path)
            entry.state = SegmentState.COLD
            entry.updated_at = now_ts
            current_bytes -= entry.size_bytes
            entry.size_bytes = 0
        self._save_manifest()

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
        if db_path.exists():
            await self._touch_segment(
                shard_key,
                state=(
                    SegmentState.HOT
                    if self._is_active_shard(shard_key)
                    else entry.state
                ),
            )
            return True
        if not archive_path.exists():
            if create_if_missing:
                entry.state = SegmentState.HOT
                entry.updated_at = get_current_time()
                self._save_manifest()
            return create_if_missing
        if policy == ColdPolicy.DENY:
            raise FileNotFoundError(f"Cold shard is offline: {archive_path.name}")
        if policy == ColdPolicy.SKIP:
            logger.warning(f"冷库跳过: {archive_path.name}")
            return False

        async with self._get_lock(shard_key):
            if db_path.exists():
                await self._touch_segment(shard_key, state=SegmentState.WARM)
                return True
            safe_archive = self._safe_resolve(archive_path)
            safe_db = self._safe_resolve(db_path)
            logger.info(f"唤醒冷库: 正在解压 {safe_archive.name}")
            await asyncio.to_thread(self._decompress_file, safe_archive, safe_db)
            await self._touch_segment(shard_key, state=SegmentState.WARM)
            await self._ensure_budget()
            logger.success(f"冷库解压完成: {safe_db.name}")
            return True

    @asynccontextmanager
    async def read_session(
        self,
        time_ctx: datetime | None = None,
        cold_policy: ColdPolicy | None = None,
    ) -> AsyncGenerator[AsyncSession, None]:
        if time_ctx is None:
            time_ctx = arrow.get(get_current_time()).datetime
        shard_key = self._get_shard_key(time_ctx)
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
            time_ctx = arrow.get(get_current_time()).datetime
        shard_key = self._get_shard_key(time_ctx)
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
        if months_span > 24:
            raise ValueError("目标超出 24 个月的最大范围")
        keys: list[str] = []
        while curr <= end:
            keys.append(curr.strftime(self.fmt))
            curr = curr.shift(months=1)
        shard_keys = list(dict.fromkeys(keys))
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
        now = arrow.get(get_current_time())
        active_keys = [
            now.shift(months=-offset).strftime(self.fmt)
            for offset in range(self.active_window_months)
        ]
        for db_file in self.base_dir.glob(f"{self.prefix}_*.db"):
            file_key = db_file.stem.replace(f"{self.prefix}_", "")
            if file_key in active_keys:
                await self._touch_segment(file_key, state=SegmentState.HOT)
                continue
            archive_path = db_file.with_suffix(".db.zst")
            async with self._get_lock(file_key):
                safe_db = self._safe_resolve(db_file)
                safe_archive = self._safe_resolve(archive_path)
                logger.info(f"归档冷库: {safe_db.name} -> {safe_archive.name}")
                await asyncio.to_thread(self._compress_file, safe_db, safe_archive)
                await db_manager.dispose(str(safe_db))
                await asyncio.to_thread(os.remove, safe_db)
                await self._touch_segment(file_key, state=SegmentState.COLD)
                logger.success(f"归档完成，已释放原始磁盘占用: {safe_db.name}")
        await self._ensure_budget()

    def iter_backup_sources(self) -> list[BackupSource]:
        sources: list[BackupSource] = []
        for db_file in sorted(self.base_dir.glob(f"{self.prefix}_*.db")):
            shard_key = db_file.stem.replace(f"{self.prefix}_", "")
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
class StaticDB(StateStore):
    """兼容别名：v2 下由 StateStore 承载。"""


@dataclass
class ShardedDB(SegmentStore):
    """兼容别名：v2 下由 SegmentStore 承载。"""


@dataclass
class CounterStore(SegmentStore):
    """计数型存储，当前复用 SegmentStore 实现。"""


@dataclass
class EventStore(SegmentStore):
    """事件型存储，当前复用 SegmentStore 实现。"""


__all__ = [
    "ArchiveCodec",
    "ColdPolicy",
    "CounterStore",
    "EventStore",
    "SegmentState",
    "ShardedDB",
    "StateStore",
    "StaticDB",
]
