"""Legacy wordbank migration helpers."""

from __future__ import annotations

import ast
import asyncio
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from io import BytesIO
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, cast

from PIL import Image, UnidentifiedImageError

from src.database.consts import WritePolicy
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.message_model import (
    MessageAtom,
    MessageShape,
    normalize_message_text,
    normalize_text,
    shape_from_event,
    shape_from_text,
)
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import SCOPE_PRIORITY

if TYPE_CHECKING:
    from psycopg2.extensions import connection as PsycopgConnection

_HEX_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_HEX_MD5_ANYWHERE_RE = re.compile(r"([0-9a-fA-F]{32})")
_LEGACY_EVENT_NAMES = {
    "AT_MENTIONED": "event:at",
    "POKE_MENTIONED": "event:poke",
    "GROUP_JOIN": "event:join",
    "GROUP_LEAVE": "event:leave",
}
_LEGACY_RULE_KEYS = {"group_id", "user_id", "role", "call_count", "$and", "$or"}
_LEGACY_ROLES = {"owner", "admin", "member"}
_LEGACY_ALL_TIME_WINDOW_SECONDS = 60 * 60 * 24 * 365 * 50
_LEGACY_IMAGE_REPO_NAME = "SakuraiSenrinPic"
_LEGACY_IMAGE_DIR_NAME = "recovered_files"
_LEGACY_IMAGE_MAPPING_NAME = "file_mapping.json"


class MigrationError(Exception):
    """Raised when a legacy wordbank record cannot be migrated."""


@dataclass(slots=True, frozen=True)
class LegacyPgConfig:
    host: str
    port: int
    user: str
    password: str
    database: str = "senrin_wordbank"


@dataclass(slots=True, frozen=True)
class LegacyImageCatalog:
    image_root: Path
    files_by_name: dict[str, Path]
    files_by_stem: dict[str, Path]
    files_by_md5: dict[str, Path]
    saved_as_by_name: dict[str, str]

    def resolve(self, file_name: str, *, url: str = "") -> Path | None:
        path, _ = self.resolve_with_source(file_name, url=url)
        return path

    def resolve_with_source(
        self,
        file_name: str,
        *,
        url: str = "",
    ) -> tuple[Path | None, str]:
        seen_candidates: set[str] = set()
        pending_candidates = [
            file_name.strip(),
            Path(file_name).name.strip(),
            url.strip(),
            Path(url).name.strip(),
        ]
        md5_candidates: list[str] = []

        while pending_candidates:
            candidate = pending_candidates.pop(0).strip()
            if not candidate or candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)

            mapped_name = self.saved_as_by_name.get(candidate.casefold())
            if mapped_name:
                for mapped_candidate in (mapped_name, Path(mapped_name).name):
                    direct = self.files_by_name.get(mapped_candidate.casefold())
                    if direct is not None:
                        return direct, "mapping"

            normalized = candidate.casefold()
            direct = self.files_by_name.get(normalized)
            if direct is not None:
                return direct, "direct_name"

            stem = Path(candidate).stem.strip()
            if stem:
                by_stem = self.files_by_stem.get(stem.casefold())
                if by_stem is not None:
                    return by_stem, "stem"
                pending_candidates.append(stem)

            for md5_hex in _extract_md5_candidates(candidate):
                md5_candidates.append(md5_hex)

        for md5_hex in md5_candidates:
            path = self.files_by_md5.get(md5_hex.casefold())
            if path is not None:
                return path, "md5_index"
            for suffix in (".webp", ".gif", ".png", ".jpg", ".jpeg"):
                candidate = self.image_root / f"{md5_hex.upper()}{suffix}"
                if candidate.is_file():
                    return candidate, "md5_scan"
        return None, "missing"


@dataclass(slots=True, frozen=True)
class LegacyEntryState:
    status: str
    enabled: int
    deleted_at: int


@dataclass(slots=True, frozen=True)
class LegacyImportedLogTarget:
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int


LegacyMigrationProgressCallback = Callable[
    [str, int, int, Mapping[str, object]],
    None,
]


