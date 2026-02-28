"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 20:17:03
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-27 20:40:42
Description: db 实例
"""

from src.lib.db.connectors import ShardedDB, StaticDB

water_message = ShardedDB(
    namespace="water_db",
    prefix="logs",
    fmt="%Y_%m",
    active_window_months=2,
)

water_summary_db = StaticDB(
    namespace="water_db",
    filename="summary.db",
)
