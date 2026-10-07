"""死信落库与补偿重放。

`BatchWriter` 在超过 `max_retries` 后会把批次放进内存死信队列。上一轮补了可观测
（health / 脚本 / 告警），但仍有三个缺口：

1. **重启即丢** —— 死信只在内存里，进程退出后无从回溯
2. **无上界泄漏** —— 持续失败会让队列无限增长
3. **无法补偿** —— 队列里的数据只能人工捞日志

这里把死信落库到 ``core_db.sys_dead_letter``，并提供按 worker / 时间范围的重放
入口。上界裁剪（`max_dead_letters`）保证内存不会无限增长，落库记录则是完整的。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.database.core.tables import DeadLetter
from src.database.instances import core_db
from src.lib.db.batch import DeadLetterRecord
from src.lib.db.ops import BaseOps
from src.lib.trace_log import log_trace_event
from src.lib.utils.common import get_current_time
from src.logger import logger

# 落库保留上限：超过后清理最旧的已落库记录，防止 core_db 无限增长
DEFAULT_DEAD_LETTER_KEEP = 2000


class DeadLetterOps(BaseOps[DeadLetter]):
    async def create(self, record: DeadLetterRecord[object]) -> int:
        stmt = sqlite_insert(DeadLetter).values(
            {
                "worker_name": record.worker_name,
                "error": record.error,
                "attempts": record.attempts,
                "item_count": len(record.batch),
                "payload": list(record.batch),
                "resolved": 0,
                "created_at": record.failed_at,
                "updated_at": record.failed_at,
            },
        )
        await self.session.execute(stmt)
        return 1

    async def list_unresolved(
        self,
        *,
        worker_name: str | None = None,
        limit: int = 100,
    ) -> list[DeadLetter]:
        stmt = (
            select(DeadLetter)
            .where(DeadLetter.resolved == 0)
            .order_by(DeadLetter.created_at.asc())
            .limit(limit)
        )
        if worker_name is not None:
            stmt = stmt.where(DeadLetter.worker_name == worker_name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_resolved(self, dead_letter_ids: Sequence[int], note: str) -> int:
        if not dead_letter_ids:
            return 0
        now_ts = get_current_time()
        for row_id in dead_letter_ids:
            await self.session.execute(
                update(DeadLetter)
                .where(DeadLetter.id == row_id)
                .values(
                    resolved=1,
                    resolved_at=now_ts,
                    resolved_note=note[:255],
                    updated_at=now_ts,
                )
            )
        return len(dead_letter_ids)

    async def prune_resolved(self, keep: int) -> int:
        """清理最旧的已处理记录，只保留最近 keep 条。"""
        total = await self.session.scalar(
            select(func.count()).select_from(DeadLetter).where(DeadLetter.resolved == 1)
        )
        if int(total or 0) <= keep:
            return 0
        cutoff = await self.session.scalar(
            select(DeadLetter.created_at)
            .where(DeadLetter.resolved == 1)
            .order_by(DeadLetter.created_at.desc())
            .offset(keep - 1)
            .limit(1)
        )
        if cutoff is None:
            return 0
        result = await self.session.execute(
            delete(DeadLetter).where(
                DeadLetter.resolved == 1,
                DeadLetter.created_at < int(cutoff),
            )
        )
        return int(cast(CursorResult, result).rowcount or 0)


@dataclass(slots=True, frozen=True)
class DeadLetterPersistReport:
    persisted: int
    failed: int


async def persist_dead_letters(
    records: Sequence[DeadLetterRecord[object]],
    *,
    keep: int = DEFAULT_DEAD_LETTER_KEEP,
) -> DeadLetterPersistReport:
    """把死信写入 core_db，并在超出保留上限后裁剪。"""
    if not records:
        return DeadLetterPersistReport(persisted=0, failed=0)

    persisted = 0
    try:
        async with core_db.session(commit=True) as session:
            ops = DeadLetterOps(session)
            for record in records:
                try:
                    await ops.create(record)
                    persisted += 1
                except Exception as exc:
                    logger.error(
                        f"死信落库失败 [{record.worker_name}]: {exc}",
                    )
            pruned = await ops.prune_resolved(keep)
        if pruned:
            logger.info(f"死信表已清理 {pruned} 条历史记录")
        log_trace_event(
            event_name="dead_letter_persist",
            source_kind="batch_writer",
            component="dead_letter_store",
            status="success",
            summary=f"Persisted {persisted} dead-letter batch(es).",
            payload_json={
                "persisted": persisted,
                "failed": len(records) - persisted,
                "pruned": pruned,
            },
        )
    except Exception as exc:
        logger.error(f"死信落库整体失败: {exc}")
        return DeadLetterPersistReport(persisted=0, failed=len(records))

    return DeadLetterPersistReport(
        persisted=persisted,
        failed=len(records) - persisted,
    )


async def replay_dead_letters(
    *,
    worker_name: str | None = None,
    limit: int = 100,
    handlers: dict[str, Callable[[list[object]], object]] | None = None,
) -> int:
    """按 worker 重放未处理死信，成功后标记 resolved。

    handlers 形如 ``{"_flush_water_logs": callable}``；未提供 handler 的记录会跳过
    并保持未处理状态，便于后续补齐。
    """
    handlers = handlers or {}
    async with core_db.session(commit=False) as session:
        records = await DeadLetterOps(session).list_unresolved(
            worker_name=worker_name,
            limit=limit,
        )
    if not records:
        return 0

    replayed = 0
    resolved_ids: list[int] = []
    for record in records:
        handler = handlers.get(record.worker_name)
        if handler is None:
            logger.warning(
                f"死信 [{record.worker_name}] 无重放 handler，暂跳过 "
                f"(id={record.id}, {record.item_count} 条)",
            )
            continue
        try:
            result = handler(record.payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.error(f"死信重放失败 [{record.worker_name}] id={record.id}: {exc}")
            continue
        resolved_ids.append(int(record.id))
        replayed += int(record.item_count)

    if resolved_ids:
        async with core_db.session(commit=True) as session:
            await DeadLetterOps(session).mark_resolved(
                resolved_ids,
                note="replayed",
            )
    logger.info(f"死信重放完成: 处理 {len(resolved_ids)} 批 / {replayed} 条")
    return replayed


__all__ = [
    "DEFAULT_DEAD_LETTER_KEEP",
    "DeadLetterOps",
    "DeadLetterPersistReport",
    "persist_dead_letters",
    "replay_dead_letters",
]
