"""Wordbank buffered writers."""

from collections.abc import Sequence

from src.lib.db.batch import BatchWriter, execute_batch_write

from .instances import wordbank_log_db
from .ops import WordbankLogOps
from .types import WordbankLogPayload


async def _bulk_insert_wordbank_logs(
    ops: WordbankLogOps,
    data: Sequence[WordbankLogPayload],
) -> int:
    """适配 ``execute_batch_write`` 的 ``Sequence`` 入参契约。

    ``WordbankLogOps.bulk_insert_logs`` 声明为 ``list``（对调用方更严，安全），
    直接作为 ``method`` 传入会因参数逆变而类型不兼容；这里补一层同实例转发。
    """
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
