"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 16:23:02
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 23:18:48
Description: db 全局常量
"""

from enum import StrEnum
from typing import ClassVar

from src.lib.enums import LocalizedMixin


class WritePolicy(LocalizedMixin, StrEnum):
    BUFFERED = "buffered"
    IMMEDIATE = "immediate"

    __label_keys__: ClassVar[dict[str, str]] = {
        "buffered": "enum.write_policy.buffered",
        "immediate": "enum.write_policy.immediate",
    }
