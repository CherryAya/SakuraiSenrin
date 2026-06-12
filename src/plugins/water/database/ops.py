"""Water 数据访问层。"""

from collections.abc import Sequence
from math import floor, sqrt
from typing import ClassVar, cast

import arrow
from sqlalchemy import CursorResult, and_, delete, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine.row import Row
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from src.lib.db.ops import BaseOps
from src.lib.utils.common import split_list

from .tables import (
    WaterActivitySeason,
    WaterArchivedDailySummary,
    WaterDailySummary,
    WaterGlobalLevel,
    WaterGroupMatrixMap,
    WaterGroupTotal,
    WaterGroupUserTotal,
    WaterHourlyCounter,
    WaterMatrixLevel,
    WaterMatrixMergeState,
    WaterMatrixTotalLevel,
    WaterPenaltyLog,
    WaterSettlementJob,
    WaterUserAchievement,
)
from .types import (
    WaterAchievementPayload,
    WaterActivitySeasonPayload,
    WaterGroupMatrixMapPayload,
    WaterGroupTotalPayload,
    WaterGroupUserTotalPayload,
    WaterMatrixExpPayload,
    WaterMatrixMergeStatePayload,
    WaterMessagePayload,
    WaterPenaltyPayload,
    WaterSettlementJobPayload,
    WaterSummaryPayload,
    WaterSummaryRecord,
    WaterUserExpPayload,
)

_DEFAULT_SQLITE_MAX_VARIABLE_NUMBER = 999
_WATER_MESSAGE_INSERT_MARGIN = 16
_WATER_MESSAGE_BIND_PARAMS = len(WaterMessagePayload.__annotations__)
_sqlite_max_variable_number: int | None = None


