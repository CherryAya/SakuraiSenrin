"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-20 00:26:45
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 14:17:04
Description: 通用工具
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import timedelta


def time_to_timedelta(time_str: str) -> timedelta:
    time_units = {
        "d": 86400,
        "h": 3600,
        "m": 60,
        "s": 1,
    }

    total_seconds = 0
    pattern = r"(\d+)([dhms])"
    matches = re.findall(pattern, time_str)

    for value, unit in matches:
        total_seconds += int(value) * time_units[unit]
    if total_seconds <= 0:
        raise ValueError
    return timedelta(seconds=total_seconds)


def split_list[T](input_list: list[T], size: int) -> list[list[T]]:
    return [input_list[i : i + size] for i in range(0, len(input_list), size)]


def get_current_time() -> int:
    return int(time.time())  # noqa: TID251
