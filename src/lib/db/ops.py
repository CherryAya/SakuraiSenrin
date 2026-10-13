"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 16:26:36
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-24 17:16:00
Description: db 基类
"""

from collections.abc import Sequence
from typing import cast, get_args

from sqlalchemy import select
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase


def affected_rows(result: Result[object]) -> int:
    """取 DML 影响行数；executemany 形态的结果对象缺 rowcount，抛 TypeError。"""
    missing = object()
    rowcount = getattr(result, "rowcount", missing)
    if rowcount is missing:
        raise TypeError(
            "affected_rows() 需要带 rowcount 的结果对象（单参 execute 的 DML 返回值），"
            f"实际收到 {type(result).__name__}；executemany 形态返回的 "
            "IteratorResult 不提供 rowcount。",
        )
    if isinstance(rowcount, bool) or not isinstance(rowcount, int):
        raise TypeError(f"rowcount 必须是 int，实际为 {type(rowcount).__name__}")
    return rowcount or 0


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

    async def get_by_id(self, id: int) -> T | None:
        return await self.session.get(self.model, id)

    async def get_all(self) -> Sequence[T]:
        result = await self.session.execute(select(self.model))
        return result.scalars().all()
