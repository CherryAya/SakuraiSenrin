"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 19:16:33
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 02:49:04
Description: water db writers
"""

from src.lib.db.batch import BatchWriter, execute_batch_write

from .instances import water_message
from .ops import WaterMessageOps
from .types import WaterMessagePayload


async def _flush_water_logs(batch: list[WaterMessagePayload]) -> None:
    if not batch:
        return

    await execute_batch_write(
        batch=batch,
        db_instance=water_message,
        ops_class=WaterMessageOps,
        method=WaterMessageOps.bulk_insert_water_message,
        time_field="created_at",
    )


water_writer = BatchWriter[WaterMessagePayload](
    flush_callback=_flush_water_logs,
    batch_size=100,
    flush_interval=3.0,
)
