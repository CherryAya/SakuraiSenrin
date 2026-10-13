"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 16:35:32
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 23:19:16
Description: 公共 types
"""

from collections.abc import Mapping, Sequence
from typing import TypeGuard, cast

type JsonScalar = str | int | float | bool | None
type JsonArray = list[JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonValue = JsonScalar | JsonArray | JsonObject


def is_object_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    """把 object 收窄成键为 str、值为 object 的映射。"""
    return isinstance(value, Mapping)


def is_object_sequence(value: object) -> TypeGuard[Sequence[object]]:
    """把 object 收窄成「元素为 object 的序列」，排除 str / bytes。"""
    return isinstance(value, (list, tuple))


def is_json_value(value: object) -> TypeGuard[JsonValue]:
    """递归校验 object 是合法 JSON 值（标量 / 列表 / 对象）。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return True
    if isinstance(value, Mapping):
        for key, item in cast("Mapping[object, object]", value).items():
            if not isinstance(key, str) or not is_json_value(item):
                return False
        return True
    if isinstance(value, (list, tuple)):
        return all(is_json_value(item) for item in cast("list[object]", value))
    return False


def is_json_object(value: object) -> TypeGuard[JsonObject]:
    """把 object 收窄成 JsonObject（键 str、值 JsonValue）。

    与 is_object_mapping 的区别：本函数额外校验值确实是 JSON 形态。
    """
    # 映射分支已校验 str 键：此处收紧 dict
    if not isinstance(value, dict):
        return False
    return is_json_value(cast("Mapping[object, object]", value))


def as_str_tuple(value: object) -> tuple[str, ...]:
    """把字符串序列收窄成字符串元组，元素去空白后丢弃空串。"""
    if not is_object_sequence(value):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item)


def as_int(value: object, default: int) -> int:
    """把任意来源值收窄成 int；bool 与其余类型一律回落 default。"""
    if isinstance(value, bool):
        return default
    return value if isinstance(value, int) else default


def as_float(value: object, default: float) -> float:
    """把任意来源值收窄成 float；bool 同样回落 default。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, float):
        return value
    return value if isinstance(value, int) else default


def as_object(value: JsonValue) -> JsonObject | None:
    """把 JSON 值收窄成 JSON 对象；其余形态返回 None。"""
    return value if isinstance(value, dict) else None


def as_str(value: object, default: str = "") -> str:
    """把任意来源值收窄成字符串；其余形态回落 default。"""
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
