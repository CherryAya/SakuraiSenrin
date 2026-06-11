from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.schema import PatchRegistry, SchemaPatch


async def _create_wordbank_search_fts_tables(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS wordbank_search_trigger_fts
            USING fts5(tokens)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS wordbank_search_response_fts
            USING fts5(tokens)
            """
        )
    )


async def _add_wordbank_image_hash_version(session: AsyncSession) -> None:
    columns = {
        row[1]
        for row in (
            await session.execute(text("PRAGMA table_info(wordbank_image)"))
        ).all()
    }
    if "hash_version" in columns:
        return
    await session.execute(
        text(
            """
            ALTER TABLE wordbank_image
            ADD COLUMN hash_version INTEGER NOT NULL DEFAULT 1
            """
        )
    )


def build_wordbank_patch_registry() -> PatchRegistry:
    registry = PatchRegistry()
    registry.register(
        SchemaPatch(
            patch_id="wordbank:create_search_fts_tables:v1",
            apply=_create_wordbank_search_fts_tables,
        )
    )
    registry.register(
        SchemaPatch(
            patch_id="wordbank:add_image_hash_version:v1",
            apply=_add_wordbank_image_hash_version,
        )
    )
    return registry
