from typing import cast

from sqlalchemy import CursorResult
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.lib.db.ops import BaseOps

from .consts import SnapshotEventType
from .tables import GroupSnapshot, UserSnapshot
from .types import GroupSnapshotPayload, UserSnapshotPayload


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
        group_id: str,
        event_type: SnapshotEventType,
        content: str,
    ) -> None:
        stmt = sqlite_insert(UserSnapshot).values(
            user_id=user_id,
            group_id=group_id,
            event_type=event_type,
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
        event_type: SnapshotEventType,
        content: str,
    ) -> None:
        stmt = sqlite_insert(GroupSnapshot).values(
            group_id=group_id,
            event_type=event_type,
            content=content,
        )
        await self.session.execute(stmt)
