"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 16:23:02
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 23:18:48
Description: db 全局常量
"""

from enum import StrEnum
from types import MappingProxyType

from src.lib.enums import LocalizedMixin


class WritePolicy(LocalizedMixin, StrEnum):
    BUFFERED = "buffered"
    IMMEDIATE = "immediate"

    __labels__ = MappingProxyType(
        {
            "buffered": "批量写入",
            "immediate": "即时写入",
        },
    )
