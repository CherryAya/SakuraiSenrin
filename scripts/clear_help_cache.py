#!/usr/bin/env python3
"""清理 help 插件的消息缓存

使用场景：
- help 插件的文案或配置发生变更（如群号修改）
- 需要强制重新生成 help 消息

只清理 source_kind='help' 的缓存，不影响其他插件的 8k+ 条缓存。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast
import sys

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult

from src.lib.message_assets import MessageAsset, message_asset_db
from src.logger import logger


async def clear_help_cache() -> None:
    """清理所有 source_kind='help' 的消息缓存"""

    # 先统计数量
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

    # 执行删除
    async with message_asset_db.write_session() as session:
        result = await session.execute(
            delete(MessageAsset).where(MessageAsset.source_kind == "help")
        )
        await session.commit()
        deleted_count = cast(CursorResult, result).rowcount

    logger.info(f"[ClearCache] Successfully deleted {deleted_count} help cache records")

    # 验证删除
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
    """统计所有缓存数量"""
    async with message_asset_db.read_session() as session:
        # 按 source_kind 分组统计
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
    logger.info("=== Clear Help Cache ===")

    # 统计清理前的状态
    logger.info("[Before] Cache statistics:")
    await count_all_cache()

    # 清理 help 缓存
    await clear_help_cache()

    # 统计清理后的状态
    logger.info("[After] Cache statistics:")
    await count_all_cache()

    logger.info("=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
