"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 16:18:02
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-02 19:48:03
Description: core db 操作类逻辑
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Unpack, cast

from sqlalchemy import CursorResult, bindparam, delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import aliased, selectinload

from src.lib.db.ops import BaseOps
from src.lib.utils.common import get_current_time

from .consts import GroupStatus, InvitationStatus, Permission
from .tables import Blacklist, Group, Invitation, InvitationMessage, Member, User
from .types import (
    BulkUpdateGroupNamePayload,
    BulkUpdateGroupStatusPayload,
    BulkUpdateMemberCardPayload,
    BulkUpdateMemberPermPayload,
    BulkUpdateUserNamePayload,
    BulkUpdateUserPermPayload,
    GroupPayload,
    GroupUpdateKwargs,
    MemberPayload,
    MemberUpdateKwargs,
    UserPayload,
    UserUpdateKwargs,
)


class UserOps(BaseOps[User]):
    async def _upsert_user(
        self,
        user_id: str,
        **kwargs: Unpack[UserUpdateKwargs],
    ) -> User:
        event_time = get_current_time()
        user_payload: UserPayload = {
            "user_id": user_id,
            "user_name": kwargs.get("user_name", f"user_{user_id}"),
            "permission": kwargs.get("permission", Permission.NORMAL),
            "created_at": event_time,
            "updated_at": event_time,
        }
        if "remark" in kwargs:
            user_payload["remark"] = kwargs["remark"]

        stmt = sqlite_insert(User).values(user_payload)
        update_set = {"updated_at": event_time}
        for key in kwargs:
            update_set[key] = getattr(stmt.excluded, key)
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.user_id],
            set_=update_set,
        )
        stmt = stmt.returning(User)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def bulk_upsert_users(self, users_data: list[UserPayload]) -> int:
        if not users_data:
            return 0
        stmt = sqlite_insert(User).values(users_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.user_id],
            set_={
                "user_name": stmt.excluded.user_name,
                "permission": stmt.excluded.permission,
                "remark": stmt.excluded.remark,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def bulk_insert_ignore(self, users_data: list[UserPayload]) -> int:
        if not users_data:
            return 0
        stmt = sqlite_insert(User).values(users_data)
        stmt = stmt.on_conflict_do_nothing()
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def bulk_upsert_names(
        self, users_data: list[BulkUpdateUserNamePayload]
    ) -> int:
        if not users_data:
            return 0
        stmt = sqlite_insert(User).values(users_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.user_id],
            set_={
                "user_name": stmt.excluded.user_name,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def bulk_update_permissions(
        self,
        users_data: list[BulkUpdateUserPermPayload],
    ) -> int:
        if not users_data:
            return 0

        stmt = (
            update(User)
            .where(User.user_id == bindparam("user_id"))
            .values(
                permission=bindparam("permission"),
                updated_at=bindparam("updated_at"),
            )
        )
        result = await self.session.execute(stmt, users_data)
        return cast(CursorResult, result).rowcount

    async def get_by_user_id(self, user_id: str) -> User | None:
        stmt = select(User).where(User.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def get_name_by_uid(self, user_id: str) -> str | None:
        stmt = select(User.user_name).where(User.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def add_user(
        self,
        user_id: str,
        user_name: str,
        permission: Permission = Permission.NORMAL,
        remark: str | None = None,
    ) -> User:
        kwargs: UserUpdateKwargs = {
            "user_name": user_name,
            "permission": permission,
        }
        if remark is not None:
            kwargs["remark"] = remark
        return await self._upsert_user(user_id, **kwargs)

    async def update_name(self, user_id: str, user_name: str) -> User:
        return await self._upsert_user(user_id, user_name=user_name)

    async def update_permission(self, user_id: str, permission: Permission) -> User:
        return await self._upsert_user(user_id, permission=permission)


class GroupOps(BaseOps[Group]):
    async def _upsert_group(
        self,
        group_id: str,
        **kwargs: Unpack[GroupUpdateKwargs],
    ) -> Group:
        event_time = get_current_time()
        group_payload: GroupPayload = {
            "group_id": group_id,
            "group_name": kwargs.get("group_name", f"group_{group_id}"),
            "status": kwargs.get("status", GroupStatus.UNAUTHORIZED),
            "created_at": event_time,
            "updated_at": event_time,
        }

        stmt = sqlite_insert(Group).values(group_payload)
        update_set = {"updated_at": event_time}
        for key in kwargs:
            update_set[key] = getattr(stmt.excluded, key)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Group.group_id],
            set_=update_set,
        )
        stmt = stmt.returning(Group)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def bulk_upsert_groups(self, groups_data: list[GroupPayload]) -> int:
        if not groups_data:
            return 0
        stmt = sqlite_insert(Group).values(groups_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Group.group_id],
            set_={
                "group_name": stmt.excluded.group_name,
                "status": stmt.excluded.status,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def bulk_insert_ignore(self, groups_data: list[GroupPayload]) -> int:
        if not groups_data:
            return 0
        stmt = sqlite_insert(Group).values(groups_data)
        stmt = stmt.on_conflict_do_nothing()
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def bulk_upsert_names(
        self, groups_data: list[BulkUpdateGroupNamePayload]
    ) -> int:
        if not groups_data:
            return 0
        stmt = sqlite_insert(Group).values(groups_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Group.group_id],
            set_={
                "group_name": stmt.excluded.group_name,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def bulk_update_statuses(
        self,
        group_statuses: list[BulkUpdateGroupStatusPayload],
    ) -> int:
        if not group_statuses:
            return 0
        stmt = (
            update(Group)
            .where(Group.group_id == bindparam("group_id"))
            .values(
                status=bindparam("status"),
                updated_at=bindparam("updated_at"),
            )
        )
        result = await self.session.execute(stmt, group_statuses)
        return cast(CursorResult, result).rowcount

    async def get_by_group_id(self, group_id: str) -> Group | None:
        stmt = select(Group).where(Group.group_id == group_id)
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def get_name_by_gid(self, group_id: str) -> str | None:
        stmt = select(Group.group_name).where(Group.group_id == group_id)
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def add_group(
        self,
        group_id: str,
        group_name: str,
        status: GroupStatus = GroupStatus.UNAUTHORIZED,
    ) -> Group:
        return await self._upsert_group(
            group_id,
            group_name=group_name,
            status=status,
        )

    async def update_status(self, group_id: str, status: GroupStatus) -> Group:
        return await self._upsert_group(group_id, status=status)

    async def update_name(self, group_id: str, group_name: str) -> Group:
        return await self._upsert_group(group_id, group_name=group_name)


class MemberOps(BaseOps[Member]):
    async def _upsert_member(
        self,
        group_id: str,
        user_id: str,
        **kwargs: Unpack[MemberUpdateKwargs],
    ) -> Member:
        event_time = get_current_time()
        member_payload: MemberPayload = {
            "group_id": group_id,
            "user_id": user_id,
            "group_card": kwargs.get("group_card", ""),
            "permission": kwargs.get("permission", Permission.NORMAL),
            "created_at": event_time,
            "updated_at": event_time,
        }

        stmt = sqlite_insert(Member).values(member_payload)
        update_set = {"updated_at": event_time}
        for key in kwargs:
            update_set[key] = getattr(stmt.excluded, key)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Member.group_id, Member.user_id],
            set_=update_set,
        )
        stmt = stmt.returning(Member)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def bulk_upsert_members(self, members_data: list[MemberPayload]) -> int:
        if not members_data:
            return 0
        stmt = sqlite_insert(Member).values(members_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Member.group_id, Member.user_id],
            set_={
                "group_card": stmt.excluded.group_card,
                "permission": stmt.excluded.permission,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def bulk_upsert_cards(
        self, cards_data: list[BulkUpdateMemberCardPayload]
    ) -> int:
        if not cards_data:
            return 0
        stmt = sqlite_insert(Member).values(cards_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Member.group_id, Member.user_id],
            set_={
                "group_card": stmt.excluded.group_card,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def bulk_update_permissions(
        self,
        perms_data: list[BulkUpdateMemberPermPayload],
    ) -> int:
        if not perms_data:
            return 0

        stmt = (
            update(Member)
            .where(
                Member.user_id == bindparam("user_id"),
                Member.group_id == bindparam("group_id"),
            )
            .values(
                permission=bindparam("permission"),
                updated_at=bindparam("updated_at"),
            )
        )
        result = await self.session.execute(stmt, perms_data)
        return cast(CursorResult, result).rowcount

    async def get_by_uid_gid(self, user_id: str, group_id: str) -> Member | None:
        stmt = select(Member).where(
            Member.user_id == user_id,
            Member.group_id == group_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def get_by_gid(self, group_id: str) -> Sequence[Member]:
        stmt = select(Member).where(Member.group_id == group_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_uid(self, user_id: str) -> Sequence[Member]:
        stmt = select(Member).where(Member.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_admin_by_uid(self, user_id: str) -> Sequence[Member]:
        stmt = (
            select(Member)
            .where(
                Member.user_id == user_id,
                Member.permission.in_([Permission.GROUP_ADMIN, Permission.GROUP_OWNER]),
            )
            .options(selectinload(Member.group))
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_card_by_uid_gid(self, user_id: str, group_id: str) -> str | None:
        stmt = select(Member.group_card).where(
            Member.group_id == group_id, Member.user_id == user_id
        )
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def add_member(
        self,
        group_id: str,
        user_id: str,
        group_card: str,
        permission: Permission = Permission.NORMAL,
    ) -> Member:
        return await self._upsert_member(
            group_id,
            user_id,
            group_card=group_card,
            permission=permission,
        )

    async def update_permission(
        self,
        user_id: str,
        group_id: str,
        permission: Permission = Permission.NORMAL,
    ) -> Member:
        return await self._upsert_member(
            group_id,
            user_id,
            permission=permission,
        )

    async def update_card(
        self,
        user_id: str,
        group_id: str,
        group_card: str,
    ) -> Member:
        return await self._upsert_member(
            group_id,
            user_id,
            group_card=group_card,
        )


class InvitationOps(BaseOps[Invitation]):
    async def create_invitation(
        self,
        group_id: str,
        inviter_id: str,
        flag: str | None,
    ) -> Invitation:
        invitation = Invitation(
            group_id=group_id,
            inviter_id=inviter_id,
            flag=flag,
            status=InvitationStatus.PENDING,
        )
        self.session.add(invitation)
        return invitation

    async def add_message_record(
        self,
        invitation_id: int,
        message_id: str,
    ) -> InvitationMessage:
        record = InvitationMessage(
            invitation_id=invitation_id,
            message_id=str(message_id),
        )
        self.session.add(record)
        return record

    async def get_by_flag(self, flag: str) -> Invitation | None:
        stmt = (
            select(Invitation)
            .where(Invitation.flag == flag)
            .options(
                selectinload(Invitation.inviter),
                selectinload(Invitation.group),
                selectinload(Invitation.operator),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def get_by_message_id(self, message_id: str) -> Invitation | None:
        stmt = (
            select(Invitation)
            .join(Invitation.messages)
            .where(InvitationMessage.message_id == str(message_id))
            .options(
                selectinload(Invitation.inviter),
                selectinload(Invitation.group),
                selectinload(Invitation.messages),
                selectinload(Invitation.operator),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_by_status(self, status: InvitationStatus) -> Sequence[Invitation]:
        subq = (
            select(
                Invitation,
                func.row_number()
                .over(
                    partition_by=Invitation.group_id,
                    order_by=Invitation.created_at.desc(),
                )
                .label("rn"),
            )
            .where(Invitation.status == status)
            .subquery()
        )

        inv_alias = aliased(Invitation, subq)

        stmt = (
            select(inv_alias)
            .where(subq.c.rn == 1)
            .options(
                selectinload(inv_alias.inviter),
                selectinload(inv_alias.group),
                selectinload(inv_alias.operator),
            )
            .order_by(inv_alias.created_at.desc())
        )

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_group_id(self, group_id: str) -> Invitation | None:
        stmt = (
            select(Invitation)
            .where(Invitation.group_id == group_id)
            .options(
                selectinload(Invitation.inviter),
                selectinload(Invitation.group),
                selectinload(Invitation.operator),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def update_status(
        self,
        invitation_id: int,
        status: InvitationStatus,
    ) -> Invitation:
        stmt = (
            update(Invitation)
            .where(Invitation.id == invitation_id)
            .values(status=status, updated_at=get_current_time())
            .returning(Invitation)
        )
        target_result = await self.session.execute(stmt)
        target_invitation = target_result.scalars().one()

        stmt_others = (
            update(Invitation)
            .where(
                Invitation.group_id == target_invitation.group_id,
                Invitation.id != invitation_id,
                Invitation.status == InvitationStatus.PENDING,
            )
            .values(status=InvitationStatus.IGNORED, updated_at=get_current_time())
        )
        await self.session.execute(stmt_others)

        return target_invitation

    async def ignore_all_pending(self) -> Sequence[Invitation]:
        stmt = (
            update(Invitation)
            .where(Invitation.status == InvitationStatus.PENDING)
            .values(status=InvitationStatus.IGNORED, updated_at=get_current_time())
            .options(selectinload(Invitation.group))
        ).returning(Invitation)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def reject_all_pending(self) -> Sequence[Invitation]:
        stmt = (
            update(Invitation)
            .where(Invitation.status == InvitationStatus.PENDING)
            .values(status=InvitationStatus.REJECTED, updated_at=get_current_time())
            .options(selectinload(Invitation.group))
        ).returning(Invitation)
        result = await self.session.execute(stmt)
        return result.scalars().all()


class BlacklistOps(BaseOps[Blacklist]):
    async def add_ban(
        self,
        target_user_id: str,
        group_id: str,
        operator_id: str,
        ban_expiry: int,
        reason: str | None = None,
    ) -> Blacklist:
        event_time = get_current_time()

        stmt = sqlite_insert(Blacklist).values(
            target_user_id=target_user_id,
            group_id=group_id,
            operator_id=operator_id,
            ban_expiry=ban_expiry,
            reason=reason,
            created_at=event_time,
            updated_at=event_time,
        )

        stmt = stmt.on_conflict_do_update(
            index_elements=[Blacklist.target_user_id, Blacklist.group_id],
            set_={
                "operator_id": stmt.excluded.operator_id,
                "ban_expiry": stmt.excluded.ban_expiry,
                "reason": stmt.excluded.reason,
                "updated_at": stmt.excluded.updated_at,
            },
        ).returning(Blacklist)

        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def unban(self, target_user_id: str, group_id: str) -> int:
        stmt = delete(Blacklist).where(
            Blacklist.target_user_id == target_user_id,
            Blacklist.group_id == group_id,
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_all(self) -> Sequence[Blacklist]:
        result = await self.session.execute(select(Blacklist))
        return result.scalars().all()

    async def get_by_uid(self, target_user_id: str) -> Sequence[Blacklist]:
        stmt = select(Blacklist).where(Blacklist.target_user_id == target_user_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_gid(self, group_id: str) -> Sequence[Blacklist]:
        stmt = select(Blacklist).where(Blacklist.group_id == group_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_uid_and_gid(
        self,
        target_user_id: str,
        group_id: str,
    ) -> Blacklist | None:
        stmt = select(Blacklist).where(
            Blacklist.target_user_id == target_user_id,
            Blacklist.group_id == group_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()
