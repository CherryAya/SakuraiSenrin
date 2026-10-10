"""封禁时长解析（admin 专用）。"""

from __future__ import annotations

from datetime import timedelta
import re

_TIME_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_PATTERN = re.compile(r"(\d+)([dhms])")


def parse_duration(time_str: str) -> timedelta:
    """把 ``1d2h3m`` 形式的时长解析为 timedelta。无有效片段时抛 ValueError。"""
    total_seconds = 0
    for value, unit in _PATTERN.findall(time_str):
        total_seconds += int(value) * _TIME_UNITS[unit]
    if total_seconds <= 0:
        raise ValueError(time_str)
    return timedelta(seconds=total_seconds)
