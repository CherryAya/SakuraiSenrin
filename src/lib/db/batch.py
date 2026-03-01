"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-08 17:18:19
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 14:07:17
Description: 批量处理器
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, NoReturn

import arrow
from loguru import logger

from src.lib.utils.common import get_current_time

if TYPE_CHECKING:
    from datetime import datetime

    from .connectors import ShardedDB
    from .ops import BaseOps


class BatchWriter[T]:
    """通用内存缓冲写入器

    执行流：积攒数据 -> 去重 -> 批量回调
    """

    def __init__(
        self,
        flush_callback: Callable[[list[T]], Awaitable[None]],
        batch_size: int = 100,
        flush_interval: float = 3.0,
    ) -> None:
        self.queue = asyncio.Queue()
        self.flush_callback = flush_callback
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._task: asyncio.Task | None = None
        self._worker_name: str | None = None

    def _ensure_worker_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._worker())
            self._worker_name = getattr(self.flush_callback, "__name__", "Unknown")
            logger.debug(f"BatchWriter worker [{self._worker_name}] started/restarted.")

    async def add(self, item: T) -> None:
        self._ensure_worker_running()
        await self.queue.put(item)

    async def add_all(self, items: list[T]) -> None:
        if not items:
            return
        self._ensure_worker_running()
        for item in items:
            self.queue.put_nowait(item)

    async def _worker(self) -> NoReturn:
        buffer = []
        last_flush = get_current_time()

        while True:
            try:
                timeout = self.flush_interval - (get_current_time() - last_flush)
                timeout = max(0.1, timeout)

                item = await asyncio.wait_for(self.queue.get(), timeout=timeout)
                buffer.append(item)
            except TimeoutError:
                pass

            if len(buffer) >= self.batch_size or (
                buffer and get_current_time() - last_flush >= self.flush_interval
            ):
                try:
                    await self.flush_callback(buffer)
                except Exception as e:
                    logger.error(f"BatchWriter {self._worker_name} flush error: {e}")
                finally:
                    buffer.clear()
                    last_flush = get_current_time()


async def execute_batch_write[PayloadT: Mapping[str, Any], OpsT: BaseOps[Any]](
    batch: Sequence[PayloadT],
    db_instance: ShardedDB,
    ops_class: type[OpsT],
    method: Callable[[OpsT, list[PayloadT]], Awaitable[Any]],
    time_field: str,
) -> None:
    """按时间戳对批量数据进行分组路由，并写入对应的分片数据库。

    Args:
        batch: 待写入的数据列表。
        db_instance: 目标分片数据库实例 (ShardedDB)。
        ops_class: 执行写入操作的 BaseOps 子类。
        method: 目标 Ops 类的未绑定异步写入方法。
        time_field: 用于计算路由的 Unix 时间戳字段名。

    注意事项:
        1. 仅支持传入 `ShardedDB` 实例，不可混用静态主库 (`StaticDB`)。
        2. `batch` 中所有字典项必须包含由 `time_field` 指定的整型时间戳字段。
        3. 内部按东八区 (Asia/Shanghai) 截取月份作为路由上下文。
        4. 任意切片写入失败将被捕获并记录日志，不会阻断其他月份切片的执行。

    Example:
        >>> logs: list[AuditLogPayload] = [{"target_id": 1, "created_at": 1735660800}]
        >>> await execute_sharded_write(
        ...     batch=logs,
        ...     db_instance=log_db,
        ...     ops_class=AuditLogOps,
        ...     method=AuditLogOps.bulk_create,
        ...     time_field="created_at"
        ... )
    """
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
            async with db_instance.session(time_ctx=time_ctx, commit=True) as session:
                ops_instance = ops_class(session)
                await method(ops_instance, grouped_items)

        except Exception as e:
            logger.error(
                f"[{logger_name}] 落盘至 {time_ctx.strftime('%Y_%m')} 分片时发生错误: {e}"  # noqa: E501
            )
