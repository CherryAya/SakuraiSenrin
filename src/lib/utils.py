"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-20 00:26:45
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-20 00:27:51
Description: 通用工具
"""

from datetime import timedelta
import re

from httpx import AsyncClient


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


def split_list(input_list: list, size: int) -> list[list]:
    return [input_list[i : i + size] for i in range(0, len(input_list), size)]


async def get_qq_avatar(user_id: str, size: int = 640) -> bytes:
    async with AsyncClient() as client:
        return (
            await client.get(f"http://q1.qlogo.cn/g?b=qq&nk={user_id}&s={size}")
        ).read()
