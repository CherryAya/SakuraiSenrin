"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 16:18:02
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-22 04:59:09
Description: core db 操作类逻辑
"""

from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import CursorResult, delete, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import selectinload

from src.lib.db.ops import BaseOps
from src.lib.types import UNSET, Unset, is_set

from .consts import GroupStatus, InvitationStatus, Permission
from .tables import Blacklist, Group, Invitation, InvitationMessage, Member, User
from .types import (
    GroupPayload,
    GroupUpdateNamePayload,
    GroupUpdateStatusPayload,
    MemberPayload,
    MemberUpdateCardPayload,
    MemberUpdatePermPayload,
    UserPayload,
    UserUpdateNamePayload,
    UserUpdatePermPayload,
)


class UserOps(BaseOps[User]):
    async def bulk_upsert_users(self, users_data: list[UserPayload]) -> int:
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

    async def bulk_insert_ignore(self, users_data: list[UserPayload]) -> int:
        if not users_data:
            return 0
        stmt = sqlite_insert(User).values(users_data)
        stmt = stmt.on_conflict_do_nothing()
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def bulk_upsert_names(self, users_data: list[UserUpdateNamePayload]) -> int:
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
        users_data: list[UserUpdatePermPayload],
    ) -> int:
        if not users_data:
            return 0

        sql = f"""
            UPDATE {User.__tablename__}
            SET permission = :permission,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = :user_id
        """
        connection = await self.session.connection()
        result = await connection.execute(text(sql), users_data)

        return cast(CursorResult, result).rowcount

    async def get_by_user_id(self, user_id: str) -> User | None:
        stmt = select(User).where(User.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def upsert_user(
        self,
        user_id: str,
        user_name: str,
        permission: Permission | Unset = UNSET,
        remark: str | Unset = UNSET,
    ) -> User:
        user_payload: UserPayload = {
            "user_id": user_id,
            "user_name": user_name,
        }
        if is_set(permission):
            user_payload["permission"] = permission
        if is_set(remark):
            user_payload["remark"] = remark

        stmt = sqlite_insert(User).values(user_payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.user_id],
            set_={
                "user_name": stmt.excluded.user_name,
                "status": stmt.excluded.status,
                "permission": stmt.excluded.permission,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        stmt = stmt.returning(User)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def upsert_name(self, user_id: str, user_name: str) -> User:
        stmt = sqlite_insert(User).values(
            user_id=user_id,
            user_name=user_name,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.user_id],
            set_={
                "user_name": stmt.excluded.user_name,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        stmt = stmt.returning(User)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def update_permission(self, user_id: str, permission: Permission) -> User:
        stmt = update(User).where(User.user_id == user_id).values(permission=permission)
        stmt = stmt.returning(User)
        result = await self.session.execute(stmt)
        return result.scalars().one()


class GroupOps(BaseOps[Group]):
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

    async def bulk_upsert_names(self, groups_data: list[GroupUpdateNamePayload]) -> int:
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
        group_statuses: list[GroupUpdateStatusPayload],
    ) -> int:
        if not group_statuses:
            return 0
        sql = f"""
            UPDATE {Group.__tablename__}
            SET status = :status,
                updated_at = CURRENT_TIMESTAMP
            WHERE group_id = :group_id
        """
        connection = await self.session.connection()
        result = await connection.execute(text(sql), group_statuses)
        return cast(CursorResult, result).rowcount

    async def get_by_group_id(self, group_id: str) -> Group | None:
        stmt = select(Group).where(Group.group_id == group_id)
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def upsert_group(
        self,
        group_id: str,
        group_name: str,
        status: GroupStatus | Unset = UNSET,
    ) -> Group:
        group_payload: GroupPayload = {
            "group_id": group_id,
            "group_name": group_name,
        }
        if is_set(status):
            group_payload["status"] = status
        stmt = sqlite_insert(Group).values(group_payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Group.group_id],
            set_={
                "group_name": stmt.excluded.group_name,
                "status": stmt.excluded.status,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        stmt = stmt.returning(Group)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def update_status(self, group_id: str, status: GroupStatus) -> Group:
        stmt = update(Group).where(Group.group_id == group_id).values(status=status)
        stmt = stmt.returning(Group)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def upsert_name(self, group_id: str, group_name: str) -> Group:
        stmt = sqlite_insert(Group).values(
            group_id=group_id,
            group_name=group_name,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Group.group_id],
            set_={
                "group_name": stmt.excluded.group_name,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        stmt = stmt.returning(Group)
        result = await self.session.execute(stmt)
        return result.scalars().one()


class MemberOps(BaseOps[Member]):
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

    async def bulk_upsert_cards(self, cards_data: list[MemberUpdateCardPayload]) -> int:
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
        perms_data: list[MemberUpdatePermPayload],
    ) -> int:
        if not perms_data:
            return 0

        sql = f"""
            UPDATE {Member.__tablename__}
            SET permission = :permission,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = :user_id
            AND group_id = :group_id
        """
        connection = await self.session.connection()
        result = await connection.execute(text(sql), perms_data)
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

    async def upsert_member(
        self,
        group_id: str,
        user_id: str,
        group_card: str,
        permission: Permission | Unset = UNSET,
    ) -> Member:
        member_payload: MemberPayload = {
            "group_id": group_id,
            "user_id": user_id,
            "group_card": group_card,
        }
        if is_set(permission):
            member_payload["permission"] = permission
        stmt = sqlite_insert(Member).values(member_payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Member.group_id, Member.user_id],
            set_={
                "group_card": stmt.excluded.group_card,
                "permission": stmt.excluded.permission,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        stmt = stmt.returning(Member)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def upsert_card(
        self,
        user_id: str,
        group_id: str,
        group_card: str | None = None,
    ) -> Member:
        stmt = sqlite_insert(Member).values(
            user_id=user_id,
            group_id=group_id,
            group_card=group_card,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Member.group_id, Member.user_id],
            set_={
                "group_card": stmt.excluded.group_card,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        stmt = stmt.returning(Member)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def update_permission(
        self,
        user_id: str,
        group_id: str,
        permission: Permission = Permission.NORMAL,
    ) -> Member:
        stmt = (
            update(Member)
            .where(Member.user_id == user_id)
            .where(Member.group_id == group_id)
            .values(permission=permission)
        )
        stmt = stmt.returning(Member)
        result = await self.session.execute(stmt)
        return result.scalars().one()


class InvitationOps(BaseOps[Invitation]):
    async def create_invitation(
        self,
        group_id: str,
        inviter_id: str,
        flag: str,
        first_message_id: str,
    ) -> Invitation:
        invitation = Invitation(
            group_id=group_id,
            inviter_id=inviter_id,
            flag=flag,
            status=InvitationStatus.PENDING,
        )
        self.session.add(invitation)
        await self.session.flush()
        msg_record = InvitationMessage(
            invitation_id=invitation.id,
            message_id=str(first_message_id),
        )
        self.session.add(msg_record)
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
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_pending_requests(
        self,
        group_id: str | None = None,
    ) -> list[Invitation]:
        stmt = (
            select(Invitation)
            .where(Invitation.status == InvitationStatus.PENDING)
            .options(selectinload(Invitation.inviter), selectinload(Invitation.group))
        )

        if group_id:
            stmt = stmt.where(Invitation.group_id == group_id)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self,
        invitation_id: int,
        status: InvitationStatus,
    ) -> Invitation:
        stmt = (
            update(Invitation)
            .where(Invitation.id == invitation_id)
            .values(status=status)
            .returning(Invitation)
        )
        result = await self.session.execute(stmt)
        return result.scalars().one()


class BlacklistOps(BaseOps[Blacklist]):
    async def add_ban(
        self,
        target_user_id: str,
        group_id: str,
        operator_id: str,
        ban_expiry: datetime,
        reason: str | Unset = UNSET,
    ) -> Blacklist:
        stmt = sqlite_insert(Blacklist).values(
            target_user_id=target_user_id,
            group_id=group_id,
            operator_id=operator_id,
            ban_expiry=ban_expiry,
            reason=reason,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Blacklist.target_user_id, Blacklist.group_id],
            set_={
                "ban_expiry": stmt.excluded.ban_expiry,
                "reason": stmt.excluded.reason,
            },
        )
        stmt = stmt.returning(Blacklist)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def unban(self, target_user_id: str, group_id: str) -> None:
        stmt = delete(Blacklist).where(
            Blacklist.target_user_id == target_user_id,
            Blacklist.group_id == group_id,
        )
        await self.session.execute(stmt)

    async def get_all(self) -> Sequence[Blacklist]:
        result = await self.session.execute(select(Blacklist))
        return result.scalars().all()

    async def get_by_target_user_id(
        self,
        target_user_id: str,
        group_id: str,
    ) -> Sequence[Blacklist]:
        stmt = select(Blacklist).where(
            Blacklist.target_user_id == target_user_id,
            Blacklist.group_id == group_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_group_id(
        self,
        group_id: str,
    ) -> Sequence[Blacklist]:
        stmt = select(Blacklist).where(Blacklist.group_id == group_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_target_user_id_and_group_id(
        self,
        target_user_id: str,
        group_id: str,
    ) -> Sequence[Blacklist]:
        stmt = select(Blacklist).where(
            Blacklist.target_user_id == target_user_id,
            Blacklist.group_id == group_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
