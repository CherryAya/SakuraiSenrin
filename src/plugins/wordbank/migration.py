"""Legacy wordbank migration helpers."""

from __future__ import annotations

import ast
import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
import json
from pathlib import Path
import re
from typing import Any

from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.message_model import (
    MessageAtom,
    MessageShape,
    normalize_text,
    shape_from_event,
)
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import SCOPE_PRIORITY

_HEX_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_LEGACY_EVENT_NAMES = {
    "AT_MENTIONED": "event:at",
    "POKE_MENTIONED": "event:poke",
    "GROUP_JOIN": "event:join",
    "GROUP_LEAVE": "event:leave",
}
_SUPPORTED_RULE_KEYS = {"group_id", "user_id"}


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
    saved_as_by_name: dict[str, str]

    def resolve(self, file_name: str, *, url: str = "") -> Path | None:
        candidates = [
            file_name.strip(),
            Path(file_name).name.strip(),
            Path(url).name.strip(),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            normalized = candidate.casefold()
            direct = self.files_by_name.get(normalized)
            if direct is not None:
                return direct
            mapped_name = self.saved_as_by_name.get(normalized)
            if mapped_name:
                mapped_path = self.files_by_name.get(mapped_name.casefold())
                if mapped_path is not None:
                    return mapped_path
            stem = Path(candidate).stem.strip()
            if stem:
                by_stem = self.files_by_stem.get(stem.casefold())
                if by_stem is not None:
                    return by_stem
                if _HEX_MD5_RE.fullmatch(stem):
                    for suffix in (".webp", ".gif", ".png", ".jpg", ".jpeg"):
                        path = self.image_root / f"{stem.upper()}{suffix}"
                        if path.is_file():
                            return path
        return None


@dataclass(slots=True, frozen=True)
class LegacyEntryState:
    status: str
    enabled: int
    deleted_at: int


@dataclass(slots=True)
class WordbankMigrationReport:
    total_rows: int = 0
    imported_rows: int = 0
    skipped_rows: int = 0
    status_counts: Counter[str] = field(default_factory=Counter)
    skipped_reasons: Counter[str] = field(default_factory=Counter)
    image_counts: Counter[str] = field(default_factory=Counter)
    imported_entry_ids: dict[int, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def add_failure(self, response_id: int, reason: str) -> None:
        self.skipped_rows += 1
        self.skipped_reasons[reason] += 1
        self.failures.append(f"{response_id}: {reason}")

    def to_dict(self) -> dict[str, object]:
        return {
            "total_rows": self.total_rows,
            "imported_rows": self.imported_rows,
            "skipped_rows": self.skipped_rows,
            "status_counts": dict(self.status_counts),
            "skipped_reasons": dict(self.skipped_reasons),
            "image_counts": dict(self.image_counts),
            "imported_entry_ids": self.imported_entry_ids,
            "failures": list(self.failures),
        }


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


def build_legacy_image_catalog(
    image_root: Path,
    mapping_path: Path | None,
) -> LegacyImageCatalog:
    files_by_name: dict[str, Path] = {}
    files_by_stem: dict[str, Path] = {}
    for path in image_root.iterdir():
        if not path.is_file():
            continue
        files_by_name[path.name.casefold()] = path
        stem = path.stem.casefold()
        files_by_stem.setdefault(stem, path)

    saved_as_by_name: dict[str, str] = {}
    if mapping_path and mapping_path.is_file():
        raw_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        if isinstance(raw_mapping, dict):
            for key, value in raw_mapping.items():
                if not isinstance(value, Mapping):
                    continue
                saved_as = value.get("saved_as")
                if not isinstance(saved_as, str) or not saved_as.strip():
                    continue
                for candidate in {str(key), Path(str(key)).name}:
                    normalized = candidate.strip().casefold()
                    if normalized:
                        saved_as_by_name[normalized] = saved_as.strip()

    return LegacyImageCatalog(
        image_root=image_root,
        files_by_name=files_by_name,
        files_by_stem=files_by_stem,
        saved_as_by_name=saved_as_by_name,
    )


def normalize_legacy_scope(
    *,
    priority: int,
    response_rule_conditions: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    unknown_keys = set(response_rule_conditions) - _SUPPORTED_RULE_KEYS
    if unknown_keys:
        fields = ",".join(sorted(unknown_keys))
        raise MigrationError(f"unsupported rule keys: {fields}")

    group_id = _extract_legacy_eq(response_rule_conditions.get("group_id"))
    user_id = _extract_legacy_eq(response_rule_conditions.get("user_id"))

    if priority == 3:
        if group_id or user_id:
            raise MigrationError("priority=3 row should not carry scoped conditions")
        return "all_groups", "", {}
    if priority == 2:
        if not group_id or user_id:
            raise MigrationError("priority=2 row must only carry group_id")
        return "current_group", group_id, {}
    if priority == 1:
        if group_id and user_id:
            return "self_in_current_group", group_id, {}
        if user_id and not group_id:
            return "self", "", {}
        raise MigrationError("priority=1 row must carry user_id")
    raise MigrationError(f"unsupported priority: {priority}")


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
            text_value = normalize_text(str(item.get("text", "") or ""))
            if text_value:
                atoms.append(MessageAtom(kind="text", text=text_value))
            continue
        if segment_type == "image":
            file_name = str(item.get("file", "") or "").strip()
            url = str(item.get("url", "") or "").strip()
            image_path = image_catalog.resolve(file_name, url=url)
            if image_path is None:
                raise MigrationError(f"image file not found: {file_name or url}")
            data = await asyncio.to_thread(image_path.read_bytes)
            image = await media_service.ingest_image_bytes(data)
            if report is not None:
                report.image_counts[image_path.suffix.lower()] += 1
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
    reset_target: bool = True,
) -> WordbankMigrationReport:
    await repository.init_all_tables()
    if reset_target:
        await repository.reset_all_data(include_images=True)
    await media_service.rebuild_cache()

    report = WordbankMigrationReport(total_rows=len(rows))
    migration_time = get_current_time()

    for row in rows:
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
                raise MigrationError("empty trigger shape")
            if response_shape.is_empty():
                raise MigrationError("empty response shape")

            scope, group_id, rule = normalize_legacy_scope(
                priority=_coerce_int(row["priority"], field="priority"),
                response_rule_conditions=_coerce_rule_mapping(
                    row.get("response_rule_conditions")
                ),
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
            entry = await repository.import_message_entry(
                trigger_shape=trigger_shape,
                response_shape=response_shape,
                rule=rule,
                scope=scope,
                priority=SCOPE_PRIORITY[scope],
                probability=normalize_legacy_probability(row.get("trigger_config")),
                weight=_coerce_int(row.get("weight", 3), field="weight"),
                group_id=group_id,
                created_by=str(row.get("created_by") or ""),
                status=state.status,
                enabled=state.enabled,
                approved_by="",
                deleted_at=state.deleted_at,
                created_at=created_at,
                updated_at=updated_at,
                trigger_mode="fullmatch",
            )
        except Exception as exc:
            report.add_failure(response_id, str(exc))
            continue

        report.imported_rows += 1
        report.status_counts[entry.status] += 1
        report.imported_entry_ids[response_id] = entry.id

    await repository.rebuild_search_index()
    return report


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


def _extract_legacy_eq(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    raw_value = value.get("$eq")
    if raw_value in (None, ""):
        return ""
    return str(raw_value)


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


__all__ = [
    "LegacyImageCatalog",
    "LegacyPgConfig",
    "WordbankMigrationReport",
    "build_legacy_image_catalog",
    "legacy_message_to_shape",
    "load_legacy_pg_config",
    "migrate_legacy_rows",
    "normalize_legacy_scope",
    "normalize_legacy_state",
    "parse_legacy_env_file",
]
