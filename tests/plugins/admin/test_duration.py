from __future__ import annotations

from datetime import timedelta

import pytest

from src.plugins.admin._services.duration import parse_duration


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1d", timedelta(days=1)),
        ("2h", timedelta(hours=2)),
        ("30m", timedelta(minutes=30)),
        ("45s", timedelta(seconds=45)),
        ("1d2h3m4s", timedelta(days=1, hours=2, minutes=3, seconds=4)),
        ("1h30m", timedelta(hours=1, minutes=30)),
        ("10s10s", timedelta(seconds=20)),
    ],
)
def test_parse_duration_accepts_compound_forms(raw: str, expected: timedelta) -> None:
    assert parse_duration(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "0d", "0s", "1x"])
def test_parse_duration_rejects_invalid_or_zero(raw: str) -> None:
    with pytest.raises(ValueError, match=r".*"):
        parse_duration(raw)


def test_parse_duration_ignores_sign_prefix() -> None:
    """正则只取数字片段，负号由调用方处理。"""
    assert parse_duration("-5m") == timedelta(minutes=5)
