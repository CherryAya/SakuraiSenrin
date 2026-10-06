"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 20:17:03
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-03 17:34:20
Description: db 实例
"""

from src.lib.backup import register_backup_database
from src.lib.db.alias import AliasStore
from src.lib.db.connectors import CounterStore, StateStore

from .ops import WaterArchivedSummaryOps, WaterMessageOps
from .patches import (
    build_water_core_patch_registry,
    build_water_message_patch_registry,
    build_water_summary_patch_registry,
)

water_message = AliasStore[WaterMessageOps](
    CounterStore(
        namespace="water_db",
        prefix="logs",
        fmt="%Y_%m",
        active_window_months=2,
    ),
    ops_class=WaterMessageOps,
    time_field="created_at",
)
water_message.patch_registry = build_water_message_patch_registry()
register_backup_database(water_message)

water_summary = AliasStore[WaterArchivedSummaryOps](
    CounterStore(
        namespace="water_db",
        prefix="summary",
        fmt="%Y_%m",
        active_window_months=4,
    ),
    ops_class=WaterArchivedSummaryOps,
    time_field="record_date",
)
water_summary.patch_registry = build_water_summary_patch_registry()
register_backup_database(water_summary)

water_core_db = StateStore(
    namespace="water_db",
    filename="core.db",
)
water_core_db.patch_registry = build_water_core_patch_registry()
register_backup_database(water_core_db)
