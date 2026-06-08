from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.schema import PatchRegistry, SchemaPatch


async def _add_water_message_time_indexes(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_water_message_time
            ON water_message (created_at)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_water_message_time_group_user
            ON water_message (created_at, group_id, user_id)
            """
        )
    )


def build_water_patch_registry() -> PatchRegistry:
    registry = PatchRegistry()
    registry.register(
        SchemaPatch(
            patch_id="water_message:add_time_indexes:v1",
            apply=_add_water_message_time_indexes,
        )
    )
    return registry
