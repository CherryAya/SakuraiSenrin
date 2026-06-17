"""Water level and ranking operations."""

from math import floor, sqrt
from typing import cast

from sqlalchemy import CursorResult, and_, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.utils.common import split_list

from .tables import WaterGlobalLevel, WaterMatrixLevel, WaterMatrixTotalLevel
from .types import WaterMatrixExpPayload, WaterUserExpPayload


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
        own_stmt = select(WaterGlobalLevel.exp).where(WaterGlobalLevel.user_id == user_id)
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
