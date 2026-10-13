"""``src.lib.types`` 收窄 helper 的边界测试。

重点覆盖两个 JSON 形态校验器：它们取代了此前在跨进程 JSON 边界上
「cast 成 TypedDict」的写法，须能识别非法形态。
"""

from __future__ import annotations

import pytest

from src.lib.types import (
    as_float,
    as_int,
    as_str,
    is_json_object,
    is_json_value,
)


@pytest.mark.parametrize(
    "value",
    ["s", 1, 1.5, True, False, None, [], [1, "a", None], {}, {"k": "v"}, {"n": [1, 2]}],
)
def test_is_json_value_accepts_json_forms(value: object) -> None:
    assert is_json_value(value) is True


@pytest.mark.parametrize(
    "value",
    [
        b"bytes",
        object(),
        {1: "non-str-key"},
        {"k": object()},
        [object()],
        {"nested": {2: "bad-key"}},
        (1, 2, object()),
    ],
)
def test_is_json_value_rejects_non_json_forms(value: object) -> None:
    assert is_json_value(value) is False


@pytest.mark.parametrize(
    "value",
    [{}, {"k": "v"}, {"n": [1, {"deep": None}]}],
)
def test_is_json_object_accepts_json_objects(value: object) -> None:
    assert is_json_object(value) is True


@pytest.mark.parametrize(
    "value",
    [
        [],
        "s",
        1,
        None,
        {1: "non-str-key"},
        {"k": object()},
    ],
)
def test_is_json_object_rejects_non_objects(value: object) -> None:
    assert is_json_object(value) is False


def test_is_json_value_handles_deep_nesting() -> None:
    deep: object = {"a": [{"b": [{"c": 1}]}]}

    assert is_json_value(deep) is True


@pytest.mark.parametrize("bad", [True, False])
def test_as_int_rejects_bool(bad: bool) -> None:
    """bool 是 int 子类，显式排除以免 JSON true 当作 1。"""
    assert as_int(bad, 0) == 0


@pytest.mark.parametrize("bad", [True, False])
def test_as_float_rejects_bool(bad: bool) -> None:
    assert as_float(bad, 0.0) == 0.0


def test_as_int_and_as_str_fall_back_on_wrong_types() -> None:
    assert as_int("100", 7) == 7
    assert as_int(None, 7) == 7
    assert as_int(3, 7) == 3
    assert as_str(5, "d") == "d"
    assert as_str("ok") == "ok"
