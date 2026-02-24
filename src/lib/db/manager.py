"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 00:40:09
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-24 17:16:36
Description: db 管理器
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from nonebot.log import logger
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class DatabaseManager:
    def __init__(self) -> None:
        self._engines: dict[str, AsyncEngine] = {}
        self._session_factories: dict[str, async_sessionmaker] = {}

    def _init_sqlite_pragma(
        self,
        dbapi_connection: Any,
        connection_record: Any,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA mmap_size={256 * 1024 * 1024}")
        cursor.close()

    async def _ensure_engine(self, url: str) -> None:
        if url in self._session_factories:
            return

        engine = create_async_engine(url, echo=True)
        event.listen(engine.sync_engine, "connect", self._init_sqlite_pragma)

        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))

        self._engines[url] = engine
        self._session_factories[url] = async_sessionmaker(
            engine,
            expire_on_commit=False,
        )
        logger.success(f"数据库初始化成功: {url}")

    @asynccontextmanager
    async def open(
        self,
        full_path: str,
        commit: bool = True,
    ) -> AsyncGenerator[AsyncSession, None]:
        url = f"sqlite+aiosqlite:///{full_path}"
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
