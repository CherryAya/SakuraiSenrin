from __future__ import annotations

from sqlalchemy import and_, delete, func, or_, select

from src.lib.utils.common import get_current_time

from .instances import self_unban_db
from .tables import SelfUnbanAttempt, SelfUnbanBase


class SelfUnbanRepository:
    @classmethod
    async def init_all_tables(cls) -> None:
        await self_unban_db.init(SelfUnbanBase)

    async def count_consumed_attempts(
        self,
        *,
        subject_type: str,
        subject_id: str,
    ) -> int:
        async with self_unban_db.read_session() as session:
            stmt = select(func.count(SelfUnbanAttempt.id)).where(
                SelfUnbanAttempt.subject_type == subject_type,
                SelfUnbanAttempt.subject_id == subject_id,
                SelfUnbanAttempt.consumes_quota == 1,
            )
            result = await session.execute(stmt)
            return int(result.scalar_one() or 0)

    async def count_user_consumed_attempts(self, requester_user_id: str) -> int:
        async with self_unban_db.read_session() as session:
            stmt = select(func.count(SelfUnbanAttempt.id)).where(
                SelfUnbanAttempt.consumes_quota == 1,
                or_(
                    and_(
                        SelfUnbanAttempt.subject_type == "user",
                        SelfUnbanAttempt.subject_id == requester_user_id,
                    ),
                    and_(
                        SelfUnbanAttempt.subject_type == "group",
                        SelfUnbanAttempt.requester_user_id == requester_user_id,
                        SelfUnbanAttempt.result == "approved",
                    ),
                ),
            )
            result = await session.execute(stmt)
            return int(result.scalar_one() or 0)

    async def create_attempt(
        self,
        *,
        subject_type: str,
        subject_id: str,
        scope_group_id: str,
        requester_user_id: str,
        reason: str,
        result: str,
        consumes_quota: bool,
    ) -> SelfUnbanAttempt:
        async with self_unban_db.write_session() as session:
            event_time = get_current_time()
            row = SelfUnbanAttempt(
                subject_type=subject_type,
                subject_id=subject_id,
                scope_group_id=scope_group_id,
                requester_user_id=requester_user_id,
                reason=reason,
                result=result,
                consumes_quota=1 if consumes_quota else 0,
                created_at=event_time,
                updated_at=event_time,
            )
            session.add(row)
            await session.flush()
            return row

    async def clear_attempts(self) -> None:
        async with self_unban_db.write_session() as session:
            await session.execute(delete(SelfUnbanAttempt))


self_unban_repo = SelfUnbanRepository()

__all__ = ["SelfUnbanRepository", "self_unban_repo"]
