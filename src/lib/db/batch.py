"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-08 17:18:19
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-06-08 16:00:00
Description: 批量处理器
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from src.lib.trace_log import log_trace_event, new_trace_id
from src.lib.utils.common import get_current_time

if TYPE_CHECKING:
    from sqlalchemy.orm import DeclarativeBase

    from .connectors import SegmentStore
    from .ops import BaseOps

from .alias import AliasStore


@dataclass(slots=True, frozen=True)
class DeadLetterRecord[T]:
    worker_name: str
    batch: tuple[T, ...]
    error: str
    failed_at: int
    attempts: int


@dataclass(slots=True)
class BatchWriterHealth:
    """writer 运行态快照，对应 ES 的 _cluster/health。"""

    worker_name: str
    dead_letter_batches: int = 0
    dead_letter_items: int = 0
    last_dead_letter_at: int = 0
    last_dead_letter_error: str = ""
    consecutive_failures: int = 0
    last_flush_at: int = 0
    last_flush_items: int = 0
    total_flushed_items: int = 0
    total_failed_items: int = 0
    # 因内存队列上界被丢弃的条数（已落库的记录不受影响）
    dropped_dead_letters: int = 0

    @property
    def is_degraded(self) -> bool:
        """存在未处理的死信即视为降级。

        死信当前只进内存，不落盘；因此只要发生就必须有人看见，否则等于静默丢数据。
        """
        return self.dead_letter_batches > 0


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
        trace_persist: bool = True,
        max_dead_letters: int = 500,
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
        self._max_dead_letters = max(1, max_dead_letters)
        self._health = BatchWriterHealth(
            worker_name=self._worker_name
            or getattr(self.flush_callback, "__name__", "Unknown"),
        )
        self._buffer: list[T] = []
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._flush_lock = asyncio.Lock()
        self._is_flushing = False
        self._last_error: Exception | None = None
        self._trace_persist = trace_persist

    @property
    def worker_name(self) -> str:
        return self._worker_name or getattr(self.flush_callback, "__name__", "Unknown")

    @property
    def dead_letters(self) -> tuple[DeadLetterRecord[T], ...]:
        return tuple(self._dead_letters)

    @property
    def health(self) -> BatchWriterHealth:
        """返回运行态快照（含死信统计），供运维脚本与启动自检读取。"""
        return replace(self._health)

    def pop_dead_letters(self) -> tuple[DeadLetterRecord[T], ...]:
        """取出并清空死信，供调用方上报或补偿。"""
        records = tuple(self._dead_letters)
        self._dead_letters.clear()
        return records

    def _ensure_worker_running(self) -> None:
        if self._closed:
            raise RuntimeError(f"BatchWriter [{self.worker_name}] has been closed")
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._worker())
            self._worker_name = getattr(self.flush_callback, "__name__", "Unknown")
            logger.debug(f"BatchWriter worker [{self._worker_name}] started/restarted.")
            log_trace_event(
                event_name="worker_started",
                source_kind="batch_writer",
                component=self.worker_name,
                status="started",
                summary=f"BatchWriter worker {self.worker_name} started.",
                payload_json={"batch_size": self.config.batch_size},
                persist=self._trace_persist,
            )

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

    async def flush_now(self) -> None:
        self._ensure_worker_running()
        self._mark_busy()

        while True:
            while True:
                try:
                    item = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._buffer.append(item)
                self.queue.task_done()

            if self._is_flushing:
                await self._idle_event.wait()
                continue

            if self._buffer:
                await self._flush_buffer()

            self._mark_idle_if_needed()
            if self.queue.empty() and not self._buffer and not self._is_flushing:
                if self._last_error is not None:
                    err = self._last_error
                    self._last_error = None
                    raise err
                return

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
        async with self._flush_lock:
            if not self._buffer:
                return FlushResult(flushed=0, attempts=0, batch=())

            batch = self._normalize_batch(self._buffer)
            self._buffer = []
            self._is_flushing = True
            last_error: Exception | None = None
            trace_id = new_trace_id(self.worker_name)

            log_trace_event(
                event_name="flush_started",
                source_kind="batch_writer",
                component=self.worker_name,
                status="started",
                summary=f"BatchWriter {self.worker_name} flush started.",
                trace_id=trace_id,
                batch_size=len(batch),
                persist=self._trace_persist,
            )

            try:
                for attempts in range(1, self.config.max_retries + 1):
                    try:
                        await self.flush_callback(list(batch))
                        self._health.consecutive_failures = 0
                        self._health.last_flush_at = get_current_time()
                        self._health.last_flush_items = len(batch)
                        self._health.total_flushed_items += len(batch)
                        log_trace_event(
                            event_name="flush_finished",
                            source_kind="batch_writer",
                            component=self.worker_name,
                            status="success",
                            summary=f"BatchWriter {self.worker_name} flush succeeded.",
                            trace_id=trace_id,
                            batch_size=len(batch),
                            attempt=attempts,
                            persist=self._trace_persist,
                        )
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
                        log_trace_event(
                            event_name="flush_retry",
                            source_kind="batch_writer",
                            component=self.worker_name,
                            status="retry",
                            summary=(
                                f"BatchWriter {self.worker_name} flush attempt "
                                f"{attempts} failed."
                            ),
                            level="ERROR",
                            trace_id=trace_id,
                            batch_size=len(batch),
                            attempt=attempts,
                            payload_json={"error": repr(e)},
                            persist=self._trace_persist,
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
                # 有上界：持续失败时不能让内存无限增长（旧实现是裸 list.append）
                if len(self._dead_letters) > self._max_dead_letters:
                    dropped = len(self._dead_letters) - self._max_dead_letters
                    del self._dead_letters[:dropped]
                    self._health.dropped_dead_letters += dropped
                    logger.error(
                        f"BatchWriter {self.worker_name} 死信队列超过上限 "
                        f"{self._max_dead_letters}，丢弃最旧的 {dropped} 条"
                        "（已落库的记录不受影响）",
                    )
                self._health.dead_letter_batches += 1
                self._health.dead_letter_items += len(batch)
                self._health.total_failed_items += len(batch)
                self._health.last_dead_letter_at = dead_letter.failed_at
                self._health.last_dead_letter_error = dead_letter.error
                self._health.consecutive_failures += 1
                logger.error(
                    f"BatchWriter {self.worker_name} moved batch to dead letter "
                    f"after {self.config.max_retries} attempts: {last_error}"
                )
                log_trace_event(
                    event_name="flush_dead_letter",
                    source_kind="batch_writer",
                    component=self.worker_name,
                    status="dead_letter",
                    summary=(
                        f"BatchWriter {self.worker_name} moved batch to dead letter."
                    ),
                    level="ERROR",
                    trace_id=trace_id,
                    batch_size=len(batch),
                    attempt=self.config.max_retries,
                    payload_json={"error": repr(last_error)},
                    persist=self._trace_persist,
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


class RoutableStore(Protocol):
    @property
    def store(self) -> SegmentStore: ...

    @property
    def ops_class_ref(self) -> type[BaseOps[DeclarativeBase]]: ...

    @property
    def time_field_ref(self) -> str: ...


async def execute_batch_write[
    PayloadT,
    OpsT: BaseOps[DeclarativeBase],
](
    batch: Sequence[PayloadT],
    db_instance: RoutableStore,
    ops_class: type[OpsT] | None,
    method: Callable[[OpsT, Sequence[PayloadT]], Awaitable[int | None]],
    time_field: str | None,
    *,
    emit_trace: bool = True,
) -> None:
    """按时间戳分组路由并写入对应分片（转发到 AliasStore.write_batch）。

    ops_class / time_field 已由 AliasStore 构造时携带，调用点可省略；保留两个
    可选参数是为了兼容「同一 alias 上用另一套 ops」的少数场景（例如 log_db 既写
    审计日志也写 trace 日志）。传了则以传入值为准。
    """
    if not batch:
        return
    target: RoutableStore = db_instance
    if ops_class is not None or time_field is not None:
        resolved_ops = cast("type[OpsT]", ops_class or db_instance.ops_class_ref)
        target = AliasStore[OpsT](
            db_instance.store,
            ops_class=resolved_ops,
            time_field=time_field or db_instance.time_field_ref,
        )
    await target.write_batch(batch, method=method, emit_trace=emit_trace)
