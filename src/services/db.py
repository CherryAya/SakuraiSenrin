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
from src.lib.db.manager import db_manager
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


def register_cross_store_journal_modes() -> None:
    """登记跨库事务参与者为 rollback journal 库：首次连接前调用，幂等。"""
    db_manager.register_rollback_journal_dir(core_db.base_dir)
    db_manager.register_rollback_journal_dir(log_db.base_dir)
    db_manager.register_rollback_journal_dir(snapshot_db.base_dir)


async def init_db() -> None:
    # 绑定 schema 前缀与 journal：须先于各库首次连接
    bind_cross_store_schemas()
    register_cross_store_journal_modes()
    await core_db.init(CoreBase)
    await log_db.init(LogBase)
    await snapshot_db.init(SnapshotBase)
    await message_asset_db.init(MessageAssetBase)
    mark_trace_logging_ready()
