"""Compatibility wrapper for legacy water migration helpers."""

from __future__ import annotations

from scripts.migrations.water import (
    LegacyWaterMigrationProgressCallback,
    LegacyWaterMigrationProgressPayload,
    LegacyWaterRow,
    WaterMigrationReport,
    build_legacy_water_rows,
    fetch_legacy_water_rows,
    import_legacy_water_rows,
    iter_legacy_water_row_batches,
    iter_legacy_water_row_batches_prefetched,
    migrate_legacy_water,
    migrate_legacy_water_from_pg,
    normalize_legacy_timestamp,
    rebuild_water_runtime_from_messages,
    reset_current_water_runtime,
    water_settlement_service,
    write_report,
)

__all__ = [
    "LegacyWaterMigrationProgressCallback",
    "LegacyWaterMigrationProgressPayload",
    "LegacyWaterRow",
    "WaterMigrationReport",
    "build_legacy_water_rows",
    "fetch_legacy_water_rows",
    "import_legacy_water_rows",
    "iter_legacy_water_row_batches",
    "iter_legacy_water_row_batches_prefetched",
    "migrate_legacy_water",
    "migrate_legacy_water_from_pg",
    "normalize_legacy_timestamp",
    "rebuild_water_runtime_from_messages",
    "reset_current_water_runtime",
    "water_settlement_service",
    "write_report",
]
