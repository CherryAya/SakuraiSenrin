"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 15:31:40
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 14:34:51
Description: 缓存 item 定义
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import TYPE_CHECKING, Self

from src.database.core.consts import GroupStatus, Permission

if TYPE_CHECKING:
    pass


@dataclass(slots=True, frozen=True)
class UserCacheItem:
    user_id: str
    name_hash: int
    permission: Permission
    is_self_ignore: bool = False

    def with_name_hash(self, new_hash: int) -> Self:
        if self.name_hash == new_hash:
            return self
        return replace(self, name_hash=new_hash)

    def with_permission(self, new_permission: Permission) -> Self:
        if self.permission == new_permission:
            return self
        return replace(self, permission=new_permission)

    def set_self_ignore(self, is_ignore: bool) -> Self:
        if self.is_self_ignore == is_ignore:
            return self
        return replace(self, is_self_ignore=is_ignore)


@dataclass(slots=True, frozen=True)
class GroupCacheItem:
    group_id: str
    name_hash: int
    status: GroupStatus
    is_all_shut: bool
    disabled_plugins: frozenset[str] = field(default_factory=frozenset)

    def with_name_hash(self, new_hash: int) -> Self:
        if self.name_hash == new_hash:
            return self
        return replace(self, name_hash=new_hash)

    def with_status(self, new_status: GroupStatus) -> Self:
        if self.status == new_status:
            return self
        return replace(self, status=new_status)

    def with_all_shut(self, is_shut: bool) -> Self:
        if self.is_all_shut == is_shut:
            return self
        return replace(self, is_all_shut=is_shut)

    def disable_plugin(self, plugin_name: str) -> Self:
        if plugin_name in self.disabled_plugins:
            return self
        new_plugins = self.disabled_plugins | {plugin_name}
        return replace(self, disabled_plugins=new_plugins)

    def enable_plugin(self, plugin_name: str) -> Self:
        if plugin_name not in self.disabled_plugins:
            return self
        new_plugins = self.disabled_plugins - {plugin_name}
        return replace(self, disabled_plugins=new_plugins)


@dataclass(slots=True, frozen=True)
class BlacklistCacheItem:
    expiry: float = math.inf

    def with_expiry(self, new_expiry: int) -> Self:
        if self.expiry == new_expiry:
            return self
        return replace(self, expiry=new_expiry)


@dataclass(slots=True, frozen=True)
class MemberCacheItem:
    card_hash: int
    permission: Permission

    def with_card_hash(self, new_hash: int) -> Self:
        if self.card_hash == new_hash:
            return self
        return replace(self, card_hash=new_hash)

    def with_permission(self, new_permission: Permission) -> Self:
        if self.permission == new_permission:
            return self
        return replace(self, permission=new_permission)
