"""Water summary and matrix/group aggregate operations."""

from collections.abc import Sequence
from typing import ClassVar, cast

from sqlalchemy import CursorResult, and_, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine.row import Row
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.ops import BaseOps
from src.lib.utils.common import split_list

from .tables import (
    WaterArchivedDailySummary,
    WaterDailySummary,
    WaterGroupMatrixMap,
    WaterGroupTotal,
    WaterGroupUserTotal,
)
from .types import (
    WaterGroupMatrixMapPayload,
    WaterGroupTotalPayload,
    WaterGroupUserTotalPayload,
    WaterSummaryPayload,
    WaterSummaryRecord,
)


class _WaterSummaryOpsBase[T: WaterDailySummary | WaterArchivedDailySummary](
    BaseOps[T]
):
    model: ClassVar[type[WaterDailySummary] | type[WaterArchivedDailySummary]]

    def _get_model_class(self) -> type[T]:
        return cast(type[T], self.model)

    def _serialize_summary_row(
        self,
        row: WaterDailySummary | WaterArchivedDailySummary,
    ) -> WaterSummaryRecord:
        return WaterSummaryRecord(
            group_id=str(row.group_id),
            user_id=str(row.user_id),
            record_date=int(row.record_date),
            msg_count=int(row.msg_count),
            active_hours=int(row.active_hours),
            hourly_counts=list(row.hourly_counts or [0] * 24)[:24],
            created_at=int(row.created_at),
            updated_at=int(row.updated_at),
        )

    async def bulk_upsert_summary(self, summary_data: list[WaterSummaryPayload]) -> int:
        if not summary_data:
            return 0

        stmt = sqlite_insert(self.model).values(summary_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                self.model.group_id,
                self.model.user_id,
                self.model.record_date,
            ],
            set_={
                "msg_count": stmt.excluded.msg_count,
                "active_hours": stmt.excluded.active_hours,
                "hourly_counts": stmt.excluded.hourly_counts,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_ranks_by_date(
        self,
        group_id: str,
        record_date: int,
    ) -> dict[str, int]:
        stmt = (
            select(self.model.user_id)
            .where(
                self.model.group_id == group_id,
                self.model.record_date == record_date,
            )
            .order_by(self.model.msg_count.desc())
        )
        result = await self.session.execute(stmt)
        return {user_id: rank for rank, user_id in enumerate(result.scalars(), 1)}

    async def get_user_recent_summaries(
        self,
        user_id: str,
        group_ids: list[str],
        start_date: int,
        end_date: int,
    ) -> list[WaterSummaryRecord]:
        if not group_ids:
            return []
        stmt = (
            select(self.model)
            .where(
                self.model.user_id == user_id,
                self.model.group_id.in_(group_ids),
                self.model.record_date >= start_date,
                self.model.record_date <= end_date,
            )
            .order_by(self.model.record_date.asc())
        )
        result = await self.session.execute(stmt)
        return [self._serialize_summary_row(row) for row in result.scalars().all()]

    async def get_user_summary_rows_by_date(
        self,
        user_id: str,
        record_date: int,
    ) -> Sequence[Row[tuple[str, int, int]]]:
        stmt = select(
            self.model.group_id,
            self.model.msg_count,
            self.model.active_hours,
        ).where(
            self.model.user_id == user_id,
            self.model.record_date == record_date,
        )
        result = await self.session.execute(stmt)
        return result.all()

    async def get_summaries_in_window(
        self,
        start_date: int,
        end_date: int,
        *,
        group_ids: list[str] | None = None,
        user_id: str | None = None,
        preserve_order: bool = True,
    ) -> list[WaterSummaryRecord]:
        stmt = select(self.model).where(
            self.model.record_date >= start_date,
            self.model.record_date <= end_date,
        )
        if group_ids is not None:
            if not group_ids:
                return []
            stmt = stmt.where(self.model.group_id.in_(group_ids))
        if user_id is not None:
            stmt = stmt.where(self.model.user_id == user_id)
        if preserve_order:
            stmt = stmt.order_by(self.model.record_date.asc())
        result = await self.session.execute(stmt)
        return [self._serialize_summary_row(row) for row in result.scalars().all()]

    async def get_first_summary_record_date(
        self,
        *,
        group_ids: list[str] | None = None,
        user_id: str | None = None,
    ) -> int | None:
        stmt = select(func.min(self.model.record_date))
        if group_ids is not None:
            if not group_ids:
                return None
            stmt = stmt.where(self.model.group_id.in_(group_ids))
        if user_id is not None:
            stmt = stmt.where(self.model.user_id == user_id)
        result = await self.session.execute(stmt)
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None

    async def get_group_user_rank(self, group_id: str, user_id: str) -> int | None:
        own_stmt = select(func.sum(self.model.msg_count)).where(
            self.model.group_id == group_id,
            self.model.user_id == user_id,
        )
        own_result = await self.session.execute(own_stmt)
        own_total = int(own_result.scalar() or 0)
        if own_total <= 0:
            return None

        grouped = (
            select(
                self.model.user_id.label("user_id"),
                func.sum(self.model.msg_count).label("total"),
            )
            .where(self.model.group_id == group_id)
            .group_by(self.model.user_id)
            .subquery()
        )
        rank_stmt = (
            select(func.count())
            .select_from(grouped)
            .where(
                or_(
                    grouped.c.total > own_total,
                    and_(grouped.c.total == own_total, grouped.c.user_id < user_id),
                )
            )
        )
        rank_result = await self.session.execute(rank_stmt)
        higher_count = int(rank_result.scalar() or 0)
        return higher_count + 1

    async def get_group_summary_rows(
        self,
        group_id: str,
    ) -> Sequence[Row[tuple[str, int, int]]]:
        stmt = select(
            self.model.user_id,
            self.model.msg_count,
            self.model.active_hours,
        ).where(self.model.group_id == group_id)
        result = await self.session.execute(stmt)
        return result.all()

    async def get_group_activity_rank(self, group_id: str) -> int | None:
        own_stmt = select(func.sum(self.model.msg_count)).where(
            self.model.group_id == group_id
        )
        own_result = await self.session.execute(own_stmt)
        own_total = int(own_result.scalar() or 0)
        if own_total <= 0:
            return None

        grouped = (
            select(
                self.model.group_id.label("group_id"),
                func.sum(self.model.msg_count).label("total"),
            )
            .group_by(self.model.group_id)
            .subquery()
        )
        rank_stmt = (
            select(func.count())
            .select_from(grouped)
            .where(
                or_(
                    grouped.c.total > own_total,
                    and_(grouped.c.total == own_total, grouped.c.group_id < group_id),
                )
            )
        )
        rank_result = await self.session.execute(rank_stmt)
        higher_count = int(rank_result.scalar() or 0)
        return higher_count + 1

    async def get_global_period_summary_rows(
        self,
        start_date: int,
        end_date: int,
    ) -> list[WaterSummaryRecord]:
        stmt = select(self.model).where(
            self.model.record_date >= start_date,
            self.model.record_date <= end_date,
        )
        result = await self.session.execute(stmt)
        return [self._serialize_summary_row(row) for row in result.scalars().all()]


class WaterSummaryOps(_WaterSummaryOpsBase[WaterDailySummary]):
    model = WaterDailySummary


class WaterArchivedSummaryOps(_WaterSummaryOpsBase[WaterArchivedDailySummary]):
    model = WaterArchivedDailySummary


class WaterGroupMatrixMapOps(BaseOps[WaterGroupMatrixMap]):
    async def get_matrix_id_by_group(self, group_id: str) -> str | None:
        stmt = select(WaterGroupMatrixMap.matrix_id).where(
            WaterGroupMatrixMap.group_id == group_id
        )
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def get_all_mappings(self) -> dict[str, str]:
        stmt = select(WaterGroupMatrixMap.group_id, WaterGroupMatrixMap.matrix_id)
        result = await self.session.execute(stmt)
        return {str(group_id): str(matrix_id) for group_id, matrix_id in result.all()}

    async def get_mappings_by_groups(self, group_ids: list[str]) -> dict[str, str]:
        if not group_ids:
            return {}
        stmt = select(
            WaterGroupMatrixMap.group_id, WaterGroupMatrixMap.matrix_id
        ).where(WaterGroupMatrixMap.group_id.in_(group_ids))
        result = await self.session.execute(stmt)
        return {str(group_id): str(matrix_id) for group_id, matrix_id in result.all()}

    async def upsert_mapping(self, payload: WaterGroupMatrixMapPayload) -> int:
        stmt = sqlite_insert(WaterGroupMatrixMap).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WaterGroupMatrixMap.group_id],
            set_={
                "matrix_id": stmt.excluded.matrix_id,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def count_groups_by_matrix(self, matrix_id: str) -> int:
        stmt = select(func.count(WaterGroupMatrixMap.group_id)).where(
            WaterGroupMatrixMap.matrix_id == matrix_id
        )
        result = await self.session.execute(stmt)
        return int(result.scalar() or 0)

    async def get_groups_by_matrix(self, matrix_id: str) -> list[str]:
        stmt = (
            select(WaterGroupMatrixMap.group_id)
            .where(WaterGroupMatrixMap.matrix_id == matrix_id)
            .order_by(WaterGroupMatrixMap.group_id.asc())
        )
        result = await self.session.execute(stmt)
        return [str(group_id) for group_id in result.scalars().all()]


class WaterGroupStatsOps:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_group_user_total(
        self,
        group_id: str,
        user_id: str,
    ) -> tuple[int, int, int] | None:
        stmt = select(
            WaterGroupUserTotal.msg_count,
            WaterGroupUserTotal.active_days,
            WaterGroupUserTotal.active_hours,
        ).where(
            WaterGroupUserTotal.group_id == group_id,
            WaterGroupUserTotal.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return (int(row[0]), int(row[1]), int(row[2]))

    async def get_group_total(self, group_id: str) -> tuple[int, int, int] | None:
        stmt = select(
            WaterGroupTotal.msg_count,
            WaterGroupTotal.active_days,
            WaterGroupTotal.active_hours,
        ).where(WaterGroupTotal.group_id == group_id)
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return (int(row[0]), int(row[1]), int(row[2]))

    async def get_group_user_rank(self, group_id: str, user_id: str) -> int | None:
        own = await self.get_group_user_total(group_id, user_id)
        if own is None or own[0] <= 0:
            return None
        own_msg_count = own[0]
        rank_stmt = select(func.count(WaterGroupUserTotal.id)).where(
            WaterGroupUserTotal.group_id == group_id,
            or_(
                WaterGroupUserTotal.msg_count > own_msg_count,
                and_(
                    WaterGroupUserTotal.msg_count == own_msg_count,
                    WaterGroupUserTotal.user_id < user_id,
                ),
            ),
        )
        rank_result = await self.session.execute(rank_stmt)
        higher_count = int(rank_result.scalar() or 0)
        return higher_count + 1

    async def get_group_activity_rank(self, group_id: str) -> int | None:
        own = await self.get_group_total(group_id)
        if own is None or own[0] <= 0:
            return None
        own_msg_count = own[0]
        rank_stmt = select(func.count(WaterGroupTotal.group_id)).where(
            or_(
                WaterGroupTotal.msg_count > own_msg_count,
                and_(
                    WaterGroupTotal.msg_count == own_msg_count,
                    WaterGroupTotal.group_id < group_id,
                ),
            )
        )
        rank_result = await self.session.execute(rank_stmt)
        higher_count = int(rank_result.scalar() or 0)
        return higher_count + 1

    async def get_group_user_totals(
        self,
        keys: list[tuple[str, str]],
    ) -> dict[tuple[str, str], tuple[int, int, int]]:
        if not keys:
            return {}
        rows: dict[tuple[str, str], tuple[int, int, int]] = {}
        for chunk in split_list(keys, 400):
            conditions = [
                (WaterGroupUserTotal.group_id == group_id)
                & (WaterGroupUserTotal.user_id == user_id)
                for group_id, user_id in chunk
            ]
            stmt = select(
                WaterGroupUserTotal.group_id,
                WaterGroupUserTotal.user_id,
                WaterGroupUserTotal.msg_count,
                WaterGroupUserTotal.active_days,
                WaterGroupUserTotal.active_hours,
            ).where(or_(*conditions))
            result = await self.session.execute(stmt)
            rows.update(
                {
                    (group_id, user_id): (msg_count, active_days, active_hours)
                    for (
                        group_id,
                        user_id,
                        msg_count,
                        active_days,
                        active_hours,
                    ) in result.all()
                }
            )
        return rows

    async def get_group_totals(
        self,
        group_ids: list[str],
    ) -> dict[str, tuple[int, int, int]]:
        if not group_ids:
            return {}
        rows: dict[str, tuple[int, int, int]] = {}
        for chunk in split_list(group_ids, 400):
            stmt = select(
                WaterGroupTotal.group_id,
                WaterGroupTotal.msg_count,
                WaterGroupTotal.active_days,
                WaterGroupTotal.active_hours,
            ).where(WaterGroupTotal.group_id.in_(chunk))
            result = await self.session.execute(stmt)
            rows.update(
                {
                    group_id: (msg_count, active_days, active_hours)
                    for group_id, msg_count, active_days, active_hours in result.all()
                }
            )
        return rows

    async def upsert_group_user_totals(
        self,
        data: list[WaterGroupUserTotalPayload],
    ) -> int:
        if not data:
            return 0
        stmt = sqlite_insert(WaterGroupUserTotal).values(data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WaterGroupUserTotal.group_id, WaterGroupUserTotal.user_id],
            set_={
                "msg_count": stmt.excluded.msg_count,
                "active_days": stmt.excluded.active_days,
                "active_hours": stmt.excluded.active_hours,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def upsert_group_totals(self, data: list[WaterGroupTotalPayload]) -> int:
        if not data:
            return 0
        stmt = sqlite_insert(WaterGroupTotal).values(data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WaterGroupTotal.group_id],
            set_={
                "msg_count": stmt.excluded.msg_count,
                "active_days": stmt.excluded.active_days,
                "active_hours": stmt.excluded.active_hours,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount
