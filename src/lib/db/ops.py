"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 16:26:36
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-24 17:16:00
Description: db 基类
"""

from collections.abc import Mapping, Sequence
from typing import cast, get_args

from sqlalchemy import insert, select
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from src.lib.types import JsonValue

_MISSING = object()


def affected_rows(result: Result[object]) -> int:
    """取 DML 影响行数。

    ``AsyncSession.execute()`` 的静态返回类型是 ``Result``，但 INSERT/UPDATE/DELETE
    单参调用实际拿到的是 ``CursorResult``，只有后者带 ``rowcount``。

    这里必须做运行时校验而非 ``cast``：executemany 形态
    （``execute(stmt, [row1, row2])``）返回 ``IteratorResult``，它与
    ``CursorResult`` 互不继承且没有 ``rowcount``。下游不仅用于计数，还有
    多处 ``affected_rows(...) > 0`` 的状态扭转判据，静默返回 0 会把「写成功」
    误判成「没写中」；因此这里 fail-fast 抛 TypeError，而不是让 AttributeError
    在 batch writer 里被重试后变成死信。

    校验用鸭子类型（``rowcount`` 属性是否存在）而非 ``isinstance(CursorResult)``：
    本函数真实需要的契约只是「有 rowcount 的结果对象」，写成具体类会拒掉合法的
    代理/桩实现（测试里的 ``_FakeResult`` 即为一例）。

    SQLAlchemy 把 ``CursorResult`` 声明成变长泛型 ``Result[Unpack[_Ts]]``，裸写
    ``CursorResult`` 会被 pyright 判为缺少类型参数；集中在此处收口，
    ops 层只需面对一个语义明确的 ``-> int``。
    """
    rowcount = getattr(result, "rowcount", _MISSING)
    if rowcount is _MISSING:
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

    async def get_list(
        self,
        limit: int = 20,
        offset: int = 0,
        **filters: object,
    ) -> Sequence[T]:
        stmt = select(self.model).limit(limit).offset(offset)
        if filters:
            stmt = stmt.filter_by(**filters)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_all(self) -> Sequence[T]:
        result = await self.session.execute(select(self.model))
        return result.scalars().all()

    async def bulk_create(
        self,
        data_list: Sequence[Mapping[str, JsonValue]],
        chunk_size: int = 1000,
    ) -> int:
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
            total_inserted += affected_rows(result)

        return total_inserted
