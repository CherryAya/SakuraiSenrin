"""``pybktree`` 的最小类型存根。

``pybktree`` 未随包发布 ``py.typed``，strict 模式下 pyright 会把 ``BKTree``
的全部成员判为 Unknown，并顺着 ``add`` / ``find`` 调用向下传播，在 wordbank
媒体相似检索链路上制造成串噪音。

这里只描述本仓库实际用到的成员签名，参数与 ``.venv/.../pybktree.py`` 的真实
定义对齐：构造器接收距离函数与可迭代初始元素，``add`` 插入单元素，
``find`` 返回按距离升序的 ``(distance, item)`` 列表。
"""

from collections.abc import Callable, Iterable, Iterator
from typing import Generic, TypeVar

_T = TypeVar("_T")

class BKTree(Generic[_T]):
    distance_func: Callable[[_T, _T], int]
    tree: tuple[_T, dict[int, tuple[_T, dict[int, object]]]] | None

    def __init__(
        self,
        distance_func: Callable[[_T, _T], int],
        items: Iterable[_T] = (),
    ) -> None: ...
    def add(self, item: _T) -> None: ...
    def find(self, item: _T, n: int) -> list[tuple[int, _T]]: ...
    def __iter__(self) -> Iterator[_T]: ...

def hamming_distance(x: int, y: int) -> int: ...
