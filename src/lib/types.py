from typing import TypeGuard


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
