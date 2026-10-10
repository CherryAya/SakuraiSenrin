"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-20 00:26:45
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 14:17:04
Description: 通用工具
"""

from __future__ import annotations

import time


def split_list[T](input_list: list[T], size: int) -> list[list[T]]:
    return [input_list[i : i + size] for i in range(0, len(input_list), size)]


def get_current_time() -> int:
    return int(time.time())  # noqa: TID251
