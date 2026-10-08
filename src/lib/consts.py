"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 16:10:12
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-02 15:43:09
Description: 公有常量
"""

from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from src.lib.enums import LocalizedMixin

GLOBAL_GROUP_FLAG = "GLOBAL_GROUP"
RESERVED_USER_FLAG = "RESERVED_USER"
PERMANENT_BAN_FLAG = -1


# 注意：文件名必须与 data/font/ 下实际跟踪的资源一致。此前这里写的是
# LXGWWenKaiMono-Regular.ttf，该文件在仓库历史中从未存在过，导致
# _load_lxgw_font 在任意平台都静默回落到 ImageFont.load_default()，
# treemap 摘要字号恒定为首位默认字体。仓库实际跟踪的是 LXGW_NERD_NOTO.ttf。
LXGW_FONG_PATH = Path("./data/font/LXGW_NERD_NOTO.ttf")
MAPLE_FONT_PATH = Path("./data/font/MapleMono-NF-CN-Regular.ttf")
MAPLE_FONT_NAME = "Maple Mono NF CN"


GLOBAL_DB_ROOT = Path("./data/db")


class TriggerType(LocalizedMixin, StrEnum):
    """插件触发方式"""

    COMMAND = "COMMAND"
    PASSIVE = "PASSIVE"
    EVENT = "EVENT"
    CRON = "CRON"

    __label_keys__: ClassVar[dict[str, str]] = {
        COMMAND: "enum.trigger.command",
        PASSIVE: "enum.trigger.passive",
        EVENT: "enum.trigger.event",
        CRON: "enum.trigger.cron",
    }
