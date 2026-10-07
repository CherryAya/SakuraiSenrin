"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 02:45:56
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-10-07 12:40:00
Description: db service
"""

from src.database.core.tables import CoreBase
from src.database.instances import core_db, log_db, snapshot_db
from src.database.log.tables import LogBase
from src.database.snapshot.tables import SnapshotBase
from src.lib.db.atomic import bind_attached_schemas
from src.lib.message_assets import MessageAssetBase, message_asset_db
from src.lib.trace_log import mark_trace_logging_ready

_schema_bound = False


def bind_cross_store_schemas() -> None:
    """给 log / snapshot 的表打 attached schema 标记（进程内一次即可）。"""
    global _schema_bound
    if _schema_bound:
        return
    bind_attached_schemas(CoreBase, LogBase, SnapshotBase)
    _schema_bound = True


async def init_db() -> None:
    # 必须在各库 init 之前绑定：跨库原子会话依赖 attached schema 前缀寻址
    bind_cross_store_schemas()
    await core_db.init(CoreBase)
    await log_db.init(LogBase)
    await snapshot_db.init(SnapshotBase)
    await message_asset_db.init(MessageAssetBase)
    mark_trace_logging_ready()
