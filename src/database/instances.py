"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 01:39:53
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 20:18:32
Description: db 实例
"""

from src.lib.db.connectors import ShardedDB, StaticDB

core_db = StaticDB(
    namespace="core_db",
    filename="core.db",
)

log_db = ShardedDB(
    namespace="log_db",
    prefix="log",
    fmt="%Y%m",
    active_window_months=2,
)

snapshot_db = ShardedDB(
    namespace="snapshot_db",
    prefix="snapshot",
    fmt="%Y%m",
    active_window_months=2,
)
