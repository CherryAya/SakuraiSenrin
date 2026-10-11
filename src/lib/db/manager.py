"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 00:40:09
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 17:27:25
Description: db 管理器
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import nonebot
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

    @staticmethod
    def _engine_key(full_path: str) -> str:
        """引擎缓存键：把库文件路径归一化成绝对形式。

        调用方可能用相对路径 open（GLOBAL_DB_ROOT 是相对路径），却用 resolve 后
        的绝对路径 dispose（归档路径传 _safe_resolve 的结果）。若按原始字符串做
        键，同一个文件会被注册成两个条目，dispose 静默空转、连接池句柄永不释放；
        Windows 上表现为分片 rename 永远 WinError 32——这正是生产归档长期失败的
        根因（测试全用绝对 tmp_path，恰好掩盖了键不匹配）。
        """
        return f"sqlite+aiosqlite:///{Path(full_path).resolve()}"

    def _init_sqlite_pragma(
        self,
        dbapi_connection: DBAPIConnection,
        connection_record: ConnectionPoolEntry,
    ) -> None:
        _ = connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute(f"PRAGMA mmap_size={256 * 1024 * 1024}")
        cursor.close()

    @staticmethod
    def _resolve_sql_echo() -> bool:
        try:
            return bool(getattr(nonebot.get_driver().config, "debug_sql_echo", False))
        except ValueError:
            return False

    async def _ensure_engine(self, url: str) -> None:
        if url in self._session_factories:
            return

        async with self._lock:
            if url in self._session_factories:
                return

            echo = self._resolve_sql_echo()
            engine = create_async_engine(url, echo=echo)
            # 单库会话把 attached schema 前缀映射回 main（SQLite 默认 schema）。
            # 这样同一批 ORM 类既能用于单库写入，也能用于跨库原子写入。
            engine = engine.execution_options(
                schema_translate_map=_standalone_schema_map(),
            )
            event.listen(engine.sync_engine, "connect", self._init_sqlite_pragma)

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
            await self._ensure_engine(url)
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
