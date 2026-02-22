"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-12 18:53:21
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-20 01:24:27
Description: 缓存声明
"""

from datetime import datetime

from src.database.core.consts import GroupStatus, Permission
from src.lib.cache.field import (
    BlacklistCacheItem,
    GroupCacheItem,
    MemberCacheItem,
    UserCacheItem,
)
from src.lib.consts import GLOBAL_GROUP_SCOPE
from src.lib.types import UNSET, Unset, is_set, resolve_unset

from .base import BaseCache


class UserCache(BaseCache[UserCacheItem]):
    def upsert_user(
        self,
        user_id: str,
        user_name: str | Unset = UNSET,
        permission: Permission | Unset = UNSET,
    ) -> None:
        """更新或创建用户缓存。

        如果用户不存在，则使用默认值创建:

        - status: 用户状态，默认为 NORMAL
        - permission: 用户权限，默认为 NORMAL
        """
        user = self.get(user_id)
        name_hash = hash(user_name)
        if not user:
            user = UserCacheItem(
                user_id=user_id,
                name_hash=name_hash,
                permission=resolve_unset(permission, Permission.NORMAL),
            )
            self.set(user_id, user)
            return

        if is_set(user_name) and user.name_hash != hash(user_name):
            user = user.with_name_hash(name_hash)
        if is_set(permission):
            user = user.with_permission(permission)
        self.set(user_id, user)

    def is_available(self, user_id: str) -> bool:
        """检查用户是否可用（未被封禁、未开启自我忽略、或是超级用户）"""
        item = self.get(user_id)
        if not item:
            return False
        if item.permission.has(Permission.SUPERUSER):
            return True
        if not item.is_self_ignore:
            return True
        return False

    def needs_update_name(self, user_id: str, user_name: str) -> bool:
        user = self.get(user_id)
        return bool(user and user.name_hash != hash(user_name))


class GroupCache(BaseCache[GroupCacheItem]):
    def upsert_group(
        self,
        group_id: str,
        group_name: str | Unset = UNSET,
        status: GroupStatus | Unset = UNSET,
        is_all_shut: bool | Unset = UNSET,
    ) -> None:
        group = self.get(group_id)
        name_hash = hash(group_name)

        if not group:
            group = GroupCacheItem(
                group_id=group_id,
                name_hash=name_hash,
                status=resolve_unset(status, GroupStatus.UNAUTHORIZED),
                is_all_shut=resolve_unset(is_all_shut, False),
            )
            self.set(group_id, group)
            return

        if is_set(group_name) and group.name_hash != hash(group_name):
            group = group.with_name_hash(name_hash)
        if is_set(is_all_shut):
            group = group.set_all_shut(is_all_shut)
        if is_set(status):
            group = group.with_status(status)
        self.set(group_id, group)

    def set_plugin_state(self, group_id: str, plugin_name: str, enabled: bool) -> None:
        group = self.get(group_id)
        if not group:
            return
        if enabled:
            new_item = group.enable_plugin(plugin_name)
        else:
            new_item = group.disable_plugin(plugin_name)
        if new_item is not group:
            self.set(group_id, new_item)

    def set_group_name(self, group_id: str, group_name: str) -> None:
        group = self.get(group_id)
        if not group:
            return
        n_group = group.with_name_hash(hash(group_name))
        if n_group is not group:
            self.set(group_id, n_group)

    def set_group_status(self, group_id: str, status: GroupStatus) -> None:
        group = self.get(group_id)
        if not group:
            return
        n_group = group.with_status(status)
        if n_group is not group:
            self.set(group_id, n_group)

    def is_available(self, group_id: str) -> bool:
        group = self.get(group_id)
        return bool(group and group.status == GroupStatus.AUTHORIZED)

    def is_shut(self, group_id: str) -> bool:
        group = self.get(group_id)
        return bool(group and group.is_all_shut)

    def is_plugin_enabled(self, group_id: str, plugin_name: str) -> bool:
        group = self.get(group_id)
        if not group:
            return True
        return plugin_name not in group.disabled_plugins


class MemberCache(BaseCache[MemberCacheItem]):
    def _gen_key(self, user_id: str, group_id: str) -> str:
        return f"MEMBER:{group_id}:{user_id}"

    def upsert_member(
        self,
        user_id: str,
        group_id: str,
        permission: Permission | Unset = UNSET,
        group_card: str | Unset = UNSET,
    ) -> None:
        key = self._gen_key(user_id, group_id)
        member = self.get(key)
        new_hash = hash(group_card or "")

        if not member:
            self.set(
                key,
                MemberCacheItem(
                    card_hash=new_hash,
                    permission=resolve_unset(permission, Permission.NORMAL),
                ),
            )
            return
        if is_set(group_card) and member.card_hash != hash(group_card):
            member = member.with_card_hash(new_hash)
        if is_set(permission):
            member = member.with_permission(permission)
        self.set(key, member)

    def get_member(self, user_id: str, group_id: str) -> MemberCacheItem | None:
        return self.get(self._gen_key(user_id, group_id))

    def delete_member(self, user_id: str, group_id: str) -> None:
        self.delete(self._gen_key(user_id, group_id))


class BlacklistCache(BaseCache[BlacklistCacheItem]):
    def _gen_key(self, user_id: str, scope: str) -> str:
        return f"BAN:{scope}:{user_id}"

    def _check_and_clean(self, key: str) -> bool:
        blacklist = self.get(key)
        if not blacklist:
            return False
        if datetime.now() > blacklist.expiry:
            self.delete(key)
            return False

        return True

    def set_ban(
        self,
        user_id: str,
        group_id: str,
        expiry: datetime,
    ) -> None:
        key = self._gen_key(user_id, group_id)
        self.set(key, BlacklistCacheItem(expiry=expiry))

    def set_unban(self, user_id: str, group_id: str) -> None:
        key = self._gen_key(user_id, group_id)
        self.delete(key)

    def get_ban(self, user_id: str, group_id: str) -> BlacklistCacheItem | None:
        key = self._gen_key(user_id, group_id)
        return self.get(key)

    def is_banned(self, user_id: str, group_id: str) -> bool:
        """
        检查用户是否被封禁。
        优先级：全局封禁 -> 群内封禁
        """
        if self._check_and_clean(self._gen_key(user_id, GLOBAL_GROUP_SCOPE)):
            return True
        if self._check_and_clean(self._gen_key(user_id, group_id)):
            return True
        return False
