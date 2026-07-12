"""Legacy source loading helpers for wordbank migration."""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping
from contextlib import closing
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg2.extensions import connection as PsycopgConnection

from .wordbank_types import LegacyImageCatalog, LegacyPgConfig

_HEX_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_HEX_MD5_ANYWHERE_RE = re.compile(r"([0-9a-fA-F]{32})")
_LEGACY_IMAGE_REPO_NAME = "SakuraiSenrinPic"
_LEGACY_IMAGE_DIR_NAME = "recovered_files"
_LEGACY_IMAGE_MAPPING_NAME = "file_mapping.json"


def parse_legacy_env_file(path: Path) -> dict[str, object]:
    values: dict[str, object] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value_text = raw_value.strip()
        if not key:
            continue
        if "#" in value_text and not value_text.startswith(("'", '"')):
            value_text = value_text.split("#", 1)[0].strip()
        if not value_text:
            continue
        try:
            values[key] = ast.literal_eval(value_text)
        except (SyntaxError, ValueError):
            values[key] = value_text.strip().strip('"').strip("'")
    return values


def load_legacy_pg_config(old_repo_root: Path) -> LegacyPgConfig:
    root_env = old_repo_root / ".env"
    environment = "dev"
    if root_env.is_file():
        parsed = parse_legacy_env_file(root_env)
        environment_value = parsed.get("ENVIRONMENT")
        if isinstance(environment_value, str) and environment_value.strip():
            environment = environment_value.strip()
    selected_env = old_repo_root / f".env.{environment}"
    if not selected_env.is_file():
        selected_env = old_repo_root / ".env.dev"
    values = parse_legacy_env_file(selected_env)
    return LegacyPgConfig(
        host=str(values["pg_host"]),
        port=_coerce_int(values["pg_port"], field="pg_port"),
        user=str(values["pg_username"]),
        password=str(values["pg_password"]),
    )


def _connect_legacy_postgres(config: LegacyPgConfig) -> PsycopgConnection:
    import psycopg2

    return psycopg2.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        dbname=config.database,
    )


def _fetch_legacy_rows_sync(
    config: LegacyPgConfig,
    *,
    sql: str,
) -> list[dict[str, object]]:
    from psycopg2.extras import RealDictCursor

    with closing(_connect_legacy_postgres(config)) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
    return [dict(row) for row in rows]


async def fetch_legacy_response_rows(
    config: LegacyPgConfig,
) -> list[dict[str, object]]:
    return await asyncio.to_thread(
        _fetch_legacy_rows_sync,
        config,
        sql="""
        SELECT
            r.response_id,
            r.trigger_id,
            r.response_text,
            r.response_rule_conditions,
            r.weight,
            r.priority,
            r.created_by,
            r.created_at,
            r.availability AS response_available,
            t.trigger_text,
            t.trigger_config,
            t.extra_info,
            COALESCE(a.current_status::text, 'PENDING') AS approval_status
        FROM response AS r
        JOIN trigger AS t
            ON t.trigger_id = r.trigger_id
        LEFT JOIN approval AS a
            ON a.response_id = r.response_id
        ORDER BY r.response_id ASC
        """,
    )


async def fetch_legacy_response_log_rows(
    config: LegacyPgConfig,
) -> list[dict[str, object]]:
    return await asyncio.to_thread(
        _fetch_legacy_rows_sync,
        config,
        sql="""
        SELECT
            rl.log_id,
            rl.message_id,
            rl.response_id,
            rl.user_id,
            rl.call_time,
            r.trigger_id,
            t.trigger_text,
            t.extra_info
        FROM response_log AS rl
        JOIN response AS r
            ON r.response_id = rl.response_id
        JOIN trigger AS t
            ON t.trigger_id = r.trigger_id
        ORDER BY rl.log_id ASC
        """,
    )


async def fetch_legacy_trigger_log_rows(
    config: LegacyPgConfig,
) -> list[dict[str, object]]:
    return await asyncio.to_thread(
        _fetch_legacy_rows_sync,
        config,
        sql="""
        SELECT
            log_id,
            message_id,
            trigger_id,
            user_id,
            call_time
        FROM trigger_log
        ORDER BY log_id ASC
        """,
    )


