"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 18:59:47
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 23:59:50
Description: user 相关实现
"""

from dataclasses import dataclass

from src.database.consts import WritePolicy
from src.database.core.consts import Permission
from src.database.core.ops import UserOps
from src.database.instances import core_db, log_db, snapshot_db
from src.database.log.consts import AuditAction, AuditCategory, AuditContext
from src.database.log.ops import AuditLogOps
from src.database.snapshot.ops import UserSnapshotOps
from src.lib.cache.field import UserCacheItem
from src.lib.cache.impl import UserCache
from src.lib.types import UNSET, Unset, is_set, resolve_unset
from src.services.writers import (
    user_create_writer,
    user_update_name_writer,
    user_update_perm_writer,
)


@dataclass
class UserChangeContext:
    """
    封装用户变更请求的上下文，包含原始数据和衍生状态。
    提供 resolve_xxx 方法统一处理 Unset -> Default 的逻辑。
    """

    user_id: str
    user_name: str | Unset = UNSET
    permission: Permission | Unset = UNSET
    is_new: bool = False

    def resolve_name(self, default: str = "") -> str:
        return resolve_unset(self.user_name, default)

    def resolve_perm(
        self,
        default: Permission = Permission.NORMAL,
    ) -> Permission:
        return resolve_unset(self.permission, default)


class UserRepository:
    def __init__(self, cache: UserCache) -> None:
        self.cache = cache

    async def _save_buffered(self, ctx: UserChangeContext) -> None:
        if ctx.is_new:
            await user_create_writer.add(
                {
                    "user_id": ctx.user_id,
                    "user_name": ctx.resolve_name(),
                    "permission": ctx.resolve_perm(),
                },
            )
            return
        if is_set(ctx.user_name):
            await user_update_name_writer.add(
                {
                    "user_id": ctx.user_id,
                    "user_name": ctx.user_name,
                },
            )
        if is_set(ctx.permission):
            await user_update_perm_writer.add(
                {
                    "user_id": ctx.user_id,
                    "permission": ctx.permission,
                },
            )

    async def _save_immediate(self, ctx: UserChangeContext) -> None:
        async with (
            core_db.session() as core_session,
            log_db.session() as log_session,
            snapshot_db.session() as snapshot_session,
        ):
            user_ops = UserOps(core_session)
            audit_log_ops = AuditLogOps(log_session)
            user_snapshot_ops = UserSnapshotOps(snapshot_session)
            if ctx.is_new:
                await user_ops.upsert_user(
                    user_id=ctx.user_id,
                    user_name=ctx.resolve_name(),
                    permission=ctx.resolve_perm(),
                )
                return
            if is_set(ctx.user_name):
                await user_ops.upsert_name(ctx.user_id, ctx.user_name)
                await user_snapshot_ops.create_user_snapshot(
                    user_id=ctx.user_id,
                    content=ctx.user_name,
                )
            if is_set(ctx.permission):
                await user_ops.update_permission(ctx.user_id, ctx.permission)
                await audit_log_ops.create_audit_log(
                    target_id=ctx.user_id,
                    context_type=AuditContext.USER,
                    category=AuditCategory.PERMISSION,
                    action=AuditAction.GRANT,
                )

    async def save_user(
        self,
        user_id: str,
        user_name: str | Unset = UNSET,
        permission: Permission | Unset = UNSET,
        policy: WritePolicy = WritePolicy.BUFFERED,
    ) -> None:
        from src.config import config

        if user_id in config.SUPERUSERS:
            permission = Permission.SUPERUSER
        ctx = UserChangeContext(user_id, user_name, permission)
        old_item = self.cache.get(user_id)
        ctx.is_new = old_item is None
        self.cache.upsert_user(user_id, user_name, permission)

        if not ctx.is_new and old_item:
            if is_set(user_name) and old_item.name_hash == hash(user_name):
                ctx.user_name = UNSET
            if is_set(permission) and old_item.permission == permission:
                ctx.permission = UNSET

        if policy == WritePolicy.BUFFERED:
            await self._save_buffered(ctx)
        elif policy == WritePolicy.IMMEDIATE:
            await self._save_immediate(ctx)

    async def warm_up(self) -> None:
        async with core_db.session() as session:
            users = await UserOps(session).get_all()
        self.cache.set_batch(
            {
                u.user_id: UserCacheItem(
                    user_id=str(u.user_id),
                    name_hash=hash(u.user_name),
                    permission=u.permission,
                )
                for u in users
            },
        )

    async def get_user(self, user_id: str) -> UserCacheItem | None:
        if item := self.cache.get(user_id):
            return item

        async with core_db.session() as session:
            db_user = await UserOps(session).get_by_user_id(user_id)
            if not db_user:
                return None
            await self.save_user(
                user_id=user_id,
                user_name=db_user.user_name,
                policy=WritePolicy.IMMEDIATE,
            )

        return self.cache.get(user_id)
