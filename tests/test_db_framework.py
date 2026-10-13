# 测试直取内部符号：pyright 私有访问豁免
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, cast

import arrow
import nonebot
import pytest
from sqlalchemy import Integer, String, func, select, text
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.database.core.consts import Permission
from src.database.core.tables import CoreBase, User
from src.database.log.consts import AuditAction, AuditCategory, AuditContext
from src.database.log.tables import AuditLog
from src.database.patches import build_core_patch_registry
from src.database.snapshot.tables import UserSnapshot
from src.lib.db.batch import BatchWriter
from src.lib.db.connectors import ColdPolicy, EventStore, StateStore
from src.lib.db.manager import db_manager
from src.lib.db.ops import BaseOps, affected_rows
from src.lib.db.schema import SchemaPatch


class _StaticBase(DeclarativeBase):
    pass


class _StaticModel(_StaticBase):
    __tablename__ = "sample_static"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)


class _ShardBase(DeclarativeBase):
    pass


class _ShardModel(_ShardBase):
    __tablename__ = "sample_shard"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    value: Mapped[str] = mapped_column(String(32), nullable=False)


class _ShardOps(BaseOps[_ShardModel]):
    """AliasStore 测试用最小 ops。"""

    async def add_value(self, value: str) -> None:
        self.session.add(_ShardModel(value=value))
        await self.session.flush()


@pytest.mark.asyncio
async def test_state_store_patch_registry_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)

    db = StateStore(namespace="framework_patch", filename="main.db")
    applied = {"count": 0}

    async def _patch(session: AsyncSession) -> None:
        applied["count"] += 1
        await session.execute(
            text("CREATE TABLE IF NOT EXISTS patch_marker (id INTEGER PRIMARY KEY)")
        )

    db.patch_registry.register(
        SchemaPatch(
            patch_id="framework:patch:marker:v1",
            apply=_patch,
        )
    )

    await db.init_schema(_StaticBase)
    await db.init_schema(_StaticBase)

    assert applied["count"] == 1

    async with db.read_session() as session:
        patch_rows = await session.execute(text("SELECT COUNT(*) FROM _schema_patch"))
        marker_rows = await session.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE name = 'patch_marker'")
        )

    assert int(patch_rows.scalar() or 0) == 1
    assert int(marker_rows.scalar() or 0) == 1


@pytest.mark.asyncio
async def test_core_patch_registry_adds_invitation_sub_type_to_legacy_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)

    db = StateStore(namespace="legacy_invitation_patch", filename="core.db")
    db.patch_registry = build_core_patch_registry()

    db_path = db.base_dir / db.filename
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE biz_invitation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id VARCHAR(32) NOT NULL,
                inviter_id VARCHAR(32) NOT NULL,
                operator_id VARCHAR(32),
                flag VARCHAR(32),
                status VARCHAR(32) NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.commit()

    await db.init_schema(CoreBase)

    async with db.read_session() as session:
        column_rows = await session.execute(text("PRAGMA table_info(biz_invitation)"))
        patch_rows = await session.execute(
            text(
                """
                SELECT COUNT(*) FROM _schema_patch
                WHERE patch_id = 'core:add_invitation_sub_type:v1'
                """
            )
        )

    columns = {str(row[1]) for row in column_rows.fetchall()}
    assert "sub_type" in columns
    assert int(patch_rows.scalar() or 0) == 1


@pytest.mark.asyncio
async def test_batch_writer_retries_and_drain() -> None:
    calls = {"count": 0}
    flushed: list[list[int]] = []

    async def _flush(batch: list[int]) -> None:
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("boom")
        flushed.append(batch)

    writer = BatchWriter[int](
        flush_callback=_flush,
        batch_size=2,
        flush_interval=0.05,
        max_retries=3,
        retry_backoff=0.01,
    )

    await writer.add_all([1, 2])
    await writer.drain()
    await writer.close()

    assert calls["count"] == 3
    assert flushed == [[1, 2]]
    assert writer.dead_letters == ()


@pytest.mark.asyncio
async def test_batch_writer_dead_letter_on_exhausted_retries() -> None:
    async def _flush(batch: list[int]) -> None:
        _ = batch
        raise RuntimeError("always fail")

    writer = BatchWriter[int](
        flush_callback=_flush,
        batch_size=1,
        flush_interval=0.05,
        max_retries=2,
        retry_backoff=0.01,
    )

    await writer.add(1)
    with pytest.raises(RuntimeError, match="always fail"):
        await writer.drain()
    await writer.close()

    assert len(writer.dead_letters) == 1
    assert writer.dead_letters[0].attempts == 2
    assert writer.dead_letters[0].batch == (1,)


@pytest.mark.asyncio
async def test_batch_writer_flush_now_flushes_without_waiting_interval() -> None:
    flushed: list[list[int]] = []

    async def _flush(batch: list[int]) -> None:
        flushed.append(batch)

    writer = BatchWriter[int](
        flush_callback=_flush,
        batch_size=10,
        flush_interval=60,
    )

    await writer.add_all([1, 2, 3])
    await writer.flush_now()
    await writer.close()

    assert flushed == [[1, 2, 3]]


@pytest.mark.asyncio
async def test_event_store_read_session_creates_active_shard(
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

    db = EventStore(namespace="framework_shard", prefix="events", fmt="%Y_%m")
    await db.init_schema(_ShardBase)

    async with db.write_session(time_ctx=arrow.get("2026-06-08").datetime) as session:
        session.add(_ShardModel(value="ok"))

    async with db.read_session(time_ctx=arrow.get("2026-06-08").datetime) as session:
        total = await session.execute(select(func.count(_ShardModel.id)))

    assert int(total.scalar() or 0) == 1


@pytest.mark.asyncio
async def test_event_store_deny_and_skip_cold_shard(
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

    db = EventStore(namespace="framework_cold", prefix="events", fmt="%Y_%m")
    db_dir = tmp_path / "framework_cold"
    db_dir.mkdir(parents=True, exist_ok=True)
    (db_dir / "events_2026_04.7z").write_bytes(b"fake archive")

    with pytest.raises(FileNotFoundError):
        async with db.read_session(time_ctx=arrow.get("2026-04-08").datetime):
            pass

    results = await db.map_reduce(
        arrow.get("2026-04-01").datetime,
        arrow.get("2026-04-30").datetime,
        lambda session: session.execute(select(1)),
        cold_policy=ColdPolicy.SKIP,
    )

    assert results == []


@pytest.mark.asyncio
async def test_event_store_archives_to_zstd_and_hydrates_with_manifest(
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

    db = EventStore(
        namespace="framework_archive",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
        cold_policy=ColdPolicy.HYDRATE,
        warm_ttl_seconds=1,
        warm_budget_mb=1,
    )
    await db.init_schema(_ShardBase)

    april = arrow.get("2026-04-08").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="cold"))
    online_wal = tmp_path / "framework_archive" / "events_2026_04.db-wal"
    online_shm = tmp_path / "framework_archive" / "events_2026_04.db-shm"
    assert online_wal.exists()
    assert online_shm.exists()

    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-08 12:00:00").int_timestamp,
    )
    await db.run_archiver_task()

    archived_path = tmp_path / "framework_archive" / "events_2026_04.db.zst"
    online_path = tmp_path / "framework_archive" / "events_2026_04.db"
    manifest_path = tmp_path / "framework_archive" / "events_manifest.json"
    assert archived_path.is_file()
    assert not online_path.exists()
    assert not online_wal.exists()
    assert not online_shm.exists()
    assert manifest_path.is_file()
    assert '"state": "cold"' in manifest_path.read_text(encoding="utf-8")

    async with db.read_session(
        time_ctx=april,
        cold_policy=ColdPolicy.HYDRATE,
    ) as session:
        total = await session.execute(select(func.count(_ShardModel.id)))
    assert int(total.scalar() or 0) == 1
    assert online_path.is_file()
    assert '"state": "warm"' in manifest_path.read_text(encoding="utf-8")

    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-10 12:00:00").int_timestamp,
    )
    await db._ensure_budget()
    assert not online_path.exists()
    assert not online_wal.exists()
    assert not online_shm.exists()
    assert '"state": "cold"' in manifest_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_segment_store_treats_current_month_as_active_at_cst_month_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """月初 00:30 CST（UTC 尚属上月）时当月分片判为 active。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    # 2026-09-01 00:30 +08:00 落在 UTC 的前一日 16:30
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-09-01 00:30:00+08:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_tz", prefix="events", fmt="%Y_%m", active_window_months=2
    )
    assert db._is_active_shard("2026_09") is True
    assert db._is_active_shard("2026_08") is True
    assert db._is_active_shard("2026_07") is False


@pytest.mark.asyncio
async def test_segment_store_archiver_skips_current_month_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 号凌晨归档任务跳过当月分片。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-09-01 00:30:00+08:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_skip", prefix="events", fmt="%Y_%m", active_window_months=2
    )
    await db.init_schema(_ShardBase)

    september = arrow.get("2026-09-01 00:10:00+08:00").datetime
    async with db.write_session(time_ctx=september) as session:
        session.add(_ShardModel(value="day1"))

    await db.run_archiver_task()

    db_dir = tmp_path / "framework_skip"
    assert (db_dir / "events_2026_09.db").is_file()
    assert not list(db_dir.glob("*.db.zst"))

    async with db.read_session(time_ctx=september) as session:
        total = await session.execute(select(func.count(_ShardModel.id)))
    assert int(total.scalar() or 0) == 1


@pytest.mark.asyncio
async def test_segment_store_archiver_does_not_drop_inflight_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """归档保留在途写事务的数据。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-09-01 12:00:00+08:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_race", prefix="events", fmt="%Y_%m", active_window_months=2
    )
    await db.init_schema(_ShardBase)

    september = arrow.get("2026-09-01 00:00:00+08:00").datetime
    async with db.write_session(time_ctx=september) as session:
        session.add(_ShardModel(value="before"))

    async def slow_writer() -> None:
        async with db.write_session(time_ctx=september) as session:
            session.add(_ShardModel(value="in-flight"))
            await asyncio.sleep(0.3)

    task = asyncio.create_task(slow_writer())
    await asyncio.sleep(0.1)

    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-11-15 12:00:00+08:00").int_timestamp,
    )
    await db.run_archiver_task()
    await task

    # 两处数据落到在线分片或归档里都可读回。
    async with db.read_session(
        time_ctx=september,
        cold_policy=ColdPolicy.HYDRATE,
    ) as session:
        rows = (
            (await session.execute(select(_ShardModel.value).order_by(_ShardModel.id)))
            .scalars()
            .all()
        )
    assert list(rows) == ["before", "in-flight"]


