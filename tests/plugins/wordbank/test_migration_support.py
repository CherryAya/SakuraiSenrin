from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from typing import Any

from PIL import Image
import pytest
from sqlalchemy import select

from src.plugins.wordbank import migration as migration_module
from src.plugins.wordbank.database.instances import wordbank_log_db
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.database.tables import WordbankLog
from src.plugins.wordbank.migration import (
    WordbankMigrationReport,
    build_legacy_image_catalog,
    extract_failure_details_from_categorized_report,
    infer_report_response_available,
    legacy_message_to_shape,
    migrate_legacy_rows,
    migrate_legacy_wordbank,
    normalize_legacy_rules,
    normalize_legacy_scope,
    normalize_legacy_state,
    parse_legacy_env_file,
    rebuild_legacy_row_from_failure_detail,
    rebuild_legacy_rows_from_failure_details,
)
from src.plugins.wordbank.services.media import WordbankMediaService


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (20, 20), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _segment_list(value: dict[str, object]) -> list[dict[str, Any]]:
    segments = value.get("segments")
    assert isinstance(segments, list)
    normalized: list[dict[str, Any]] = []
    for item in segments:
        assert isinstance(item, dict)
        normalized.append(item)
    return normalized


__all__ = [
    "UTC",
    "Any",
    "BytesIO",
    "Image",
    "Mapping",
    "Path",
    "WordbankLog",
    "WordbankMediaService",
    "WordbankMigrationReport",
    "WordbankRepository",
    "_png_bytes",
    "_segment_list",
    "build_legacy_image_catalog",
    "datetime",
    "extract_failure_details_from_categorized_report",
    "infer_report_response_available",
    "json",
    "legacy_message_to_shape",
    "migrate_legacy_rows",
    "migrate_legacy_wordbank",
    "migration_module",
    "normalize_legacy_rules",
    "normalize_legacy_scope",
    "normalize_legacy_state",
    "parse_legacy_env_file",
    "pytest",
    "rebuild_legacy_row_from_failure_detail",
    "rebuild_legacy_rows_from_failure_details",
    "select",
    "sqlite3",
    "wordbank_log_db",
]
