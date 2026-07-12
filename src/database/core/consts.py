"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 03:34:40
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-03 12:22:52
Description: core db 常量
"""

from enum import IntFlag, StrEnum, auto
from typing import ClassVar

from src.lib.enums import LocalizedMixin


class GroupStatus(LocalizedMixin, StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    AUTHORIZED = "AUTHORIZED"
    DORMANT = "DORMANT"
    BANNED = "BANNED"
    LEFT = "LEFT"

    __label_keys__: ClassVar[dict[str, str]] = {
        UNAUTHORIZED: "enum.group_status.unauthorized",
        AUTHORIZED: "enum.group_status.authorized",
        DORMANT: "enum.group_status.dormant",
        BANNED: "enum.group_status.banned",
        LEFT: "enum.group_status.left",
    }

    @property
    def is_unauthorized(self) -> bool:
        return self == GroupStatus.UNAUTHORIZED

    @property
    def is_authorized(self) -> bool:
        return self == GroupStatus.AUTHORIZED

    @property
    def is_dormant(self) -> bool:
        return self == GroupStatus.DORMANT

    @property
    def is_banned(self) -> bool:
        return self == GroupStatus.BANNED

    @property
    def is_left(self) -> bool:
        return self == GroupStatus.LEFT

    @property
    def is_working(self) -> bool:
        return self in (GroupStatus.AUTHORIZED, GroupStatus.DORMANT)

    @property
    def can_be_woken_up(self) -> bool:
        return self == GroupStatus.DORMANT


class InvitationStatus(LocalizedMixin, StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    IGNORED = "IGNORED"

    __label_keys__: ClassVar[dict[str, str]] = {
        PENDING: "enum.invitation_status.pending",
        APPROVED: "enum.invitation_status.approved",
        REJECTED: "enum.invitation_status.rejected",
        IGNORED: "enum.invitation_status.ignored",
    }

    @property
    def is_pending(self) -> bool:
        return self == InvitationStatus.PENDING

    @property
    def is_approved(self) -> bool:
        return self == InvitationStatus.APPROVED

    @property
    def is_rejected(self) -> bool:
        return self == InvitationStatus.REJECTED

    @property
    def is_ignored(self) -> bool:
        return self == InvitationStatus.IGNORED

    @property
    def is_processed(self) -> bool:
        return self != InvitationStatus.PENDING

    @property
    def is_denied(self) -> bool:
        return self in (InvitationStatus.REJECTED, InvitationStatus.IGNORED)


class Permission(LocalizedMixin, IntFlag):
    NONE = 0
    NORMAL = auto()
    GROUP_ADMIN = auto()
    GROUP_OWNER = auto()
    SUPERUSER = auto()

    __label_keys__: ClassVar[dict[int, str]] = {
        NONE: "enum.permission.none",
        NORMAL: "enum.permission.normal",
        GROUP_ADMIN: "enum.permission.group_admin",
        GROUP_OWNER: "enum.permission.group_owner",
        SUPERUSER: "enum.permission.superuser",
    }

    def has(self, perm: "Permission") -> bool:
        """位运算判断权限"""
        return (self & perm) == perm
