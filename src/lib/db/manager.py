"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 00:40:09
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 17:27:25
Description: db 管理器
"""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import ConnectionPoolEntry

from src.logger import logger


def _standalone_schema_map() -> dict[str | None, str | None]:
    from src.lib.db.atomic import STANDALONE_SCHEMA_TRANSLATE_MAP

    return dict(STANDALONE_SCHEMA_TRANSLATE_MAP)


class DatabaseManager:
    def __init__(self) -> None:
        self._engines: dict[str, AsyncEngine] = {}
        self._session_factories: dict[str, async_sessionmaker[AsyncSession]] = {}
        self._lock = asyncio.Lock()
        self._rollback_journal_dirs: list[Path] = []

    def register_rollback_journal_dir(self, directory: Path) -> None:
        """登记跨库事务参与者目录：首次连接前调用，journal 固定 DELETE。"""
        resolved = Path(directory).resolve()
        if resolved not in self._rollback_journal_dirs:
            self._rollback_journal_dirs.append(resolved)

    def _journal_mode_for(self, full_path: str) -> str:
        resolved = Path(full_path).resolve()
        for directory in self._rollback_journal_dirs:
            if resolved.is_relative_to(directory):
                return "DELETE"
        return "WAL"

    @staticmethod
    def _engine_key(full_path: str) -> str:
        """引擎缓存键：路径归一化为绝对形式。"""
        return f"sqlite+aiosqlite:///{Path(full_path).resolve()}"

    def _make_pragma_listener(
        self,
        full_path: str,
    ) -> Callable[[DBAPIConnection, ConnectionPoolEntry], None]:
        """生成连接初始化钩子：按库选 journal 模式。"""

        def _listener(
            dbapi_connection: DBAPIConnection,
            connection_record: ConnectionPoolEntry,
        ) -> None:
            _ = connection_record
            cursor = dbapi_connection.cursor()
            cursor.execute(f"PRAGMA journal_mode={self._journal_mode_for(full_path)}")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute(f"PRAGMA mmap_size={256 * 1024 * 1024}")
            cursor.close()

        return _listener

    @staticmethod
    def _resolve_sql_echo() -> bool:
        from src.config import config

        return config.DEBUG_SQL_ECHO

    async def _ensure_engine(self, full_path: str) -> None:
        url = self._engine_key(full_path)
        if url in self._session_factories:
            return

        async with self._lock:
            if url in self._session_factories:
                return

            echo = self._resolve_sql_echo()
            engine = create_async_engine(url, echo=echo)
            # 单库会话：attached schema 归 main
            engine = engine.execution_options(
                schema_translate_map=_standalone_schema_map(),
            )
            event.listen(
                engine.sync_engine,
                "connect",
                self._make_pragma_listener(full_path),
            )

            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))

            self._engines[url] = engine
            self._session_factories[url] = async_sessionmaker(
                engine,
                expire_on_commit=False,
            )
            logger.success(f"数据库初始化成功: {url}")

    async def dispose(self, full_path: str) -> None:
        url = self._engine_key(full_path)
        async with self._lock:
            if url in self._engines:
                engine = self._engines.pop(url)
                self._session_factories.pop(url, None)
                await engine.dispose()
                logger.debug(f"释放 connection: {full_path}")

    async def dispose_all(self) -> None:
        async with self._lock:
            engines = list(self._engines.items())
            self._engines.clear()
            self._session_factories.clear()
        for url, engine in engines:
            await engine.dispose()
            logger.debug(f"释放 connection: {url}")

    @asynccontextmanager
    async def open(
        self,
        full_path: str,
        commit: bool = True,
    ) -> AsyncGenerator[AsyncSession, None]:
        url = self._engine_key(full_path)
        if url not in self._session_factories:
            await self._ensure_engine(full_path)
        factory = self._session_factories[url]
        async with factory() as sess:
            try:
                yield sess
                if commit:
                    await sess.commit()
            except Exception:
                await sess.rollback()
                raise


db_manager = DatabaseManager()
