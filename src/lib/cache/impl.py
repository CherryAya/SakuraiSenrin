from dataclasses import dataclass, field
from datetime import datetime

from src.database.core.consts import GroupStatus, Permission, UserStatus
from src.lib.consts import GLOBAL_SCOPE

from .base import BaseCache


@dataclass(slots=True)
class UserCacheItem:
    user_id: str
    name_hash: int
    status: UserStatus
    permission: Permission
    is_self_ignore: bool = False


@dataclass(slots=True)
class GroupCacheItem:
    group_id: str
    name_hash: int
    status: GroupStatus
    is_all_shut: bool
    disabled_plugins: set[str] = field(default_factory=set)


@dataclass(slots=True)
class BlacklistCacheItem:
    expiry: datetime = datetime.min
    reason: str | None = None


@dataclass(slots=True)
class MemberCacheItem:
    card_hash: int
    permission: Permission


class UserCache(BaseCache[UserCacheItem]):
    def set_user(
        self,
        user_id: str,
        user_name: str | None,
        status: UserStatus,
        permission: Permission,
    ) -> None:
        self.set(
            user_id,
            UserCacheItem(
                user_id=user_id,
                name_hash=hash(user_name),
                status=status,
                permission=permission,
            ),
        )

    def set_smart(
        self,
        user_id: str,
        user_name: str | None,
        status: UserStatus,
        permission: Permission,
    ) -> UserCacheItem:
        old_item = self.get(user_id)
        final_status = status
        name_hash = hash(user_name)
        if final_status is None:
            final_status = old_item.status if old_item else UserStatus.NORMAL
        if (
            old_item
            and old_item.name_hash != name_hash
            and old_item.status != final_status
            and old_item.permission != permission
        ):
            return old_item

        self.set_user(
            user_id=user_id,
            user_name=user_name,
            status=final_status,
            permission=permission,
        )
        return self[user_id]

    def is_available(self, user_id: str) -> bool:
        """检查顺序：命中缓存 -> 权限检查 -> 状态检查 -> 自闭检查"""
        item = self.get(user_id)
        if not item:
            return False
        if item.permission.has(Permission.SUPERUSER):
            return True
        if item.status != UserStatus.NORMAL:
            return False
        if item.is_self_ignore:
            return False
        return True

    def needs_update_name(self, user_id: str, user_name: str) -> bool:
        item = self.get(user_id)
        if not item:
            return False
        return item.name_hash != hash(user_name)


class GroupCache(BaseCache[GroupCacheItem]):
    def set_group(
        self,
        group_id: str,
        group_name: str | None,
        status: GroupStatus,
        is_all_shut: bool,
    ) -> None:
        self.set(
            group_id,
            GroupCacheItem(
                group_id=group_id,
                name_hash=hash(group_name),
                status=status,
                is_all_shut=is_all_shut,
            ),
        )

    def is_available(self, group_id: str) -> bool:
        item = self.get(group_id)
        return item is not None and item.status == GroupStatus.NORMAL

    def is_shut(self, group_id: str) -> bool:
        item = self.get(group_id)
        return item.is_all_shut if item else False

    def update_shut_state(self, group_id: str, is_shut: bool) -> None:
        item = self.get(group_id)
        if item:
            item.is_all_shut = is_shut

    def is_plugin_enabled(self, group_id: str, plugin_name: str) -> bool:
        item = self.get(group_id)
        if not item:
            return True
        return plugin_name not in item.disabled_plugins

    def needs_update_name(self, group_id: str, group_name: str) -> bool:
        item = self.get(group_id)
        if not item:
            return False
        return item.name_hash != hash(group_name)

    def set_smart(
        self,
        group_id: str,
        group_name: str | None,
        is_all_shut: bool,
        status: GroupStatus | None = None,
    ) -> GroupCacheItem:
        old_item = self.get(group_id)
        final_status = status
        name_hash = hash(group_name)
        if final_status is None:
            final_status = old_item.status if old_item else GroupStatus.UNAUTHORIZED
        if (
            old_item
            and old_item.name_hash != name_hash
            and old_item.is_all_shut != is_all_shut
            and old_item.status != final_status
        ):
            return old_item

        self.set_group(
            group_id=group_id,
            group_name=group_name,
            status=final_status,
            is_all_shut=is_all_shut,
        )
        return self[group_id]


class MemberCache(BaseCache[MemberCacheItem]):
    def _gen_key(self, user_id: str, group_id: str) -> str:
        return f"MEMBER:{group_id}:{user_id}"

    def set_member(
        self,
        user_id: str,
        group_id: str,
        group_card: str | None,
        permission: Permission,
    ) -> None:
        key = self._gen_key(user_id, group_id)
        self.set(
            key,
            MemberCacheItem(
                card_hash=hash(group_card),
                permission=permission,
            ),
        )

    def get_member(self, user_id: str, group_id: str) -> MemberCacheItem | None:
        key = self._gen_key(user_id, group_id)
        return self.get(key)

    def delete_member(self, user_id: str, group_id: str) -> None:
        key = self._gen_key(user_id, group_id)
        self.delete(key)

    def needs_sync(self, user_id: str, group_id: str, group_card: str) -> bool:
        key = self._gen_key(user_id, group_id)
        item = self.get(key)
        if not item:
            return True
        return item.card_hash != hash(group_card)


class BlacklistCache(BaseCache[BlacklistCacheItem]):
    def _gen_key(self, user_id: str, group_id: str) -> str:
        return f"BAN:{group_id}:{user_id}"

    def set_ban(
        self,
        user_id: str,
        group_id: str,
        expiry: datetime,
        reason: str | None = None,
    ) -> None:
        key = self._gen_key(user_id, group_id)
        self.set(key, BlacklistCacheItem(expiry=expiry, reason=reason))

    def is_banned(self, user_id: str, group_id: str) -> bool:
        if self._check_key(self._gen_key(user_id, GLOBAL_SCOPE)):
            return True
        if self._check_key(self._gen_key(user_id, group_id)):
            return True
        return False

    def _check_key(self, key: str) -> bool:
        item = self.get(key)
        if not item:
            return False
        if item.expiry > datetime.now():
            self.delete(key)
            return False
        return True
