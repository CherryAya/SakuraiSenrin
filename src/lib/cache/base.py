"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-25 21:43:39
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 22:36:07
Description: 缓存基类
"""

from abc import ABC


class BaseCache[T](ABC):
    def __init__(self) -> None:
        self._storage: dict[str, T] = {}

    def _to_key(self, key: str | int) -> str:
        return str(key)

    def get_storage(self) -> dict[str, T]:
        return self._storage

    def get(self, key: str | int) -> T | None:
        real_key = self._to_key(key)
        return self._storage.get(real_key)

    def set(self, key: str | int, value: T) -> None:
        real_key = self._to_key(key)
        self._storage[real_key] = value

    def set_batch(self, items: dict[str | int, T]) -> None:
        normalized_data = {self._to_key(key): value for key, value in items.items()}
        self._storage.update(normalized_data)

    def delete(self, key: str | int) -> None:
        real_key = self._to_key(key)
        if real_key in self._storage:
            del self._storage[real_key]

    def exists(self, key: str | int) -> bool:
        return self._to_key(key) in self._storage

    def clear(self) -> None:
        self._storage.clear()

    def count(self) -> int:
        return len(self._storage)

    def __contains__(self, key: str | int) -> bool:
        return self.exists(key)

    def __setitem__(self, key: str | int, value: T) -> None:
        self.set(key, value)

    def __getitem__(self, key: str | int) -> T:
        real_key = self._to_key(key)
        return self._storage[real_key]

    def __delitem__(self, key: str | int) -> None:
        self.delete(key)
