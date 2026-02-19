"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-13 19:46:12
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 23:59:44
Description: member 相关实现
"""

from dataclasses import dataclass

from src.database.consts import WritePolicy
from src.database.core.consts import Permission
from src.database.core.ops import MemberOps
from src.database.core.types import (
    MemberPayload,
    MemberUpdateCardPayload,
    MemberUpdatePermPayload,
)
from src.database.instances import core_db, log_db, snapshot_db
from src.database.log.consts import AuditAction, AuditCategory, AuditContext
from src.database.log.ops import AuditLogOps
from src.database.snapshot.ops import MemberSnapshotOps
from src.lib.cache.field import MemberCacheItem
from src.lib.cache.impl import MemberCache
from src.lib.types import UNSET, Unset, is_set, resolve_unset
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
    is_new: bool = False

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
        if ctx.is_new:
            payload: MemberPayload = {
                "group_id": ctx.group_id,
                "user_id": ctx.user_id,
                "group_card": ctx.resolve_card(),
                "permission": ctx.resolve_perm(),
            }
            await member_create_writer.add(payload)
            return

        if is_set(ctx.group_card):
            card_payload: MemberUpdateCardPayload = {
                "group_id": ctx.group_id,
                "user_id": ctx.user_id,
                "group_card": ctx.group_card,
            }
            await member_update_card_writer.add(card_payload)

        if is_set(ctx.permission):
            perm_payload: MemberUpdatePermPayload = {
                "group_id": ctx.group_id,
                "user_id": ctx.user_id,
                "permission": ctx.permission,
            }
            await member_update_perm_writer.add(perm_payload)

    async def _save_immediate(self, ctx: MemberChangeContext) -> None:
        async with (
            core_db.session() as core_session,
            log_db.session() as log_session,
            snapshot_db.session() as snapshot_session,
        ):
            member_ops = MemberOps(core_session)
            audit_log_ops = AuditLogOps(log_session)
            member_snapshot_ops = MemberSnapshotOps(snapshot_session)

            if ctx.is_new:
                await member_ops.upsert_member(
                    group_id=ctx.group_id,
                    user_id=ctx.user_id,
                    group_card=ctx.resolve_card(),
                    permission=ctx.resolve_perm(),
                )
                return

            if is_set(ctx.group_card):
                await member_ops.upsert_card(
                    ctx.user_id,
                    ctx.group_id,
                    ctx.group_card,
                )
                await member_snapshot_ops.create_member_snapshot(
                    user_id=ctx.user_id,
                    group_id=ctx.group_id,
                    content=ctx.group_card,
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
                    category=AuditCategory.PERMISSION,
                    action=AuditAction.CHANGE,
                )

    async def save_member(
        self,
        user_id: str,
        group_id: str,
        group_card: str | Unset = UNSET,
        permission: Permission | Unset = UNSET,
        policy: WritePolicy = WritePolicy.BUFFERED,
    ) -> None:
        ctx = MemberChangeContext(user_id, group_id, group_card, permission)
        old_item = self.cache.get_member(user_id, group_id)
        ctx.is_new = old_item is None
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
        async with core_db.session() as session:
            members = await MemberOps(session).get_all()

        self.cache.set_batch(
            {
                self.cache._gen_key(m.user_id, m.group_id): MemberCacheItem(
                    card_hash=hash(m.group_card),
                    permission=m.permission,
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

        async with core_db.session() as session:
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
