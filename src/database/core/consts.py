from enum import IntFlag, StrEnum, auto
from types import MappingProxyType

from src.lib.enums import LocalizedMixin


class UserStatus(LocalizedMixin, StrEnum):
    NORMAL = "NORMAL"
    BANNED = "BANNED"

    __labels__ = MappingProxyType(
        {
            NORMAL: "正常",
            BANNED: "封禁",
        },
    )


class GroupStatus(LocalizedMixin, StrEnum):
    NORMAL = "NORMAL"
    BANNED = "BANNED"
    LEFT = "LEFT"
    UNAUTHORIZED = "UNAUTHORIZED"

    __labels__ = MappingProxyType(
        {
            NORMAL: "正常",
            BANNED: "封禁",
            LEFT: "已退群",
            UNAUTHORIZED: "未授权",
        },
    )


class InvitationStatus(LocalizedMixin, StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    IGNORED = "IGNORED"

    __labels__ = MappingProxyType(
        {
            PENDING: "待审批",
            APPROVED: "已同意",
            REJECTED: "已拒绝",
            IGNORED: "已忽略",
        },
    )


class Permission(LocalizedMixin, IntFlag):
    NONE = 0
    NORMAL = auto()
    GROUP_ADMIN = auto()
    GROUP_OWNER = auto()
    SUPERUSER = auto()

    __labels__ = MappingProxyType(
        {
            NONE: "无权限",
            NORMAL: "普通用户",
            GROUP_ADMIN: "群管理员",
            GROUP_OWNER: "群主",
            SUPERUSER: "超级管理员",
        },
    )

    def has(self, perm: "Permission") -> bool:
        """位运算判断权限"""
        return (self & perm) == perm
