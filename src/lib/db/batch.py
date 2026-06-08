"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-08 17:18:19
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-06-08 16:00:00
Description: 批量处理器
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import arrow
from loguru import logger

from src.lib.utils.common import get_current_time

if TYPE_CHECKING:
    from datetime import datetime

    from .connectors import ShardedDB
    from .ops import BaseOps


@dataclass(slots=True, frozen=True)
class DeadLetterRecord[T]:
    worker_name: str
    batch: tuple[T, ...]
    error: str
    failed_at: int
    attempts: int


@dataclass(slots=True, frozen=True)
class FlushResult[T]:
    flushed: int
    attempts: int
    batch: tuple[T, ...]


@dataclass(slots=True, frozen=True)
class BufferedWriterConfig[T]:
    batch_size: int = 100
    flush_interval: float = 3.0
    max_retries: int = 3
    retry_backoff: float = 0.2
    dedupe_key: Callable[[T], str] | None = None


class BatchWriter[T]:
    """通用内存缓冲写入器。"""

    def __init__(
        self,
        flush_callback: Callable[[list[T]], Awaitable[None]],
        batch_size: int = 100,
        flush_interval: float = 3.0,
        *,
        max_retries: int = 3,
        retry_backoff: float = 0.2,
        dedupe_key: Callable[[T], str] | None = None,
    ) -> None:
        self.queue: asyncio.Queue[T] = asyncio.Queue()
        self.flush_callback = flush_callback
        self.config = BufferedWriterConfig(
            batch_size=batch_size,
            flush_interval=flush_interval,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            dedupe_key=dedupe_key,
        )
        self._task: asyncio.Task[None] | None = None
        self._worker_name: str | None = None
        self._closed = False
        self._dead_letters: list[DeadLetterRecord[T]] = []
        self._buffer: list[T] = []
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._is_flushing = False
        self._last_error: Exception | None = None

    @property
    def worker_name(self) -> str:
        return self._worker_name or getattr(self.flush_callback, "__name__", "Unknown")

    @property
    def dead_letters(self) -> tuple[DeadLetterRecord[T], ...]:
        return tuple(self._dead_letters)

    def _ensure_worker_running(self) -> None:
        if self._closed:
            raise RuntimeError(f"BatchWriter [{self.worker_name}] has been closed")
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._worker())
            self._worker_name = getattr(self.flush_callback, "__name__", "Unknown")
            logger.debug(f"BatchWriter worker [{self._worker_name}] started/restarted.")

    def _mark_busy(self) -> None:
        self._idle_event.clear()

    def _mark_idle_if_needed(self) -> None:
        if self.queue.empty() and not self._buffer and not self._is_flushing:
            self._idle_event.set()

    def _normalize_batch(self, buffer: Sequence[T]) -> list[T]:
        if self.config.dedupe_key is None:
            return list(buffer)
        deduped: dict[str, T] = {}
        for item in buffer:
            deduped[self.config.dedupe_key(item)] = item
        return list(deduped.values())

    async def add(self, item: T) -> None:
        self._ensure_worker_running()
        self._mark_busy()
        await self.queue.put(item)

    async def add_all(self, items: list[T]) -> None:
        if not items:
            return
        self._ensure_worker_running()
        self._mark_busy()
        for item in items:
            self.queue.put_nowait(item)

    async def drain(self) -> None:
        self._ensure_worker_running()
        while True:
            self._mark_idle_if_needed()
            if self.queue.empty() and not self._buffer and not self._is_flushing:
                if self._last_error is not None:
                    err = self._last_error
                    self._last_error = None
                    raise err
                return
            await self._idle_event.wait()

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._mark_idle_if_needed()

    async def _worker(self) -> None:
        loop = asyncio.get_running_loop()
        last_flush = loop.time()

        while True:
            try:
                timeout = self.config.flush_interval - (loop.time() - last_flush)
                timeout = max(0.1, timeout)
                item = await asyncio.wait_for(self.queue.get(), timeout=timeout)
                self._buffer.append(item)
                self.queue.task_done()
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                if self._buffer:
                    await self._flush_buffer()
                self._mark_idle_if_needed()
                raise

            should_flush = len(self._buffer) >= self.config.batch_size or (
                self._buffer and loop.time() - last_flush >= self.config.flush_interval
            )
            if not should_flush:
                self._mark_idle_if_needed()
                continue

            await self._flush_buffer()
            last_flush = loop.time()
            self._mark_idle_if_needed()

    async def _flush_buffer(self) -> FlushResult[T]:
        if not self._buffer:
            return FlushResult(flushed=0, attempts=0, batch=())

        batch = self._normalize_batch(self._buffer)
        self._buffer = []
        self._is_flushing = True
        last_error: Exception | None = None

        try:
            for attempts in range(1, self.config.max_retries + 1):
                try:
                    await self.flush_callback(list(batch))
                    return FlushResult(
                        flushed=len(batch),
                        attempts=attempts,
                        batch=tuple(batch),
                    )
                except Exception as e:
                    last_error = e
                    logger.error(
                        f"BatchWriter {self.worker_name} flush attempt "
                        f"{attempts} failed: {e}"
                    )
                    if attempts < self.config.max_retries:
                        await asyncio.sleep(self.config.retry_backoff * attempts)

            assert last_error is not None
            dead_letter = DeadLetterRecord(
                worker_name=self.worker_name,
                batch=tuple(batch),
                error=repr(last_error),
                failed_at=get_current_time(),
                attempts=self.config.max_retries,
            )
            self._dead_letters.append(dead_letter)
            logger.error(
                f"BatchWriter {self.worker_name} moved batch to dead letter "
                f"after {self.config.max_retries} attempts: {last_error}"
            )
            self._last_error = last_error
            return FlushResult(
                flushed=0,
                attempts=self.config.max_retries,
                batch=tuple(batch),
            )
        finally:
            self._is_flushing = False
            self._mark_idle_if_needed()


async def execute_batch_write[PayloadT: Mapping[str, Any], OpsT: BaseOps[Any]](
    batch: Sequence[PayloadT],
    db_instance: ShardedDB,
    ops_class: type[OpsT],
    method: Callable[[OpsT, list[PayloadT]], Awaitable[Any]],
    time_field: str,
) -> None:
    """按时间戳对批量数据进行分组路由，并写入对应的分片数据库。"""
    if not batch:
        return

    logger_name = ops_class.__name__
    route_map: dict[datetime, list[PayloadT]] = defaultdict(list)

    for item in batch:
        ts = item[time_field]
        dt = arrow.get(ts).to("Asia/Shanghai").datetime
        route_ctx = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        route_map[route_ctx].append(item)

    for time_ctx, grouped_items in route_map.items():
        try:
            async with db_instance.write_session(time_ctx=time_ctx) as session:
                ops_instance = ops_class(session)
                await method(ops_instance, grouped_items)
        except Exception as e:
            logger.error(
                f"[{logger_name}] 落盘至 "
                f"{time_ctx.strftime('%Y_%m')} 分片时发生错误: {e}"
            )
            raise
