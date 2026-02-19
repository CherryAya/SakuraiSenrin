"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-07 02:54:36
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 22:33:56
Description: snapshot db 操作类
"""

from typing import cast

from sqlalchemy import CursorResult
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.lib.db.ops import BaseOps

from .tables import GroupSnapshot, MemberSnapshot, UserSnapshot
from .types import GroupSnapshotPayload, MemberSnapshotPayload, UserSnapshotPayload


class UserSnapshotOps(BaseOps[UserSnapshot]):
    async def bulk_create_user_snapshots(
        self,
        snapshots: list[UserSnapshotPayload],
    ) -> int:
        if not snapshots:
            return 0
        stmt = sqlite_insert(UserSnapshot).values(snapshots)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def create_user_snapshot(
        self,
        user_id: str,
        content: str,
    ) -> None:
        stmt = sqlite_insert(UserSnapshot).values(
            user_id=user_id,
            content=content,
        )
        await self.session.execute(stmt)


class GroupSnapshotOps(BaseOps[GroupSnapshot]):
    async def bulk_create_group_snapshots(
        self,
        snapshots: list[GroupSnapshotPayload],
    ) -> int:
        if not snapshots:
            return 0
        stmt = sqlite_insert(GroupSnapshot).values(snapshots)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def create_group_snapshot(
        self,
        group_id: str,
        content: str,
    ) -> None:
        stmt = sqlite_insert(GroupSnapshot).values(
            group_id=group_id,
            content=content,
        )
        await self.session.execute(stmt)


class MemberSnapshotOps(BaseOps[MemberSnapshot]):
    async def bulk_create_member_snapshots(
        self,
        snapshots: list[MemberSnapshotPayload],
    ) -> int:
        if not snapshots:
            return 0
        stmt = sqlite_insert(MemberSnapshot).values(snapshots)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def create_member_snapshot(
        self,
        user_id: str,
        group_id: str,
        content: str,
    ) -> None:
        stmt = sqlite_insert(MemberSnapshot).values(
            user_id=user_id,
            group_id=group_id,
            content=content,
        )
        await self.session.execute(stmt)
