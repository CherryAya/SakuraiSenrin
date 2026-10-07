"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 16:35:32
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 23:19:16
Description: 公共 types
"""

from typing import TypeGuard

type JsonScalar = str | int | float | bool | None
type JsonArray = list[JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonValue = JsonScalar | JsonArray | JsonObject


def as_str_tuple(value: object) -> tuple[str, ...]:
    """把「字符串序列」收窄成字符串元组，元素去空白后丢弃空串。

    刻意接受 ``list`` 与 ``tuple`` 两种来源：JSON 解析结果是 ``list``，
    而插件元数据里的 ``extra["docs"]["aliases"]`` 直接写成元组。
    ``str``/``bytes`` 本身也是 Sequence，必须排除，否则会被逐字符拆开。
    """
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item)


def as_int(value: JsonValue, default: int) -> int:
    """把 JSON 值收窄成 int；非整数（含 ``bool``）一律回落 default。

    ``bool`` 是 ``int`` 的子类，JSON 里的 ``true`` 若不显式排除会被当成 1/0
    静默通过；``int("100")`` 这类字符串转换同样会掩盖数据来源问题。
    """
    if isinstance(value, bool):
        return default
    return value if isinstance(value, int) else default


def as_float(value: JsonValue, default: float) -> float:
    """把 JSON 值收窄成 float；``bool`` 同样排除。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, float):
        return value
    return value if isinstance(value, int) else default


def as_object(value: JsonValue) -> JsonObject | None:
    """把 JSON 值收窄成对象；非 dict（含 list）返回 None。"""
    return value if isinstance(value, dict) else None


def as_str(value: JsonValue, default: str = "") -> str:
    """把 JSON 值收窄成字符串；非 str（含 int/float/bool）返回 default。"""
    return value if isinstance(value, str) else default


class _Unset:
    def __repr__(self) -> str:
        return "<UNSET>"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()
type Unset = _Unset


def is_set[T](value: T | Unset) -> TypeGuard[T]:
    return value is not UNSET


def resolve_unset[T](value: T | Unset, default: T) -> T:
    return value if is_set(value) else default
