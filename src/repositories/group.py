"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 19:46:09
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-21 02:02:21
Description: group 相关实现
"""

from dataclasses import dataclass

from src.database.consts import WritePolicy
from src.database.core.consts import GroupStatus
from src.database.core.ops import GroupOps
from src.database.instances import core_db, log_db, snapshot_db
from src.database.log.consts import AuditAction, AuditCategory, AuditContext
from src.database.log.ops import AuditLogOps
from src.database.snapshot.ops import GroupSnapshotOps
from src.lib.cache.field import GroupCacheItem
from src.lib.cache.impl import GroupCache
from src.lib.types import UNSET, Unset, is_set, resolve_unset
from src.services.writers import (
    group_create_writer,
    group_update_name_writer,
    group_update_status_writer,
)


@dataclass
class GroupChangeContext:
    """
    封装群组变更请求的上下文。
    注意：is_all_shut 通常是运行时状态，但如果有持久化需求也可以包含在内。
    """

    group_id: str
    group_name: str | Unset = UNSET
    status: GroupStatus | Unset = UNSET
    is_all_shut: bool | Unset = UNSET
    is_new: bool = False

    def resolve_name(self, default: str = "") -> str:
        return resolve_unset(self.group_name, default)

    def resolve_status(
        self,
        default: GroupStatus = GroupStatus.UNAUTHORIZED,
    ) -> GroupStatus:
        return resolve_unset(self.status, default)

    def resolve_shut(self, default: bool = False) -> bool:
        return resolve_unset(self.is_all_shut, default)


class GroupRepository:
    def __init__(self, cache: GroupCache) -> None:
        self.cache = cache

    async def _save_buffered(self, ctx: GroupChangeContext) -> None:
        if ctx.is_new:
            await group_create_writer.add(
                {
                    "group_id": ctx.group_id,
                    "group_name": ctx.resolve_name(),
                    "status": ctx.resolve_status(),
                },
            )
            return

        if is_set(ctx.group_name):
            await group_update_name_writer.add(
                {
                    "group_id": ctx.group_id,
                    "group_name": ctx.group_name,
                },
            )

        if is_set(ctx.status):
            await group_update_status_writer.add(
                {
                    "group_id": ctx.group_id,
                    "status": ctx.status,
                },
            )

    async def _save_immediate(self, ctx: GroupChangeContext) -> None:
        async with (
            core_db.session() as core_session,
            log_db.session() as log_session,
            snapshot_db.session() as snapshot_session,
        ):
            group_ops = GroupOps(core_session)
            audit_log_ops = AuditLogOps(log_session)
            group_snapshot_ops = GroupSnapshotOps(snapshot_session)

            if ctx.is_new:
                await group_ops.upsert_group(
                    group_id=ctx.group_id,
                    group_name=ctx.resolve_name(),
                    status=ctx.resolve_status(),
                )
                return

            if is_set(ctx.group_name):
                await group_ops.upsert_name(ctx.group_id, ctx.group_name)
                await group_snapshot_ops.create_group_snapshot(
                    group_id=ctx.group_id,
                    content=ctx.group_name,
                )

            if is_set(ctx.status):
                await group_ops.update_status(ctx.group_id, ctx.status)
                await audit_log_ops.create_audit_log(
                    target_id=ctx.group_id,
                    context_type=AuditContext.GROUP,
                    category=AuditCategory.PERMISSION,
                    action=AuditAction.CHANGE,
                )

    async def save_group(
        self,
        group_id: str,
        group_name: str | Unset = UNSET,
        status: GroupStatus | Unset = UNSET,
        is_all_shut: bool | Unset = UNSET,
        policy: WritePolicy = WritePolicy.BUFFERED,
    ) -> None:
        """
        Write-Behind:
        1. 更新缓存 (Source of Truth)
        2. 计算 Context (Diff)
        3. 策略分流 (Buffered / Immediate)
        """
        ctx = GroupChangeContext(group_id, group_name, status, is_all_shut)
        old_item = self.cache.get(group_id)
        ctx.is_new = old_item is None

        self.cache.upsert_group(group_id, group_name, status, is_all_shut)
        if not ctx.is_new and old_item:
            if is_set(group_name) and old_item.name_hash == hash(group_name):
                ctx.group_name = UNSET
            if is_set(status) and old_item.status == status:
                ctx.status = UNSET
            if is_set(is_all_shut) and old_item.is_all_shut == is_all_shut:
                ctx.is_all_shut = UNSET

        if policy == WritePolicy.BUFFERED:
            await self._save_buffered(ctx)
        elif policy == WritePolicy.IMMEDIATE:
            await self._save_immediate(ctx)

    async def warm_up(self) -> None:
        async with core_db.session() as session:
            db_groups = await GroupOps(session).get_all()

        self.cache.set_batch(
            {
                g.group_id: GroupCacheItem(
                    group_id=str(g.group_id),
                    name_hash=hash(g.group_name),
                    status=g.status,
                    is_all_shut=False,
                )
                for g in db_groups
            },
        )

    async def get_group(self, group_id: str) -> GroupCacheItem | None:
        if item := self.cache.get(group_id):
            return item

        async with core_db.session() as session:
            db_group = await GroupOps(session).get_by_group_id(group_id)
            if not db_group:
                return None

    async def update_status(self, group_id: str, status: GroupStatus) -> None:
        return await self.save_group(
            group_id=group_id,
            status=status,
            policy=WritePolicy.IMMEDIATE,
        )

    async def update_name(self, group_id: str, group_name: str) -> None:
        return await self.save_group(
            group_id=group_id,
            group_name=group_name,
            policy=WritePolicy.IMMEDIATE,
        )
