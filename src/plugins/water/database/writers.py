"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 19:16:33
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 20:26:11
Description: water db writers
"""

from collections import defaultdict
from datetime import datetime

import arrow

from src.lib.db.batch import BatchWriter
from src.logger import logger

from .instances import water_message
from .ops import WaterMessageOps
from .types import WaterMessagePayload


async def _flush_water_logs(batch: list[WaterMessagePayload]) -> None:
    if not batch:
        return

    route_map: dict[datetime, list[WaterMessagePayload]] = defaultdict(list)

    for log in batch:
        dt = arrow.get(log["created_at"]).datetime
        route_ctx = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        route_map[route_ctx].append(log)

    for time_ctx, water_messager in route_map.items():
        try:
            async with water_message.session(time_ctx=time_ctx, commit=True) as session:
                await WaterMessageOps(session).bulk_insert_water_message(water_messager)
        except Exception as e:
            logger.error(
                f"[WaterWriter] 落盘至 {time_ctx.strftime('%Y_%m')} 分片时发生错误: {e}"
            )


water_writer = BatchWriter[WaterMessagePayload](
    flush_callback=_flush_water_logs,
    batch_size=100,
    flush_interval=3.0,
)
