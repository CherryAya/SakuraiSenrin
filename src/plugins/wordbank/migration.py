"""Compatibility wrapper for legacy wordbank migration helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.migrations import wordbank as _impl
from scripts.migrations.wordbank import (
    LegacyImageCatalog,
    LegacyImportTarget,
    LegacyMigrationProgressCallback,
    LegacyPgConfig,
    MigrationError,
    WordbankMigrationReport,
    build_legacy_image_catalog,
    extract_failure_details_from_categorized_report,
    fetch_legacy_addition_log_rows,
    fetch_legacy_message_approval_rows,
    fetch_legacy_response_log_rows,
    fetch_legacy_response_rows,
    fetch_legacy_trigger_log_rows,
    infer_report_response_available,
    legacy_message_to_shape,
    load_legacy_pg_config,
    migrate_legacy_approval_message_refs,
    migrate_legacy_response_logs,
    migrate_legacy_rows,
    migrate_legacy_trigger_logs,
    migrate_legacy_wordbank,
    normalize_legacy_rules,
    normalize_legacy_scope,
    normalize_legacy_state,
    parse_legacy_env_file,
    rebuild_legacy_row_from_failure_detail,
    rebuild_legacy_rows_from_failure_details,
)
from scripts.migrations.wordbank import _resolve_legacy_call_window_seconds


async def migrate_legacy_wordbank(
    old_repo_root: Path,
    *,
    repository: Any,
    media_service: Any,
    image_root: Path | None = None,
    mapping_path: Path | None = None,
    pg_config: LegacyPgConfig | None = None,
    reset_target: bool = True,
    import_logs: bool = True,
    progress: LegacyMigrationProgressCallback | None = None,
    progress_every: int = 100,
) -> WordbankMigrationReport:
    resolved_pg_config = pg_config or load_legacy_pg_config(old_repo_root)
    rows = await fetch_legacy_response_rows(resolved_pg_config)
    response_log_rows: Sequence[Mapping[str, object]] = ()
    trigger_log_rows: Sequence[Mapping[str, object]] = ()
    addition_log_rows: Sequence[Mapping[str, object]] = ()
    message_approval_rows: Sequence[Mapping[str, object]] = ()
    if import_logs:
        response_log_rows = await fetch_legacy_response_log_rows(resolved_pg_config)
        trigger_log_rows = await fetch_legacy_trigger_log_rows(resolved_pg_config)
        addition_log_rows = await fetch_legacy_addition_log_rows(resolved_pg_config)
        message_approval_rows = await fetch_legacy_message_approval_rows(
            resolved_pg_config
        )
    resolved_image_root = image_root or _impl._default_legacy_image_root(old_repo_root)
    resolved_mapping_path = (
        mapping_path
        if mapping_path is not None
        else _impl._default_legacy_image_mapping_path(old_repo_root)
    )
    return await migrate_legacy_rows(
        rows,
        repository=repository,
        media_service=media_service,
        image_catalog=build_legacy_image_catalog(
            resolved_image_root,
            resolved_mapping_path,
        ),
        response_log_rows=response_log_rows,
        trigger_log_rows=trigger_log_rows,
        addition_log_rows=addition_log_rows,
        message_approval_rows=message_approval_rows,
        reset_target=reset_target,
        progress=progress,
        progress_every=progress_every,
    )


def load_legacy_pg_config(old_repo_root: Path) -> LegacyPgConfig:
    return _impl.load_legacy_pg_config(old_repo_root)


__all__ = [
    "LegacyImageCatalog",
    "LegacyImportTarget",
    "LegacyMigrationProgressCallback",
    "LegacyPgConfig",
    "MigrationError",
    "WordbankMigrationReport",
    "build_legacy_image_catalog",
    "extract_failure_details_from_categorized_report",
    "fetch_legacy_addition_log_rows",
    "fetch_legacy_message_approval_rows",
    "fetch_legacy_response_log_rows",
    "fetch_legacy_response_rows",
    "fetch_legacy_trigger_log_rows",
    "infer_report_response_available",
    "legacy_message_to_shape",
    "load_legacy_pg_config",
    "migrate_legacy_approval_message_refs",
    "migrate_legacy_response_logs",
    "migrate_legacy_rows",
    "migrate_legacy_trigger_logs",
    "migrate_legacy_wordbank",
    "normalize_legacy_rules",
    "normalize_legacy_scope",
    "normalize_legacy_state",
    "parse_legacy_env_file",
    "rebuild_legacy_row_from_failure_detail",
    "rebuild_legacy_rows_from_failure_details",
    "_resolve_legacy_call_window_seconds",
]