async def fetch_legacy_addition_log_rows(
    config: LegacyPgConfig,
) -> list[dict[str, object]]:
    return await asyncio.to_thread(
        _fetch_legacy_rows_sync,
        config,
        sql="""
        SELECT
            log_id,
            trigger_id,
            response_id,
            user_id,
            add_time,
            add_source,
            created_message_id,
            approval_id
        FROM addition_log
        ORDER BY log_id ASC
        """,
    )


async def fetch_legacy_message_approval_rows(
    config: LegacyPgConfig,
) -> list[dict[str, object]]:
    return await asyncio.to_thread(
        _fetch_legacy_rows_sync,
        config,
        sql="""
        SELECT
            ma.id,
            ma.message_id,
            ma.approval_id,
            a.response_id,
            a.user_id AS approval_user_id,
            a.created_at AS approval_created_at
        FROM message_approval AS ma
        JOIN approval AS a
            ON a.approval_id = ma.approval_id
        ORDER BY ma.id ASC
        """,
    )


def build_legacy_image_catalog(
    image_root: Path,
    mapping_path: Path | None,
) -> LegacyImageCatalog:
    if not image_root.is_dir():
        raise FileNotFoundError(f"legacy image root not found: {image_root}")
    files_by_name: dict[str, Path] = {}
    files_by_stem: dict[str, Path] = {}
    files_by_md5: dict[str, Path] = {}
    for path in image_root.iterdir():
        if not path.is_file():
            continue
        files_by_name[path.name.casefold()] = path
        files_by_stem.setdefault(path.stem.casefold(), path)
        for md5_hex in _extract_md5_candidates(path.name):
            files_by_md5.setdefault(md5_hex.casefold(), path)

    saved_as_by_name: dict[str, str] = {}
    if mapping_path and mapping_path.is_file():
        raw_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        if isinstance(raw_mapping, dict):
            for key, value in raw_mapping.items():
                saved_as = _extract_legacy_saved_as(value)
                if not saved_as:
                    continue
                for candidate in {str(key), Path(str(key)).name}:
                    normalized = candidate.strip().casefold()
                    if normalized:
                        saved_as_by_name[normalized] = saved_as

    return LegacyImageCatalog(
        image_root=image_root,
        files_by_name=files_by_name,
        files_by_stem=files_by_stem,
        files_by_md5=files_by_md5,
        saved_as_by_name=saved_as_by_name,
    )


def _extract_legacy_saved_as(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        saved_as = value.get("saved_as")
        if isinstance(saved_as, str):
            return saved_as.strip()
    return ""


def _extract_md5_candidates(value: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    raw_values = [value.strip(), Path(value).name.strip(), Path(value).stem.strip()]
    for raw in raw_values:
        if not raw:
            continue
        for matched in _HEX_MD5_ANYWHERE_RE.findall(raw):
            normalized = matched.upper()
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(normalized)
        compact = re.sub(r"[^0-9a-fA-F]", "", raw)
        if _HEX_MD5_RE.fullmatch(compact):
            normalized = compact.upper()
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(normalized)
    return candidates


def _default_legacy_image_root(old_repo_root: Path) -> Path:
    return old_repo_root.parent / _LEGACY_IMAGE_REPO_NAME / _LEGACY_IMAGE_DIR_NAME


def _default_legacy_image_mapping_path(old_repo_root: Path) -> Path:
    return old_repo_root.parent / _LEGACY_IMAGE_REPO_NAME / _LEGACY_IMAGE_MAPPING_NAME


def _coerce_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return int(stripped)
            except ValueError as exc:
                raise ValueError(f"invalid integer for {field}: {value}") from exc
    raise ValueError(f"invalid integer for {field}: {value!r}")


__all__ = [
    "_default_legacy_image_mapping_path",
    "_default_legacy_image_root",
    "build_legacy_image_catalog",
    "fetch_legacy_addition_log_rows",
    "fetch_legacy_message_approval_rows",
    "fetch_legacy_response_log_rows",
    "fetch_legacy_response_rows",
    "fetch_legacy_trigger_log_rows",
    "load_legacy_pg_config",
    "parse_legacy_env_file",
]
