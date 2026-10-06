from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

import arrow
import nonebot
import pytest
from sqlalchemy import Integer, String, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.database.core.tables import CoreBase
from src.database.patches import build_core_patch_registry
from src.lib.db.batch import BatchWriter
from src.lib.db.connectors import ColdPolicy, EventStore, StateStore
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
