from abc import ABC, abstractmethod
from contextlib import _AsyncGeneratorContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, final

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from src.lib.db.manager import db_manager

DB_ROOT = Path("data/db")


class BaseDB(ABC):
    """数据库管理的抽象基类。

    子类必须实现 session() 方法，返回一个 AsyncSession 的上下文管理器。
    """

    @abstractmethod
    def session(
        self,
        commit: bool = True,
        *args: Any,
        **kwargs: Any,
    ) -> _AsyncGeneratorContextManager[AsyncSession, None]:
        pass

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
        path = DB_ROOT / self.filename
        return db_manager.open(str(path), commit)


@final
@dataclass
class ShardedDB(BaseDB):
    """分片数据库管理类（时间切分模式）。

    适用于数据量巨大、且具有明显时间属性的数据。
    该类会根据时间上下文 (`time_ctx`) 动态生成文件名，实现按天、按月或按年自动分库。
    例如：操作日志等流水数据。

    Attributes:
        prefix (str): 数据库文件名的前缀（例如 "logs"）。
        fmt (str): 时间格式化字符串 (strftime 格式)，用于生成文件后缀。
                    - 按月分库: "%Y_%m" -> logs_2026_02.db
                    - 按日分库: "%Y_%m_%d" -> logs_2026_02_12.db

    Example:
        >>> log_db = ShardedDB(prefix="app_log", fmt="%Y_%m")
        >>>
        >>> async with log_db.session(commit=True) as session:
        >>>     session.add(Log(msg="User Login"))
        >>>     # data/db/app_log_2026_02.db
        >>>
        >>> last_month = datetime(2026, 2, 12)
        >>> async with log_db.session(time_ctx=last_month) as session:
        >>>     # data/db/app_log_2026_02.db
        >>>     stmt = select(Log).limit(10)
        >>>     ...
    """

    prefix: str
    fmt: str

    def session(
        self,
        time_ctx: datetime = datetime.now(),
        commit: bool = True,
    ) -> _AsyncGeneratorContextManager[AsyncSession, None]:
        filename = f"{self.prefix}_{time_ctx.strftime(self.fmt)}.db"
        path = DB_ROOT / filename
        return db_manager.open(str(path), commit)