@pytest.mark.asyncio
async def test_segment_store_reinitializes_schema_after_shard_file_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分片文件删除后重建 schema 并恢复写入。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-09-01 12:00:00+08:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_heal", prefix="events", fmt="%Y_%m", active_window_months=2
    )
    await db.init_schema(_ShardBase)

    september = arrow.get("2026-09-01 00:00:00+08:00").datetime
    async with db.write_session(time_ctx=september) as session:
        session.add(_ShardModel(value="first"))
    await db.flush_manifest()

    db_path = tmp_path / "framework_heal" / "events_2026_09.db"
    # 模拟「分片文件被外部删除」：先释放连接池里的引擎，Windows 下引擎
    # 句柄钉住文件、挡住 unlink。产品代码的 retire 与分片重建路径
    # 同样先 dispose 再动文件。
    await db_manager.dispose(str(db_path))
    db_path.unlink()

    async with db.write_session(time_ctx=september) as session:
        session.add(_ShardModel(value="second"))

    assert db_path.is_file()
    async with db.read_session(time_ctx=september) as session:
        rows = (
            (await session.execute(select(_ShardModel.value).order_by(_ShardModel.id)))
            .scalars()
            .all()
        )
    assert list(rows) == ["second"]


@pytest.mark.asyncio
async def test_segment_store_manifest_write_is_atomic_under_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """manifest 走 tmp + os.replace，并发 touch 后仍必须是合法 JSON。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-09-01 12:00:00+08:00").int_timestamp,
    )

    db = EventStore(namespace="framework_manifest", prefix="events", fmt="%Y_%m")
    await db.init_schema(_ShardBase)

    async with db.write_session(time_ctx=arrow.get("2026-09-01").datetime) as session:
        session.add(_ShardModel(value="x"))

    await asyncio.gather(*(db._touch_segment("2026_09") for _ in range(50)))
    await db.flush_manifest()

    db_dir = tmp_path / "framework_manifest"
    payload = json.loads((db_dir / "events_manifest.json").read_text(encoding="utf-8"))
    assert "2026_09" in payload["segments"]
    assert not list(db_dir.glob("*.tmp"))


@pytest.mark.asyncio
async def test_segment_store_map_reduce_includes_current_month_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CST 月初窗口扫描包含当月分片。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    # 2026-09-01 07:30 +08:00
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-09-01 07:30:00+08:00").int_timestamp,
    )

    db = EventStore(namespace="framework_scan", prefix="events", fmt="%Y_%m")
    await db.init_schema(_ShardBase)

    # 当月分片里放一条数据
    september = arrow.get("2026-09-01 00:10:00+08:00").datetime
    async with db.write_session(time_ctx=september) as session:
        session.add(_ShardModel(value="new-month"))

    # wordbank 风格的 UTC-aware 90 天窗口
    now_ts = arrow.get("2026-09-01 07:30:00+08:00").int_timestamp
    start_time = datetime.fromtimestamp(now_ts - 90 * 86400, UTC)
    end_time = datetime.fromtimestamp(now_ts, UTC)

    results = await db.map_reduce(
        start_time,
        end_time,
        lambda _session: _session.execute(
            select(_ShardModel.value).where(_ShardModel.value == "new-month")
        ),
    )
    assert any(rows.scalars().all() == ["new-month"] for rows in results)

    # naive 输入（store 时区墙钟）也必须正确
    results_naive = await db.map_reduce(
        arrow.get("2026-08-25").datetime,
        arrow.get("2026-09-05").datetime,
        lambda _session: _session.execute(
            select(_ShardModel.value).where(_ShardModel.value == "new-month")
        ),
    )
    assert any(rows.scalars().all() == ["new-month"] for rows in results_naive)


@pytest.mark.asyncio
async def test_segment_store_seals_oversized_active_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对齐 ES ILM 的 max_primary_shard_size：活跃分片超阈值也要 seal。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-09-15 12:00:00+08:00").int_timestamp,
    )
    # 阈值设成 0 MB，任何非空分片都算超限，省去造大数据
    monkeypatch.setattr(connectors_module, "SEGMENT_SIZE_ROLLOVER_MB", 0)

    db = EventStore(namespace="framework_rollover", prefix="events", fmt="%Y_%m")
    await db.init_schema(_ShardBase)

    september = arrow.get("2026-09-10 00:00:00+08:00").datetime
    async with db.write_session(time_ctx=september) as session:
        session.add(_ShardModel(value="row-0"))

    # 2026_09 在活跃窗口内，体积超阈值触发 seal
    db_dir = tmp_path / "framework_rollover"
    assert db._is_active_shard("2026_09") is True
    await db.run_archiver_task()

    assert not (db_dir / "events_2026_09.db").exists()
    assert (db_dir / "events_2026_09.db.zst").is_file()

    async with db.read_session(
        time_ctx=september,
        cold_policy=ColdPolicy.HYDRATE,
    ) as session:
        total = await session.execute(select(func.count(_ShardModel.id)))
    assert int(total.scalar() or 0) == 1


@pytest.mark.asyncio
async def test_segment_store_retention_removes_archived_shards_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对齐 ES ILM delete 阶段：只删超期归档，在线分片原样保留。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-04-10 12:00:00+08:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_retention",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
        retention_months=3,
    )
    await db.init_schema(_ShardBase)

    january = arrow.get("2026-01-08").datetime
    async with db.write_session(time_ctx=january) as session:
        session.add(_ShardModel(value="old"))

    # 4 月：1 月已出活跃窗口 -> 归档；保留期 3 个月，1 月尚在保留期内
    await db.run_archiver_task()
    db_dir = tmp_path / "framework_retention"
    assert (db_dir / "events_2026_01.db.zst").is_file()

    # 推进到 5 月：保留期边界退到 2 月，1 月超期被回收
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-05-20 12:00:00+08:00").int_timestamp,
    )
    await db.run_archiver_task()
    assert not (db_dir / "events_2026_01.db.zst").exists()
    assert not (db_dir / "events_2026_01.db").exists()

    # 在线分片原样保留
    april = arrow.get("2026-04-05").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="current"))
    assert (db_dir / "events_2026_04.db").is_file()


@pytest.mark.asyncio
async def test_segment_store_health_reports_shard_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """health 快照（对应 _cat/indices）反映在线/归档/活跃三态。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-04-10 12:00:00+08:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_health",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
    )
    await db.init_schema(_ShardBase)

    january = arrow.get("2026-01-08").datetime
    async with db.write_session(time_ctx=january) as session:
        session.add(_ShardModel(value="old"))
    april = arrow.get("2026-04-05").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="current"))

    await db.run_archiver_task()
    health = {row["shard"]: row for row in db.shard_health()}

    assert health["2026_01"]["archived"] is True
    assert health["2026_01"]["online"] is False
    assert health["2026_01"]["archived_at"] > 0
    assert health["2026_04"]["online"] is True
    assert health["2026_04"]["is_active"] is True
    assert health["2026_04"]["size_bytes"] > 0


