"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 02:45:56
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 23:59:57
Description: db service
"""

from src.database.core.tables import CoreBase
from src.database.instances import core_db, log_db, snapshot_db
from src.database.log.tables import LogBase
from src.database.snapshot.tables import SnapshotBase
from src.lib.message_assets import MessageAssetBase, message_asset_db
from src.lib.trace_log import mark_trace_logging_ready


async def init_db() -> None:
    await core_db.init(CoreBase)
    await log_db.init(LogBase)
    await snapshot_db.init(SnapshotBase)
    await message_asset_db.init(MessageAssetBase)
    mark_trace_logging_ready()
