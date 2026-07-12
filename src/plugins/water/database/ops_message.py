"""Water message shard operations."""

from collections.abc import Sequence
from typing import cast

import arrow
from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine.row import Row
from sqlalchemy.sql import text

from src.lib.db.ops import BaseOps
from src.lib.utils.common import split_list

from .tables import WaterHourlyCounter
from .types import WaterMessagePayload

_DEFAULT_SQLITE_MAX_VARIABLE_NUMBER = 999
_WATER_MESSAGE_INSERT_MARGIN = 16
_WATER_MESSAGE_BIND_PARAMS = len(WaterMessagePayload.__annotations__)
_sqlite_max_variable_number: int | None = None


class WaterMessageOps(BaseOps[WaterHourlyCounter]):
    async def _resolve_message_insert_chunk_size(self) -> int:
        global _sqlite_max_variable_number

        from . import ops as ops_module

        exported_limit = getattr(ops_module, "_sqlite_max_variable_number", None)
        if isinstance(exported_limit, int):
            _sqlite_max_variable_number = exported_limit

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
            setattr(ops_module, "_sqlite_max_variable_number", max_variables)

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
            .where(WaterHourlyCounter.record_date == record_date)
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
            .where(WaterHourlyCounter.record_date == record_date)
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
            .where(WaterHourlyCounter.record_date == record_date)
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
