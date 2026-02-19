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


async def init_db() -> None:
    await core_db.init(CoreBase)
    await log_db.init(LogBase)
    await snapshot_db.init(SnapshotBase)
