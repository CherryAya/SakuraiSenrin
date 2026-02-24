"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-25 00:56:11
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-24 17:16:54
Description: db orm
"""

from datetime import datetime
from enum import IntFlag
from typing import Any, TypeVar

from sqlalchemy import DateTime, Integer, Table, TypeDecorator, event, func, text
from sqlalchemy.engine import Connection, Dialect
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def _add_sqlite_updated_at_trigger(
    target: Table,
    connection: Connection,
    **kwargs: Any,
) -> None:
    if connection.dialect.name != "sqlite":
        return

    if "updated_at" not in target.c:
        return

    table_name = target.name
    trigger_name = f"trg_update_{table_name}_timestamp"
    sql = f"""
    CREATE TRIGGER IF NOT EXISTS {trigger_name}
    AFTER UPDATE ON {table_name}
    FOR EACH ROW
    BEGIN
        UPDATE {table_name}
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = OLD.id;
    END;
    """

    connection.execute(text(sql))


event.listen(Table, "after_create", _add_sqlite_updated_at_trigger)
