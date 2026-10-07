"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 19:46:12
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-02 19:48:53
Description: member 相关实现
"""

from collections.abc import Sequence
from dataclasses import dataclass

from src.database.consts import WritePolicy
from src.database.core.consts import Permission
from src.database.core.ops import MemberOps
from src.database.core.tables import Member
from src.database.instances import core_db
from src.database.log.consts import AuditAction, AuditCategory, AuditContext
from src.database.log.ops import AuditLogOps
from src.database.snapshot.ops import MemberSnapshotOps
from src.lib.cache.field import MemberCacheItem
from src.lib.cache.impl import MemberCache
from src.lib.db.atomic import system_atomic_session
from src.lib.types import UNSET, Unset, is_set, resolve_unset
from src.lib.utils.common import get_current_time
from src.services.writers import (
    member_create_writer,
    member_update_card_writer,
    member_update_perm_writer,
)


@dataclass
class MemberChangeContext:
    """
    封装群成员变更上下文。
    """

    user_id: str
    group_id: str
    group_card: str | Unset = UNSET
    permission: Permission | Unset = UNSET
    persisted_permission: Permission = Permission.NORMAL
    is_new: bool = False
    # 操作者：权限变更需可归因，审计表 operator_id 依赖它
    operator_id: str = ""

    def resolve_card(self, default: str = "") -> str:
        return resolve_unset(self.group_card, default)

    def resolve_perm(
        self,
        default: Permission = Permission.NORMAL,
    ) -> Permission:
        return resolve_unset(self.permission, default)


class MemberRepository:
    def __init__(self, cache: MemberCache) -> None:
        self.cache = cache

    async def _save_buffered(self, ctx: MemberChangeContext) -> None:
        """
        分流到三个不同的 Writer：
        1. create: 新成员
        2. update_card: 改名片
        3. update_perm: 改权限
        """
        event_time = get_current_time()
        if ctx.is_new:
            await member_create_writer.add(
                {
                    "group_id": ctx.group_id,
                    "user_id": ctx.user_id,
                    "group_card": ctx.resolve_card(),
                    "permission": ctx.resolve_perm(),
                    "created_at": event_time,
                    "updated_at": event_time,
                }
            )
            return

        if is_set(ctx.group_card):
            await member_update_card_writer.add(
                {
                    "created_at": event_time,
                    "group_id": ctx.group_id,
                    "user_id": ctx.user_id,
                    "group_card": ctx.resolve_card(),
                    "permission": ctx.persisted_permission,
                    "updated_at": event_time,
                }
            )

        if is_set(ctx.permission):
            await member_update_perm_writer.add(
                {
                    "group_id": ctx.group_id,
                    "user_id": ctx.user_id,
                    "permission": ctx.resolve_perm(),
                    "updated_at": event_time,
                    "operator_id": ctx.operator_id,
                }
            )

    async def _save_immediate(self, ctx: MemberChangeContext) -> None:
        event_time = get_current_time()
        # 单一事务：core + 当月 log 分片 + 当月 snapshot 分片。
        async with system_atomic_session(event_time) as session:
            member_ops = MemberOps(session)
            audit_log_ops = AuditLogOps(session)
            member_snapshot_ops = MemberSnapshotOps(session)

            if ctx.is_new:
                await member_ops.add_member(
                    group_id=ctx.group_id,
                    user_id=ctx.user_id,
                    group_card=ctx.resolve_card(),
                    permission=ctx.resolve_perm(),
                )
                return

            if is_set(ctx.group_card):
                await member_ops.update_card(
                    ctx.user_id,
                    ctx.group_id,
                    ctx.group_card,
                )
                await member_snapshot_ops.create_member_snapshot(
                    user_id=ctx.user_id,
                    group_id=ctx.group_id,
                    content=ctx.group_card,
                    created_at=event_time,
                )
            if is_set(ctx.permission):
                await member_ops.update_permission(
                    ctx.user_id,
                    ctx.group_id,
                    ctx.permission,
                )
                await audit_log_ops.create_audit_log(
                    target_id=ctx.user_id,
                    context_type=AuditContext.GROUP,
                    context_id=ctx.group_id,
                    category=AuditCategory.PERMISSION,
                    action=AuditAction.CHANGE,
                    operator_id=ctx.operator_id,
                )

    async def save_member(
        self,
        user_id: str,
        group_id: str,
        group_card: str | Unset = UNSET,
        permission: Permission | Unset = UNSET,
        policy: WritePolicy = WritePolicy.BUFFERED,
        operator_id: str = "",
    ) -> None:
        ctx = MemberChangeContext(user_id, group_id, group_card, permission)
        ctx.operator_id = operator_id
        old_item = self.cache.get_member(user_id, group_id)
        ctx.is_new = old_item is None
        if old_item is not None:
            ctx.persisted_permission = old_item.permission
        elif is_set(permission):
            ctx.persisted_permission = permission
        self.cache.upsert_member(user_id, group_id, permission, group_card)

        if not ctx.is_new and old_item:
            if is_set(group_card) and old_item.card_hash == hash(group_card):
                ctx.group_card = UNSET

            if is_set(permission) and old_item.permission == permission:
                ctx.permission = UNSET

        if policy == WritePolicy.BUFFERED:
            await self._save_buffered(ctx)
        elif policy == WritePolicy.IMMEDIATE:
            await self._save_immediate(ctx)

    async def warm_up(self) -> None:
        async with core_db.session(commit=False) as session:
            members = await MemberOps(session).get_all()

        self.cache.set_batch(
            {
                self.cache._gen_key(m.user_id, m.group_id): MemberCacheItem(
                    card_hash=hash(m.group_card),
                    permission=m.permission,
                    group_card=m.group_card,
                )
                for m in members
            },
        )

    async def get_member(
        self,
        user_id: str,
        group_id: str,
    ) -> MemberCacheItem | None:
        if item := self.cache.get_member(user_id, group_id):
            return item

        async with core_db.session(commit=False) as session:
            db_member = await MemberOps(session).get_by_uid_gid(user_id, group_id)
            if not db_member:
                return None

            self.cache.upsert_member(
                user_id=str(db_member.user_id),
                group_id=str(db_member.group_id),
                group_card=db_member.group_card,
                permission=db_member.permission,
            )
            return self.cache.get_member(user_id, group_id)

    async def get_card_by_uid_gid(self, user_id: str, group_id: str) -> str | None:
        if item := self.cache.get_member(user_id, group_id):
            if item.group_card:
                return item.group_card
        async with core_db.session(commit=False) as session:
            db_member = await MemberOps(session).get_by_uid_gid(user_id, group_id)
        if db_member is None:
            return None
        self.cache.upsert_member(
            user_id=str(db_member.user_id),
            group_id=str(db_member.group_id),
            group_card=db_member.group_card,
            permission=db_member.permission,
        )
        return db_member.group_card or None

    async def get_admin_member_by_uid(self, user_id: str) -> Sequence[Member]:
        async with core_db.session(commit=False) as session:
            return await MemberOps(session).get_admin_by_uid(user_id)

    async def get_distinct_user_count(self, group_id: str) -> int:
        async with core_db.session(commit=False) as session:
            return await MemberOps(session).get_distinct_user_count(group_id)

    async def get_intersection_user_count(self, group_a: str, group_b: str) -> int:
        async with core_db.session(commit=False) as session:
            return await MemberOps(session).get_intersection_user_count(
                group_a,
                group_b,
            )
