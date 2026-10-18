#!/usr/bin/env python3
"""清理 help 插件的消息缓存。

沿用场景：help 文案或配置变更（如群号修改）后强制重新生成。
只清 source_kind='help' 的条目，其他插件缓存照旧。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import nonebot
from sqlalchemy import delete, select

from src.lib.db.ops import affected_rows
from src.lib.message_assets import MessageAsset, message_asset_db
from src.logger import logger


async def clear_help_cache() -> None:
    """清理所有 source_kind='help' 的消息缓存。"""
    async with message_asset_db.read_session() as session:
        count_result = await session.execute(
            select(MessageAsset).where(MessageAsset.source_kind == "help")
        )
        total_count = len(count_result.all())

    if total_count == 0:
        logger.info("[ClearCache] No help cache found, nothing to clear")
        return

    logger.info(
        f"[ClearCache] Found {total_count} help cache records, preparing to delete..."
    )

    async with message_asset_db.write_session() as session:
        result = await session.execute(
            delete(MessageAsset).where(MessageAsset.source_kind == "help")
        )
        await session.commit()
        deleted_count = affected_rows(result)

    logger.info(f"[ClearCache] Successfully deleted {deleted_count} help cache records")

    async with message_asset_db.read_session() as session:
        verify_result = await session.execute(
            select(MessageAsset).where(MessageAsset.source_kind == "help")
        )
        remaining = len(verify_result.all())

    if remaining > 0:
        logger.warning(f"[ClearCache] Still {remaining} help cache records remaining!")
    else:
        logger.info("[ClearCache] Verification passed: all help cache cleared")


async def count_all_cache() -> None:
    """统计所有缓存数量。"""
    async with message_asset_db.read_session() as session:
        result = await session.execute(
            select(MessageAsset.source_kind, MessageAsset.id)
        )
        rows = result.all()

        by_source: dict[str, int] = {}
        for row in rows:
            source = row[0] or "(empty)"
            by_source[source] = by_source.get(source, 0) + 1

        total = sum(by_source.values())
        logger.info(f"[ClearCache] Total cache records: {total}")
        for source, count in sorted(by_source.items(), key=lambda x: -x[1]):
            logger.info(f"  - {source}: {count}")


async def main() -> None:
    nonebot.init()
    logger.info("=== Clear Help Cache ===")

    logger.info("[Before] Cache statistics:")
    await count_all_cache()

    await clear_help_cache()

    logger.info("[After] Cache statistics:")
    await count_all_cache()

    logger.info("=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
