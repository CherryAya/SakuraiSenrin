from collections.abc import Sequence
from typing import Any, cast, get_args

from sqlalchemy import CursorResult, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase


class BaseOps[T: DeclarativeBase]:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.model = self._get_model_class()

    def _get_model_class(self) -> type[T]:
        for base in getattr(self.__class__, "__orig_bases__", []):
            if getattr(base, "__origin__", None) is not BaseOps:
                continue
            if (args := get_args(base)) and issubclass(args[0], DeclarativeBase):
                return cast(type[T], args[0])
        raise ValueError(
            f"类 {self.__class__.__name__} 必须继承自 BaseOps[Model]，"
            "例如: class UserOps(BaseOps[User])",
        )

    async def get_by_id(self, id: str) -> T | None:
        return await self.session.get(self.model, id)

    async def get_list(
        self,
        limit: int = 20,
        offset: int = 0,
        **filters: Any,
    ) -> Sequence[T]:
        stmt = select(self.model).limit(limit).offset(offset)
        if filters:
            stmt = stmt.filter_by(**filters)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_all(self) -> Sequence[T]:
        result = await self.session.execute(select(self.model))
        return result.scalars().all()

    async def bulk_create(self, data_list: list[dict], chunk_size: int = 1000) -> int:
        """
        【通用】极速批量插入 (Insert Only)
        适用于日志、流水等不涉及"更新旧数据"的场景。

        :param data_list: 字典列表，例如 [{"user_id": "1", "name": "A"}, ...]
        :param chunk_size: SQLite 单条 SQL 参数有限制，建议分块执行
        :return: 插入的总行数
        """
        if not data_list:
            return 0

        total_inserted = 0
        for i in range(0, len(data_list), chunk_size):
            chunk = data_list[i : i + chunk_size]
            stmt = insert(self.model).values(chunk)
            result = await self.session.execute(stmt)
            total_inserted += cast(CursorResult, result).rowcount

        return total_inserted
