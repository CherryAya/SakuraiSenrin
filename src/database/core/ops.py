from typing import cast

from sqlalchemy import CursorResult, bindparam, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.lib.db.ops import BaseOps

from .consts import GroupStatus
from .tables import Blacklist, Group, Member, User
from .types import (
    GroupPayload,
    GroupUpdateNamePayload,
    MemberPayload,
    UserPayload,
    UserUpdateNamePayload,
)


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

    async def bulk_update_names(self, groups_data: list[GroupUpdateNamePayload]) -> int:
        if not groups_data:
            return 0
        stmt = (
            update(Group)
            .where(Group.group_id == bindparam("group_id"))
            .values(group_name=bindparam("group_name"))
        )

        result = await self.session.execute(stmt, groups_data)
        return cast(CursorResult, result).rowcount

    async def upsert_group_status(self, group_id: str, status: GroupStatus) -> Group:
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

    async def bulk_update_names(self, users_data: list[UserUpdateNamePayload]) -> int:
        if not users_data:
            return 0
        stmt = (
            update(User)
            .where(User.user_id == bindparam("user_id"))
            .values(user_name=bindparam("user_name"))
        )
        result = await self.session.execute(stmt, users_data)
        return cast(CursorResult, result).rowcount


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


class BlacklistOps(BaseOps[Blacklist]):
    pass
