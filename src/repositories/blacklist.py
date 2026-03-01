"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 21:03:25
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 14:41:22
Description: blacklist 相关实现
"""

from __future__ import annotations

import math

import arrow

from src.database.core.ops import BlacklistOps
from src.database.instances import core_db, log_db
from src.database.log.consts import AuditAction, AuditCategory, AuditContext
from src.database.log.ops import AuditLogOps
from src.lib.cache.field import BlacklistCacheItem
from src.lib.cache.impl import BlacklistCache
from src.lib.consts import GLOBAL_GROUP_SCOPE
from src.lib.types import UNSET, Unset
from src.lib.utils.common import get_current_time

_AUDIT_CTX_TYPE_DICT = {
    GLOBAL_GROUP_SCOPE: AuditContext.GLOBAL,
}


class BlacklistRepository:
    def __init__(
        self,
        cache: BlacklistCache,
    ) -> None:
        self.cache = cache

    async def warm_up(self) -> None:
        async with core_db.session() as session:
            data = await BlacklistOps(session).get_all()
        self.cache.set_batch(
            {
                self.cache._gen_key(d.target_user_id, d.group_id): BlacklistCacheItem(
                    expiry=d.ban_expiry if d.ban_expiry != -1 else math.inf,
                )
                for d in data
            },
        )

    async def get_blacklist(
        self,
        user_id: str,
        group_id: str,
    ) -> BlacklistCacheItem | None:
        if item := self.cache.get_ban(user_id, group_id):
            return item

        async with core_db.session() as session:
            db_item = await BlacklistOps(session).get_by_uid_and_gid(
                target_user_id=user_id,
                group_id=group_id,
            )
            if not db_item:
                return None

        self.cache.set_ban(user_id, group_id, db_item.ban_expiry)
        return self.cache.get_ban(user_id, group_id)

    async def is_banned(self, user_id: str, group_id: str) -> bool:
        # await self.get_blacklist(user_id, group_id)   #TODO: 🤔
        return self.cache.is_banned(user_id, group_id)

    async def add_ban(
        self,
        target_user_id: str,
        group_id: str,
        operator_id: str,
        duration: float = math.inf,
        reason: str | Unset = UNSET,
    ) -> None:
        expiry = (
            -1
            if duration == math.inf
            else arrow.get(get_current_time()).shift(seconds=duration).int_timestamp
        )
        self.cache.set_ban(target_user_id, group_id, expiry)

        async with core_db.session() as core_session:
            await BlacklistOps(core_session).add_ban(
                target_user_id=target_user_id,
                group_id=group_id,
                operator_id=operator_id,
                ban_expiry=expiry,
                reason=reason,
            )
        async with log_db.session() as log_session:
            await AuditLogOps(log_session).create_audit_log(
                target_id=target_user_id,
                context_type=_AUDIT_CTX_TYPE_DICT.get(group_id, AuditContext.GROUP),
                category=AuditCategory.ACCESS,
                action=AuditAction.BAN,
            )

    async def set_unban(
        self,
        target_user_id: str,
        group_id: str,
        operator_id: str,
    ) -> None:
        blacklist = await self.get_blacklist(target_user_id, group_id)
        if not blacklist:
            return
        self.cache.set_unban(target_user_id, group_id)

        async with core_db.session() as core_session:
            await BlacklistOps(core_session).unban(target_user_id, group_id)

        async with log_db.session() as log_session:
            await AuditLogOps(log_session).create_audit_log(
                target_id=target_user_id,
                context_type=_AUDIT_CTX_TYPE_DICT.get(group_id, AuditContext.GROUP),
                category=AuditCategory.ACCESS,
                action=AuditAction.UNBAN,
                operator_id=operator_id,
            )
