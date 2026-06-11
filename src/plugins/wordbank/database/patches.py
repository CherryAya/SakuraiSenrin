from __future__ import annotations

from typing import Any, cast

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


async def _reset_wordbank_message_tables(session: AsyncSession) -> None:
    from .tables import (
        WordbankApprovalMessage,
        WordbankDeleteVote,
        WordbankDeleteVoteSupport,
        WordbankResponseItem,
        WordbankResponseMessage,
        WordbankSearchDocument,
        WordbankSearchImageMap,
        WordbankTriggerGroup,
        WordbankTriggerVariant,
    )

    table_names = (
        "wordbank_search_trigger_fts",
        "wordbank_search_response_fts",
        "wordbank_entry",
        "wordbank_trigger",
        "wordbank_response",
        WordbankApprovalMessage.__tablename__,
        WordbankResponseMessage.__tablename__,
        WordbankDeleteVoteSupport.__tablename__,
        WordbankDeleteVote.__tablename__,
        WordbankSearchDocument.__tablename__,
        WordbankSearchImageMap.__tablename__,
        WordbankResponseItem.__tablename__,
        WordbankTriggerVariant.__tablename__,
        WordbankTriggerGroup.__tablename__,
    )
    for table_name in table_names:
        await session.execute(text(f"DROP TABLE IF EXISTS {table_name}"))

    connection = await session.connection()
    for table in (
        WordbankTriggerGroup.__table__,
        WordbankTriggerVariant.__table__,
        WordbankResponseItem.__table__,
        WordbankSearchDocument.__table__,
        WordbankSearchImageMap.__table__,
        WordbankDeleteVote.__table__,
        WordbankDeleteVoteSupport.__table__,
        WordbankResponseMessage.__table__,
        WordbankApprovalMessage.__table__,
    ):
        await connection.run_sync(
            lambda sync_conn, current_table=table: cast(Any, current_table).create(
                sync_conn,
                checkfirst=True,
            )
        )
    await _create_wordbank_search_fts_tables(session)


def build_wordbank_patch_registry() -> PatchRegistry:
    registry = PatchRegistry()
    registry.register(
        SchemaPatch(
            patch_id="wordbank:reset_group_tables:v3",
            apply=_reset_wordbank_message_tables,
        )
    )
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