@dataclass(slots=True)
class WordbankMigrationReport:
    total_rows: int = 0
    imported_rows: int = 0
    imported_groups: int = 0
    imported_response_items: int = 0
    imported_entries: int = 0
    skipped_rows: int = 0
    status_counts: Counter[str] = field(default_factory=Counter)
    skipped_reasons: Counter[str] = field(default_factory=Counter)
    image_counts: Counter[str] = field(default_factory=Counter)
    image_resolution_counts: Counter[str] = field(default_factory=Counter)
    imported_entry_ids: dict[int, list[int]] = field(default_factory=dict)
    imported_group_ids: dict[int, int] = field(default_factory=dict)
    response_count_by_group_id: dict[int, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    failure_details: list[dict[str, object]] = field(default_factory=list)
    total_log_rows: int = 0
    imported_log_rows: int = 0
    skipped_log_rows: int = 0
    log_failures: list[str] = field(default_factory=list)
    log_failure_details: list[dict[str, object]] = field(default_factory=list)
    total_trigger_log_rows: int = 0
    imported_trigger_log_rows: int = 0
    skipped_trigger_log_rows: int = 0
    trigger_log_failures: list[str] = field(default_factory=list)
    trigger_log_failure_details: list[dict[str, object]] = field(default_factory=list)
    total_approval_ref_rows: int = 0
    imported_approval_ref_rows: int = 0
    skipped_approval_ref_rows: int = 0
    approval_ref_failures: list[str] = field(default_factory=list)
    approval_ref_failure_details: list[dict[str, object]] = field(default_factory=list)

    def add_failure(
        self,
        response_id: int,
        reason: str,
        *,
        row: Mapping[str, object] | None = None,
    ) -> None:
        self.skipped_rows += 1
        self.skipped_reasons[reason] += 1
        self.failures.append(f"{response_id}: {reason}")
        if row is not None:
            self.failure_details.append(
                {
                    "response_id": response_id,
                    "trigger_id": _safe_report_int(row.get("trigger_id")),
                    "reason": reason,
                    "approval_status": str(row.get("approval_status") or ""),
                    "priority": _safe_report_int(row.get("priority")),
                    "weight": _safe_report_int(row.get("weight")),
                    "created_by": str(row.get("created_by") or ""),
                    "created_at": _safe_report_timestamp(row.get("created_at")),
                    "trigger": _summarize_legacy_message_payload(
                        row.get("trigger_text"),
                        extra_info=row.get("extra_info"),
                    ),
                    "response": _summarize_legacy_message_payload(
                        row.get("response_text")
                    ),
                    "response_rule_conditions": _safe_jsonish(
                        row.get("response_rule_conditions")
                    ),
                    "trigger_config": _safe_jsonish(row.get("trigger_config")),
                    "extra_info": _safe_jsonish(row.get("extra_info")),
                }
            )

    def add_log_failure(
        self,
        log_id: int | None,
        reason: str,
        *,
        row: Mapping[str, object] | None = None,
    ) -> None:
        self.skipped_log_rows += 1
        log_label = str(log_id) if log_id is not None else "unknown"
        self.log_failures.append(f"{log_label}: {reason}")
        if row is not None:
            self.log_failure_details.append(
                {
                    "log_id": log_id,
                    "response_id": _safe_report_int(row.get("response_id")),
                    "trigger_id": _safe_report_int(row.get("trigger_id")),
                    "reason": reason,
                    "message_id": str(row.get("message_id") or ""),
                    "user_id": str(row.get("user_id") or ""),
                    "call_time": _safe_report_timestamp(row.get("call_time")),
                }
            )

    def add_approval_ref_failure(
        self,
        message_id: str,
        reason: str,
        *,
        row: Mapping[str, object] | None = None,
    ) -> None:
        self.skipped_approval_ref_rows += 1
        self.approval_ref_failures.append(f"{message_id or 'unknown'}: {reason}")
        if row is not None:
            self.approval_ref_failure_details.append(
                {
                    "message_id": message_id,
                    "approval_id": _safe_report_int(row.get("approval_id")),
                    "response_id": _safe_report_int(row.get("response_id")),
                    "reason": reason,
                    "source_message_id": str(row.get("source_message_id") or ""),
                    "group_id": str(row.get("group_id") or ""),
                    "user_id": str(row.get("user_id") or ""),
                    "created_at": _safe_report_timestamp(
                        row.get("created_at")
                        or row.get("approval_created_at")
                        or row.get("add_time")
                    ),
                }
            )

    def add_trigger_log_failure(
        self,
        log_id: int | None,
        reason: str,
        *,
        row: Mapping[str, object] | None = None,
    ) -> None:
        self.skipped_trigger_log_rows += 1
        log_label = str(log_id) if log_id is not None else "unknown"
        self.trigger_log_failures.append(f"{log_label}: {reason}")
        if row is not None:
            self.trigger_log_failure_details.append(
                {
                    "log_id": log_id,
                    "trigger_id": _safe_report_int(row.get("trigger_id")),
                    "reason": reason,
                    "message_id": str(row.get("message_id") or ""),
                    "user_id": str(row.get("user_id") or ""),
                    "call_time": _safe_report_timestamp(row.get("call_time")),
                }
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_rows": self.total_rows,
            "imported_rows": self.imported_rows,
            "imported_groups": self.imported_groups,
            "imported_response_items": self.imported_response_items,
            "imported_entries": self.imported_entries,
            "skipped_rows": self.skipped_rows,
            "status_counts": dict(self.status_counts),
            "skipped_reasons": dict(self.skipped_reasons),
            "image_counts": dict(self.image_counts),
            "image_resolution_counts": dict(self.image_resolution_counts),
            "imported_entry_ids": self.imported_entry_ids,
            "imported_group_ids": self.imported_group_ids,
            "response_distribution": dict(self.response_distribution),
            "failures": list(self.failures),
            "failure_details": list(self.failure_details),
            "failure_categories": self.to_failure_categories_dict(),
            "total_log_rows": self.total_log_rows,
            "imported_log_rows": self.imported_log_rows,
            "skipped_log_rows": self.skipped_log_rows,
            "log_failures": list(self.log_failures),
            "log_failure_details": list(self.log_failure_details),
            "total_trigger_log_rows": self.total_trigger_log_rows,
            "imported_trigger_log_rows": self.imported_trigger_log_rows,
            "skipped_trigger_log_rows": self.skipped_trigger_log_rows,
            "trigger_log_failures": list(self.trigger_log_failures),
            "trigger_log_failure_details": list(self.trigger_log_failure_details),
            "total_approval_ref_rows": self.total_approval_ref_rows,
            "imported_approval_ref_rows": self.imported_approval_ref_rows,
            "skipped_approval_ref_rows": self.skipped_approval_ref_rows,
            "approval_ref_failures": list(self.approval_ref_failures),
            "approval_ref_failure_details": list(self.approval_ref_failure_details),
        }

    @property
    def response_distribution(self) -> Counter[int]:
        distribution: Counter[int] = Counter()
        for count in self.response_count_by_group_id.values():
            distribution[count] += 1
        return distribution

    def to_failure_categories_dict(self) -> dict[str, object]:
        categories: dict[str, dict[str, object]] = {}
        for detail in self.failure_details:
            reason = str(detail.get("reason") or "")
            category = _categorize_failure_reason(reason)
            bucket = categories.setdefault(
                category,
                {
                    "count": 0,
                    "reasons": {},
                    "items": [],
                },
            )
            count = cast(int, bucket["count"])
            bucket["count"] = count + 1
            reasons = bucket["reasons"]
            if isinstance(reasons, dict):
                reasons[reason] = int(reasons.get(reason, 0)) + 1
            items = bucket["items"]
            if isinstance(items, list):
                items.append(detail)
        return {
            "total_failed": self.skipped_rows,
            "categories": categories,
        }


@dataclass(slots=True, frozen=True)
class LegacyImportTarget:
    scope: str
    group_id: str
    role: str = "any"
    call_count_min: int = 0
    call_count_max: int = 0
    call_count_window_seconds: int = 0

    @property
    def rule(self) -> dict[str, Any]:
        rule: dict[str, Any] = {}
        if self.role != "any":
            rule["roles"] = self.role
        if self.call_count_window_seconds > 0:
            rule["call_count"] = {
                "window_seconds": self.call_count_window_seconds,
                "min": self.call_count_min,
                "max": self.call_count_max,
            }
        return rule


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
        stem = path.stem.casefold()
        files_by_stem.setdefault(stem, path)
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


def normalize_legacy_scope(
    *,
    priority: int,
    response_rule_conditions: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    targets = normalize_legacy_rules(
        priority=priority,
        response_rule_conditions=response_rule_conditions,
    )
    if len(targets) != 1:
        raise MigrationError("legacy rule expands to multiple variants")
    target = targets[0]
    return target.scope, target.group_id, target.rule


def normalize_legacy_state(
    *,
    approval_status: str,
    response_available: bool,
    migration_time: int,
) -> LegacyEntryState:
    normalized = approval_status.strip().upper()
    if normalized == "APPROVED":
        if response_available:
            return LegacyEntryState(status="approved", enabled=1, deleted_at=0)
        return LegacyEntryState(
            status="approved",
            enabled=0,
            deleted_at=migration_time,
        )
    if normalized == "PENDING":
        return LegacyEntryState(status="pending", enabled=0, deleted_at=0)
    if normalized in {"REJECTED", "WITHDRAWN"}:
        return LegacyEntryState(status="rejected", enabled=0, deleted_at=0)
    raise MigrationError(f"unsupported approval status: {approval_status}")


def load_legacy_json(value: object) -> Any:
    if value in (None, ""):
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        return json.loads(value)
    raise MigrationError(f"unsupported json payload type: {type(value).__name__}")


def normalize_legacy_probability(trigger_config: object) -> float:
    payload = load_legacy_json(trigger_config)
    if not isinstance(payload, Mapping):
        return 1.0
    probability = payload.get("probability", 1.0)
    try:
        normalized = float(probability)
    except (TypeError, ValueError) as exc:
        raise MigrationError("invalid trigger probability") from exc
    return min(max(normalized, 0.0), 1.0)


def normalize_legacy_timestamp(value: object, *, fallback: int) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return int(value.replace(tzinfo=UTC).timestamp())
        return int(value.timestamp())
    if isinstance(value, date):
        return int(
            datetime(
                value.year,
                value.month,
                value.day,
                tzinfo=UTC,
            ).timestamp()
        )
    if isinstance(value, (int, float)):
        return int(value)
    return fallback


def _normalize_legacy_log_message_type(
    row: Mapping[str, object],
    *,
    group_id: str,
) -> str:
    explicit = str(row.get("message_type") or "").strip().lower()
    if explicit in {"group", "private"}:
        return explicit
    if explicit:
        return explicit[:16]
    is_group = row.get("is_group")
    if isinstance(is_group, bool):
        return "group" if is_group else "private"
    return "group" if group_id else "unknown"


def _message_ref_shard_key(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y_%m")


def _normalize_legacy_add_source(value: object) -> dict[str, object]:
    payload = load_legacy_json(value)
    return dict(payload) if isinstance(payload, Mapping) else {}


def _legacy_addition_message_type(add_source: Mapping[str, object]) -> str:
    return "group" if str(add_source.get("group_id") or "").strip() else "private"


def _extract_legacy_saved_as(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        saved_as = value.get("saved_as")
        if isinstance(saved_as, str):
            return saved_as.strip()
    return ""


def _default_legacy_image_root(old_repo_root: Path) -> Path:
    return old_repo_root.parent / _LEGACY_IMAGE_REPO_NAME / _LEGACY_IMAGE_DIR_NAME


def _default_legacy_image_mapping_path(old_repo_root: Path) -> Path:
    return old_repo_root.parent / _LEGACY_IMAGE_REPO_NAME / _LEGACY_IMAGE_MAPPING_NAME


async def legacy_message_to_shape(
    payload: object,
    *,
    extra_info: object = None,
    image_catalog: LegacyImageCatalog,
    media_service: WordbankMediaService,
    report: WordbankMigrationReport | None = None,
) -> MessageShape:
    event_shape = _shape_from_legacy_extra_info(extra_info)
    if event_shape is not None:
        return event_shape

    segments = load_legacy_json(payload)
    if not isinstance(segments, list):
        raise MigrationError("legacy message payload must be a list")

    atoms: list[MessageAtom] = []
    for item in segments:
        if not isinstance(item, Mapping):
            continue
        segment_type = str(item.get("type", "") or "").strip().lower()
        if segment_type == "text":
            text_value = normalize_message_text(
                str(item.get("text", "") or ""),
                preserve_blank_text=True,
            )
            if text_value:
                atoms.append(MessageAtom(kind="text", text=text_value))
            continue
        if segment_type == "image":
            file_name = str(item.get("file", "") or "").strip()
            url = str(item.get("url", "") or "").strip()
            image_path, resolution_source = image_catalog.resolve_with_source(
                file_name,
                url=url,
            )
            if image_path is None:
                raise MigrationError(f"image file not found: {file_name or url}")
            data = await asyncio.to_thread(image_path.read_bytes)
            source_name = file_name or url or image_path.name
            _validate_legacy_image_bytes(data, source=source_name)
            image = await media_service.ingest_image_bytes(data)
            if report is not None:
                report.image_counts[image_path.suffix.lower()] += 1
                report.image_resolution_counts[resolution_source] += 1
            atoms.append(
                MessageAtom(kind="image", canonical_image_id=image.canonical_id)
            )
            continue
        if segment_type == "at":
            target_id = str(item.get("qq", "") or "").strip()
            if target_id:
                atoms.append(MessageAtom(kind="at", target_id=target_id))
            continue
        if segment_type == "face":
            face_id = str(item.get("id", "") or "").strip()
            placeholder = normalize_text(f"[face:{face_id}]")
            if placeholder:
                atoms.append(MessageAtom(kind="text", text=placeholder))
            continue

    return MessageShape(tuple(atoms))


async def migrate_legacy_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    repository: WordbankRepository,
    media_service: WordbankMediaService,
    image_catalog: LegacyImageCatalog,
    response_log_rows: Sequence[Mapping[str, object]] = (),
    trigger_log_rows: Sequence[Mapping[str, object]] = (),
    addition_log_rows: Sequence[Mapping[str, object]] = (),
    message_approval_rows: Sequence[Mapping[str, object]] = (),
    reset_target: bool = True,
    progress: LegacyMigrationProgressCallback | None = None,
    progress_every: int = 100,
) -> WordbankMigrationReport:
    await repository.init_all_tables()
    if reset_target:
        await repository.reset_all_data(include_images=True, include_logs=True)
    await media_service.rebuild_cache()

    report = WordbankMigrationReport(total_rows=len(rows))
    migration_time = get_current_time()
    imported_log_targets: dict[int, LegacyImportedLogTarget] = {}
    imported_trigger_targets: dict[int, LegacyImportedLogTarget] = {}
    _emit_progress(
        progress,
        phase="entries",
        current=0,
        total=len(rows),
        detail={"reset_target": reset_target},
    )

    for index, row in enumerate(rows, start=1):
        response_id = _coerce_int(row["response_id"], field="response_id")
        try:
            trigger_shape = await legacy_message_to_shape(
                row.get("trigger_text"),
                extra_info=row.get("extra_info"),
                image_catalog=image_catalog,
                media_service=media_service,
                report=report,
            )
            response_shape = await legacy_message_to_shape(
                row.get("response_text"),
                image_catalog=image_catalog,
                media_service=media_service,
                report=report,
            )
            if trigger_shape.is_empty():
                trigger_shape = shape_from_text(" ", preserve_blank_text=True)
            if response_shape.is_empty():
                raise MigrationError("empty response shape")

            targets = normalize_legacy_rules(
                priority=_coerce_int(row["priority"], field="priority"),
                response_rule_conditions=_coerce_rule_mapping(
                    row.get("response_rule_conditions")
                ),
                trigger_config=row.get("trigger_config"),
            )
            state = normalize_legacy_state(
                approval_status=str(row.get("approval_status", "PENDING")),
                response_available=bool(row.get("response_available", False)),
                migration_time=migration_time,
            )
            created_at = normalize_legacy_timestamp(
                row.get("created_at"),
                fallback=migration_time,
            )
            updated_at = max(created_at, state.deleted_at or created_at)
            probability = normalize_legacy_probability(row.get("trigger_config"))
            weight = _coerce_int(row.get("weight", 3), field="weight")
            created_by = str(row.get("created_by") or "")
            imported_ids: list[int] = []
            primary_log_target: LegacyImportedLogTarget | None = None
            for target in targets:
                entry = await repository.import_message_entry(
                    trigger_shape=trigger_shape,
                    response_shape=response_shape,
                    rule=target.rule,
                    scope=target.scope,
                    priority=SCOPE_PRIORITY[target.scope],
                    probability=probability,
                    weight=weight,
                    group_id=target.group_id,
                    created_by=created_by,
                    status=state.status,
                    enabled=state.enabled,
                    approved_by="",
                    deleted_at=state.deleted_at,
                    created_at=created_at,
                    updated_at=updated_at,
                )
                imported_ids.append(entry.response_item_id)
                if primary_log_target is None:
                    trigger_variants = entry.trigger_group.trigger_variants
                    if not trigger_variants:
                        raise MigrationError("imported trigger group has no variants")
                    primary_log_target = LegacyImportedLogTarget(
                        trigger_group_id=entry.trigger_group_id,
                        trigger_variant_id=trigger_variants[0].id,
                        response_item_id=entry.response_item_id,
                    )
                report.imported_group_ids[response_id] = entry.trigger_group_id
                report.response_count_by_group_id[entry.trigger_group_id] = (
                    report.response_count_by_group_id.get(entry.trigger_group_id, 0) + 1
                )
                if entry.created_group:
                    report.imported_groups += 1
                report.imported_response_items += 1
                report.imported_entries += 1
                report.status_counts[entry.status] += 1
            report.imported_entry_ids[response_id] = imported_ids
            if primary_log_target is not None:
                imported_log_targets[response_id] = primary_log_target
                trigger_id = _optional_coerce_int(
                    row.get("trigger_id"),
                    field="trigger_id",
                )
                if trigger_id is not None:
                    imported_trigger_targets.setdefault(trigger_id, primary_log_target)
        except Exception as exc:
            report.add_failure(response_id, str(exc), row=row)
        else:
            report.imported_rows += 1
        _emit_progress_if_needed(
            progress,
            phase="entries",
            current=index,
            total=len(rows),
            every=progress_every,
            detail={
                "imported_rows": report.imported_rows,
                "skipped_rows": report.skipped_rows,
                "last_response_id": response_id,
            },
        )

    if response_log_rows:
        imported_message_ids = await migrate_legacy_response_logs(
            response_log_rows,
            repository=repository,
            imported_targets=imported_log_targets,
            report=report,
            progress=progress,
            progress_every=progress_every,
        )
    else:
        imported_message_ids = set()
    if trigger_log_rows:
        await migrate_legacy_trigger_logs(
            trigger_log_rows,
            repository=repository,
            imported_targets=imported_trigger_targets,
            imported_message_ids=imported_message_ids,
            report=report,
            progress=progress,
            progress_every=progress_every,
        )
    if message_approval_rows:
        await migrate_legacy_approval_message_refs(
            message_approval_rows,
            repository=repository,
            imported_targets=imported_log_targets,
            addition_log_rows=addition_log_rows,
            report=report,
            progress=progress,
            progress_every=progress_every,
        )

    _emit_progress(
        progress,
        phase="search_index",
        current=0,
        total=1,
        detail={},
    )
    await repository.rebuild_search_index()
    _emit_progress(
        progress,
        phase="search_index",
        current=1,
        total=1,
        detail={},
    )
    return report


async def migrate_legacy_response_logs(
    rows: Sequence[Mapping[str, object]],
    *,
    repository: WordbankRepository,
    imported_targets: Mapping[int, LegacyImportedLogTarget],
    report: WordbankMigrationReport | None = None,
    progress: LegacyMigrationProgressCallback | None = None,
    progress_every: int = 100,
) -> set[str]:
    imported_message_ids: set[str] = set()
    if report is not None:
        report.total_log_rows += len(rows)
    _emit_progress(
        progress,
        phase="response_logs",
        current=0,
        total=len(rows),
        detail={},
    )
    for index, row in enumerate(rows, start=1):
        log_id = _optional_coerce_int(row.get("log_id"), field="log_id")
        try:
            response_id = _coerce_int(row["response_id"], field="response_id")
            imported_target = imported_targets.get(response_id)
            if imported_target is None:
                raise MigrationError(
                    f"missing imported response mapping for response_id={response_id}"
                )
            created_at = normalize_legacy_timestamp(
                row.get("call_time"),
                fallback=get_current_time(),
            )
            group_id = str(row.get("group_id") or "").strip()
            message_type = _normalize_legacy_log_message_type(row, group_id=group_id)
            await repository.save_log(
                {
                    "trigger_group_id": imported_target.trigger_group_id,
                    "trigger_variant_id": imported_target.trigger_variant_id,
                    "response_item_id": imported_target.response_item_id,
                    "group_id": group_id,
                    "user_id": str(row.get("user_id") or ""),
                    "message_type": message_type,
                    "created_at": created_at,
                },
                policy=WritePolicy.IMMEDIATE,
            )
            message_id = str(row.get("message_id") or "").strip()
            if message_id:
                imported_message_ids.add(message_id)
            if report is not None:
                report.imported_log_rows += 1
        except Exception as exc:
            if report is not None:
                report.add_log_failure(log_id, str(exc), row=row)
        _emit_progress_if_needed(
            progress,
            phase="response_logs",
            current=index,
            total=len(rows),
            every=progress_every,
            detail={
                "imported_log_rows": report.imported_log_rows if report else 0,
                "skipped_log_rows": report.skipped_log_rows if report else 0,
                "last_log_id": log_id or 0,
            },
        )
    await repository.drain_logs()
    return imported_message_ids


async def migrate_legacy_trigger_logs(
    rows: Sequence[Mapping[str, object]],
    *,
    repository: WordbankRepository,
    imported_targets: Mapping[int, LegacyImportedLogTarget],
    imported_message_ids: set[str],
    report: WordbankMigrationReport | None = None,
    progress: LegacyMigrationProgressCallback | None = None,
    progress_every: int = 100,
) -> None:
    if report is not None:
        report.total_trigger_log_rows += len(rows)
    _emit_progress(
        progress,
        phase="trigger_logs",
        current=0,
        total=len(rows),
        detail={},
    )
    for index, row in enumerate(rows, start=1):
        log_id = _optional_coerce_int(row.get("log_id"), field="log_id")
        message_id = str(row.get("message_id") or "").strip()
        try:
            if message_id and message_id in imported_message_ids:
                continue
            trigger_id = _coerce_int(row["trigger_id"], field="trigger_id")
            imported_target = imported_targets.get(trigger_id)
            if imported_target is None:
                raise MigrationError(
                    f"missing imported trigger mapping for trigger_id={trigger_id}"
                )
            created_at = normalize_legacy_timestamp(
                row.get("call_time"),
                fallback=get_current_time(),
            )
            await repository.save_log(
                {
                    "trigger_group_id": imported_target.trigger_group_id,
                    "trigger_variant_id": imported_target.trigger_variant_id,
                    "response_item_id": imported_target.response_item_id,
                    "group_id": "",
                    "user_id": str(row.get("user_id") or ""),
                    "message_type": "unknown",
                    "created_at": created_at,
                },
                policy=WritePolicy.IMMEDIATE,
            )
            if message_id:
                imported_message_ids.add(message_id)
            if report is not None:
                report.imported_trigger_log_rows += 1
        except Exception as exc:
            if report is not None:
                report.add_trigger_log_failure(log_id, str(exc), row=row)
        _emit_progress_if_needed(
            progress,
            phase="trigger_logs",
            current=index,
            total=len(rows),
            every=progress_every,
            detail={
                "imported_trigger_log_rows": (
                    report.imported_trigger_log_rows if report else 0
                ),
                "skipped_trigger_log_rows": (
                    report.skipped_trigger_log_rows if report else 0
                ),
                "last_log_id": log_id or 0,
            },
        )
    await repository.drain_logs()


async def migrate_legacy_approval_message_refs(
    rows: Sequence[Mapping[str, object]],
    *,
    repository: WordbankRepository,
    imported_targets: Mapping[int, LegacyImportedLogTarget],
    addition_log_rows: Sequence[Mapping[str, object]] = (),
    report: WordbankMigrationReport | None = None,
    progress: LegacyMigrationProgressCallback | None = None,
    progress_every: int = 100,
) -> None:
    addition_by_approval_id: dict[int, Mapping[str, object]] = {}
    addition_by_response_id: dict[int, Mapping[str, object]] = {}
    for addition_row in addition_log_rows:
        approval_id = _optional_coerce_int(
            addition_row.get("approval_id"),
            field="approval_id",
        )
        response_id = _optional_coerce_int(
            addition_row.get("response_id"),
            field="response_id",
        )
        if approval_id is not None:
            addition_by_approval_id.setdefault(approval_id, addition_row)
        if response_id is not None:
            addition_by_response_id.setdefault(response_id, addition_row)

    if report is not None:
        report.total_approval_ref_rows += len(rows)
    _emit_progress(
        progress,
        phase="approval_refs",
        current=0,
        total=len(rows),
        detail={},
    )

    for index, row in enumerate(rows, start=1):
        message_id = str(row.get("message_id") or "").strip()
        try:
            if not message_id:
                raise MigrationError("approval message_id is empty")
            response_id = _coerce_int(row["response_id"], field="response_id")
            imported_target = imported_targets.get(response_id)
            if imported_target is None:
                raise MigrationError(
                    f"missing imported response mapping for response_id={response_id}"
                )
            approval_id = _optional_coerce_int(
                row.get("approval_id"),
                field="approval_id",
            )
            addition_row = (
                addition_by_approval_id.get(approval_id)
                if approval_id is not None
                else None
            ) or addition_by_response_id.get(response_id)
            add_source = _normalize_legacy_add_source(
                addition_row.get("add_source") if addition_row is not None else None
            )
            created_at = normalize_legacy_timestamp(
                row.get("approval_created_at")
                or (addition_row.get("add_time") if addition_row is not None else None),
                fallback=get_current_time(),
            )
            user_id = str(
                add_source.get("user_id")
                or (
                    addition_row.get("user_id")
                    if addition_row is not None
                    else row.get("approval_user_id")
                )
                or ""
            ).strip()
            group_id = str(add_source.get("group_id") or "").strip()
            source_message_id = str(
                addition_row.get("created_message_id")
                if addition_row is not None
                else ""
            ).strip()
            await repository.record_message_ref(
                {
                    "message_id": message_id,
                    "ref_kind": "approval",
                    "shard_key": _message_ref_shard_key(created_at),
                    "trigger_group_id": imported_target.trigger_group_id,
                    "trigger_variant_id": imported_target.trigger_variant_id,
                    "response_item_id": imported_target.response_item_id,
                    "group_id": group_id,
                    "user_id": user_id,
                    "message_type": _legacy_addition_message_type(add_source),
                    "source_message_id": source_message_id,
                    "context_type": "",
                    "current_page": 1,
                    "keyword": "",
                    "field": "",
                    "creator_id": "",
                    "has_image": 0,
                    "group_ids_json": "[]",
                    "created_at": created_at,
                    "updated_at": created_at,
                }
            )
            if report is not None:
                report.imported_approval_ref_rows += 1
        except Exception as exc:
            if report is not None:
                report.add_approval_ref_failure(message_id, str(exc), row=row)
        _emit_progress_if_needed(
            progress,
            phase="approval_refs",
            current=index,
            total=len(rows),
            every=progress_every,
            detail={
                "imported_approval_ref_rows": (
                    report.imported_approval_ref_rows if report else 0
                ),
                "skipped_approval_ref_rows": (
                    report.skipped_approval_ref_rows if report else 0
                ),
                "last_message_id": message_id,
            },
        )


async def migrate_legacy_wordbank(
    old_repo_root: Path,
    *,
    repository: WordbankRepository,
    media_service: WordbankMediaService,
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
    resolved_image_root = image_root or _default_legacy_image_root(old_repo_root)
    resolved_mapping_path = (
        mapping_path
        if mapping_path is not None
        else _default_legacy_image_mapping_path(old_repo_root)
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


def _emit_progress(
    callback: LegacyMigrationProgressCallback | None,
    *,
    phase: str,
    current: int,
    total: int,
    detail: Mapping[str, object],
) -> None:
    if callback is None:
        return
    callback(phase, current, total, detail)


def _emit_progress_if_needed(
    callback: LegacyMigrationProgressCallback | None,
    *,
    phase: str,
    current: int,
    total: int,
    every: int,
    detail: Mapping[str, object],
) -> None:
    if callback is None:
        return
    if total <= 0:
        callback(phase, current, total, detail)
        return
    step = max(1, every)
    if current == 1 or current == total or current % step == 0:
        callback(phase, current, total, detail)


def _coerce_rule_mapping(value: object) -> Mapping[str, Any]:
    payload = load_legacy_json(value)
    if isinstance(payload, Mapping):
        return payload
    raise MigrationError("legacy rule conditions must be a mapping")


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
                raise MigrationError(f"invalid integer for {field}: {value}") from exc
    raise MigrationError(f"invalid integer for {field}: {value!r}")


def _optional_coerce_int(value: object, *, field: str) -> int | None:
    if value in (None, ""):
        return None
    return _coerce_int(value, field=field)


def _extract_legacy_eq(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    raw_value = value.get("$eq")
    if raw_value in (None, ""):
        return ""
    return str(raw_value)


def normalize_legacy_rules(
    *,
    priority: int,
    response_rule_conditions: Mapping[str, Any],
    trigger_config: object = None,
) -> list[LegacyImportTarget]:
    branches = _expand_legacy_rule_tree(response_rule_conditions)
    targets = [
        _normalize_legacy_branch(
            priority=priority,
            branch=branch,
            trigger_config=trigger_config,
        )
        for branch in branches
    ]
    return _prune_subsumed_targets(targets)


def _normalize_legacy_branch(
    *,
    priority: int,
    branch: Mapping[str, object],
    trigger_config: object,
) -> LegacyImportTarget:
    group_id = str(branch.get("group_id") or "")
    user_id = str(branch.get("user_id") or "")
    role = str(branch.get("role") or "any")
    has_call_count = isinstance(branch.get("call_count"), tuple)
    has_non_scope_constraints = role != "any" or has_call_count

    if group_id and user_id:
        scope = "self_in_current_group"
    elif user_id:
        scope = "self"
    elif group_id:
        scope = "current_group"
    elif priority == 3:
        scope = "all_groups"
    elif priority == 2:
        # Old data can contain rules like {"$or": [{"group_id": ...}, {}]}.
        # The empty branch semantically widens the rule to global scope.
        scope = "all_groups" if not group_id and not user_id else "current_group"
    elif priority == 1:
        # Priority in the legacy schema was inferred from the whole rule, not each
        # expanded branch. Branches without user/group restrictions should widen to
        # the least restrictive compatible scope.
        scope = "all_groups" if has_non_scope_constraints else "self"
    else:
        raise MigrationError(f"unsupported priority: {priority}")
    if role not in {"any", *_LEGACY_ROLES}:
        raise MigrationError(f"unsupported role value: {role}")

    call_count_min = 0
    call_count_max = 0
    call_count_window_seconds = 0
    raw_call_count = branch.get("call_count")
    if isinstance(raw_call_count, tuple):
        call_count_min = int(raw_call_count[0])
        call_count_max = int(raw_call_count[1])
        call_count_window_seconds = _resolve_legacy_call_window_seconds(trigger_config)

    return LegacyImportTarget(
        scope=scope,
        group_id=group_id,
        role=role,
        call_count_min=call_count_min,
        call_count_max=call_count_max,
        call_count_window_seconds=call_count_window_seconds,
    )


def _expand_legacy_rule_tree(rule: Mapping[str, Any]) -> list[dict[str, object]]:
    unknown_keys = set(rule) - _LEGACY_RULE_KEYS
    if unknown_keys:
        fields = ",".join(sorted(unknown_keys))
        raise MigrationError(f"unsupported rule keys: {fields}")

    branches: list[dict[str, object]] = [{}]
    for key, value in rule.items():
        field_branches = _expand_legacy_rule_item(key, value)
        branches = _combine_legacy_branches(branches, field_branches)
        if not branches:
            raise MigrationError("legacy rule resolves to no valid branch")
    return branches


def _expand_legacy_rule_item(key: str, value: object) -> list[dict[str, object]]:
    if key == "$and":
        items = _coerce_rule_list(value, field="$and")
        branches: list[dict[str, object]] = [{}]
        for item in items:
            expanded = _expand_legacy_rule_tree(item)
            branches = _combine_legacy_branches(branches, expanded)
        return branches or [{}]
    if key == "$or":
        items = _coerce_rule_list(value, field="$or")
        branches: list[dict[str, object]] = []
        for item in items:
            branches.extend(_expand_legacy_rule_tree(item))
        return _dedupe_legacy_branches(branches or [{}])
    if key == "group_id":
        group_id = _extract_required_legacy_eq(value, field="group_id")
        return [{"group_id": group_id}] if group_id else [{}]
    if key == "user_id":
        user_id = _extract_required_legacy_eq(value, field="user_id")
        return [{"user_id": user_id}] if user_id else [{}]
    if key == "role":
        role = _extract_legacy_role(value)
        return [{"role": role}] if role != "any" else [{}]
    if key == "call_count":
        return [{"call_count": _extract_legacy_call_count_bounds(value)}]
    raise MigrationError(f"unsupported rule keys: {key}")


def _coerce_rule_list(value: object, *, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise MigrationError(f"{field} must be a rule list")
    items: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise MigrationError(f"{field} contains a non-mapping rule")
        items.append(item)
    return items


def _combine_legacy_branches(
    left: Sequence[Mapping[str, object]],
    right: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    combined: list[dict[str, object]] = []
    for left_branch in left:
        for right_branch in right:
            merged = _merge_legacy_branches(left_branch, right_branch)
            if merged is not None:
                combined.append(merged)
    return _dedupe_legacy_branches(combined)


def _merge_legacy_branches(
    left: Mapping[str, object],
    right: Mapping[str, object],
) -> dict[str, object] | None:
    merged = dict(left)
    for key, value in right.items():
        if key not in merged:
            merged[key] = value
            continue
        resolved = _merge_legacy_field_value(key, merged[key], value)
        if resolved is None:
            return None
        merged[key] = resolved
    return merged


def _merge_legacy_field_value(
    key: str,
    left: object,
    right: object,
) -> object | None:
    if key in {"group_id", "user_id", "role"}:
        return left if left == right else None
    if key == "call_count":
        if not isinstance(left, tuple) or not isinstance(right, tuple):
            raise MigrationError("invalid call_count merge state")
        left_min, left_max = int(left[0]), int(left[1])
        right_min, right_max = int(right[0]), int(right[1])
        merged_min = max(left_min, right_min)
        merged_max = _intersect_upper_bound(left_max, right_max)
        if merged_max != 0 and merged_min > merged_max:
            return None
        return (merged_min, merged_max)
    raise MigrationError(f"unsupported rule keys: {key}")


def _dedupe_legacy_branches(
    branches: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[tuple[tuple[str, object], ...]] = set()
    for branch in branches:
        key = tuple(sorted(branch.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(branch))
    return deduped


def _extract_required_legacy_eq(value: object, *, field: str) -> str:
    extracted = _extract_legacy_eq(value)
    if not extracted:
        raise MigrationError(f"{field} must use $eq")
    return extracted


def _extract_legacy_role(value: object) -> str:
    role = _extract_required_legacy_eq(value, field="role").strip().lower()
    if role not in _LEGACY_ROLES:
        raise MigrationError(f"unsupported role value: {role}")
    return role


def _extract_legacy_call_count_bounds(value: object) -> tuple[int, int]:
    if not isinstance(value, Mapping):
        raise MigrationError("call_count must use comparison operators")
    minimum = 0
    maximum = 0
    upper_bounded = False
    for operator, raw in value.items():
        if operator == "$gt":
            minimum = max(minimum, _coerce_int(raw, field="call_count") + 1)
        elif operator == "$gte":
            minimum = max(minimum, _coerce_int(raw, field="call_count"))
        elif operator == "$lt":
            upper_bounded = True
            maximum = _intersect_upper_bound(
                maximum if upper_bounded else 0,
                _coerce_int(raw, field="call_count") - 1,
            )
        elif operator == "$lte":
            upper_bounded = True
            maximum = _intersect_upper_bound(
                maximum if upper_bounded else 0,
                _coerce_int(raw, field="call_count"),
            )
        elif operator == "$range":
            if (
                not isinstance(raw, Sequence)
                or isinstance(raw, str | bytes | bytearray)
                or len(raw) != 2
            ):
                raise MigrationError("call_count $range must contain two integers")
            minimum = max(minimum, _coerce_int(raw[0], field="call_count"))
            upper_bounded = True
            maximum = _intersect_upper_bound(
                maximum if upper_bounded else 0,
                _coerce_int(raw[1], field="call_count"),
            )
        else:
            raise MigrationError(f"unsupported call_count operator: {operator}")
    if upper_bounded and maximum != 0 and minimum > maximum:
        raise MigrationError("call_count rule is contradictory")
    return (minimum, maximum if upper_bounded else 0)


def _intersect_upper_bound(left: int, right: int) -> int:
    if left == 0:
        return right
    if right == 0:
        return left
    return min(left, right)


def _resolve_legacy_call_window_seconds(trigger_config: object) -> int:
    payload = load_legacy_json(trigger_config)
    if not isinstance(payload, Mapping):
        return _LEGACY_ALL_TIME_WINDOW_SECONDS
    lifecycle = payload.get("lifecycle")
    if lifecycle in (None, ""):
        return _LEGACY_ALL_TIME_WINDOW_SECONDS
    try:
        seconds = int(float(lifecycle))
    except (TypeError, ValueError):
        return _LEGACY_ALL_TIME_WINDOW_SECONDS
    return seconds if seconds > 0 else _LEGACY_ALL_TIME_WINDOW_SECONDS


def _prune_subsumed_targets(
    targets: Sequence[LegacyImportTarget],
) -> list[LegacyImportTarget]:
    unique_targets: list[LegacyImportTarget] = []
    seen: set[tuple[object, ...]] = set()
    for target in targets:
        key = (
            target.scope,
            target.group_id,
            target.role,
            target.call_count_min,
            target.call_count_max,
            target.call_count_window_seconds,
        )
        if key in seen:
            continue
        seen.add(key)
        unique_targets.append(target)

    pruned: list[LegacyImportTarget] = []
    for candidate in unique_targets:
        if any(
            other != candidate and _legacy_target_subsumes(other, candidate)
            for other in unique_targets
        ):
            continue
        pruned.append(candidate)
    return pruned


def _legacy_target_subsumes(
    left: LegacyImportTarget,
    right: LegacyImportTarget,
) -> bool:
    return (
        _legacy_scope_subsumes(left, right)
        and _legacy_role_subsumes(left.role, right.role)
        and _legacy_call_count_subsumes(left, right)
    )


def _legacy_scope_subsumes(left: LegacyImportTarget, right: LegacyImportTarget) -> bool:
    if left.scope == right.scope and left.group_id == right.group_id:
        return True
    if left.scope == "all_groups" and right.scope == "current_group":
        return True
    if left.scope == "current_group" and right.scope == "self_in_current_group":
        return left.group_id == right.group_id
    if left.scope == "self" and right.scope == "self_in_current_group":
        return True
    return False


def _legacy_role_subsumes(left: str, right: str) -> bool:
    return left == "any" or left == right


def _legacy_call_count_subsumes(
    left: LegacyImportTarget,
    right: LegacyImportTarget,
) -> bool:
    if left.call_count_window_seconds == 0:
        return True
    if right.call_count_window_seconds == 0:
        return False
    if left.call_count_window_seconds != right.call_count_window_seconds:
        return False
    right_max = right.call_count_max or 10**18
    left_max = left.call_count_max or 10**18
    return left.call_count_min <= right.call_count_min and left_max >= right_max


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


def _shape_from_legacy_extra_info(extra_info: object) -> MessageShape | None:
    if extra_info in (None, ""):
        return None
    payload = load_legacy_json(extra_info)
    if not isinstance(payload, Mapping):
        return None
    action = str(payload.get("action", "") or "").strip().upper()
    event_name = _LEGACY_EVENT_NAMES.get(action)
    if not event_name:
        raise MigrationError(f"unsupported legacy event action: {action}")
    return shape_from_event(event_name)


def _safe_jsonish(value: object) -> object:
    try:
        return load_legacy_json(value)
    except Exception:
        return value


def _safe_report_int(value: object) -> int | None:
    try:
        return _coerce_int(value, field="report")
    except Exception:
        return None


def _safe_report_timestamp(value: object) -> int | None:
    try:
        return normalize_legacy_timestamp(value, fallback=0)
    except Exception:
        return None


def infer_report_response_available(approval_status: object) -> bool:
    normalized = str(approval_status or "").strip().upper()
    return normalized == "APPROVED"


def rebuild_legacy_row_from_failure_detail(
    detail: Mapping[str, object],
) -> dict[str, object]:
    trigger_summary = detail.get("trigger")
    response_summary = detail.get("response")
    trigger_kind = (
        str(trigger_summary.get("kind") or "")
        if isinstance(trigger_summary, Mapping)
        else ""
    )
    response_kind = (
        str(response_summary.get("kind") or "")
        if isinstance(response_summary, Mapping)
        else ""
    )

    trigger_segments = (
        trigger_summary.get("segments", [])
        if isinstance(trigger_summary, Mapping) and trigger_kind == "message"
        else []
    )
    response_segments = (
        response_summary.get("segments", [])
        if isinstance(response_summary, Mapping) and response_kind == "message"
        else []
    )
    extra_info: object = None
    if isinstance(trigger_summary, Mapping) and trigger_kind == "event":
        extra_info = trigger_summary.get("extra_info")

    return {
        "response_id": _coerce_int(detail.get("response_id"), field="response_id"),
        "trigger_id": _coerce_int(detail.get("trigger_id"), field="trigger_id"),
        "response_text": json.dumps(response_segments, ensure_ascii=False),
        "response_rule_conditions": detail.get("response_rule_conditions") or {},
        "weight": _coerce_int(detail.get("weight", 3), field="weight"),
        "priority": _coerce_int(detail.get("priority", 3), field="priority"),
        "created_by": str(detail.get("created_by") or ""),
        "created_at": normalize_legacy_timestamp(
            detail.get("created_at"),
            fallback=get_current_time(),
        ),
        "response_available": infer_report_response_available(
            detail.get("approval_status")
        ),
        "trigger_text": json.dumps(trigger_segments, ensure_ascii=False),
        "trigger_config": detail.get("trigger_config") or {},
        "extra_info": extra_info,
        "approval_status": str(detail.get("approval_status") or "PENDING"),
    }


def rebuild_legacy_rows_from_failure_details(
    details: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    return [rebuild_legacy_row_from_failure_detail(detail) for detail in details]


def extract_failure_details_from_categorized_report(
    payload: Mapping[str, object],
    *,
    categories: Sequence[str] | None = None,
) -> list[dict[str, object]]:
    raw_categories = payload.get("categories")
    if not isinstance(raw_categories, Mapping):
        raise MigrationError("categorized report missing categories")

    selected_categories = list(categories or ())
    if not selected_categories or selected_categories == ["all"]:
        selected_categories = list(raw_categories.keys())

    details: list[dict[str, object]] = []
    for category in selected_categories:
        bucket = raw_categories.get(category)
        if not isinstance(bucket, Mapping):
            continue
        items = bucket.get("items")
        if not isinstance(items, Sequence) or isinstance(
            items, (str, bytes, bytearray)
        ):
            continue
        for item in items:
            if isinstance(item, Mapping):
                details.append(dict(item))
    return details


def _summarize_legacy_message_payload(
    payload: object,
    *,
    extra_info: object = None,
) -> dict[str, object]:
    if extra_info not in (None, ""):
        return {
            "kind": "event",
            "extra_info": _safe_jsonish(extra_info),
        }

    loaded = _safe_jsonish(payload)
    if not isinstance(loaded, list):
        return {
            "kind": "raw",
            "value": loaded,
        }

    summary_segments: list[dict[str, object]] = []
    text_preview_parts: list[str] = []
    for segment in loaded[:12]:
        if not isinstance(segment, Mapping):
            summary_segments.append({"type": "unknown", "value": str(segment)})
            continue
        segment_type = str(segment.get("type") or "")
        if segment_type == "text":
            text_value = str(segment.get("text") or "")
            if text_value:
                text_preview_parts.append(text_value)
            summary_segments.append(
                {
                    "type": "text",
                    "text": _truncate_text(text_value, limit=80),
                }
            )
            continue
        if segment_type == "image":
            summary_segments.append(
                {
                    "type": "image",
                    "file": str(segment.get("file") or ""),
                    "url": str(segment.get("url") or ""),
                }
            )
            continue
        if segment_type == "at":
            summary_segments.append(
                {
                    "type": "at",
                    "qq": str(segment.get("qq") or ""),
                }
            )
            continue
        if segment_type == "face":
            summary_segments.append(
                {
                    "type": "face",
                    "id": str(segment.get("id") or ""),
                }
            )
            continue
        summary_segments.append(
            {
                "type": segment_type or "unknown",
                "data": dict(segment),
            }
        )

    return {
        "kind": "message",
        "segment_count": len(loaded),
        "text_preview": _truncate_text("".join(text_preview_parts), limit=120),
        "segments": summary_segments,
    }


def _truncate_text(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def _validate_legacy_image_bytes(data: bytes, *, source: str) -> None:
    if not data:
        raise MigrationError(f"image file empty: {source}")
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
    except UnidentifiedImageError as exc:
        raise MigrationError(f"image file is not a valid image: {source}") from exc
    except OSError as exc:
        raise MigrationError(f"image file decode failed: {source}") from exc


def _categorize_failure_reason(reason: str) -> str:
    if reason.startswith("image file empty:"):
        return "image_file_empty"
    if reason.startswith("image file is not a valid image:"):
        return "image_file_invalid"
    if reason.startswith("image file decode failed:"):
        return "image_file_decode_failed"
    if reason.startswith("image file not found:"):
        return "image_file_missing"
    if reason.startswith("empty trigger shape"):
        return "trigger_shape_empty"
    if reason.startswith("empty response shape"):
        return "response_shape_empty"
    if "unsupported rule" in reason or "priority=" in reason:
        return "rule_invalid"
    if "invalid trigger probability" in reason:
        return "trigger_config_invalid"
    if "approval status" in reason:
        return "approval_status_invalid"
    return "other"


__all__ = [
    "LegacyImageCatalog",
    "LegacyImportTarget",
    "LegacyPgConfig",
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
]
