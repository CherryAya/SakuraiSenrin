"""SQLite 写入分块：单条语句的绑定参数受编译期上限约束。"""

from __future__ import annotations


def split_chunks[T](items: list[T], size: int) -> list[list[T]]:
    """把 items 按 size 切成若干块；size 非正时整块返回。"""
    if size <= 0:
        return [items] if items else []
    return [items[i : i + size] for i in range(0, len(items), size)]
