"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 00:39:22
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 19:34:49
Description: db 连接器
"""

from abc import ABC, abstractmethod
import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import _AsyncGeneratorContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
from typing import Any, final

import arrow
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from src.lib.consts import GLOBAL_DB_ROOT
from src.logger import logger

from .manager import db_manager


@dataclass
class BaseDB(ABC):
    """数据库管理的抽象基类。

    子类必须实现 session() 方法，返回一个 AsyncSession 的上下文管理器。
    """

    namespace: str

    @abstractmethod
    def session(
        self,
        commit: bool = True,
        *args: Any,
        **kwargs: Any,
    ) -> _AsyncGeneratorContextManager[AsyncSession, None]:
        pass

    @property
    def base_dir(self) -> Path:
        d = GLOBAL_DB_ROOT / self.namespace
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def init(self, base: type[DeclarativeBase]) -> None:
        """根据提供的 ORM 基类初始化数据库表结构。

        该方法会连接数据库（如果不存在则创建），并执行 `create_all` 来创建所有定义的表。

        Args:
            base (type[DeclarativeBase]): 包含 ORM 模型定义的 SQLAlchemy 基类。

        Example:
            >>> # 在应用启动时调用
            >>> await my_db.init(Base)
        """
        async with self.session() as session:
            engine = session.bind
            assert isinstance(engine, AsyncEngine)
            async with engine.begin() as conn:
                await conn.run_sync(base.metadata.create_all)


@final
@dataclass
class StaticDB(BaseDB):
    """静态数据库管理类（单文件模式）。

    用于存储需要长期使用的数据。
    例如：CORE 数据库，存储用户信息、封禁信息等。

    Example:
        >>> # 1. 定义实例：管理 data/db/users.db
        >>> user_db = StaticDB(filename="users.db")
        >>>
        >>> # 2. 使用会话进行查询
        >>> async with user_db.session() as session:
        >>>     stmt = select(User).where(User.id == 1)
        >>>     result = await session.execute(stmt)
    """

    filename: str

    def session(
        self,
        commit: bool = True,
    ) -> _AsyncGeneratorContextManager[AsyncSession, None]:
        path = self.base_dir / self.filename
        return db_manager.open(str(path), commit)


@final
@dataclass
class ShardedDB(BaseDB):
    """分片数据库管理类（时间切分与冷热分离模式）。

    专门用于管理按时间切分的流水数据（如日志、聊天记录），内置跨库查询与冷库自动压缩/唤醒机制。

    ⚠️ 环境依赖：
        - 系统中必须安装 `7z` 命令。

    Attributes:
        prefix (str): 数据库文件名的前缀，例如 "water_logs"。
        fmt (str): 时间格式化字符串，用于生成分片后缀，默认为按月 "%Y_%m"。
        active_window_months (int): 热数据存活窗口，默认 2（当前月与上个月），之外的数据将被自动压缩。

    Examples:
        1. 初始化实例
        >>> water_db = ShardedDB(prefix="water_logs", fmt="%Y_%m")

        2. 日常单库读写（自动路由到目标时间的分库）
        >>> import arrow
        >>> now = arrow.now().datetime
        >>> async with water_db.session(time_ctx=now) as session:
        >>>     session.add(WaterMessage(user_id=123, group_id=456))

        3. 跨库聚合查询（遇到冷库会自动静默解压）
        >>> start_time = arrow.get("2026-01-01").datetime
        >>> end_time = arrow.get("2026-03-31").datetime
        >>>
        >>> async def count_msgs(session) -> int:
        >>>     stmt = select(func.count()).where(WaterMessage.user_id == 123)
        >>>     return (await session.execute(stmt)).scalar() or 0
        >>>
        >>> results: list[int] = await water_db.map_reduce(start_time, end_time, count_msgs)
        >>> total = sum(results)

        4. 配合定时任务执行冷库压缩（如使用 APScheduler）
        >>> @scheduler.scheduled_job("cron", hour=3)
        >>> async def archive_job():
        >>>     await water_db.run_archiver_task()
    """  # noqa: E501

    prefix: str
    fmt: str = "%Y_%m"
    active_window_months: int = 2
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def _get_shard_key(self, dt: datetime) -> str:
        return dt.strftime(self.fmt)

    def _get_file_paths(self, shard_key: str) -> tuple[Path, Path]:
        base = self.base_dir / f"{self.prefix}_{shard_key}"
        return base.with_suffix(".db"), base.with_suffix(".7z")

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

    async def _ensure_shard_online(self, shard_key: str) -> None:
        db_path, zip_path = self._get_file_paths(shard_key)
        if db_path.exists() or not zip_path.exists():
            return

        async with self._get_lock(shard_key):
            if db_path.exists():
                return

            safe_zip = self._safe_resolve(zip_path)
            safe_out = self._safe_resolve(self.base_dir)

            logger.info(f"唤醒冷库: 正在静默解压 {safe_zip.name}")

            process = await asyncio.create_subprocess_exec(
                "7z",
                "x",
                str(safe_zip),
                f"-o{safe_out}",
                "-aos",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                await asyncio.wait_for(process.communicate(), timeout=45.0)
            except TimeoutError:
                process.kill()
                logger.error(f"解压超时被强杀: {safe_zip.name}")
                raise

            if process.returncode == 0:
                logger.success(f"冷库解压完成: {db_path.name}")
            else:
                logger.error(f"解压失败, Exit Code: {process.returncode}")

    @asynccontextmanager
    async def session(
        self,
        commit: bool = True,
        time_ctx: datetime | None = None,
    ) -> AsyncGenerator[AsyncSession, None]:
        if time_ctx is None:
            time_ctx = datetime.now()

        shard_key = self._get_shard_key(time_ctx)
        await self._ensure_shard_online(shard_key)

        db_path, _ = self._get_file_paths(shard_key)
        async with db_manager.open(str(db_path), commit) as sess:
            yield sess

    async def map_reduce[T](
        self,
        start_time: datetime,
        end_time: datetime,
        query_func: Callable[[AsyncSession], Awaitable[T]],
    ) -> list[T]:
        curr = arrow.get(start_time).floor("month")
        end = arrow.get(end_time).floor("month")
        months_span = (
            (end_time.year - start_time.year) * 12 + end_time.month - start_time.month
        )
        if months_span > 6:
            raise ValueError("目标超出 6 个月的最大范围")
        keys = []
        while curr <= end:
            keys.append(curr.strftime(self.fmt))
            curr = curr.shift(months=1)

        shard_keys = list(dict.fromkeys(keys))

        results: list[T] = []
        for key in shard_keys:
            await self._ensure_shard_online(key)
            db_path, _ = self._get_file_paths(key)
            if db_path.exists():
                async with db_manager.open(str(db_path), commit=False) as sess:
                    results.append(await query_func(sess))
        return results

    async def run_archiver_task(self) -> None:
        now = arrow.now()
        active_keys = [now.strftime(self.fmt), now.shift(months=-1).strftime(self.fmt)]

        for db_file in self.base_dir.glob(f"{self.prefix}_*.db"):
            file_key = db_file.stem.replace(f"{self.prefix}_", "")

            if file_key in active_keys:
                continue

            zip_path = db_file.with_suffix(".7z")
            async with self._get_lock(file_key):
                safe_db = self._safe_resolve(db_file)
                safe_zip = self._safe_resolve(zip_path)

                logger.info(f"归档冷库: {safe_db.name} -> {safe_zip.name}")

                process = await asyncio.create_subprocess_exec(
                    "7z",
                    "a",
                    "-t7z",
                    "-m0=lzma2",
                    str(safe_zip),
                    str(safe_db),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )

                _, stderr = await process.communicate()

                try:
                    assert process.returncode == 0
                    await db_manager.dispose(str(safe_db))
                    os.remove(safe_db)
                    logger.success(f"归档完成，已释放原始磁盘占用: {safe_db.name}")
                except AssertionError:
                    logger.error(
                        f"归档压缩失败: {stderr.decode('utf-8', errors='ignore')}"
                    )
                except PermissionError:
                    logger.warning(
                        f"归档文件被占用，保留源文件，明日重试: {safe_db.name}"
                    )
