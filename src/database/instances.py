"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 01:39:53
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 22:34:28
Description: db 实例
"""

from src.lib.db.connectors import ShardedDB, StaticDB

core_db = StaticDB(filename="core.db")
log_db = ShardedDB(prefix="log", fmt="%Y%m")
snapshot_db = ShardedDB(prefix="snapshot", fmt="%Y%m")
