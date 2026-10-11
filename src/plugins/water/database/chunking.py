"""SQLite 写入的参数上限分块工具。

单条语句的绑定参数数量有上限（`SQLITE_MAX_VARIABLE_NUMBER`），
批量写入需按上限切分，避免 "too many SQL variables"。
"""

from __future__ import annotations


def split_chunks[T](items: list[T], size: int) -> list[list[T]]:
    """把 items 按 size 切成若干块。size <= 0 时返回单一整块。"""
    if size <= 0:
        return [items] if items else []
    return [items[i : i + size] for i in range(0, len(items), size)]
