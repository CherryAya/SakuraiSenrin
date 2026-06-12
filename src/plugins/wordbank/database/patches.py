from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.schema import PatchRegistry, SchemaPatch


async def _drop_wordbank_log_matched_text(session: AsyncSession) -> None:
    columns = await session.execute(text("PRAGMA table_info(wordbank_log)"))
    column_names = {str(row[1]) for row in columns.all()}
    if "matched_text" not in column_names:
        return
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS wordbank_log_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_group_id INTEGER NOT NULL,
                trigger_variant_id INTEGER NOT NULL,
                response_item_id INTEGER NOT NULL,
                group_id VARCHAR(64) NOT NULL DEFAULT '',
                user_id VARCHAR(64) NOT NULL DEFAULT '',
                message_type VARCHAR(16) NOT NULL DEFAULT 'text',
                created_at INTEGER NOT NULL
            )
            """
        )
    )
    await session.execute(
        text(
            """
            INSERT INTO wordbank_log_new (
                id,
                trigger_group_id,
                trigger_variant_id,
                response_item_id,
                group_id,
                user_id,
                message_type,
                created_at
            )
            SELECT
                id,
                trigger_group_id,
                trigger_variant_id,
                response_item_id,
                group_id,
                user_id,
                message_type,
                created_at
            FROM wordbank_log
            """
        )
    )
    await session.execute(text("DROP TABLE wordbank_log"))
    await session.execute(text("ALTER TABLE wordbank_log_new RENAME TO wordbank_log"))
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_wordbank_log_response_time
            ON wordbank_log (response_item_id, created_at)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_wordbank_log_group_time
            ON wordbank_log (group_id, created_at)
            """
        )
    )


def build_wordbank_log_patch_registry() -> PatchRegistry:
    registry = PatchRegistry()
    registry.register(
        SchemaPatch(
            patch_id="wordbank_log:drop_matched_text:v1",
            apply=_drop_wordbank_log_matched_text,
        )
    )
    return registry
