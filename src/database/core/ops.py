from collections.abc import Sequence
from typing import cast

from sqlalchemy import CursorResult, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.lib.db.ops import BaseOps

from .consts import GroupStatus, Permission, UserStatus
from .tables import Blacklist, Group, Member, User
from .types import (
    GroupPayload,
    GroupUpdateNamePayload,
    MemberPayload,
    MemberUpdateCardPayload,
    MemberUpdatePermPayload,
    UserPayload,
    UserUpdateNamePayload,
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
                "status": stmt.excluded.status,
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

    async def get_by_user_id(self, user_id: str) -> User | None:
        stmt = select(User).where(User.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def upsert_user(self, user_payload: UserPayload) -> User:
        stmt = sqlite_insert(User).values([user_payload])
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

    async def upsert_status(self, user_id: str, status: UserStatus) -> User:
        stmt = sqlite_insert(User).values(
            user_id=user_id,
            status=status,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.user_id],
            set_={
                "status": stmt.excluded.status,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        stmt = stmt.returning(User)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def upsert_permission(self, user_id: str, permission: Permission) -> User:
        stmt = sqlite_insert(User).values(
            user_id=user_id,
            permission=permission,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.user_id],
            set_={
                "permission": stmt.excluded.permission,
                "updated_at": stmt.excluded.updated_at,
            },
        )
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

    async def get_by_group_id(self, group_id: str) -> Group | None:
        stmt = select(Group).where(Group.group_id == group_id)
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def upsert_group(self, group_payload: GroupPayload) -> Group:
        stmt = sqlite_insert(Group).values([group_payload])
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

    async def upsert_status(self, group_id: str, status: GroupStatus) -> Group:
        stmt = sqlite_insert(Group).values(
            group_id=group_id,
            status=status,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Group.group_id],
            set_={
                "status": stmt.excluded.status,
            },
        )
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

    async def bulk_upsert_permissions(
        self,
        perms_data: list[MemberUpdatePermPayload],
    ) -> int:
        if not perms_data:
            return 0
        stmt = sqlite_insert(Member).values(perms_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Member.group_id, Member.user_id],
            set_={
                "permission": stmt.excluded.permission,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
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

    async def upsert_member(self, member_payload: MemberPayload) -> Member:
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

    async def upsert_permission(
        self,
        user_id: str,
        group_id: str,
        permission: Permission = Permission.NORMAL,
    ) -> Member:
        stmt = sqlite_insert(Member).values(
            user_id=user_id,
            group_id=group_id,
            permission=permission,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Member.group_id, Member.user_id],
            set_={
                "permission": stmt.excluded.permission,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        stmt = stmt.returning(Member)
        result = await self.session.execute(stmt)
        return result.scalars().one()


class BlacklistOps(BaseOps[Blacklist]):
    pass
