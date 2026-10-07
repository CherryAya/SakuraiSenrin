"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 01:39:53
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-10-07 03:40:00
Description: db 实例
"""

from src.lib.backup import register_backup_database
from src.lib.db.alias import AliasStore
from src.lib.db.connectors import EventStore, StateStore

from .log.ops import TraceEventLogOps
from .patches import (
    build_core_patch_registry,
    build_log_patch_registry,
    build_snapshot_patch_registry,
)
from .snapshot.ops import UserSnapshotOps

core_db = StateStore(
    namespace="core_db",
    filename="core.db",
)
core_db.patch_registry = build_core_patch_registry()
register_backup_database(core_db)

log_db = AliasStore[TraceEventLogOps](
    EventStore(
        namespace="log_db",
        prefix="log",
        fmt="%Y%m",
        active_window_months=2,
    ),
    ops_class=TraceEventLogOps,
    time_field="created_at",
)
log_db.patch_registry = build_log_patch_registry()
register_backup_database(log_db)

snapshot_db = AliasStore[UserSnapshotOps](
    EventStore(
        namespace="snapshot_db",
        prefix="snapshot",
        fmt="%Y%m",
        active_window_months=2,
    ),
    ops_class=UserSnapshotOps,
    time_field="created_at",
)
snapshot_db.patch_registry = build_snapshot_patch_registry()
register_backup_database(snapshot_db)
