# 测试/脚本需直接验证内部不变量，故关闭跨模块私有符号告警
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

import arrow
import nonebot
import pytest
from sqlalchemy import Integer, String, func, select, text
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
from src.lib.db.ops import BaseOps
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
    """回归：月初 00:30 CST 时 UTC 仍在上个月，当月分片必须被判为 active。

    历史故障：active 判定用 UTC，导致 1 号凌晨当月分片被判为冷分片并被归档删除。
    """
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    # 2026-09-01 00:30 +08:00 == 2026-08-31 16:30 UTC
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
    """回归：1 号凌晨跑归档任务，不得归档当月分片。"""
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
    """回归：归档不得丢弃在途写事务的数据。

    历史故障：归档任务在写事务飞行途中 unlink 分片文件，导致写入落到已删除的
    inode，数据静默丢失；或重连到新建空库报 "no such table"。
    """
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

    # 无论落在在线分片还是归档里，两条数据都必须可读回。
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
    """回归：分片文件被删除后必须重建 schema 并可继续写入。

    历史故障：_initialized_shards 只记路径不记文件身份，文件重建后不再执行
    create_all，导致该分片整月 "no such table" 写不进去且不自愈。
    """
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
    """回归：CST 月初窗口扫描必须包含当月分片。

    历史故障：map_reduce 用 UTC floor 取月，导致 9/1 00:00-08:00 CST 期间
    漏掉当月分片，wordbank 触发计数漏算、限流规则失效 8 小时。
    """
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
    # 阈值设成 0 MB，使任何非空分片都算超限，无需真的造大数据
    monkeypatch.setattr(connectors_module, "SEGMENT_SIZE_ROLLOVER_MB", 0)

    db = EventStore(namespace="framework_rollover", prefix="events", fmt="%Y_%m")
    await db.init_schema(_ShardBase)

    september = arrow.get("2026-09-10 00:00:00+08:00").datetime
    async with db.write_session(time_ctx=september) as session:
        session.add(_ShardModel(value="row-0"))

    # 2026_09 仍在活跃窗口内，但体积已超阈值
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
    """对齐 ES ILM delete 阶段：只删超期归档，绝不碰在线分片。"""
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

    # 4 月：1 月已出活跃窗口 -> 归档；保留期 3 个月，1 月仍在保留期内
    await db.run_archiver_task()
    db_dir = tmp_path / "framework_retention"
    assert (db_dir / "events_2026_01.db.zst").is_file()

    # 推进到 5 月：保留期边界退到 2 月，1 月超出保留期应被回收
    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-05-20 12:00:00+08:00").int_timestamp,
    )
    await db.run_archiver_task()
    assert not (db_dir / "events_2026_01.db.zst").exists()
    assert not (db_dir / "events_2026_01.db").exists()

    # 在线分片不受影响
    april = arrow.get("2026-04-05").datetime
    async with db.write_session(time_ctx=april) as session:
        session.add(_ShardModel(value="current"))
    assert (db_dir / "events_2026_04.db").is_file()


@pytest.mark.asyncio
async def test_segment_store_health_reports_shard_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分片健康快照（对应 _cat/indices）应能反映在线/归档/活跃状态。"""
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
    """alias 只需 payload 的时间字段，业务侧不再手算分片键。"""
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

    # 时间戳 / record_date 字符串 / record_date 整数 / 分片键，四种形态一致
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
    """回归：CST 月初 8 小时内写入必须落在当月分片。"""
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
    """scan_shards 应限制并发度，取代原先无上限的裸 gather。"""
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
    """health 必须反映落盘量、失败量与死信批次，否则死信仍是不可见的静默丢数据。"""
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
    """pop_dead_letters 供上报/补偿使用，取出后必须清空避免重复上报。"""

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
    """回归：内存死信队列必须有上界，持续失败不能无限占用内存。"""

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
    # 保留最新的 3 条，丢弃最旧的 3 条
    assert writer.health.dropped_dead_letters == 3
    assert writer.dead_letters[-1].batch == (5,)
    await writer.close()


@pytest.mark.asyncio
async def test_dead_letter_persists_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """死信须落库以支持关机后回溯，并可用原 flush 回调重放后标记 resolved。"""
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
    """writer_health 必须能汇总 core/water/wordbank 三处注册的 writer。"""
    from src.services.writer_health import build_health_report

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
    """回归：跨库事务失败时 core / log / snapshot 三个物理文件必须全部回滚。

    历史故障：_save_immediate 开三个独立会话，snapshot 写入失败时 core 行已提交，
    留下「已改主库但缺审计/快照」的中间态。
    """
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
    """正向路径：一次原子事务把三张表分别写进三个物理文件，且单库会话可读。"""
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
    """参与跨库事务的文件必须是 rollback journal。

    应用层错误回滚在 WAL 下也成立，但 SQLite 官方明确 WAL 模式下跨 attached
    database 的 commit **不原子**（进程/电源故障会撕裂）；只有 rollback journal
    的 super-journal 才提供跨文件原子提交。因此这里锁定 journal_mode。
    """
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
    from src.lib.db.atomic import system_atomic_session

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
