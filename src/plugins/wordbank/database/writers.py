"""wordbank 缓冲写入器。"""

from collections.abc import Sequence

from src.lib.db.batch import BatchWriter, execute_batch_write

from .instances import wordbank_log_db
from .ops import WordbankLogOps
from .types import WordbankLogPayload


async def _bulk_insert_wordbank_logs(
    ops: WordbankLogOps,
    data: Sequence[WordbankLogPayload],
) -> int:
    """适配 execute_batch_write 的 Sequence 入参契约：同实例转发一层。"""
    return await ops.bulk_insert_logs(list(data))


async def _flush_wordbank_logs(batch: list[WordbankLogPayload]) -> None:
    if not batch:
        return
    await execute_batch_write(
        batch=batch,
        db_instance=wordbank_log_db,
        ops_class=WordbankLogOps,
        method=_bulk_insert_wordbank_logs,
        time_field="created_at",
    )


wordbank_log_writer = BatchWriter[WordbankLogPayload](
    flush_callback=_flush_wordbank_logs,
    batch_size=100,
    flush_interval=3.0,
)
