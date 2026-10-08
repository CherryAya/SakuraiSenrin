"""squarify 的类型存根。

squarify 未随包发布 py.typed，strict 模式下 pyright 会把它的全部成员判为
Unknown，并顺着 ``normalize_sizes`` / ``padded_squarify`` 的每次调用向下传播，
在 wordbank treemap 与 water renderer 两处制造大量与本仓库代码无关的错误。

真实实现（squarify/__init__.py）中：
* ``squarify`` / ``padded_squarify`` 返回 ``list[dict]``，每个 dict 含
  ``x`` / ``y`` / ``dx`` / ``dy`` 四个数值键；
* ``normalize_sizes`` 返回 ``list[numeric]``（实现里已 map 成 float）。

这里按「本仓库实际用法 + 真实返回形态」给出签名，输入声明为可迭代的数值，
不追求描述内部推导细节。
"""

from collections.abc import Iterable, Mapping

def normalize_sizes(sizes: Iterable[float], dx: float, dy: float) -> list[float]: ...
def squarify(
    sizes: Iterable[float], x: float, y: float, dx: float, dy: float
) -> list[dict[str, float]]: ...
def padded_squarify(
    sizes: Iterable[float], x: float, y: float, dx: float, dy: float
) -> list[dict[str, float]]: ...
def plot(
    sizes: Iterable[float],
    norm_x: float = ...,
    norm_y: float = ...,
    color: object = ...,
    label: object = ...,
    value: object = ...,
    ax: object = ...,
    pad: bool = ...,
    bar_kwargs: Mapping[str, object] | None = ...,
    text_kwargs: Mapping[str, object] | None = ...,
    **kwargs: object,
) -> object: ...
