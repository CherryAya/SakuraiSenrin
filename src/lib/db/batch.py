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
from typing import TYPE_CHECKING, ClassVar, Protocol

from loguru import logger

from src.lib.trace_log import log_trace_event, new_trace_id
from src.lib.utils.time_utils import get_current_time

if TYPE_CHECKING:
    from .connectors import SegmentStore

from .alias import AliasStore, OpsFactory

# flush 回调的单次执行上限（秒）。回调链路上任一 await 永不返回（锁泄漏、
# 阻塞 IO 卡死、外部依赖无超时）都会让 worker 永久楔死：此后队列无界积压、
# 查询路径的 flush_now/drain 一并挂起、关机 drain 也卡住——只有重启能恢复。
# 有看门狗后，超时被取消并走「失败重试 -> 死信」的既有降级路径：内存有界、
# 状态可见、无需重启。
DEFAULT_FLUSH_TIMEOUT_SECONDS = 30.0


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
    # flush 回调超时（看门狗触发）的次数。持续增长说明回调链路卡死，
    # 需要排查被调方为何不返回。
    flush_timeouts: int = 0
    # 当前待落盘条目数（队列 + 内存缓冲），供运维观察积压趋势
    pending_items: int = 0

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
    # 单次 flush 回调的执行上限（秒）。None 表示不设看门狗（仅用于测试或
    # 明确接受永久楔死风险的场景）；生产 writer 一律保留默认值。
    flush_timeout: float | None = DEFAULT_FLUSH_TIMEOUT_SECONDS


class BatchWriter[T]:
    """通用内存缓冲写入器。

    实例在构造时自注册，`iter_registered_writers` 供运行态巡检与关机落盘使用。
    """

    _registry: ClassVar[list["BatchWriter[object]"]] = []

    @classmethod
    def iter_registered(cls) -> tuple["BatchWriter[object]", ...]:
        """返回进程内全部已注册的 writer（按注册顺序）。"""
        return tuple(cls._registry)

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
        flush_timeout: float | None = DEFAULT_FLUSH_TIMEOUT_SECONDS,
    ) -> None:
        self.queue: asyncio.Queue[T] = asyncio.Queue()
        self.flush_callback = flush_callback
        self.config = BufferedWriterConfig(
            batch_size=batch_size,
            flush_interval=flush_interval,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            dedupe_key=dedupe_key,
            flush_timeout=flush_timeout,
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
        self._inflight_items = 0
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._flush_lock = asyncio.Lock()
        self._is_flushing = False
        self._last_error: Exception | None = None
        self._trace_persist = trace_persist
        type(self)._registry.append(self)  # type: ignore[arg-type]

    @property
    def worker_name(self) -> str:
        return self._worker_name or getattr(self.flush_callback, "__name__", "Unknown")

    @property
    def dead_letters(self) -> tuple[DeadLetterRecord[T], ...]:
        return tuple(self._dead_letters)

    @property
    def health(self) -> BatchWriterHealth:
        """返回运行态快照（含死信统计），供运维脚本与启动自检读取。"""
        snapshot = replace(self._health)
        snapshot.pending_items = (
            self.queue.qsize() + len(self._buffer) + self._inflight_items
        )
        return snapshot

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

    async def _invoke_flush_callback(self, batch: list[T]) -> None:
        """调用 flush 回调，带可选看门狗超时。

        回调链路上任一 await 永不返回都会让 worker 永久楔死（`_flush_lock`
        被永久持有、`_ensure_worker_running` 见任务未结束而不重建），此后队列
        无界积压、查询侧 flush_now/drain 一并挂起，只有重启才能恢复。超时后
        wait_for 会取消回调协程并抛 TimeoutError，走既有「重试 -> 死信」降级
        路径：内存有界、状态可见、worker 继续服务。

        已知边界：若回调内部是同步阻塞（占满事件循环），wait_for 的定时器
        也发不出去——那种情况需要调用方自己把阻塞操作挪进线程池。
        """
        timeout = self.config.flush_timeout
        if timeout is None:
            await self.flush_callback(batch)
            return
        try:
            await asyncio.wait_for(self.flush_callback(batch), timeout=timeout)
        except TimeoutError:
            self._health.flush_timeouts += 1
            logger.error(
                f"BatchWriter {self.worker_name} flush 回调超过 {timeout:g}s "
                f"未返回，看门狗已取消本次调用（将进入重试/死信路径）"
            )
            log_trace_event(
                event_name="flush_timeout",
                source_kind="batch_writer",
                component=self.worker_name,
                status="timeout",
                summary=(
                    f"BatchWriter {self.worker_name} flush watchdog cancelled "
                    f"a stuck callback."
                ),
                level="ERROR",
                batch_size=len(batch),
                payload_json={"flush_timeout_seconds": timeout},
                persist=self._trace_persist,
            )
            raise

    async def _flush_buffer(self) -> FlushResult[T]:
        async with self._flush_lock:
            if not self._buffer:
                return FlushResult(flushed=0, attempts=0, batch=())

            batch = self._normalize_batch(self._buffer)
            self._buffer = []
            last_error: Exception | None = None
            trace_id = new_trace_id(self.worker_name)

            # 在飞批次计数：pending_items 必须覆盖「已出缓冲、尚未入账」的这段，
            # 否则回调卡住时（最需要观察积压的时刻）计数反而归零。
            self._inflight_items = len(batch)

            # _is_flushing 的置位与复位必须同在 try 内：此前置位在 try 之前，
            # 且中间还夹着 log_trace_event——一旦该调用抛错，finally 不执行，
            # _is_flushing 永久为 True，后续 flush_now 全部楔死。
            try:
                self._is_flushing = True
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

                for attempts in range(1, self.config.max_retries + 1):
                    try:
                        await self._invoke_flush_callback(list(batch))
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
                self._inflight_items = 0
                self._mark_idle_if_needed()


class RoutableStore(Protocol):
    """``execute_batch_write`` 需要的全部能力：一个物理分片库与一个默认时间字段。

    刻意只声明这两项，而不是直接写 ``AliasStore[OpsT]``：``AliasStore`` 在 ops
    参数上不变（``ops_class`` 落在 ``__init__`` 输入位），而调用点需要「同一物理
    分片上换一套 ops」（log_db 是 ``AliasStore[TraceEventLogOps]``，却要写
    ``AuditLogOps``）。ops 由 ``execute_batch_write`` 的 ``ops_class`` 独立携带，
    因此本协议不需要描述写入方法，任何具体 ``AliasStore`` 都能结构匹配。
    """

    @property
    def store(self) -> SegmentStore: ...

    @property
    def time_field_ref(self) -> str: ...


async def execute_batch_write[PayloadT, OpsT: OpsFactory](
    batch: Sequence[PayloadT],
    db_instance: RoutableStore,
    ops_class: type[OpsT],
    method: Callable[[OpsT, Sequence[PayloadT]], Awaitable[int | None]],
    time_field: str | None,
    *,
    emit_trace: bool = True,
) -> None:
    """按时间戳分组路由并写入对应分片（转发到 AliasStore.write_batch）。

    ``ops_class`` 必须显式给出，因为调用点常常要用「与 db 自带的 ops 不同的 ops」
    写同一物理分片（例如 log_db 既写审计日志也写 trace 日志）；``time_field``
    省略时沿用 db_instance 自带的时间字段。
    """
    if not batch:
        return
    target = AliasStore(
        db_instance.store,
        ops_class=ops_class,
        time_field=time_field or db_instance.time_field_ref,
    )
    await target.write_batch(batch, method=method, emit_trace=emit_trace)
