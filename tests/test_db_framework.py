from __future__ import annotations

from pathlib import Path

import arrow
import pytest
from sqlalchemy import Integer, String, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.lib.db.batch import BatchWriter
from src.lib.db.connectors import ColdPolicy, ShardedDB, StaticDB
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
async def test_static_db_patch_registry_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)

    db = StaticDB(namespace="framework_patch", filename="main.db")
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
async def test_sharded_db_read_session_creates_active_shard(
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

    db = ShardedDB(namespace="framework_shard", prefix="events", fmt="%Y_%m")
    await db.init_schema(_ShardBase)

    async with db.write_session(time_ctx=arrow.get("2026-06-08").datetime) as session:
        session.add(_ShardModel(value="ok"))

    async with db.read_session(time_ctx=arrow.get("2026-06-08").datetime) as session:
        total = await session.execute(select(func.count(_ShardModel.id)))

    assert int(total.scalar() or 0) == 1


@pytest.mark.asyncio
async def test_sharded_db_deny_and_skip_cold_shard(
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

    db = ShardedDB(namespace="framework_cold", prefix="events", fmt="%Y_%m")
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
