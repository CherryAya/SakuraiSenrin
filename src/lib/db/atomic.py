"""跨物理库的原子事务。

`core_db` / `log_db` / `snapshot_db` 是三个独立文件。此前 repositories 用
``async with (core_db.session(), log_db.session(), snapshot_db.session())``
一次逻辑写入开三个事务，退出时按 snapshot -> log -> core 依次提交：任一环节
失败都会留下「主库已提交但审计/快照缺失」的中间态。

本模块用 SQLite 的 ``ATTACH`` 把多个物理文件挂到**同一个连接**上，于是它们
共享同一个事务边界：要么全部提交，要么全部回滚。

为什么必须切 DELETE journal
---------------------------
SQLite 官方明确：WAL 模式下跨多个 attached database 的事务**只在各文件内部
原子，跨文件不原子**（commit 期间进程/电源故障会撕裂）；rollback journal 模式
则通过 super-journal 提供跨文件原子提交。而 ``journal_mode`` 是**文件的持久
属性**，无法对同一文件按需切换。

代价经实测可接受：本项目写入走 BatchWriter（batch=50 / 3s），约 17 行/秒，
而 DELETE 模式的批量写吞吐约 18 万行/秒，比 WAL 慢约 2 倍但仍有四个数量级余量。

因此参与跨库事务的文件使用 DELETE journal；不参与的单库写入仍留在 WAL。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import ConnectionPoolEntry

from src.lib.utils.common import get_current_time
from src.logger import logger

# ATTACH 出来的库在 ORM 里用 schema 前缀寻址。取名避开 SQLite 保留字。
SCHEMA_LOG = "aud"
SCHEMA_SNAPSHOT = "snapsht"

_SCHEMA_TRANSLATE_MAP: dict[str | None, str | None] = {
    None: None,
    SCHEMA_LOG: SCHEMA_LOG,
    SCHEMA_SNAPSHOT: SCHEMA_SNAPSHOT,
}

# 单库会话用：把 attached schema 前缀映射回 main。这样 log/snapshot 的表在
# 独立会话里仍落在各自的主文件上（SQLite 默认 schema 即 main）。
STANDALONE_SCHEMA_TRANSLATE_MAP: dict[str | None, str | None] = {
    None: None,
    SCHEMA_LOG: None,
    SCHEMA_SNAPSHOT: None,
}


def bind_attached_schemas(
    core_base: type[DeclarativeBase],
    log_base: type[DeclarativeBase],
    snapshot_base: type[DeclarativeBase],
) -> None:
    """给 log / snapshot 的表打上 attached schema 前缀。

    仅需调用一次（幂等）。之后同一批 ORM 类既能被单独库 session 使用，也能被
    跨库原子 session 使用——后者靠 ``schema_translate_map`` 决定落到哪个文件。
    """
    for table in log_base.metadata.tables.values():
        table.schema = SCHEMA_LOG
    for table in snapshot_base.metadata.tables.values():
        table.schema = SCHEMA_SNAPSHOT
    _ = core_base


class AtomicStore:
    """把若干物理库文件挂到同一连接，提供单一事务边界。

    典型用法::

        async with AtomicStore(primary=core_db, attached=[log_shard, snap_shard]) as tx:
            async with tx.session() as s:
                ...  # 对任意 attached 库的写入都在同一事务里
    """

    def __init__(
        self,
        *,
        primary_path: Path,
        attached_paths: Sequence[Path],
        schema_bases: Sequence[type[DeclarativeBase]],
        journal_mode: str = "DELETE",
    ) -> None:
        self._primary_path = primary_path
        # (schema, path)：schema 必须与 bind_attached_schemas 打的标记一致
        self._attached: list[tuple[str, Path]] = [
            (self._infer_schema(path), path) for path in attached_paths
        ]
        self._schema_bases = list(schema_bases)
        self._journal_mode = journal_mode
        self._engine: AsyncEngine | None = None
        self._session: AsyncSession | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _infer_schema(path: Path) -> str:
        name = path.name
        if "snapshot" in name:
            return SCHEMA_SNAPSHOT
        if "log" in name:
            return SCHEMA_LOG
        raise ValueError(
            f"无法为 {path.name!r} 推断 attached schema；请显式传入 (schema, path)",
        )

    def _build_engine(self) -> AsyncEngine:
        engine = create_async_engine(f"sqlite+aiosqlite:///{self._primary_path}")
        attached = tuple(self._attached)

        @event.listens_for(engine.sync_engine, "connect")
        def _on_connect(
            dbapi_conn: DBAPIConnection,
            _record: ConnectionPoolEntry,
        ) -> None:
            cursor = dbapi_conn.cursor()
            # 参与跨库事务的文件必须用 rollback journal，否则跨文件不原子
            cursor.execute(f"PRAGMA journal_mode={self._journal_mode}")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            for schema, path in attached:
                # schema 名不能参数化，这里只接受本模块定义的常量
                cursor.execute(f"ATTACH ? AS {schema}", (str(path),))
            cursor.close()

        return engine

    async def _ensure_ready(self) -> AsyncSession:
        if self._session is not None:
            return self._session
        async with self._lock:
            if self._session is not None:
                return self._session
            engine = self._build_engine()
            # schema_translate_map 是 execution option，挂在 engine 上而非 session
            engine = engine.execution_options(
                schema_translate_map=_SCHEMA_TRANSLATE_MAP,
            )
            for base in self._schema_bases:
                async with engine.begin() as conn:
                    await conn.run_sync(base.metadata.create_all)
            self._engine = engine
            maker = async_sessionmaker(engine, expire_on_commit=False)
            self._session = maker()
            return self._session

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """开启一个覆盖全部 attached 库的会话（单一事务）。"""
        session = await self._ensure_ready()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def dispose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    @property
    def attached_schemas(self) -> tuple[str, ...]:
        return tuple(schema for schema, _ in self._attached)


@asynccontextmanager
async def atomic_session(
    *,
    primary_path: Path,
    attached_paths: Sequence[Path],
    schema_bases: Sequence[type[DeclarativeBase]],
    journal_mode: str = "DELETE",
) -> AsyncGenerator[AsyncSession, None]:
    """一次性跨库原子会话。

    每次调用都会新建引擎（ATTACH 目标含运行期决定的分片，如当月 log 分片），
    开销主要在文件级；调用方应避免在超短循环里逐条使用。
    """
    store = AtomicStore(
        primary_path=primary_path,
        attached_paths=attached_paths,
        schema_bases=schema_bases,
        journal_mode=journal_mode,
    )
    try:
        async with store.session() as session:
            yield session
    finally:
        await store.dispose()


def ensure_journal_mode(path: Path, mode: str) -> str:
    """把已有文件切到指定 journal_mode，返回实际模式。

    ``journal_mode`` 是文件头里的持久属性，切换会重写文件头；本函数幂等，
    已是目标模式则不做任何事。
    """
    import sqlite3

    if not path.exists():
        return mode
    conn = sqlite3.connect(path)
    try:
        current = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if current == mode.lower():
            return current
        actual = str(conn.execute(f"PRAGMA journal_mode={mode}").fetchone()[0])
        if actual.lower() != mode.lower():
            logger.warning(
                f"{path.name}: 期望 journal_mode={mode}，实际 {actual}",
            )
        return actual
    finally:
        conn.close()


@asynccontextmanager
async def system_atomic_session(
    moment: int | None = None,
    *,
    with_snapshot: bool = True,
) -> AsyncGenerator[AsyncSession, None]:
    """core_db + 当月 log 分片 (+ 当月 snapshot 分片) 的单一事务。

    分片目标按 ``moment`` 的业务时区在运行期解析，因此月末跨零点写入也不会
    写到错误的分片。
    """
    from src.database.core.tables import CoreBase
    from src.database.instances import core_db, log_db, snapshot_db
    from src.database.log.tables import LogBase
    from src.database.snapshot.tables import SnapshotBase
    from src.services.db import bind_cross_store_schemas

    bind_cross_store_schemas()

    moment = get_current_time() if moment is None else moment
    log_path = log_db.shard_file_for(moment)
    attached = [log_path]
    bases: list[type[DeclarativeBase]] = [LogBase]
    if with_snapshot:
        attached.append(snapshot_db.shard_file_for(moment))
        bases.append(SnapshotBase)

    async with atomic_session(
        primary_path=core_db.db_path,
        attached_paths=attached,
        schema_bases=[CoreBase, *bases],
    ) as session:
        yield session


__all__ = [
    "SCHEMA_LOG",
    "SCHEMA_SNAPSHOT",
    "AtomicStore",
    "atomic_session",
    "bind_attached_schemas",
    "ensure_journal_mode",
    "system_atomic_session",
]
