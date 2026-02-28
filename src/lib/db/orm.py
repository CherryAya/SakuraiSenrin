"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-25 00:56:11
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-27 20:44:57
Description: db orm
"""

from enum import IntFlag
from typing import Any, TypeVar

from sqlalchemy import Integer, TypeDecorator
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column

T = TypeVar("T", bound=IntFlag)


class IntFlagType(TypeDecorator[T]):
    impl = Integer
    cache_ok = True

    def __init__(self, enum_class: type[T], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._enum_class: type[T] = enum_class

    def process_bind_param(self, value: T | int | None, dialect: Dialect) -> int | None:
        if value is None:
            return None
        if isinstance(value, self._enum_class):
            return value.value
        if isinstance(value, int):
            return value

    def process_result_value(self, value: int | None, dialect: Dialect) -> T | None:
        if value is None:
            return None
        return self._enum_class(value)


class TimeMixin:
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)
