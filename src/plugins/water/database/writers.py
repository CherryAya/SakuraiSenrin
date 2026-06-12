"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 19:16:33
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 02:49:04
Description: water db writers
"""

import arrow

from src.lib.db.batch import BatchWriter, execute_batch_write

from .instances import water_message
from .ops import WaterMessageOps
from .types import WaterMessagePayload, WaterMessageWritePayload


async def _flush_water_logs(batch: list[WaterMessageWritePayload]) -> None:
    if not batch:
        return

    grouped_payloads: dict[str, list[WaterMessagePayload]] = {}
    for item in batch:
        route_key = (
            arrow.get(int(item["created_at"])).to("Asia/Shanghai").floor("month")
        ).format("YYYY_MM")
        grouped_payloads.setdefault(route_key, []).append(
            {
                "group_id": item["group_id"],
                "user_id": item["user_id"],
                "record_date": item["record_date"],
                "hour": item["hour"],
                "msg_count": item["msg_count"],
            }
        )

    async def _write_grouped(
        ops: WaterMessageOps,
        grouped_batch: list[WaterMessageWritePayload],
    ) -> None:
        if not grouped_batch:
            return
        route_key = (
            arrow.get(int(grouped_batch[0]["created_at"]))
            .to("Asia/Shanghai")
            .floor("month")
            .format("YYYY_MM")
        )
        await ops.bulk_insert_water_message(grouped_payloads.get(route_key, []))

    await execute_batch_write(
        batch=batch,
        db_instance=water_message,
        ops_class=WaterMessageOps,
        method=_write_grouped,
        time_field="created_at",
    )


water_writer = BatchWriter[WaterMessageWritePayload](
    flush_callback=_flush_water_logs,
    batch_size=100,
    flush_interval=3.0,
    dedupe_key=lambda item: (
        f"{item['record_date']}:{item['hour']}:{item['group_id']}:{item['user_id']}"
    ),
)
