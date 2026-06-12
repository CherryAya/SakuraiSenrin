from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.schema import PatchRegistry, SchemaPatch


async def _add_water_message_time_indexes(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_water_hourly_counter_date_hour
            ON water_hourly_counter (record_date, hour)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_water_hourly_counter_date_group_user
            ON water_hourly_counter (record_date, group_id, user_id)
            """
        )
    )


def build_water_patch_registry() -> PatchRegistry:
    registry = PatchRegistry()
    registry.register(
        SchemaPatch(
            patch_id="water_hourly_counter:add_indexes:v2",
            apply=_add_water_message_time_indexes,
        )
    )
    return registry
