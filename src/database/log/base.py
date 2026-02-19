"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-16 15:11:57
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 22:33:07
Description: log db 基本类型
"""

from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar

from src.lib.enums import LocalizedMixin


class BaseAuditEnum(LocalizedMixin, StrEnum):
    """
    [强制规范] 审计日志枚举基类。

    所有核心枚举 (AuditCategory, AuditAction) 和插件自定义枚举
    **必须** 继承此类，否则类型检查不通过。
    """

    __labels__: ClassVar[MappingProxyType[str, str]]
