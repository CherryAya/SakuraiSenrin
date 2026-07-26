"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 19:46:09
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-02 19:30:37
Description: group 相关实现
"""

from dataclasses import dataclass

from sqlalchemy import select

from src.database.consts import WritePolicy
from src.database.core.consts import GroupStatus
from src.database.core.ops import GroupOps
from src.database.core.tables import Group
from src.database.instances import core_db, log_db, snapshot_db
from src.database.log.consts import AuditAction, AuditCategory, AuditContext
from src.database.log.ops import AuditLogOps
from src.database.snapshot.ops import GroupSnapshotOps
from src.lib.cache.field import GroupCacheItem
from src.lib.cache.impl import GroupCache
from src.lib.types import UNSET, Unset, is_set, resolve_unset
from src.lib.utils.common import get_current_time
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
    pre_ban_status: GroupStatus | None | Unset = UNSET
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

    def resolve_pre_ban(
        self,
        default: GroupStatus | None = None,
    ) -> GroupStatus | None:
        return resolve_unset(self.pre_ban_status, default)


@dataclass(slots=True, frozen=True)
class GroupRestoreResult:
    restored_status: GroupStatus
    used_fallback: bool = False


class GroupRepository:
    def __init__(self, cache: GroupCache) -> None:
        self.cache = cache

    async def _save_buffered(self, ctx: GroupChangeContext) -> None:
        event_time = get_current_time()
        if ctx.is_new:
            await group_create_writer.add(
                {
                    "group_id": ctx.group_id,
                    "group_name": ctx.resolve_name(),
                    "status": ctx.resolve_status(),
                    "pre_ban_status": ctx.resolve_pre_ban(),
                    "created_at": event_time,
                    "updated_at": event_time,
                },
            )
            return

        if is_set(ctx.group_name):
            await group_update_name_writer.add(
                {
                    "created_at": event_time,
                    "group_id": ctx.group_id,
                    "group_name": ctx.group_name,
                    "updated_at": event_time,
                },
            )

        if is_set(ctx.status) or is_set(ctx.pre_ban_status):
            await group_update_status_writer.add(
                {
                    "group_id": ctx.group_id,
                    "status": ctx.resolve_status(),
                    "pre_ban_status": ctx.resolve_pre_ban(),
                    "updated_at": event_time,
                },
            )

    async def _save_immediate(self, ctx: GroupChangeContext) -> None:
        event_time = get_current_time()
        async with (
            core_db.session() as core_session,
            log_db.session() as log_session,
            snapshot_db.session() as snapshot_session,
        ):
            group_ops = GroupOps(core_session)
            audit_log_ops = AuditLogOps(log_session)
            group_snapshot_ops = GroupSnapshotOps(snapshot_session)

            if ctx.is_new:
                await group_ops.add_group(
                    group_id=ctx.group_id,
                    group_name=ctx.resolve_name(),
                    status=ctx.resolve_status(),
                )
                return

            if is_set(ctx.group_name):
                await group_ops.update_name(ctx.group_id, ctx.group_name)
                await group_snapshot_ops.create_group_snapshot(
                    group_id=ctx.group_id,
                    content=ctx.group_name,
                    created_at=event_time,
                )

            if is_set(ctx.status) or is_set(ctx.pre_ban_status):
                await group_ops.update_status_with_pre_ban(
                    ctx.group_id,
                    ctx.resolve_status(),
                    ctx.resolve_pre_ban(),
                )
                await audit_log_ops.create_audit_log(
                    target_id=ctx.group_id,
                    context_type=AuditContext.GROUP,
                    category=AuditCategory.PERMISSION,
                    action=AuditAction.CHANGE,
                )

    async def _hydrate_cache_item(self, group_id: str) -> GroupCacheItem | None:
        async with core_db.session() as session:
            db_group = await GroupOps(session).get_by_group_id(group_id)
        if db_group is None:
            return None
        self.cache.upsert_group(
            group_id=str(db_group.group_id),
            group_name=db_group.group_name,
            status=db_group.status,
            pre_ban_status=getattr(db_group, "pre_ban_status", None),
        )
        return self.cache.get(group_id)

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
        if old_item is None:
            old_item = await self._hydrate_cache_item(group_id)
        ctx.is_new = old_item is None

        next_pre_ban_status: GroupStatus | None | Unset = UNSET
        if old_item is not None and is_set(status):
            if status.is_banned:
                if not old_item.status.is_banned:
                    next_pre_ban_status = old_item.status
                else:
                    next_pre_ban_status = old_item.pre_ban_status
            elif old_item.status.is_banned:
                next_pre_ban_status = None
            else:
                next_pre_ban_status = old_item.pre_ban_status
        if is_set(next_pre_ban_status):
            ctx.pre_ban_status = next_pre_ban_status

        self.cache.upsert_group(
            group_id,
            group_name,
            status,
            is_all_shut,
            ctx.pre_ban_status,
        )
        if not ctx.is_new and old_item:
            if is_set(group_name) and old_item.name_hash == hash(group_name):
                ctx.group_name = UNSET
            if is_set(status) and old_item.status == status:
                ctx.status = UNSET
            if is_set(is_all_shut) and old_item.is_all_shut == is_all_shut:
                ctx.is_all_shut = UNSET
            if (
                is_set(ctx.pre_ban_status)
                and old_item.pre_ban_status == ctx.resolve_pre_ban()
            ):
                ctx.pre_ban_status = UNSET

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
                    display_name=g.group_name,
                    pre_ban_status=g.pre_ban_status,
                )
                for g in db_groups
            },
        )

    async def get_group(self, group_id: str) -> GroupCacheItem | None:
        if item := self.cache.get(group_id):
            return item

        return await self._hydrate_cache_item(group_id)

    async def get_name_by_gid(self, group_id: str) -> str | None:
        if item := self.cache.get(group_id):
            if item.display_name:
                return item.display_name
        async with core_db.session() as session:
            db_group = await GroupOps(session).get_by_group_id(group_id)
        if db_group is None:
            return None
        self.cache.upsert_group(
            group_id=str(db_group.group_id),
            group_name=db_group.group_name,
            status=db_group.status,
            pre_ban_status=getattr(db_group, "pre_ban_status", None),
        )
        return db_group.group_name or None

    async def get_names_by_gids(self, group_ids: list[str]) -> dict[str, str]:
        if not group_ids:
            return {}
        unique_group_ids = list(dict.fromkeys(group_ids))
        resolved: dict[str, str] = {}
        missing_group_ids: list[str] = []
        for group_id in unique_group_ids:
            item = self.cache.get(group_id)
            if item is not None and item.display_name:
                resolved[group_id] = item.display_name
                continue
            missing_group_ids.append(group_id)
        if not missing_group_ids:
            return resolved
        async with core_db.session() as session:
            stmt = select(Group).where(Group.group_id.in_(missing_group_ids))
            result = await session.execute(stmt)
            db_groups = result.scalars().all()
        for db_group in db_groups:
            resolved_group_id = str(db_group.group_id)
            self.cache.upsert_group(
                group_id=resolved_group_id,
                group_name=db_group.group_name,
                status=db_group.status,
                pre_ban_status=getattr(db_group, "pre_ban_status", None),
            )
            resolved[resolved_group_id] = db_group.group_name
        return resolved

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

    async def restore_pre_ban_status(
        self,
        group_id: str,
    ) -> GroupRestoreResult | None:
        group = await self.get_group(group_id)
        if group is None:
            return None
        restored_status = group.pre_ban_status or GroupStatus.UNAUTHORIZED
        used_fallback = group.pre_ban_status is None
        await self.save_group(
            group_id=group_id,
            status=restored_status,
            policy=WritePolicy.IMMEDIATE,
        )
        return GroupRestoreResult(
            restored_status=restored_status,
            used_fallback=used_fallback,
        )

    def update_all_shut(self, group_id: str, is_shut: bool) -> None:
        return self.cache.upsert_group(
            group_id=group_id,
            is_all_shut=is_shut,
        )

    async def get_working_group_ids(self) -> list[str]:
        async with core_db.session() as session:
            return await GroupOps(session).get_working_group_ids()