class WaterMessageOps(BaseOps[WaterHourlyCounter]):
    async def _resolve_message_insert_chunk_size(self) -> int:
        global _sqlite_max_variable_number

        if _sqlite_max_variable_number is None:
            max_variables = _DEFAULT_SQLITE_MAX_VARIABLE_NUMBER
            result = await self.session.execute(text("PRAGMA compile_options"))
            for option in result.scalars():
                if not option.startswith("MAX_VARIABLE_NUMBER="):
                    continue
                _, _, raw_value = option.partition("=")
                max_variables = int(raw_value)
                break
            _sqlite_max_variable_number = max_variables

        return max(
            1,
            (_sqlite_max_variable_number - _WATER_MESSAGE_INSERT_MARGIN)
            // _WATER_MESSAGE_BIND_PARAMS,
        )

    async def bulk_insert_water_message(self, data: list[WaterMessagePayload]) -> int:
        if not data:
            return 0
        chunk_size = await self._resolve_message_insert_chunk_size()
        inserted = 0
        for chunk in split_list(data, chunk_size):
            stmt = sqlite_insert(WaterHourlyCounter).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    WaterHourlyCounter.record_date,
                    WaterHourlyCounter.hour,
                    WaterHourlyCounter.group_id,
                    WaterHourlyCounter.user_id,
                ],
                set_={
                    "msg_count": (
                        WaterHourlyCounter.msg_count + stmt.excluded.msg_count
                    ),
                },
            )
            result = await self.session.execute(stmt)
            inserted += cast(CursorResult, result).rowcount
        return inserted

    async def get_top_users(
        self,
        group_id: str,
        start_ts: int,
        end_ts: int,
        limit: int = 20,
    ) -> Sequence[Row[tuple[str, int]]]:
        _ = end_ts
        record_date = int(
            arrow.get(start_ts).to("Asia/Shanghai").floor("day").format("YYYYMMDD")
        )
        stmt = (
            select(
                WaterHourlyCounter.user_id,
                func.sum(WaterHourlyCounter.msg_count).label("count"),
            )
            .where(
                WaterHourlyCounter.group_id == group_id,
                WaterHourlyCounter.record_date == record_date,
            )
            .group_by(WaterHourlyCounter.user_id)
            .order_by(func.sum(WaterHourlyCounter.msg_count).desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.all()

    async def get_today_group_rank(
        self, group_id: str, start_ts: int, end_ts: int
    ) -> int:
        _ = end_ts
        record_date = int(
            arrow.get(start_ts).to("Asia/Shanghai").floor("day").format("YYYYMMDD")
        )
        stmt = (
            select(WaterHourlyCounter.group_id)
            .where(
                WaterHourlyCounter.record_date == record_date,
            )
            .group_by(WaterHourlyCounter.group_id)
            .order_by(func.sum(WaterHourlyCounter.msg_count).desc())
        )
        result = await self.session.execute(stmt)
        groups = result.scalars().all()
        return groups.index(group_id) + 1 if group_id in groups else 999

    async def get_users_timestamps(
        self, group_id: str, user_ids: list[str], start_ts: int, end_ts: int
    ) -> Sequence[Row[tuple[str, int, int]]]:
        _ = end_ts
        record_date = int(
            arrow.get(start_ts).to("Asia/Shanghai").floor("day").format("YYYYMMDD")
        )
        stmt = select(
            WaterHourlyCounter.user_id,
            WaterHourlyCounter.hour,
            WaterHourlyCounter.msg_count,
        ).where(
            WaterHourlyCounter.group_id == group_id,
            WaterHourlyCounter.user_id.in_(user_ids),
            WaterHourlyCounter.record_date == record_date,
        )
        result = await self.session.execute(stmt)
        return result.all()

    async def aggregate_daily_stats(
        self,
        start_ts: int,
        end_ts: int,
    ) -> Sequence[Row[tuple[str, str, int, int]]]:
        """聚合日计数 -> (group_id, user_id, msg_count, active_hours)."""
        _ = end_ts
        record_date = int(
            arrow.get(start_ts).to("Asia/Shanghai").floor("day").format("YYYYMMDD")
        )
        stmt = (
            select(
                WaterHourlyCounter.group_id,
                WaterHourlyCounter.user_id,
                func.sum(WaterHourlyCounter.msg_count).label("msg_count"),
                func.count(WaterHourlyCounter.hour).label("active_hours"),
            )
            .where(
                WaterHourlyCounter.record_date == record_date,
            )
            .group_by(
                WaterHourlyCounter.group_id,
                WaterHourlyCounter.user_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.all()

    async def aggregate_daily_hourly_stats(
        self,
        start_ts: int,
        end_ts: int,
    ) -> Sequence[tuple[str, str, int, int]]:
        """聚合日计数 -> (group_id, user_id, hour, count)."""
        _ = end_ts
        record_date = int(
            arrow.get(start_ts).to("Asia/Shanghai").floor("day").format("YYYYMMDD")
        )
        stmt = (
            select(
                WaterHourlyCounter.group_id,
                WaterHourlyCounter.user_id,
                WaterHourlyCounter.hour.label("hour"),
                WaterHourlyCounter.msg_count.label("msg_count"),
            )
            .where(
                WaterHourlyCounter.record_date == record_date,
            )
            .group_by(
                WaterHourlyCounter.group_id,
                WaterHourlyCounter.user_id,
                WaterHourlyCounter.hour,
            )
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        return [
            (group_id, user_id, int(hour), msg_count)
            for group_id, user_id, hour, msg_count in rows
        ]

    async def prune_before(self, before_ts: int) -> int:
        before_date = int(
            arrow.get(before_ts).to("Asia/Shanghai").floor("day").format("YYYYMMDD")
        )
        stmt = delete(WaterHourlyCounter).where(
            WaterHourlyCounter.record_date < before_date
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount


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
        result = await self.session.execute(stmt.order_by(self.model.record_date.asc()))
        return [self._serialize_summary_row(row) for row in result.scalars().all()]

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
        ).where(
            self.model.group_id == group_id,
        )
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
                    and_(
                        grouped.c.total == own_total,
                        grouped.c.group_id < group_id,
                    ),
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


class WaterLevelOps:
    """资产升级相关的聚合写入。"""

    _READ_CHUNK_SIZE = 400

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _personal_level(exp: int) -> int:
        return max(1, floor(sqrt(max(0, exp) / 100)))

    @staticmethod
    def _matrix_level(exp: int) -> int:
        return max(1, floor(sqrt(max(0, exp) / 2000)))

    async def get_matrix_levels(
        self, keys: list[tuple[str, str]]
    ) -> dict[tuple[str, str], tuple[int, int, int]]:
        if not keys:
            return {}
        rows: dict[tuple[str, str], tuple[int, int, int]] = {}
        for chunk in split_list(keys, self._READ_CHUNK_SIZE):
            conditions = [
                (WaterMatrixLevel.matrix_id == matrix_id)
                & (WaterMatrixLevel.user_id == user_id)
                for matrix_id, user_id in chunk
            ]
            stmt = select(
                WaterMatrixLevel.matrix_id,
                WaterMatrixLevel.user_id,
                WaterMatrixLevel.exp,
                WaterMatrixLevel.season_exp,
                WaterMatrixLevel.level,
            ).where(or_(*conditions))
            result = await self.session.execute(stmt)
            rows.update(
                {
                    (matrix_id, user_id): (exp, season_exp, level)
                    for matrix_id, user_id, exp, season_exp, level in result.all()
                }
            )
        return rows

    async def get_matrix_level(
        self,
        matrix_id: str,
        user_id: str,
    ) -> tuple[int, int, int] | None:
        stmt = select(
            WaterMatrixLevel.exp,
            WaterMatrixLevel.season_exp,
            WaterMatrixLevel.level,
        ).where(
            WaterMatrixLevel.matrix_id == matrix_id,
            WaterMatrixLevel.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return (row[0], row[1], row[2])

    async def get_global_levels(
        self, user_ids: list[str]
    ) -> dict[str, tuple[int, int, int]]:
        if not user_ids:
            return {}
        rows: dict[str, tuple[int, int, int]] = {}
        for chunk in split_list(user_ids, self._READ_CHUNK_SIZE):
            stmt = select(
                WaterGlobalLevel.user_id,
                WaterGlobalLevel.exp,
                WaterGlobalLevel.season_exp,
                WaterGlobalLevel.level,
            ).where(WaterGlobalLevel.user_id.in_(chunk))
            result = await self.session.execute(stmt)
            rows.update(
                {
                    user_id: (exp, season_exp, level)
                    for user_id, exp, season_exp, level in result.all()
                }
            )
        return rows

    async def get_global_level(self, user_id: str) -> tuple[int, int, int] | None:
        stmt = select(
            WaterGlobalLevel.exp,
            WaterGlobalLevel.season_exp,
            WaterGlobalLevel.level,
        ).where(WaterGlobalLevel.user_id == user_id)
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return (row[0], row[1], row[2])

    async def get_user_global_rank(self, user_id: str) -> int | None:
        own_stmt = select(WaterGlobalLevel.exp).where(
            WaterGlobalLevel.user_id == user_id
        )
        own_result = await self.session.execute(own_stmt)
        own_exp = own_result.scalar()
        if own_exp is None:
            return None

        rank_stmt = select(func.count(WaterGlobalLevel.user_id)).where(
            or_(
                WaterGlobalLevel.exp > own_exp,
                and_(
                    WaterGlobalLevel.exp == own_exp,
                    WaterGlobalLevel.user_id < user_id,
                ),
            )
        )
        rank_result = await self.session.execute(rank_stmt)
        higher_count = int(rank_result.scalar() or 0)
        return higher_count + 1

    async def exists_other_global_lv10(self, user_id: str) -> bool:
        stmt = (
            select(func.count(WaterGlobalLevel.user_id))
            .where(
                WaterGlobalLevel.level >= 10,
                WaterGlobalLevel.user_id != user_id,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return (result.scalar() or 0) > 0

    async def get_matrix_totals(
        self, matrix_ids: list[str]
    ) -> dict[str, tuple[int, int, int]]:
        if not matrix_ids:
            return {}
        rows: dict[str, tuple[int, int, int]] = {}
        for chunk in split_list(matrix_ids, self._READ_CHUNK_SIZE):
            stmt = select(
                WaterMatrixTotalLevel.matrix_id,
                WaterMatrixTotalLevel.exp,
                WaterMatrixTotalLevel.season_exp,
                WaterMatrixTotalLevel.level,
            ).where(WaterMatrixTotalLevel.matrix_id.in_(chunk))
            result = await self.session.execute(stmt)
            rows.update(
                {
                    mid: (exp, season_exp, level)
                    for mid, exp, season_exp, level in result.all()
                }
            )
        return rows

    async def get_matrix_total(self, matrix_id: str) -> tuple[int, int, int] | None:
        stmt = select(
            WaterMatrixTotalLevel.exp,
            WaterMatrixTotalLevel.season_exp,
            WaterMatrixTotalLevel.level,
        ).where(WaterMatrixTotalLevel.matrix_id == matrix_id)
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return (row[0], row[1], row[2])

    async def get_matrix_rank(self, matrix_id: str) -> int | None:
        own_stmt = select(WaterMatrixTotalLevel.exp).where(
            WaterMatrixTotalLevel.matrix_id == matrix_id
        )
        own_result = await self.session.execute(own_stmt)
        own_exp = own_result.scalar()
        if own_exp is None:
            return None

        rank_stmt = select(func.count(WaterMatrixTotalLevel.matrix_id)).where(
            or_(
                WaterMatrixTotalLevel.exp > own_exp,
                and_(
                    WaterMatrixTotalLevel.exp == own_exp,
                    WaterMatrixTotalLevel.matrix_id < matrix_id,
                ),
            )
        )
        rank_result = await self.session.execute(rank_stmt)
        higher_count = int(rank_result.scalar() or 0)
        return higher_count + 1

    async def get_user_matrix_rank(self, matrix_id: str, user_id: str) -> int | None:
        own_stmt = select(WaterMatrixLevel.exp).where(
            WaterMatrixLevel.matrix_id == matrix_id,
            WaterMatrixLevel.user_id == user_id,
        )
        own_result = await self.session.execute(own_stmt)
        own_exp = own_result.scalar()
        if own_exp is None:
            return None

        rank_stmt = select(func.count(WaterMatrixLevel.id)).where(
            WaterMatrixLevel.matrix_id == matrix_id,
            or_(
                WaterMatrixLevel.exp > own_exp,
                and_(
                    WaterMatrixLevel.exp == own_exp,
                    WaterMatrixLevel.user_id < user_id,
                ),
            ),
        )
        rank_result = await self.session.execute(rank_stmt)
        higher_count = int(rank_result.scalar() or 0)
        return higher_count + 1

    async def upsert_matrix_levels(self, data: list[WaterUserExpPayload]) -> int:
        if not data:
            return 0
        stmt = sqlite_insert(WaterMatrixLevel).values(
            [
                {
                    "matrix_id": item["matrix_id"],
                    "user_id": item["user_id"],
                    "exp": max(0, item["delta_exp"]),
                    "season_exp": max(0, item["delta_season_exp"]),
                    "level": self._personal_level(item["delta_exp"]),
                    "active_days": 0,
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                }
                for item in data
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[WaterMatrixLevel.matrix_id, WaterMatrixLevel.user_id],
            set_={
                "exp": stmt.excluded.exp,
                "season_exp": stmt.excluded.season_exp,
                "level": stmt.excluded.level,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def upsert_global_levels(self, data: list[WaterUserExpPayload]) -> int:
        if not data:
            return 0
        stmt = sqlite_insert(WaterGlobalLevel).values(
            [
                {
                    "user_id": item["user_id"],
                    "exp": max(0, item["delta_exp"]),
                    "season_exp": max(0, item["delta_season_exp"]),
                    "level": self._personal_level(item["delta_exp"]),
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                }
                for item in data
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[WaterGlobalLevel.user_id],
            set_={
                "exp": stmt.excluded.exp,
                "season_exp": stmt.excluded.season_exp,
                "level": stmt.excluded.level,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def upsert_matrix_totals(self, data: list[WaterMatrixExpPayload]) -> int:
        if not data:
            return 0
        stmt = sqlite_insert(WaterMatrixTotalLevel).values(
            [
                {
                    "matrix_id": item["matrix_id"],
                    "exp": max(0, item["delta_exp"]),
                    "season_exp": max(0, item["delta_season_exp"]),
                    "level": self._matrix_level(item["delta_exp"]),
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                }
                for item in data
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[WaterMatrixTotalLevel.matrix_id],
            set_={
                "exp": stmt.excluded.exp,
                "season_exp": stmt.excluded.season_exp,
                "level": stmt.excluded.level,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def apply_exp_deduction_matrix(
        self,
        matrix_id: str,
        user_id: str,
        delta: int,
    ) -> int:
        stmt = (
            update(WaterMatrixLevel)
            .where(
                WaterMatrixLevel.matrix_id == matrix_id,
                WaterMatrixLevel.user_id == user_id,
            )
            .values(
                exp=func.max(0, WaterMatrixLevel.exp - abs(delta)),
                season_exp=func.max(0, WaterMatrixLevel.season_exp - abs(delta)),
            )
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def apply_exp_compensation_matrix(
        self,
        matrix_id: str,
        user_id: str,
        delta: int,
    ) -> int:
        gain = abs(delta)
        stmt = (
            update(WaterMatrixLevel)
            .where(
                WaterMatrixLevel.matrix_id == matrix_id,
                WaterMatrixLevel.user_id == user_id,
            )
            .values(
                exp=WaterMatrixLevel.exp + gain,
                season_exp=WaterMatrixLevel.season_exp + gain,
            )
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount


class WaterPenaltyOps(BaseOps[WaterPenaltyLog]):
    async def insert_penalty_logs(self, data: list[WaterPenaltyPayload]) -> int:
        if not data:
            return 0
        stmt = sqlite_insert(WaterPenaltyLog).values(data)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_penalty_by_id(self, penalty_id: int) -> WaterPenaltyLog | None:
        return await self.session.get(WaterPenaltyLog, penalty_id)

    async def get_user_penalties_by_date(
        self,
        user_id: str,
        record_date: int,
    ) -> list[WaterPenaltyLog]:
        stmt = select(WaterPenaltyLog).where(
            WaterPenaltyLog.user_id == user_id,
            WaterPenaltyLog.record_date == record_date,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def revoke_penalty(self, penalty_id: int, revoked_at: int) -> int:
        stmt = (
            update(WaterPenaltyLog)
            .where(WaterPenaltyLog.id == penalty_id, WaterPenaltyLog.is_revoked == 0)
            .values(is_revoked=1, revoked_at=revoked_at, updated_at=revoked_at)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount


class WaterSettlementJobOps(BaseOps[WaterSettlementJob]):
    async def ensure_job(self, payload: WaterSettlementJobPayload) -> int:
        stmt = sqlite_insert(WaterSettlementJob).values(payload)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[WaterSettlementJob.record_date]
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_job(self, record_date: int) -> WaterSettlementJob | None:
        return await self.session.get(WaterSettlementJob, record_date)

    async def try_start_job(
        self,
        record_date: int,
        now_ts: int,
        stale_after: int,
        force: bool = False,
    ) -> bool:
        await self.ensure_job(
            {
                "record_date": record_date,
                "status": "pending",
                "started_at": 0,
                "finished_at": 0,
                "error": "",
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        stale_before = max(0, now_ts - stale_after)
        if force:
            stmt = (
                update(WaterSettlementJob)
                .where(WaterSettlementJob.record_date == record_date)
                .values(
                    status="running",
                    started_at=now_ts,
                    finished_at=0,
                    error="",
                    updated_at=now_ts,
                )
            )
        else:
            stmt = (
                update(WaterSettlementJob)
                .where(
                    WaterSettlementJob.record_date == record_date,
                    WaterSettlementJob.status != "success",
                    (
                        (WaterSettlementJob.status.in_(["pending", "failed"]))
                        | (
                            (WaterSettlementJob.status == "running")
                            & (WaterSettlementJob.started_at <= stale_before)
                        )
                    ),
                )
                .values(
                    status="running",
                    started_at=now_ts,
                    finished_at=0,
                    error="",
                    updated_at=now_ts,
                )
            )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0

    async def mark_success(self, record_date: int, now_ts: int) -> int:
        stmt = (
            update(WaterSettlementJob)
            .where(WaterSettlementJob.record_date == record_date)
            .values(
                status="success",
                finished_at=now_ts,
                error="",
                updated_at=now_ts,
            )
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def mark_failed(self, record_date: int, now_ts: int, error: str) -> int:
        stmt = (
            update(WaterSettlementJob)
            .where(WaterSettlementJob.record_date == record_date)
            .values(
                status="failed",
                finished_at=now_ts,
                error=error[:2000],
                updated_at=now_ts,
            )
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_latest_job(self) -> WaterSettlementJob | None:
        stmt = (
            select(WaterSettlementJob)
            .order_by(WaterSettlementJob.record_date.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def get_last_success_record_date(self) -> int:
        stmt = select(func.max(WaterSettlementJob.record_date)).where(
            WaterSettlementJob.status == "success"
        )
        result = await self.session.execute(stmt)
        return int(result.scalar() or 0)


class WaterMatrixMergeStateOps(BaseOps[WaterMatrixMergeState]):
    async def ensure_row(self, payload: WaterMatrixMergeStatePayload) -> int:
        stmt = sqlite_insert(WaterMatrixMergeState).values(payload)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[WaterMatrixMergeState.group_id]
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_state(self, group_id: str) -> WaterMatrixMergeState | None:
        return await self.session.get(WaterMatrixMergeState, group_id)

    async def mark_first_seen(self, group_id: str, now_ts: int) -> bool:
        await self.ensure_row(
            {
                "group_id": group_id,
                "first_seen_at": None,
                "is_ignored": 0,
                "status": "",
                "target_matrix_id": "",
                "operator_id": "",
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        stmt = (
            update(WaterMatrixMergeState)
            .where(
                WaterMatrixMergeState.group_id == group_id,
                WaterMatrixMergeState.first_seen_at.is_(None),
            )
            .values(first_seen_at=now_ts, updated_at=now_ts)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0

    async def get_first_seen_groups(self) -> set[str]:
        stmt = select(WaterMatrixMergeState.group_id).where(
            WaterMatrixMergeState.first_seen_at.is_not(None)
        )
        result = await self.session.execute(stmt)
        return {str(group_id) for group_id in result.scalars().all()}

    async def set_ignored(self, group_id: str, now_ts: int) -> bool:
        await self.ensure_row(
            {
                "group_id": group_id,
                "first_seen_at": None,
                "is_ignored": 0,
                "status": "",
                "target_matrix_id": "",
                "operator_id": "",
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        stmt = (
            update(WaterMatrixMergeState)
            .where(
                WaterMatrixMergeState.group_id == group_id,
                WaterMatrixMergeState.is_ignored == 0,
            )
            .values(is_ignored=1, updated_at=now_ts)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0

    async def get_ignored_groups(self) -> set[str]:
        stmt = select(WaterMatrixMergeState.group_id).where(
            WaterMatrixMergeState.is_ignored == 1
        )
        result = await self.session.execute(stmt)
        return {str(group_id) for group_id in result.scalars().all()}

    async def set_pending_target(
        self,
        group_id: str,
        target_matrix_id: str,
        now_ts: int,
    ) -> int:
        await self.ensure_row(
            {
                "group_id": group_id,
                "first_seen_at": None,
                "is_ignored": 0,
                "status": "",
                "target_matrix_id": "",
                "operator_id": "",
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        stmt = (
            update(WaterMatrixMergeState)
            .where(WaterMatrixMergeState.group_id == group_id)
            .values(
                status="pending",
                target_matrix_id=target_matrix_id,
                operator_id="",
                updated_at=now_ts,
            )
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def set_intention_once(
        self,
        group_id: str,
        action: str,
        operator_id: str,
        now_ts: int,
        target_matrix_id: str | None = None,
    ) -> bool:
        await self.ensure_row(
            {
                "group_id": group_id,
                "first_seen_at": None,
                "is_ignored": 0,
                "status": "",
                "target_matrix_id": "",
                "operator_id": "",
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        values = {
            "status": action,
            "operator_id": operator_id,
            "updated_at": now_ts,
        }
        if target_matrix_id is not None:
            values["target_matrix_id"] = target_matrix_id

        stmt = (
            update(WaterMatrixMergeState)
            .where(
                WaterMatrixMergeState.group_id == group_id,
                ~WaterMatrixMergeState.status.in_(["merge", "reject"]),
            )
            .values(**values)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0


class WaterAchievementOps(BaseOps[WaterUserAchievement]):
    async def get_unlocked_items(self, user_id: str) -> list[tuple[str, str, str, int]]:
        stmt = (
            select(
                WaterUserAchievement.achievement_id,
                WaterUserAchievement.track_type,
                WaterUserAchievement.season_id,
                WaterUserAchievement.unlocked_at,
            )
            .where(WaterUserAchievement.user_id == user_id)
            .order_by(WaterUserAchievement.unlocked_at.asc())
        )
        result = await self.session.execute(stmt)
        return [
            (
                str(achievement_id),
                str(track_type),
                str(season_id),
                int(unlocked_at),
            )
            for achievement_id, track_type, season_id, unlocked_at in result.all()
        ]

    async def bulk_unlock(self, data: list[WaterAchievementPayload]) -> int:
        if not data:
            return 0
        stmt = sqlite_insert(WaterUserAchievement).values(data)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[
                WaterUserAchievement.user_id,
                WaterUserAchievement.achievement_id,
                WaterUserAchievement.season_id,
            ]
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount


class WaterActivitySeasonOps(BaseOps[WaterActivitySeason]):
    async def create(self, payload: WaterActivitySeasonPayload) -> int:
        stmt = sqlite_insert(WaterActivitySeason).values(payload)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_by_season_id(self, season_id: str) -> WaterActivitySeason | None:
        return await self.session.get(WaterActivitySeason, season_id)

    async def update(
        self,
        season_id: str,
        **values: object,
    ) -> int:
        stmt = (
            update(WaterActivitySeason)
            .where(WaterActivitySeason.season_id == season_id)
            .values(**values)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def delete(self, season_id: str) -> int:
        stmt = delete(WaterActivitySeason).where(
            WaterActivitySeason.season_id == season_id
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def list_by_status(
        self,
        statuses: list[str] | None = None,
    ) -> list[WaterActivitySeason]:
        stmt = select(WaterActivitySeason)
        if statuses:
            stmt = stmt.where(WaterActivitySeason.status.in_(statuses))
        stmt = stmt.order_by(
            WaterActivitySeason.start_date.desc(),
            WaterActivitySeason.season_id.asc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_current_published(self, today: int) -> list[WaterActivitySeason]:
        stmt = (
            select(WaterActivitySeason)
            .where(
                WaterActivitySeason.status == "published",
                WaterActivitySeason.start_date <= today,
                WaterActivitySeason.end_date >= today,
            )
            .order_by(
                WaterActivitySeason.start_date.asc(),
                WaterActivitySeason.season_id.asc(),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def search_published_candidates(
        self, keyword: str
    ) -> list[WaterActivitySeason]:
        like_pattern = f"%{keyword}%"
        stmt = (
            select(WaterActivitySeason)
            .where(
                WaterActivitySeason.status == "published",
                or_(
                    WaterActivitySeason.season_id == keyword,
                    WaterActivitySeason.name == keyword,
                    WaterActivitySeason.normalized_name == keyword,
                    WaterActivitySeason.season_id.like(like_pattern),
                    WaterActivitySeason.name.like(like_pattern),
                    WaterActivitySeason.normalized_name.like(like_pattern),
                ),
            )
            .order_by(
                WaterActivitySeason.start_date.desc(),
                WaterActivitySeason.season_id.asc(),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
