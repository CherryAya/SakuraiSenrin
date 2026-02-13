from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Self

from src.database.core.consts import GroupStatus, Permission, UserStatus


@dataclass(slots=True, frozen=True)
class UserCacheItem:
    user_id: str
    name_hash: int
    status: UserStatus
    permission: Permission
    is_self_ignore: bool = False

    def with_name_hash(self, new_hash: int) -> Self:
        if self.name_hash == new_hash:
            return self
        return replace(self, name_hash=new_hash)

    def with_status(self, new_status: UserStatus) -> Self:
        if self.status == new_status:
            return self
        return replace(self, status=new_status)

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

    def set_all_shut(self, is_shut: bool) -> Self:
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
    expiry: datetime = datetime.min
    reason: str | None = None

    def with_expiry(self, new_expiry: datetime) -> Self:
        if self.expiry == new_expiry:
            return self
        return replace(self, expiry=new_expiry)

    def with_reason(self, new_reason: str | None) -> Self:
        if self.reason == new_reason:
            return self
        return replace(self, reason=new_reason)


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
