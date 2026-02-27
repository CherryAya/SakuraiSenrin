"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 19:15:52
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 20:26:06
Description: water db ops
"""

from collections.abc import Sequence
from typing import cast

from sqlalchemy import CursorResult, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine.row import Row

from src.lib.db.ops import BaseOps

from .tables import WaterDailySummary, WaterMessage
from .types import WaterMessagePayload


class WaterMessageOps(BaseOps[WaterMessage]):
    async def bulk_insert_water_message(
        self,
        water_message_data: list[WaterMessagePayload],
    ) -> int:
        if not water_message_data:
            return 0
        stmt = sqlite_insert(WaterMessage).values(water_message_data)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_top_users(
        self,
        group_id: int,
        start_ts: int,
        end_ts: int,
        limit: int = 20,
    ) -> Sequence[Row[tuple[str, int]]]:
        stmt = (
            select(WaterMessage.user_id, func.count(WaterMessage.id).label("count"))
            .where(
                WaterMessage.group_id == group_id,
                WaterMessage.created_at >= start_ts,
                WaterMessage.created_at <= end_ts,
            )
            .group_by(WaterMessage.user_id)
            .order_by(func.count(WaterMessage.id).desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.all()


class WaterSummaryOps(BaseOps[WaterDailySummary]):
    async def bulk_upsert_summary(self, summary_data: list[dict]) -> int:
        if not summary_data:
            return 0

        stmt = sqlite_insert(WaterDailySummary).values(summary_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                WaterDailySummary.group_id,
                WaterDailySummary.user_id,
                WaterDailySummary.record_date,
            ],
            set_={"msg_count": stmt.excluded.msg_count},
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_ranks_by_date(
        self,
        group_id: int,
        record_date: int,
    ) -> dict[str, int]:
        stmt = (
            select(WaterDailySummary.user_id)
            .where(
                WaterDailySummary.group_id == group_id,
                WaterDailySummary.record_date == record_date,
            )
            .order_by(WaterDailySummary.msg_count.desc())
        )
        result = await self.session.execute(stmt)
        return {user_id: rank for rank, user_id in enumerate(result.scalars(), 1)}
