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
        # 与跨库原子事务（atomic.py）的协调，见 register_rollback_journal_dir。
        self._rollback_journal_dirs: list[Path] = []

    def register_rollback_journal_dir(self, directory: Path) -> None:
        """登记「必须保持 rollback journal」的库目录（跨库事务参与者）。

        参与跨库原子事务的库必须常驻 rollback journal（DELETE）：SQLite 只在
        rollback journal 下用 super-journal 提供跨 attached database 的原子
        提交，WAL 模式下跨文件 commit 在崩溃时会撕裂。而 journal_mode 是文件
        属性，**存在其它连接时不允许切换**——所以「先用 WAL 打开、等 ATTACH
        时再切 DELETE」必然失败：本管理器的连接池常驻句柄，原子路径的
        `PRAGMA journal_mode=DELETE` 每次都得到 `database is locked`，全部跨库
        原子写（封禁/改名/审批……）静默失败。

        因此这些库从第一次打开起就必须按本登记选择 DELETE。必须在任何连接
        建立之前调用（生产唯一入口是 services.db.init_db）；重复登记幂等。

        按目录（而非单文件）登记：log/snapshot 是按月分片的，每月新分片都会
        经由本管理器打开。
        """
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
        """引擎缓存键：把库文件路径归一化成绝对形式。

        调用方可能用相对路径 open（GLOBAL_DB_ROOT 是相对路径），却用 resolve 后
        的绝对路径 dispose（归档路径传 _safe_resolve 的结果）。若按原始字符串做
        键，同一个文件会被注册成两个条目，dispose 静默空转、连接池句柄永不释放；
        Windows 上表现为分片 rename 永远 WinError 32——这正是生产归档长期失败的
        根因（测试全用绝对 tmp_path，恰好掩盖了键不匹配）。
        """
        return f"sqlite+aiosqlite:///{Path(full_path).resolve()}"

    def _make_pragma_listener(
        self,
        full_path: str,
    ) -> Callable[[DBAPIConnection, ConnectionPoolEntry], None]:
        """生成本引擎的连接初始化钩子（journal 模式按库决定）。

        钩子按引擎闭包捕获库路径：连接池按库复用连接，PRAGMA 必须在每条连接
        建立时设置，且只对当前库生效。
        """

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
        try:
            return bool(getattr(nonebot.get_driver().config, "debug_sql_echo", False))
        except ValueError:
            return False

    async def _ensure_engine(self, full_path: str) -> None:
        url = self._engine_key(full_path)
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