@pytest.mark.asyncio
async def test_alias_store_routes_by_payload_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """alias 只需 payload 的时间字段，业务侧省去手算分片键。"""
    from src.lib.db import connectors as connectors_module
    from src.lib.db.alias import AliasStore

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-09-15 12:00:00+08:00").int_timestamp,
    )

    store = EventStore(namespace="alias_rt", prefix="events", fmt="%Y_%m")
    await store.init_schema(_ShardBase)
    alias: AliasStore[Any] = AliasStore(
        store,
        ops_class=_ShardOps,
        time_field="created_at",
    )

    # 时间戳 / record_date 字符串 / 整数 / 分片键，四形态等价
    ts = arrow.get("2026-08-15 10:00:00+08:00").int_timestamp
    assert alias.shard_key_for(ts) == "2026_08"
    assert alias.shard_key_for("20260815") == "2026_08"
    assert alias.shard_key_for(20260815) == "2026_08"
    assert alias.shard_key_for("2026_08") == "2026_08"

    payloads = [
        {"created_at": ts, "value": "aug"},
        {
            "created_at": arrow.get("2026-09-02 10:00:00+08:00").int_timestamp,
            "value": "sep",
        },
    ]

    async def _insert(ops: Any, rows: list[dict[str, Any]]) -> int:
        for row in rows:
            ops.session.add(_ShardModel(value=row["value"]))
        await ops.session.flush()
        return len(rows)

    written = await alias.write_batch(payloads, method=_insert)
    assert written == 2

    db_dir = tmp_path / "alias_rt"
    assert (db_dir / "events_2026_08.db").is_file()
    assert (db_dir / "events_2026_09.db").is_file()


@pytest.mark.asyncio
async def test_alias_store_write_batch_uses_store_timezone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CST 月初 8 小时内写入落在当月分片。"""
    from src.lib.db import connectors as connectors_module
    from src.lib.db.alias import AliasStore

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    # 2026-09-01 07:00 +08:00
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-09-01 07:00:00+08:00").int_timestamp,
    )

    store = EventStore(namespace="alias_tz", prefix="events", fmt="%Y_%m")
    await store.init_schema(_ShardBase)
    alias: AliasStore[Any] = AliasStore(
        store,
        ops_class=_ShardOps,
        time_field="created_at",
    )

    ts = arrow.get("2026-09-01 07:00:00+08:00").int_timestamp
    assert alias.shard_key_for(ts) == "2026_09"

    async def _insert(ops: Any, rows: list[dict[str, Any]]) -> int:
        ops.session.add(_ShardModel(value=rows[0]["value"]))
        await ops.session.flush()
        return 1

    await alias.write_batch([{"created_at": ts, "value": "x"}], method=_insert)
    assert (tmp_path / "alias_tz" / "events_2026_09.db").is_file()


@pytest.mark.asyncio
async def test_alias_store_scan_shards_bounds_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scan_shards 限制并发度，替代原先裸 gather。"""
    from src.lib.db import connectors as connectors_module
    from src.lib.db.alias import AliasStore

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-15 12:00:00+08:00").int_timestamp,
    )

    store = EventStore(
        namespace="alias_scan",
        prefix="events",
        fmt="%Y_%m",
        map_reduce_concurrency=2,
    )
    await store.init_schema(_ShardBase)
    alias: AliasStore[Any] = AliasStore(
        store,
        ops_class=_ShardOps,
        time_field="created_at",
    )

    keys = ["2026_01", "2026_02", "2026_03", "2026_04", "2026_05"]
    for key in keys:
        async with alias.write_session_for(key) as session:
            session.add(_ShardModel(value=key))

    active = 0
    peak = 0

    async def _probe(_session: Any) -> list[str]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return ["ok"]

    results = await alias.scan_shards(keys, _probe, concurrency=2)
    assert len(results) == len(keys)
    assert peak <= 2


@pytest.mark.asyncio
async def test_batch_writer_health_tracks_flush_and_dead_letters() -> None:
    """health 覆盖落盘量、失败量与死信批次（死信此前属于静默丢数据）。"""
    flushed: list[list[int]] = []

    async def _ok(batch: list[int]) -> None:
        flushed.append(batch)

    writer = BatchWriter[int](flush_callback=_ok, batch_size=10, flush_interval=0.05)
    await writer.add_all([1, 2, 3])
    await writer.drain()

    health = writer.health
    assert health.total_flushed_items == 3
    assert health.dead_letter_batches == 0
    assert health.is_degraded is False
    assert health.consecutive_failures == 0
    assert health.last_flush_at > 0

    async def _boom(_batch: list[int]) -> None:
        raise RuntimeError("db down")

    bad = BatchWriter[int](
        flush_callback=_boom,
        batch_size=10,
        flush_interval=0.05,
        max_retries=2,
        retry_backoff=0.0,
    )
    await bad.add_all([1, 2])
    # drain 会把暂存的错误重抛出来，这正是既有的对外契约
    with pytest.raises(RuntimeError, match="db down"):
        await bad.drain()

    bad_health = bad.health
    assert bad_health.is_degraded is True
    assert bad_health.dead_letter_batches == 1
    assert bad_health.dead_letter_items == 2
    assert bad_health.total_failed_items == 2
    assert bad_health.last_dead_letter_error
    assert bad_health.consecutive_failures == 1


@pytest.mark.asyncio
async def test_batch_writer_pop_dead_letters_clears_queue() -> None:
    """pop_dead_letters 供上报/补偿使用，取出即清空。"""

    async def _boom(_batch: list[int]) -> None:
        raise RuntimeError("nope")

    writer = BatchWriter[int](
        flush_callback=_boom,
        batch_size=10,
        flush_interval=0.05,
        max_retries=1,
        retry_backoff=0.0,
    )
    await writer.add_all([7])
    with pytest.raises(RuntimeError, match="nope"):
        await writer.drain()

    popped = writer.pop_dead_letters()
    assert len(popped) == 1
    assert popped[0].batch == (7,)
    assert writer.pop_dead_letters() == ()


