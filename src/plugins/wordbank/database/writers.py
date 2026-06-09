"""Wordbank buffered writers."""

from src.lib.db.batch import BatchWriter, execute_batch_write

from .instances import wordbank_log_db
from .ops import WordbankLogOps
from .types import WordbankLogPayload


async def _flush_wordbank_logs(batch: list[WordbankLogPayload]) -> None:
    if not batch:
        return
    await execute_batch_write(
        batch=batch,
        db_instance=wordbank_log_db,
        ops_class=WordbankLogOps,
        method=WordbankLogOps.bulk_insert_logs,
        time_field="created_at",
    )


wordbank_log_writer = BatchWriter[WordbankLogPayload](
    flush_callback=_flush_wordbank_logs,
    batch_size=100,
    flush_interval=3.0,
)
