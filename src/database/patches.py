from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.schema import PatchRegistry, SchemaPatch


async def _add_group_locale_setting(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS biz_group_locale_setting (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id VARCHAR(32) NOT NULL,
                locale VARCHAR(32) NOT NULL,
                last_operator_id VARCHAR(32),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(group_id) REFERENCES biz_group(group_id) ON DELETE CASCADE
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_group_locale_setting
            ON biz_group_locale_setting (group_id)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_group_locale
            ON biz_group_locale_setting (group_id, locale)
            """
        )
    )


async def _add_invitation_sub_type(session: AsyncSession) -> None:
    pragma_result = await session.execute(text("PRAGMA table_info(biz_invitation)"))
    columns = {str(row[1]) for row in pragma_result.fetchall()}
    if "sub_type" in columns:
        return
    await session.execute(
        text(
            """
            ALTER TABLE biz_invitation
            ADD COLUMN sub_type VARCHAR(16) NOT NULL DEFAULT 'invite'
            """
        )
    )


async def _add_group_pre_ban_status(session: AsyncSession) -> None:
    pragma_result = await session.execute(text("PRAGMA table_info(biz_group)"))
    columns = {str(row[1]) for row in pragma_result.fetchall()}
    if "pre_ban_status" in columns:
        return
    await session.execute(
        text(
            """
            ALTER TABLE biz_group
            ADD COLUMN pre_ban_status VARCHAR(16)
            """
        )
    )


async def _add_dead_letter_table(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS sys_dead_letter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_name VARCHAR(64) NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0,
                item_count INTEGER NOT NULL DEFAULT 0,
                payload JSON NOT NULL DEFAULT '[]',
                resolved INTEGER NOT NULL DEFAULT 0,
                resolved_at INTEGER,
                resolved_note VARCHAR(255),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_dead_letter_worker_created
            ON sys_dead_letter (worker_name, created_at)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_dead_letter_unresolved
            ON sys_dead_letter (resolved, created_at)
            """
        )
    )


def build_core_patch_registry() -> PatchRegistry:
    registry = PatchRegistry()
    registry.register(
        SchemaPatch(
            patch_id="core:add_dead_letter:v1",
            apply=_add_dead_letter_table,
        )
    )
    registry.register(
        SchemaPatch(
            patch_id="core:add_group_locale_setting:v1",
            apply=_add_group_locale_setting,
        )
    )
    registry.register(
        SchemaPatch(
            patch_id="core:add_invitation_sub_type:v1",
            apply=_add_invitation_sub_type,
        )
    )
    registry.register(
        SchemaPatch(
            patch_id="core:add_group_pre_ban_status:v1",
            apply=_add_group_pre_ban_status,
        )
    )
    return registry


def build_log_patch_registry() -> PatchRegistry:
    return PatchRegistry()


def build_snapshot_patch_registry() -> PatchRegistry:
    return PatchRegistry()
