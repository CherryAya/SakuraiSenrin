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


async def _drop_water_message_redundant_indexes(session: AsyncSession) -> None:
    await session.execute(
        text("DROP INDEX IF EXISTS idx_water_hourly_counter_date_hour")
    )
    await session.execute(
        text("DROP INDEX IF EXISTS idx_water_hourly_counter_group_user_date")
    )


async def _migrate_water_summary_hourly_counts_to_blob(session: AsyncSession) -> None:
    rows = (
        await session.execute(
            text(
                """
                SELECT group_id, user_id, record_date, hourly_counts
                FROM water_daily_summary
                """
            )
        )
    ).all()
    if not rows:
        return

    from src.plugins.water.database.hourly_counts import (
        decode_hourly_counts,
        encode_hourly_counts,
    )

    for group_id, user_id, record_date, hourly_counts in rows:
        await session.execute(
            text(
                """
                UPDATE water_daily_summary
                SET hourly_counts = :hourly_counts
                WHERE group_id = :group_id
                  AND user_id = :user_id
                  AND record_date = :record_date
                """
            ),
            {
                "group_id": group_id,
                "user_id": user_id,
                "record_date": record_date,
                "hourly_counts": encode_hourly_counts(
                    decode_hourly_counts(hourly_counts)
                ),
            },
        )


async def _add_water_group_stats_indexes(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_water_group_total_msg
            ON water_group_total (msg_count)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_water_group_user_total_group_msg
            ON water_group_user_total (group_id, msg_count)
            """
        )
    )


def build_water_message_patch_registry() -> PatchRegistry:
    registry = PatchRegistry()
    registry.register(
        SchemaPatch(
            patch_id="water_hourly_counter:add_indexes:v2",
            apply=_add_water_message_time_indexes,
        )
    )
    registry.register(
        SchemaPatch(
            patch_id="water_hourly_counter:drop_redundant_indexes:v3",
            apply=_drop_water_message_redundant_indexes,
        )
    )
    return registry


def build_water_summary_patch_registry() -> PatchRegistry:
    registry = PatchRegistry()
    registry.register(
        SchemaPatch(
            patch_id="water_daily_summary:blob_hourly_counts:v3",
            apply=_migrate_water_summary_hourly_counts_to_blob,
        )
    )
    return registry


def build_water_core_patch_registry() -> PatchRegistry:
    registry = build_water_summary_patch_registry()
    registry.register(
        SchemaPatch(
            patch_id="water_group_stats:create_indexes:v4",
            apply=_add_water_group_stats_indexes,
        )
    )
    return registry


__all__ = [
    "build_water_core_patch_registry",
    "build_water_message_patch_registry",
    "build_water_summary_patch_registry",
]