@pytest.mark.asyncio
async def test_batch_writer_dead_letter_queue_is_bounded() -> None:
    """内存死信队列有上界，持续失败时占用可控。"""

    async def _boom(_batch: list[int]) -> None:
        raise RuntimeError("down")

    writer = BatchWriter[int](
        flush_callback=_boom,
        batch_size=1,
        flush_interval=0.05,
        max_retries=1,
        retry_backoff=0.0,
        max_dead_letters=3,
    )
    for item in range(6):
        await writer.add(item)
        with pytest.raises(RuntimeError, match="down"):
            await writer.drain()

    assert len(writer.dead_letters) == 3
    # 保留最新 3 条，丢弃最旧 3 条
    assert writer.health.dropped_dead_letters == 3
    assert writer.dead_letters[-1].batch == (5,)
    await writer.close()


@pytest.mark.asyncio
async def test_batch_writer_watchdog_unwedges_stuck_flush_callback() -> None:
    """flush 回调永久挂起时，看门狗让 writer 继续服务。

    旧行为：worker 卡死在回调上，队列持续积压；现行契约：超时取消回调
    并将整批转入死信。等待都套 wait_for 上限。"""
    stuck = asyncio.Event()

    async def _stuck(batch: list[int]) -> None:
        _ = batch
        await stuck.wait()

    writer = BatchWriter[int](
        flush_callback=_stuck,
        batch_size=10,
        flush_interval=0.05,
        max_retries=2,
        retry_backoff=0.0,
        flush_timeout=0.1,
    )

    await writer.add(1)
    # 首次尝试即被看门狗取消 -> 整批进入死信；drain 重抛 TimeoutError
    # wait_for 5s 是测试兜底：看门狗失效时 drain 会挂起。
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(writer.drain(), timeout=5)

    health = writer.health
    # 看门狗只触发一次：超时即死信防双写。
    assert health.flush_timeouts == 1
    assert health.dead_letter_batches == 1
    assert writer.dead_letters[0].batch == (1,)

    # 放行后写入恢复正常：writer 照常服务。
    stuck.set()
    await writer.add(2)
    await asyncio.wait_for(writer.drain(), timeout=5)
    assert writer.health.total_flushed_items == 1

    await writer.close()


@pytest.mark.asyncio
async def test_batch_writer_flush_timeout_none_disables_watchdog() -> None:
    """flush_timeout=None 显式关闭看门狗（仅测试/明确接受的场景使用）。"""

    async def _ok(batch: list[int]) -> None:
        _ = batch

    writer = BatchWriter[int](
        flush_callback=_ok,
        batch_size=10,
        flush_interval=0.05,
        flush_timeout=None,
    )
    assert writer.config.flush_timeout is None

    await writer.add(1)
    await writer.drain()
    assert writer.health.total_flushed_items == 1
    assert writer.health.flush_timeouts == 0
    await writer.close()


@pytest.mark.asyncio
async def test_batch_writer_health_exposes_pending_depth() -> None:
    """health.pending_items 覆盖队列、缓冲与在飞批次三个阶段。

    在飞计数刻意补上：回调卡住时批次已离开缓冲、账目挂起，只算
    queue+buffer 会显示为 0，运维容易误判。"""
    block = asyncio.Event()
    started = asyncio.Event()

    async def _slow(batch: list[int]) -> None:
        _ = batch
        started.set()
        await block.wait()

    writer = BatchWriter[int](
        flush_callback=_slow,
        batch_size=1000,
        flush_interval=0.05,
        flush_timeout=None,
    )
    try:
        await writer.add_all([1, 2, 3])
        # 等 worker 把批次取进回调——此后批次处于「在飞」状态。
        await asyncio.wait_for(started.wait(), timeout=5)
        assert writer.health.pending_items == 3

        block.set()
        await asyncio.wait_for(writer.drain(), timeout=5)
        assert writer.health.pending_items == 0
    finally:
        # 断言失败也要放行回调并关停 worker，避免残留 worker 拖住进程。
        block.set()
        await writer.close()


