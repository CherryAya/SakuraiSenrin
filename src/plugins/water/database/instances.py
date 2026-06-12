"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 20:17:03
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-03 17:34:20
Description: db 实例
"""

from src.lib.db.connectors import CounterStore, StateStore

from .patches import (
    build_water_message_patch_registry,
    build_water_summary_patch_registry,
)

water_message = CounterStore(
    namespace="water_db",
    prefix="logs",
    fmt="%Y_%m",
    active_window_months=2,
)
water_message.patch_registry = build_water_message_patch_registry()

water_summary = CounterStore(
    namespace="water_db",
    prefix="summary",
    fmt="%Y_%m",
    active_window_months=4,
)
water_summary.patch_registry = build_water_summary_patch_registry()

water_core_db = StateStore(
    namespace="water_db",
    filename="core.db",
)
