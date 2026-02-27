"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 16:10:12
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 19:25:09
Description: 公有常量
"""

from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from src.lib.enums import LocalizedMixin

GLOBAL_GROUP_SCOPE = "GLOBAL_GROUP"
RESERVED_USER_SCOPE = "RESERVED_USER"
LXGW_FONG_PATH = Path("./data/font/LXGWWenKaiMono-Regular.ttf")
MAPLE_FONT_PATH = Path("./data/font/MapleMono-NF-CN-Regular.ttf")
GLOBAL_DB_ROOT = Path("./data/db")


class TriggerType(LocalizedMixin, StrEnum):
    """插件触发方式"""

    COMMAND = "COMMAND"
    PASSIVE = "PASSIVE"
    EVENT = "EVENT"
    CRON = "CRON"

    __labels__ = MappingProxyType(
        {
            COMMAND: "指令触发",
            PASSIVE: "被动触发",
            EVENT: "事件触发",
            CRON: "定时任务",
        },
    )
