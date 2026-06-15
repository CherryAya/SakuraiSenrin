from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.schema import PatchRegistry, SchemaPatch


async def _patch_wordbank_image_media_columns(session: AsyncSession) -> None:
    columns = await session.execute(text("PRAGMA table_info(wordbank_image)"))
    column_names = {str(row[1]) for row in columns.all()}
    if not column_names:
        return

    column_defs = {
        "remote_storage_path": "TEXT NOT NULL DEFAULT ''",
        "local_cache_path": "TEXT NOT NULL DEFAULT ''",
        "cache_file_size": "INTEGER NOT NULL DEFAULT 0",
        "last_accessed_at": "INTEGER NOT NULL DEFAULT 0",
        "cache_last_hit_at": "INTEGER NOT NULL DEFAULT 0",
        "remote_sync_status": "VARCHAR(16) NOT NULL DEFAULT 'pending'",
        "remote_synced_at": "INTEGER NOT NULL DEFAULT 0",
        "remote_etag": "TEXT NOT NULL DEFAULT ''",
        "remote_object_size": "INTEGER NOT NULL DEFAULT 0",
    }
    for column_name, column_def in column_defs.items():
        if column_name in column_names:
            continue
        await session.execute(
            text(f"ALTER TABLE wordbank_image ADD COLUMN {column_name} {column_def}")
        )

    await session.execute(
        text(
            """
            UPDATE wordbank_image
            SET
                remote_storage_path = CASE
                    WHEN remote_storage_path != '' THEN remote_storage_path
                    WHEN storage_path LIKE 'r2://%' OR storage_path LIKE 'github://%'
                    THEN storage_path
                    ELSE ''
                END,
                remote_sync_status = CASE
                    WHEN remote_storage_path != '' THEN remote_sync_status
                    WHEN storage_path LIKE 'r2://%' OR storage_path LIKE 'github://%'
                    THEN 'synced'
                    WHEN storage_path != '' THEN 'pending'
                    ELSE remote_sync_status
                END,
                remote_synced_at = CASE
                    WHEN remote_synced_at > 0 THEN remote_synced_at
                    WHEN storage_path LIKE 'r2://%' OR storage_path LIKE 'github://%'
                    THEN updated_at
                    ELSE remote_synced_at
                END,
                remote_object_size = CASE
                    WHEN remote_object_size > 0 THEN remote_object_size
                    WHEN storage_path LIKE 'r2://%' OR storage_path LIKE 'github://%'
                    THEN file_size
                    ELSE remote_object_size
                END
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_wordbank_image_remote_sync
            ON wordbank_image (remote_sync_status, id)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_wordbank_image_cache_lru
            ON wordbank_image (cache_last_hit_at, last_accessed_at, updated_at)
            """
        )
    )


async def _patch_wordbank_trigger_group_probability(session: AsyncSession) -> None:
    columns = await session.execute(text("PRAGMA table_info(wordbank_trigger_group)"))
    column_names = {str(row[1]) for row in columns.all()}
    if not column_names or "probability" in column_names:
        return
    await session.execute(
        text(
            """
            ALTER TABLE wordbank_trigger_group
            ADD COLUMN probability FLOAT NOT NULL DEFAULT 1.0
            """
        )
    )


async def _drop_wordbank_response_item_probability(session: AsyncSession) -> None:
    columns = await session.execute(text("PRAGMA table_info(wordbank_response_item)"))
    column_names = {str(row[1]) for row in columns.all()}
    if not column_names or "probability" not in column_names:
        return
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS wordbank_response_item_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_group_id INTEGER NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                enabled INTEGER NOT NULL DEFAULT 1,
                scope VARCHAR(32) NOT NULL,
                priority INTEGER NOT NULL,
                weight INTEGER NOT NULL DEFAULT 3,
                rule JSON NOT NULL DEFAULT '{}',
                group_id VARCHAR(64) NOT NULL DEFAULT '',
                created_by VARCHAR(64) NOT NULL,
                approved_by VARCHAR(64) NOT NULL DEFAULT '',
                deleted_at INTEGER NOT NULL DEFAULT 0,
                text TEXT NOT NULL DEFAULT '',
                message_json TEXT NOT NULL DEFAULT '[]',
                exact_md5 VARCHAR(32) NOT NULL DEFAULT '',
                structure_key TEXT NOT NULL DEFAULT '',
                search_text TEXT NOT NULL DEFAULT '',
                search_tokens TEXT NOT NULL DEFAULT '',
                image_keys TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
    )
    await session.execute(
        text(
            """
            INSERT INTO wordbank_response_item_new (
                id,
                trigger_group_id,
                status,
                enabled,
                scope,
                priority,
                weight,
                rule,
                group_id,
                created_by,
                approved_by,
                deleted_at,
                text,
                message_json,
                exact_md5,
                structure_key,
                search_text,
                search_tokens,
                image_keys,
                created_at,
                updated_at
            )
            SELECT
                id,
                trigger_group_id,
                status,
                enabled,
                scope,
                priority,
                weight,
                rule,
                group_id,
                created_by,
                approved_by,
                deleted_at,
                text,
                message_json,
                exact_md5,
                structure_key,
                search_text,
                search_tokens,
                image_keys,
                created_at,
                updated_at
            FROM wordbank_response_item
            """
        )
    )
    await session.execute(text("DROP TABLE wordbank_response_item"))
    await session.execute(
        text(
            """
            ALTER TABLE wordbank_response_item_new
            RENAME TO wordbank_response_item
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_wordbank_response_item_group_status
            ON wordbank_response_item (
                trigger_group_id,
                status,
                enabled,
                deleted_at
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_wordbank_response_item_scope
            ON wordbank_response_item (scope, group_id)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_wordbank_response_item_created_by
            ON wordbank_response_item (created_by, created_at)
            """
        )
    )


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


def build_wordbank_main_patch_registry() -> PatchRegistry:
    registry = PatchRegistry()
    registry.register(
        SchemaPatch(
            patch_id="wordbank_main:image_media_columns:v1",
            apply=_patch_wordbank_image_media_columns,
        )
    )
    registry.register(
        SchemaPatch(
            patch_id="wordbank_main:trigger_group_probability:v1",
            apply=_patch_wordbank_trigger_group_probability,
        )
    )
    registry.register(
        SchemaPatch(
            patch_id="wordbank_main:drop_response_item_probability:v1",
            apply=_drop_wordbank_response_item_probability,
        )
    )
    return registry


def build_wordbank_log_patch_registry() -> PatchRegistry:
    registry = PatchRegistry()
    registry.register(
        SchemaPatch(
            patch_id="wordbank_log:drop_matched_text:v1",
            apply=_drop_wordbank_log_matched_text,
        )
    )
    return registry