@pytest.mark.asyncio
async def test_batch_writer_is_flushing_resets_when_trace_logging_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_is_flushing` 置位在 try 内，异常时该标志照常复位。

    旧实现把置位放在 try 之前，中间夹着 log_trace_event；该调用抛错时
    finally 失效，后续所有 flush_now 永久楔死。"""

    async def _ok(batch: list[int]) -> None:
        _ = batch

    writer = BatchWriter[int](
        flush_callback=_ok,
        batch_size=1,
        flush_interval=0.05,
        flush_timeout=None,
    )

    from src.lib.db import batch as batch_module

    real_log = batch_module.log_trace_event
    fired = {"count": 0}

    def _flaky_log(*args: object, **kwargs: object) -> str:
        # 只让 flush_started 这一次抛错：它正是旧实现里位于「置位之后、
        # try 之前」的那次调用。
        if kwargs.get("event_name") == "flush_started" and fired["count"] == 0:
            fired["count"] += 1
            raise RuntimeError("trace sink exploded")
        return real_log(*args, **kwargs)

    monkeypatch.setattr(batch_module, "log_trace_event", _flaky_log)

    await writer.add(1)
    # wait_for 兜底：旧行为下楔死表现为挂起，这里以断言失败收场。
    with pytest.raises(RuntimeError, match="trace sink exploded"):
        await asyncio.wait_for(writer.flush_now(), timeout=5)

    # 关键断言：标志位已复位，writer 照常服务。
    assert writer._is_flushing is False
    await writer.add(2)
    await asyncio.wait_for(writer.drain(), timeout=5)
    assert writer.health.total_flushed_items >= 1
    await writer.close()


@pytest.mark.asyncio
async def test_dead_letter_persists_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """死信落库以支持关机后回溯，并可用原 flush 回调重放后标记 resolved。"""
    from src.lib.db import connectors as connectors_module
    from src.services.dead_letter_store import (
        DeadLetterOps,
        persist_dead_letters,
        replay_dead_letters,
    )

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-10-07 12:00:00+08:00").int_timestamp,
    )

    from src.database.core.tables import CoreBase

    core_db = StateStore(namespace="core_db", filename="core.db")
    core_db.patch_registry = build_core_patch_registry()
    await core_db.init(CoreBase)

    async def _boom(_batch: list[int]) -> None:
        raise RuntimeError("db down")

    writer = BatchWriter[int](
        flush_callback=_boom,
        batch_size=1,
        flush_interval=0.05,
        max_retries=1,
        retry_backoff=0.0,
    )
    await writer.add_all([1, 2, 3])
    with pytest.raises(RuntimeError, match="db down"):
        await writer.drain()

    records = writer.pop_dead_letters()
    assert len(records) == 3

    monkeypatch.setattr("src.services.dead_letter_store.core_db", core_db)
    report = await persist_dead_letters(records)
    assert report.persisted == 3
    assert report.failed == 0

    async with core_db.session(commit=False) as session:
        stored = await DeadLetterOps(session).list_unresolved()
    assert len(stored) == 3
    assert {row.worker_name for row in stored} == {writer.worker_name}
    assert sorted(row.payload for row in stored) == [[1], [2], [3]]
    assert all(row.resolved == 0 for row in stored)

    replayed_payloads: list[list[int]] = []

    async def _replay(payload: list[int]) -> None:
        replayed_payloads.append(payload)

    total = await replay_dead_letters(handlers={writer.worker_name: _replay})
    assert total == 3
    assert sorted(replayed_payloads) == [[1], [2], [3]]

    async with core_db.session(commit=False) as session:
        remaining = await DeadLetterOps(session).list_unresolved()
    assert remaining == []

    async with core_db.session(commit=False) as session:
        remaining = await DeadLetterOps(session).list_unresolved()
    assert remaining == []


@pytest.mark.asyncio
async def test_writer_health_report_aggregates_registered_writers() -> None:
    """writer_health 汇总 core/water/wordbank 三处注册的 writer。"""
    import src.plugins.water.database.writers
    import src.plugins.wordbank.database.writers
    from src.services.writer_health import build_health_report
    import src.services.writers  # noqa: F401

    report = build_health_report()
    names = {name for name, _ in report.detail}
    assert "_flush_create_user" in names
    assert "_flush_water_logs" in names
    assert "_flush_wordbank_logs" in names
    assert report.dead_letter_items >= 0


def _read_table_text(db_file: Path, table: str) -> str:
    if not db_file.exists():
        return ""
    conn = sqlite3.connect(db_file)
    try:
        return repr(conn.execute(f"SELECT * FROM {table}").fetchall())
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_atomic_session_rolls_back_all_attached_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨库事务失败时 core / log / snapshot 三个物理文件全部回滚。"""
    from src.lib.db import connectors as connectors_module
    from src.services.db import bind_cross_store_schemas

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-10-07 12:00:00+08:00").int_timestamp,
    )

    from src.database.core.ops import UserOps
    from src.database.log.ops import AuditLogOps
    from src.database.snapshot.ops import UserSnapshotOps
    from src.lib.db.atomic import system_atomic_session

    bind_cross_store_schemas()

    async def _write_across_all_stores() -> None:
        async with system_atomic_session() as session:
            await UserOps(session).add_user(
                user_id="u1",
                user_name="Alice",
                permission=Permission.NORMAL,
            )
            await UserSnapshotOps(session).create_user_snapshot(
                user_id="u1",
                content="Alice",
                created_at=1,
            )
            await AuditLogOps(session).create_audit_log(
                target_id=None,  # type: ignore[arg-type]
                context_type=AuditContext.USER,
                category=AuditCategory.PERMISSION,
                action=AuditAction.GRANT,
            )

    with pytest.raises(Exception, match=r"NOT NULL|IntegrityError"):
        await _write_across_all_stores()

    assert _read_table_text(tmp_path / "core_db" / "core.db", "biz_user") == "[]"
    assert (
        _read_table_text(
            tmp_path / "snapshot_db" / "snapshot_202610.db",
            "obs_user_snapshot",
        )
        == "[]"
    )
    assert (
        _read_table_text(tmp_path / "log_db" / "log_202610.db", "sys_audit_log") == "[]"
    )


@pytest.mark.asyncio
async def test_atomic_session_commits_across_all_attached_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一次原子事务把三张表分别写进三个物理文件，且单库会话可读。"""
    from src.lib.db import connectors as connectors_module
    from src.services.db import bind_cross_store_schemas

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-10-07 12:00:00+08:00").int_timestamp,
    )

    from src.database.core.ops import UserOps
    from src.database.instances import core_db, log_db, snapshot_db
    from src.database.log.ops import AuditLogOps
    from src.database.snapshot.ops import UserSnapshotOps
    from src.lib.db.atomic import system_atomic_session

    bind_cross_store_schemas()
    async with system_atomic_session() as session:
        await UserOps(session).add_user(
            user_id="u1",
            user_name="Alice",
            permission=Permission.NORMAL,
        )
        await UserSnapshotOps(session).create_user_snapshot(
            user_id="u1",
            content="Alice",
            created_at=1,
        )
        await AuditLogOps(session).create_audit_log(
            target_id="u1",
            context_type=AuditContext.USER,
            category=AuditCategory.PERMISSION,
            action=AuditAction.GRANT,
        )

    assert "u1" in _read_table_text(tmp_path / "core_db" / "core.db", "biz_user")
    assert "u1" in _read_table_text(
        tmp_path / "snapshot_db" / "snapshot_202610.db",
        "obs_user_snapshot",
    )
    assert "u1" in _read_table_text(
        tmp_path / "log_db" / "log_202610.db", "sys_audit_log"
    )

    # 单库会话也能读到：attached schema 已映射回 main
    async with core_db.session(commit=False) as s:
        total = await s.scalar(select(func.count()).select_from(User))
    assert int(total or 0) == 1

    october = arrow.get("2026-10-07").datetime
    async with log_db.read_session_for(october) as s:
        total = await s.scalar(select(func.count()).select_from(AuditLog))
    assert int(total or 0) == 1
    async with snapshot_db.read_session_for(october) as s:
        total = await s.scalar(select(func.count()).select_from(UserSnapshot))
    assert int(total or 0) == 1


@pytest.mark.asyncio
async def test_atomic_session_uses_rollback_journal_for_cross_file_atomicity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """参与跨库事务的文件走 rollback journal。

    应用层错误回滚在 WAL 下同样成立，但 SQLite 官方明确 WAL 模式下跨
    attached database 的 commit 会撕裂；原子提交靠 super-journal。"""
    from src.lib.db import connectors as connectors_module
    from src.services.db import bind_cross_store_schemas

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-10-07 12:00:00+08:00").int_timestamp,
    )
    bind_cross_store_schemas()

    from src.database.core.ops import UserOps
    from src.database.instances import core_db, log_db, snapshot_db
    from src.lib.db.atomic import system_atomic_session

    # 真实启动先经 services.db 登记跨库事务参与者走 rollback
    # journal。分片被管理器以 WAL 打开过后，journal 模式就此定型。
    from src.lib.db.manager import db_manager

    monkeypatch.setattr(db_manager, "_rollback_journal_dirs", [])
    db_manager.register_rollback_journal_dir(core_db.base_dir)
    db_manager.register_rollback_journal_dir(log_db.base_dir)
    db_manager.register_rollback_journal_dir(snapshot_db.base_dir)

    async with system_atomic_session() as session:
        await UserOps(session).add_user(
            user_id="u1",
            user_name="Alice",
            permission=Permission.NORMAL,
        )

    for name in (
        "core_db/core.db",
        "log_db/log_202610.db",
        "snapshot_db/snapshot_202610.db",
    ):
        conn = sqlite3.connect(tmp_path / name)
        try:
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            conn.close()
        assert mode == "delete", f"{name} 应为 delete，实际 {mode}"


def test_database_manager_uses_debug_sql_echo_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db.manager import DatabaseManager

    manager = DatabaseManager()
    monkeypatch.setattr(
        nonebot,
        "get_driver",
        lambda: type(
            "_Driver",
            (),
            {"config": type("_Config", (), {"debug_sql_echo": True})()},
        )(),
    )

    assert manager._resolve_sql_echo() is True


def test_database_manager_sql_echo_falls_back_when_nonebot_uninitialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db.manager import DatabaseManager

    manager = DatabaseManager()

    def _raise() -> object:
        raise ValueError("NoneBot has not been initialized.")

    monkeypatch.setattr(nonebot, "get_driver", _raise)

    assert manager._resolve_sql_echo() is False


class _RowcountStub:
    """鸭子类型的 rowcount 提供者：契约与具体类解耦。"""

    def __init__(self, rowcount: object) -> None:
        self.rowcount = rowcount


def test_affected_rows_accepts_cursor_result() -> None:
    """affected_rows 对单参 execute 的 DML 返回值正常取到 rowcount。"""
    from sqlalchemy import CursorResult

    result = CursorResult.__new__(CursorResult)
    object.__setattr__(result, "rowcount", 3)

    assert affected_rows(result) == 3


def test_affected_rows_accepts_duck_typed_result() -> None:
    """契约是「有 rowcount 的结果对象」：代理/桩实现照常可用。"""
    result = cast("Result[object]", _RowcountStub(1))

    assert affected_rows(result) == 1


def test_affected_rows_rejects_iterator_result_loudly() -> None:
    """executemany 的 IteratorResult 缺失 rowcount 时当场报错。

    旧实现用 cast 断言成 CursorResult，下游打开 rowcount 时抛
    AttributeError，整批写入沿重试路径静默中止。"""
    from sqlalchemy.engine import IteratorResult

    result = IteratorResult.__new__(IteratorResult)

    with pytest.raises(TypeError, match="rowcount"):
        affected_rows(result)


@pytest.mark.parametrize("bad_rowcount", [True, False, "3", 3.5, None])
def test_affected_rows_rejects_non_integer_rowcount(bad_rowcount: object) -> None:
    """rowcount 取真整数：bool 会伪装成 1/0，字符串与浮点属异常形态。"""
    result = cast("Result[object]", _RowcountStub(bad_rowcount))

    with pytest.raises(TypeError, match="int"):
        affected_rows(result)


# --------------------------------------------
# 归档健壮性：引擎键归一化 / staging 残留 / 原子落地 / 诚实上报
# --------------------------------------------


@pytest.mark.asyncio
async def test_database_manager_dispose_matches_relative_open_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """相对路径 open 的引擎，用绝对路径 dispose 照样命中缓存。

    生产 GLOBAL_DB_ROOT 是相对路径，归档却用绝对路径调 dispose；
    缓存键按原始字符串时二者错开，dispose 静默空转。"""
    monkeypatch.chdir(tmp_path)
    db_manager_local = type(db_manager)()
    relative_path = Path("probe") / "store" / "shard.db"
    relative_path.parent.mkdir(parents=True)

    async with db_manager_local.open(str(relative_path), commit=True) as session:
        await session.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))

    # 绝对路径 dispose，等价于归档路径 _safe_resolve 后的形态。
    await db_manager_local.dispose(str(relative_path.resolve()))

    # 句柄已释放：Windows 上此时 rename 才能成功。
    renamed = relative_path.with_suffix(".renamed")
    os.replace(relative_path, renamed)
    assert renamed.is_file()


@pytest.mark.asyncio
async def test_archiver_completes_with_relative_db_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """相对 GLOBAL_DB_ROOT 下同一进程可完成归档（生产配置端到端）。

    写入非活跃月分片 -> 推进时钟 -> 归档巡检，产出 .zst、移走在线库、
    manifest 转 cold。修复前该路径被 WinError 32 挡住且失败被吞。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", Path("./dbroot"))
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-08 12:00:00").int_timestamp,
    )

    db = EventStore(
        namespace="relative_root",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
    )
    await db.init_schema(_ShardBase)

    april = arrow.get("2026-04-08").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="cold"))

    report = await db.run_archiver_task()

    # cwd 已切到 tmp_path：相对 root 下的物理文件就在这里。
    archived_path = tmp_path / "dbroot" / "relative_root" / "events_2026_04.db.zst"
    online_path = tmp_path / "dbroot" / "relative_root" / "events_2026_04.db"
    assert report.archived == ["2026_04"]
    assert report.ok is True
    assert archived_path.is_file()
    assert not online_path.exists()


@pytest.mark.asyncio
async def test_run_archiver_task_recovers_staging_residue_when_db_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """归档中断留下的 .archiving 残留在巡检时归位。

    复刻 kill 点：retire 完成（db 已改名成 .archiving）、压缩进行中。
    巡检把残留归位成在线分片并记入 healed。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-08 12:00:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_heal_staging",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
    )
    await db.init_schema(_ShardBase)

    april = arrow.get("2026-04-08").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="residue"))

    db_path = tmp_path / "framework_heal_staging" / "events_2026_04.db"
    staging_path = db_path.with_suffix(".archiving")
    # 复刻 kill 点：retire 前缀已完成。
    await db._retire_shard_file("2026_04", db_path)
    assert staging_path.is_file()
    assert not db_path.exists()

    report = await db.run_archiver_task()

    assert report.healed == ["2026_04"]
    assert not staging_path.exists()
    # 归位后的分片随即按正常流程归档；数据完整可读（hydrate 读回全部行）。
    async with db.read_session(time_ctx=april, cold_policy=ColdPolicy.HYDRATE) as s:
        values = (await s.execute(select(_ShardModel.value))).scalars().all()
    assert list(values) == ["residue"]


@pytest.mark.asyncio
async def test_retire_shard_file_refuses_to_overwrite_staging_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """staging 残留与在线分片并存时拒绝覆盖，冲突显式上报。

    旧实现会把新库 os.replace 到同名 staging，残留里的真实行被静默
    销毁；现在两者都保留并把冲突显式上报。"""
    from src.lib.db import connectors as connectors_module
    from src.lib.db.connectors import ArchiveStagingConflictError

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-08 12:00:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_conflict",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
    )
    await db.init_schema(_ShardBase)

    april = arrow.get("2026-04-08").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="old-row"))

    db_path = tmp_path / "framework_conflict" / "events_2026_04.db"
    staging_path = db_path.with_suffix(".archiving")
    await db._retire_shard_file("2026_04", db_path)

    def _read_values(path: Path) -> list[str]:
        # 显式关闭连接：with sqlite3.connect(...) 退出时只提交，句柄交由
        # GC 释放；Windows 下残留句柄会钉住文件、阻挡后续 rename。
        conn = sqlite3.connect(path)
        try:
            return [row[0] for row in conn.execute("SELECT value FROM sample_shard")]
        finally:
            conn.close()

    # 直接构造「残留 + 在线库并存」的磁盘状态：模拟旧版写路径重建的空库。
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE sample_shard (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO sample_shard (value) VALUES ('new-row')")
        conn.commit()
    finally:
        conn.close()

    assert _read_values(staging_path) == ["old-row"]
    assert _read_values(db_path) == ["new-row"]

    # 直接路径：拒绝覆盖并抛显式异常。
    with pytest.raises(ArchiveStagingConflictError):
        await db._retire_shard_file("2026_04", db_path)

    # 巡检路径：冲突进入报告，两侧数据原样保留。
    report = await db.run_archiver_task()

    assert [f.shard_key for f in report.failures] == ["2026_04"]
    assert report.failures[0].outcome.value == "conflict"
    assert report.ok is False
    assert report.archived == []
    assert not db_path.with_suffix(".db.zst").exists()
    assert _read_values(staging_path) == ["old-row"]
    assert _read_values(db_path) == ["new-row"]


@pytest.mark.asyncio
async def test_compress_failure_leaves_no_partial_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """压缩中途失败只留下在线库回滚，磁盘上只余完整归档。

    旧实现 destination.open("wb") 直写：崩在中间留下「像归档、实为
    截断」的 .zst；它被 hydrate 后是损坏库，该分片写入永久卡死。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-08 12:00:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_atomic",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
    )
    await db.init_schema(_ShardBase)
    april = arrow.get("2026-04-08").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="payload"))

    def _failing_copy(src: object, dst: object) -> None:
        cast(Any, dst).write(b"partial-bytes")
        raise RuntimeError("compress interrupted")

    monkeypatch.setattr(
        connectors_module.zstd.ZstdCompressor,
        "copy_stream",
        _failing_copy,
    )

    report = await db.run_archiver_task()

    archive_path = tmp_path / "framework_atomic" / "events_2026_04.db.zst"
    tmp_path_archive = archive_path.with_suffix(".zst.tmp")
    online_path = tmp_path / "framework_atomic" / "events_2026_04.db"
    assert archive_path.exists() is False
    assert tmp_path_archive.exists() is False
    assert online_path.is_file()
    assert [f.shard_key for f in report.failures] == ["2026_04"]
    assert report.failures[0].outcome.value == "failed"
    async with db.read_session(time_ctx=april, cold_policy=ColdPolicy.HYDRATE) as s:
        values = (await s.execute(select(_ShardModel.value))).scalars().all()
    assert list(values) == ["payload"]


@pytest.mark.asyncio
async def test_read_session_heals_staging_residue_before_creating_empty_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """db 缺失 + 残留存在时，读路径先把残留归位再建空分片。

    修复前活跃月的读直接建空库，残留里的行沦为孤儿数据。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-04-08 12:00:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_read_heal",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
    )
    await db.init_schema(_ShardBase)
    april = arrow.get("2026-04-08").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="kept"))

    db_path = tmp_path / "framework_read_heal" / "events_2026_04.db"
    staging_path = db_path.with_suffix(".archiving")
    await db._retire_shard_file("2026_04", db_path)

    async with db.read_session(time_ctx=april) as session:
        values = (await session.execute(select(_ShardModel.value))).scalars().all()
    assert list(values) == ["kept"]
    assert not staging_path.exists()
    assert db_path.is_file()


@pytest.mark.asyncio
async def test_ensure_budget_skips_eviction_without_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WARM 分片缺失可用归档时跳过逐出，保住唯一副本。

    逐出 = 删除在线文件；此前于归档缺失时照删，唯一完整数据被销毁。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-08 12:00:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_budget",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
        warm_ttl_seconds=0,
        warm_budget_mb=0,
    )
    await db.init_schema(_ShardBase)
    april = arrow.get("2026-04-08").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="only-copy"))

    db_path = tmp_path / "framework_budget" / "events_2026_04.db"
    archive_path = db_path.with_suffix(".db.zst")
    # WARM 状态但归档缺失（模拟归档停产/被外部删除）。
    entry = db._manifest_entry("2026_04", db_path, archive_path)
    entry.state = connectors_module.SegmentState.WARM
    assert not archive_path.exists()

    await db._ensure_budget()

    assert db_path.is_file()
    async with db.read_session(time_ctx=april) as session:
        values = (await session.execute(select(_ShardModel.value))).scalars().all()
    assert list(values) == ["only-copy"]


# --------------------------------------------
# 分片生命周期：读路径锁 / 归档完整性 / manifest 容错
# --------------------------------------------


@pytest.mark.asyncio
async def test_map_reduce_holds_shard_lock_against_archiver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """扫描读路径持分片锁，归档在读的中间态原地等待。

    旧行为：map_reduce 核对「分片在线」后径直 open；归档在这间隙把
    文件 rename 走，查询静默返回 0 行。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-08 12:00:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_readlock",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
    )
    await db.init_schema(_ShardBase)

    april = arrow.get("2026-04-08").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="kept"))

    observed: list[int] = []
    blocked_mid_query: list[bool] = []
    archiver_task: asyncio.Task[object] | None = None

    async def _query(session: AsyncSession) -> int:
        # 查询「飞行中」时启动归档：读路径缺锁则归档立即搬走文件
        nonlocal archiver_task
        archiver_task = asyncio.create_task(db.run_archiver_task())
        # 给归档一个到达分片锁的窗口；有锁时它会停在这里等查询结束。
        await asyncio.sleep(0.1)
        blocked_mid_query.append(not archiver_task.done())
        count = int(
            (await session.execute(select(func.count(_ShardModel.id)))).scalar() or 0
        )
        observed.append(count)
        return count

    results = await db.map_reduce(
        arrow.get("2026-04-01").datetime,
        arrow.get("2026-04-30").datetime,
        _query,
    )

    # 查询期间归档被分片锁挡住（此时仍属进行中），查询读到完整数据。
    assert blocked_mid_query == [True]
    assert observed == [1]
    assert results == [1]

    # 查询结束后归档正常继续完成。
    assert archiver_task is not None
    await asyncio.wait_for(archiver_task, timeout=10)


@pytest.mark.asyncio
async def test_hydrate_rejects_truncated_archive_and_quarantines_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """截断归档显式报错并隔离，照常库处理只允许完整归档。

    旧行为：copy_stream 对截断的 zstd 帧静默返回已解出字节，半截库被
    当成正常结果——查询返回 0 行、写入还「成功」，整月数据当空月处理。"""
    from src.lib.db import connectors as connectors_module
    from src.lib.db.connectors import ArchiveIntegrityError

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-08 12:00:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_integrity",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
    )
    await db.init_schema(_ShardBase)
    april = arrow.get("2026-04-08").datetime
    async with db.write_session(time_ctx=april) as session:
        for idx in range(100):
            session.add(_ShardModel(value=f"row-{idx}"))

    report = await db.run_archiver_task()
    assert report.archived == ["2026_04"]

    archive_path = tmp_path / "framework_integrity" / "events_2026_04.db.zst"
    blob = archive_path.read_bytes()
    archive_path.write_bytes(blob[: int(len(blob) * 0.6)])

    with pytest.raises(ArchiveIntegrityError):
        async with db.read_session(
            time_ctx=april,
            cold_policy=ColdPolicy.HYDRATE,
        ) as _session:
            pass

    # 坏档进入隔离区留证，在线库保持空白。
    quarantined = list(
        (tmp_path / "framework_integrity").glob("events_2026_04.db.zst.corrupt.*")
    )
    assert len(quarantined) == 1
    assert not archive_path.exists()
    assert not (tmp_path / "framework_integrity" / "events_2026_04.db").exists()


@pytest.mark.asyncio
async def test_manifest_flush_failure_does_not_block_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """manifest 落盘失败止步于旁路，业务写入照常完成。

    manifest 是旁路加速状态（真实数据在分片文件里）；旧实现把一次
    磁盘/权限问题放大成整批写入失败。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-08 12:00:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_manifest_tolerance",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
    )
    await db.init_schema(_ShardBase)

    def _always_fail(self: object) -> None:
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(
        connectors_module.SegmentStore,
        "_write_manifest_atomic",
        _always_fail,
    )

    # 用一个「全新」月份：只有 create_if_missing 的建分片占位分支
    # 会真正调用 flush_manifest()。已存在的月份会绕过该分支，测试将空转。
    fresh = arrow.get("2027-01-08").datetime
    async with db.write_session(time_ctx=fresh) as session:
        session.add(_ShardModel(value="written-without-manifest"))

    async with db.read_session(time_ctx=fresh) as session:
        values = (await session.execute(select(_ShardModel.value))).scalars().all()
    assert list(values) == ["written-without-manifest"]


@pytest.mark.asyncio
async def test_ensure_budget_rechecks_warm_state_under_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """逐出前在锁内复检状态，防「快照 WARM、实际已 HOT」误删新数据。

    逐出候选列表来自快照，循环里每个 await 都会让出事件循环；期间若有
    写入把该分片提升回 HOT，仍按快照逐出就会删掉唯一持有新写入的副本。"""
    from src.lib.db import connectors as connectors_module
    from src.lib.db.connectors import SegmentState

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-06-08 12:00:00").int_timestamp,
    )

    db = EventStore(
        namespace="framework_evict_race",
        prefix="events",
        fmt="%Y_%m",
        active_window_months=1,
        warm_ttl_seconds=0,
        warm_budget_mb=0,
    )
    await db.init_schema(_ShardBase)

    april = arrow.get("2026-04-08").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="original"))
    await db.run_archiver_task()

    db_path = tmp_path / "framework_evict_race" / "events_2026_04.db"
    archive_path = db_path.with_suffix(".db.zst")
    assert archive_path.is_file()

    # 复刻 hydrate 之后的 WARM 在线库（手动控制逐出时机）。
    await asyncio.to_thread(db._decompress_file, archive_path, db_path)
    await db._touch_segment("2026_04", state=SegmentState.WARM)
    assert db_path.is_file()

    # 模拟并发写入在「快照之后、取锁之前」完成：写入会把分片提升为 HOT。
    real_shard_lock = db._shard_lock

    def _flip_then_lock(shard_key: str) -> object:
        entry = db._load_manifest().segments.get(shard_key)
        if entry is not None:
            entry.state = SegmentState.HOT
        return real_shard_lock(shard_key)

    monkeypatch.setattr(db, "_shard_lock", _flip_then_lock)

    await db._ensure_budget()

    # 锁内复检为 HOT：逐出就此跳过，新数据留在在线库。
    assert db_path.is_file()
    async with db.read_session(time_ctx=april) as session:
        values = (await session.execute(select(_ShardModel.value))).scalars().all()
    assert list(values) == ["original"]


@pytest.mark.asyncio
async def test_atomic_write_works_after_production_init_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """走完生产启动顺序后，跨库原子写照常可用。

    生产启动先 init_db() 并常驻连接池，此后封号/改名/审批走原子会话；
    SQLite 缺其它连接时才允许改 journal_mode，先开 WAL 再切 DELETE 会锁。"""
    from src.lib.db import connectors as connectors_module
    from src.lib.db import manager as manager_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-10-07 12:00:00+08:00").int_timestamp,
    )

    # 干净的登记状态（其他用例可能登记过别的根目录）。
    manager = manager_module.db_manager
    monkeypatch.setattr(manager, "_rollback_journal_dirs", [])
    # 全新根目录对应全新引擎；清空池里旧根目录的引擎。
    await manager.dispose_all()

    from src.services.db import init_db

    await init_db()

    # init_db 之后三个库已经是 DELETE，原子路径只是沿用。
    for name in ("core_db/core.db",):
        conn = sqlite3.connect(tmp_path / name)
        try:
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            conn.close()
        assert mode == "delete", f"{name} 应为 delete，实际 {mode}"

    # 关键断言：生产顺序下原子写恢复可用（此处曾是 database is locked）。
    from src.database.core.consts import Permission
    from src.database.core.ops import UserOps
    from src.lib.db.atomic import system_atomic_session
    from src.services.db import bind_cross_store_schemas

    bind_cross_store_schemas()
    async with system_atomic_session() as session:
        await UserOps(session).add_user(
            user_id="u-init-seq",
            user_name="InitSeq",
            permission=Permission.NORMAL,
        )


@pytest.mark.asyncio
async def test_watchdog_timeout_does_not_retry_callback() -> None:
    """看门狗超时后回调只被调用一次，重试会对已提交分片双写。

    回调（write_batch）按分片逐个独立提交；取消发生后难以得知提交到
    哪一步，重试整批会把已提交部分再写一遍。"""
    calls: list[list[int]] = []
    stuck = asyncio.Event()

    async def _stuck(batch: list[int]) -> None:
        calls.append(list(batch))
        await stuck.wait()

    writer = BatchWriter[int](
        flush_callback=_stuck,
        batch_size=10,
        flush_interval=0.05,
        max_retries=3,  # 预留多次重试预算，确认超时路径绕开重试
        retry_backoff=0.0,
        flush_timeout=0.1,
    )

    await writer.add(1)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(writer.drain(), timeout=5)

    # 关键断言：回调只被调用一次，重试通道关闭。
    assert len(calls) == 1
    assert writer.health.flush_timeouts == 1
    assert writer.dead_letters[0].attempts == 1

    # 死信保留整批，供关机落库与人工重放。
    assert writer.dead_letters[0].batch == (1,)

    stuck.set()
    await writer.close()


@pytest.mark.asyncio
async def test_atomic_session_revives_sealed_shard_before_attach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """当月分片被 seal 后，跨库原子写先唤醒它再 ATTACH。

    旧行为：直接拿分片路径 ATTACH。分片被 force seal（只剩 .zst）时，
    SQLite 凭空建出空库并在其中完成事务，审计/快照与归档里的旧行分裂。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-10-15 12:00:00+08:00").int_timestamp,
    )

    from src.database.core.consts import Permission
    from src.database.core.ops import UserOps

    # 生产启动会经 services.db 登记参与者库走 rollback journal。
    from src.database.instances import core_db, log_db, snapshot_db
    from src.database.log.consts import AuditAction, AuditCategory, AuditContext
    from src.database.log.ops import AuditLogOps
    from src.database.log.tables import LogBase
    from src.lib.db.atomic import system_atomic_session
    from src.lib.db.manager import db_manager
    from src.services.db import bind_cross_store_schemas

    monkeypatch.setattr(db_manager, "_rollback_journal_dirs", [])
    for store in (core_db, log_db, snapshot_db):
        db_manager.register_rollback_journal_dir(store.base_dir)
    bind_cross_store_schemas()
    await log_db.store.init(LogBase)

    # 1) 正常写入一条审计日志。
    async with system_atomic_session() as session:
        await UserOps(session).add_user(
            user_id="u1", user_name="U1", permission=Permission.NORMAL
        )
        await AuditLogOps(session).create_audit_log(
            target_id="t1",
            context_type=AuditContext.USER,
            category=AuditCategory.PERMISSION,
            action=AuditAction.GRANT,
        )

    # 2) 复刻体积闸门 force seal 当月 log 分片。
    log_shard = log_db.store._get_file_paths("202610")[0]
    await db_manager.dispose(str(log_shard))
    outcome, _ = await log_db.store._archive_shard("202610", force=True)
    from src.lib.db.connectors import SegmentArchiveOutcome

    assert outcome == SegmentArchiveOutcome.ARCHIVED
    assert not log_shard.exists()

    # 3) 再走一次原子写：归档被唤醒并复用，影子空库就此绝迹。
    async with system_atomic_session() as session:
        await AuditLogOps(session).create_audit_log(
            target_id="t2",
            context_type=AuditContext.USER,
            category=AuditCategory.PERMISSION,
            action=AuditAction.GRANT,
        )

    conn = sqlite3.connect(log_shard)
    try:
        rows = sorted(
            row[0] for row in conn.execute("SELECT target_id FROM sys_audit_log")
        )
    finally:
        conn.close()
    # 关键断言：旧行（t1）与新行（t2）同处一个分片，影子库分裂绝迹。
    assert rows == ["t1", "t2"]


@pytest.mark.asyncio
async def test_manifest_tmp_path_is_unique_per_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """manifest 落盘的临时文件名按写者唯一。

    主进程与 cron 子进程（以及线程池里的多个写者）可能同时落盘；固定
    名 ".json.tmp" 会让两边互踩，manifest 从此损坏。"""
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-09-01 12:00:00+08:00").int_timestamp,
    )

    db = EventStore(namespace="framework_manifest_unique", prefix="events", fmt="%Y_%m")
    await db.init_schema(_ShardBase)

    recorded: list[str] = []
    real_replace = os.replace

    def _recording_replace(src: object, dst: object) -> None:
        recorded.append(str(src))
        real_replace(src, dst)

    monkeypatch.setattr(connectors_module.os, "replace", _recording_replace)

    await db.flush_manifest()
    await asyncio.to_thread(db._write_manifest_atomic)

    assert len(recorded) == 2
    # 两次写入（两线程）各自使用独立临时路径。
    assert recorded[0] != recorded[1], f"临时名冲突: {recorded}"
    assert all(path.endswith(".tmp") for path in recorded)


@pytest.mark.asyncio
async def test_restore_reset_clears_shard_caches_for_in_place_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """备份恢复（就地覆盖、inode 保持）后复位分片库内存态。

    _initialized_shards 按 (st_dev, st_ino) 判「schema 已建」；copy2
    就地覆盖保留 inode，身份相同即误命中缓存，补丁照旧跳过重放。"""
    from src.lib.backup.registry import (
        register_backup_database,
        reset_registered_database_runtime_states,
    )
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-09-01 12:00:00+08:00").int_timestamp,
    )

    db = EventStore(namespace="framework_restore_reset", prefix="events", fmt="%Y_%m")
    await db.init_schema(_ShardBase)
    async with db.write_session(time_ctx=arrow.get("2026-09-01").datetime) as session:
        session.add(_ShardModel(value="before"))
    await db.flush_manifest()

    # 记录内存态确实已建立（缓存非空）。
    assert db._initialized_shards
    assert db._manifest is not None

    # 复刻恢复覆盖：外部备份文件 copy2 就地覆盖分片。
    db_path = tmp_path / "framework_restore_reset" / "events_2026_09.db"
    backup_copy = tmp_path / "backup_events_2026_09.db"
    import shutil

    shutil.copy2(db_path, backup_copy)

    register_backup_database(db)
    reset_registered_database_runtime_states()

    # 关键断言：schema 标记与 manifest 缓存都已复位，下次访问重放补丁。
    assert db._initialized_shards == {}
    assert db._manifest is None
    assert db._manifest_dirty is False
    _ = backup_copy
