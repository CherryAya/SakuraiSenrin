import asyncio
from collections.abc import Awaitable, Callable
import time
from typing import NoReturn, TypeVar

from loguru import logger

T = TypeVar("T")


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

    def _ensure_worker_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._worker())
            worker_name = getattr(self.flush_callback, "__name__", "Unknown")
            logger.debug(f"BatchWriter worker [{worker_name}] started/restarted.")

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
        last_flush = time.time()

        while True:
            try:
                timeout = self.flush_interval - (time.time() - last_flush)
                timeout = max(0.1, timeout)

                item = await asyncio.wait_for(self.queue.get(), timeout=timeout)
                buffer.append(item)
            except TimeoutError:
                pass

            if len(buffer) >= self.batch_size or (
                buffer and time.time() - last_flush >= self.flush_interval
            ):
                try:
                    await self.flush_callback(buffer)
                except Exception as e:
                    logger.error(f"BatchWriter 写入失败: {e}")
                finally:
                    buffer.clear()
                    last_flush = time.time()
