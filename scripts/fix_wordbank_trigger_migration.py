"""Apply an exported wordbank trigger-fix plan to SQLite without PostgreSQL."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migrations.wordbank_legacy_source import (
    _default_legacy_image_mapping_path,
    _default_legacy_image_root,
    build_legacy_image_catalog,
    load_legacy_pg_config,
)
from scripts.migrations.wordbank_rules import (
    MigrationError,
    _coerce_int,
    load_legacy_json,
    normalize_legacy_message_text_preserving_newlines,
    normalize_legacy_probability,
    normalize_legacy_rules,
    normalize_legacy_state,
    normalize_legacy_timestamp,
    shape_from_legacy_extra_info,
)
from scripts.migrations.wordbank_types import LegacyPgConfig
from src.lib.utils.common import get_current_time
from src.logger import logger
from src.plugins.wordbank.database.repo_shared import (
    group_status_from_responses,
    merge_image_keys,
    parse_image_keys,
    representative_response,
    search_preview_responses,
)
from src.plugins.wordbank.message_model import (
    MessageAtom,
    MessageShape,
    fingerprint_shape,
    shape_from_event,
    shape_from_payload,
    shape_from_text,
    shape_to_payload,
)

DEFAULT_DB_PATH = ROOT / "data" / "db" / "wordbank_db" / "wordbank_main.db"
DEFAULT_PLAN_PATH = ROOT / ".devtest" / "wordbank-trigger-fix-plan.json"
DEFAULT_REPORT_PATH = ROOT / ".devtest" / "wordbank-trigger-fix-apply-report.json"
PLAN_VERSION = 1
VARIANT_FIELD_NAMES = (
    "trigger_text",
    "message_json",
    "exact_md5",
    "structure_key",
    "search_text",
    "search_tokens",
    "image_keys",
)
GROUP_SAFE_MATCH_FIELDS = (
    "structure_key",
    "search_tokens",
    "image_keys",
)


@dataclass(slots=True)
class TriggerFixExportReport:
    scanned_rows: int = 0
    matched_rows: int = 0
    planned_groups: int = 0
    planned_variants: int = 0
    already_matching_groups: int = 0
    skipped_rows: int = 0
    skipped_reasons: Counter[str] = field(default_factory=Counter)
    planned_group_ids: list[int] = field(default_factory=list)
    already_matching_group_ids: list[int] = field(default_factory=list)

    def add_skip(self, reason: str) -> None:
        self.skipped_rows += 1
        self.skipped_reasons[reason] += 1

    def finalize(
        self,
        *,
        operations: Sequence[Mapping[str, object]],
        already_matching_group_ids: set[int],
    ) -> None:
        self.planned_group_ids = sorted(
            int(cast(int, operation["trigger_group_id"])) for operation in operations
        )
        self.planned_groups = len(self.planned_group_ids)
        self.planned_variants = sum(
            len(cast(Sequence[object], operation["expected_variants"]))
            for operation in operations
        )
        self.already_matching_group_ids = sorted(already_matching_group_ids)
        self.already_matching_groups = len(self.already_matching_group_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "scanned_rows": self.scanned_rows,
            "matched_rows": self.matched_rows,
            "planned_groups": self.planned_groups,
            "planned_variants": self.planned_variants,
            "already_matching_groups": self.already_matching_groups,
            "skipped_rows": self.skipped_rows,
            "skipped_reasons": dict(self.skipped_reasons),
            "planned_group_ids": self.planned_group_ids,
            "already_matching_group_ids": self.already_matching_group_ids,
        }


@dataclass(slots=True)
class TriggerFixApplyReport:
    scanned_operations: int = 0
    updated_groups: int = 0
    updated_variants: int = 0
    already_applied_groups: int = 0
    skipped_operations: int = 0
    skipped_reasons: Counter[str] = field(default_factory=Counter)
    updated_group_ids: list[int] = field(default_factory=list)

    def add_skip(self, reason: str) -> None:
        self.skipped_operations += 1
        self.skipped_reasons[reason] += 1

    def add_update(self, group_id: int, variant_count: int) -> None:
        self.updated_groups += 1
        self.updated_variants += variant_count
        self.updated_group_ids.append(group_id)

    def add_already_applied(self) -> None:
        self.already_applied_groups += 1

    def to_dict(self) -> dict[str, object]:
        return {
            "scanned_operations": self.scanned_operations,
            "updated_groups": self.updated_groups,
            "updated_variants": self.updated_variants,
            "already_applied_groups": self.already_applied_groups,
            "skipped_operations": self.skipped_operations,
            "skipped_reasons": dict(self.skipped_reasons),
            "updated_group_ids": self.updated_group_ids,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="path to the target wordbank_main.db",
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_PLAN_PATH),
        help="path to the exported trigger-fix plan JSON",
    )
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT_PATH),
        help="where to write the apply report JSON",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan and validate without writing changes",
    )
    return parser.parse_args()


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path | None]:
    old_repo_root = Path(args.old_repo).resolve()
    image_root = (
        Path(args.image_root).resolve()
        if args.image_root
        else _default_legacy_image_root(old_repo_root)
    )
    mapping_path = (
        Path(args.mapping_file).resolve()
        if args.mapping_file
        else _default_legacy_image_mapping_path(old_repo_root)
    )
    if not mapping_path.is_file():
        mapping_path = None
    return old_repo_root, image_root, mapping_path


def build_pg_config(args: argparse.Namespace) -> LegacyPgConfig:
    defaults = load_legacy_pg_config(Path(args.old_repo))
    return LegacyPgConfig(
        host=args.pg_host or defaults.host,
        port=args.pg_port or defaults.port,
        user=args.pg_user or defaults.user,
        password=args.pg_password or defaults.password,
        database=args.pg_database or defaults.database,
    )


def _load_json_dict(value: object) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        loaded = json.loads(value)
        if isinstance(loaded, dict):
            return dict(loaded)
    return {}


def _canonical_rule_json(rule: dict[str, Any]) -> str:
    return json.dumps(rule, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _current_image_canonical_ids(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT id, canonical_image_id, md5
        FROM wordbank_image
        """
    ).fetchall()
    image_map: dict[str, int] = {}
    for row in rows:
        md5_hex = str(row["md5"] or "")
        if not md5_hex:
            continue
        canonical_id = row["canonical_image_id"]
        image_map[md5_hex] = int(canonical_id or row["id"])
    return image_map


def _load_image_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _resolve_legacy_image(
    item: Mapping[str, Any],
    *,
    image_catalog: Any,
    current_image_ids: dict[str, int],
) -> int:
    file_name = str(item.get("file", "") or "").strip()
    url = str(item.get("url", "") or "").strip()
    image_path, _ = image_catalog.resolve_with_source(file_name, url=url)
    if image_path is None:
        raise MigrationError(f"image file not found: {file_name or url}")
    md5_hex = _load_image_md5(image_path)
    canonical_id = current_image_ids.get(md5_hex)
    if canonical_id is None:
        raise MigrationError(f"image not present in sqlite: {image_path.name}")
    return canonical_id


def _legacy_message_to_shape(
    payload: object,
    *,
    extra_info: object,
    image_catalog: Any,
    current_image_ids: dict[str, int],
    preserve_text_newlines: bool = False,
) -> MessageShape:
    event_shape = shape_from_legacy_extra_info(extra_info)
    if event_shape is not None:
        if isinstance(event_shape, MessageShape):
            return event_shape
        if isinstance(event_shape, str):
            return shape_from_event(event_shape)
        raise MigrationError("legacy extra_info produced unsupported message shape")

    segments = load_legacy_json(payload)
    if not isinstance(segments, list):
        raise MigrationError("legacy message payload must be a list")

    atoms: list[MessageAtom] = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        segment_type = str(item.get("type", "") or "").strip().lower()
        if segment_type == "text":
            raw_text = str(item.get("text", "") or "")
            text_value = (
                normalize_legacy_message_text_preserving_newlines(raw_text)
                if preserve_text_newlines
                else raw_text
            )
            shape = shape_from_text(text_value, preserve_blank_text=True)
            atoms.extend(shape.atoms)
            continue
        if segment_type == "image":
            canonical_id = _resolve_legacy_image(
                item,
                image_catalog=image_catalog,
                current_image_ids=current_image_ids,
            )
            atoms.append(MessageAtom(kind="image", canonical_image_id=canonical_id))
            continue
        if segment_type == "at":
            target_id = str(item.get("qq", "") or "").strip()
            if target_id:
                atoms.append(MessageAtom(kind="at", target_id=target_id))
            continue
        if segment_type == "face":
            face_id = str(item.get("id", "") or "").strip()
            atoms.append(MessageAtom(kind="text", text=f"[face:{face_id}]"))
    return MessageShape(tuple(atoms))


def _legacy_response_shape(
    row: Mapping[str, Any],
    *,
    image_catalog: Any,
    current_image_ids: dict[str, int],
) -> MessageShape:
    return _legacy_message_to_shape(
        row.get("response_text"),
        extra_info=None,
        image_catalog=image_catalog,
        current_image_ids=current_image_ids,
        preserve_text_newlines=True,
    )


def _legacy_trigger_shape(
    row: Mapping[str, Any],
    *,
    image_catalog: Any,
    current_image_ids: dict[str, int],
) -> MessageShape:
    return _legacy_message_to_shape(
        row.get("trigger_text"),
        extra_info=row.get("extra_info"),
        image_catalog=image_catalog,
        current_image_ids=current_image_ids,
        preserve_text_newlines=False,
    )


def _response_match_key(
    *,
    probability: float,
    target_scope: str,
    target_group_id: str,
    rule: dict[str, Any],
    response_shape: MessageShape,
    status: str,
    enabled: int,
    deleted_at: int,
    created_by: str,
    approved_by: str,
    weight: int,
    priority: int,
    created_at: int,
) -> tuple[object, ...]:
    fingerprint = fingerprint_shape(response_shape)
    return (
        probability,
        status,
        enabled,
        target_scope,
        priority,
        weight,
        _canonical_rule_json(rule),
        target_group_id,
        created_by,
        approved_by,
        deleted_at,
        created_at,
        fingerprint.summary_text,
        fingerprint.exact_md5,
        fingerprint.structure_key,
        fingerprint.search_text,
        fingerprint.search_tokens,
        fingerprint.image_keys,
    )


def _current_response_match_key(
    row: Mapping[str, Any],
    *,
    probability: float,
) -> tuple[object, ...]:
    message_shape = shape_from_payload(str(row["message_json"] or "[]"))
    fingerprint = fingerprint_shape(message_shape)
    rule = _load_json_dict(row["rule"])
    return (
        probability,
        str(row["status"] or ""),
        int(row["enabled"] or 0),
        str(row["scope"] or ""),
        int(row["priority"] or 0),
        int(row["weight"] or 0),
        _canonical_rule_json(rule),
        str(row["group_id"] or ""),
        str(row["created_by"] or ""),
        str(row["approved_by"] or ""),
        int(row["deleted_at"] or 0),
        int(row["created_at"] or 0),
        fingerprint.summary_text,
        fingerprint.exact_md5,
        fingerprint.structure_key,
        fingerprint.search_text,
        fingerprint.search_tokens,
        fingerprint.image_keys,
    )


def _load_current_rows(
    connection: sqlite3.Connection,
) -> tuple[
    dict[tuple[object, ...], list[sqlite3.Row]],
    dict[int, list[sqlite3.Row]],
]:
    response_rows = connection.execute(
        """
        SELECT
            ri.*,
            tg.probability AS group_probability
        FROM wordbank_response_item ri
        JOIN wordbank_trigger_group tg
          ON tg.id = ri.trigger_group_id
        ORDER BY ri.id ASC
        """
    ).fetchall()
    responses_by_key: dict[tuple[object, ...], list[sqlite3.Row]] = defaultdict(list)
    for row in response_rows:
        key = _current_response_match_key(
            row,
            probability=float(row["group_probability"]),
        )
        responses_by_key[key].append(row)

    variant_rows = connection.execute(
        """
        SELECT *
        FROM wordbank_trigger_variant
        ORDER BY id ASC
        """
    ).fetchall()
    variants_by_group: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in variant_rows:
        variants_by_group[int(row["trigger_group_id"])].append(row)

    return responses_by_key, variants_by_group


def _load_target_group_id(
    *,
    row: Mapping[str, Any],
    image_catalog: Any,
    current_image_ids: dict[str, int],
    responses_by_key: dict[tuple[object, ...], list[sqlite3.Row]],
) -> tuple[int, MessageShape]:
    response_shape = _legacy_response_shape(
        row,
        image_catalog=image_catalog,
        current_image_ids=current_image_ids,
    )
    state = normalize_legacy_state(
        approval_status=str(row.get("approval_status", "PENDING")),
        response_available=bool(row.get("response_available", False)),
        migration_time=normalize_legacy_timestamp(
            row.get("created_at"),
            fallback=0,
        ),
    )
    probability = normalize_legacy_probability(row.get("trigger_config"))
    created_at = normalize_legacy_timestamp(row.get("created_at"), fallback=0)
    weight = _coerce_int(row.get("weight", 3), field="weight")
    priority = _coerce_int(row.get("priority", 3), field="priority")
    targets = normalize_legacy_rules(
        priority=priority,
        response_rule_conditions=_load_json_dict(row.get("response_rule_conditions")),
        trigger_config=row.get("trigger_config"),
    )
    if not targets:
        raise MigrationError("legacy rule expands to no valid branch")

    group_ids: set[int] = set()
    for target in targets:
        key = _response_match_key(
            probability=probability,
            target_scope=target.scope,
            target_group_id=target.group_id,
            rule=target.rule,
            response_shape=response_shape,
            status=state.status,
            enabled=state.enabled,
            deleted_at=state.deleted_at,
            created_by=str(row.get("created_by") or ""),
            approved_by="",
            weight=weight,
            priority=priority,
            created_at=created_at,
        )
        candidates = responses_by_key.get(key, [])
        if len(candidates) != 1:
            raise MigrationError(
                f"response match not unique for target scope={target.scope}"
            )
        matched_row = candidates[0]
        group_ids.add(int(matched_row["trigger_group_id"]))

    if len(group_ids) != 1:
        raise MigrationError("response matches span multiple trigger groups")
    group_id = group_ids.pop()
    trigger_shape = _legacy_trigger_shape(
        row,
        image_catalog=image_catalog,
        current_image_ids=current_image_ids,
    )
    return group_id, trigger_shape


def _build_document_payload(
    *,
    group: SimpleNamespace,
    variants: list[SimpleNamespace],
    responses: list[SimpleNamespace],
) -> dict[str, object] | None:
    if not variants:
        return None
    visible_responses = [response for response in responses if response.deleted_at == 0]
    if not visible_responses:
        return None
    representative = representative_response(cast(Sequence[Any], visible_responses))
    if representative is None:
        return None
    preview_responses = search_preview_responses(cast(Sequence[Any], responses))
    response_tokens = " ".join(
        token
        for response in visible_responses
        for token in [response.search_tokens]
        if token
    )
    return {
        "trigger_group_id": group.id,
        "status": group.status,
        "enabled": group.enabled,
        "group_id": group.group_id,
        "created_by": group.created_by,
        "deleted_at": group.deleted_at,
        "scope": representative.scope,
        "probability": group.probability,
        "weight": representative.weight,
        "trigger_text": variants[0].trigger_text,
        "trigger_exact_md5": variants[0].exact_md5,
        "trigger_structure_key": variants[0].structure_key,
        "trigger_image_keys": variants[0].image_keys,
        "response_text": representative.text,
        "response_preview_json": json.dumps(
            [response.text for response in preview_responses if response.text],
            ensure_ascii=False,
        ),
        "response_count": len(visible_responses),
        "active_response_count": sum(
            1
            for response in visible_responses
            if response.status == "approved" and response.enabled == 1
        ),
        "response_image_keys": merge_image_keys(
            [response.image_keys for response in visible_responses]
        ),
        "trigger_tokens": variants[0].search_tokens,
        "response_tokens": response_tokens,
        "updated_at": max(
            [group.updated_at, variants[0].updated_at]
            + [response.updated_at for response in responses]
        ),
    }


def _rebuild_search_index(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS wordbank_search_trigger_fts
        USING fts5(tokens)
        """
    )
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS wordbank_search_response_fts
        USING fts5(tokens)
        """
    )

    group_rows = [
        SimpleNamespace(**dict(row))
        for row in connection.execute(
            "SELECT * FROM wordbank_trigger_group ORDER BY id ASC"
        ).fetchall()
    ]
    variant_rows = [
        SimpleNamespace(**dict(row))
        for row in connection.execute(
            "SELECT * FROM wordbank_trigger_variant ORDER BY id ASC"
        ).fetchall()
    ]
    response_rows = [
        SimpleNamespace(**dict(row))
        for row in connection.execute(
            "SELECT * FROM wordbank_response_item ORDER BY id ASC"
        ).fetchall()
    ]

    variants_by_group: dict[int, list[SimpleNamespace]] = defaultdict(list)
    responses_by_group: dict[int, list[SimpleNamespace]] = defaultdict(list)
    for row in variant_rows:
        variants_by_group[int(row.trigger_group_id)].append(row)
    for row in response_rows:
        responses_by_group[int(row.trigger_group_id)].append(row)

    documents: list[dict[str, object]] = []
    image_map_rows: list[dict[str, int | str]] = []
    for group in group_rows:
        group_responses = responses_by_group.get(group.id, [])
        if not group_responses:
            continue
        status, enabled, deleted_at = group_status_from_responses(
            cast(Sequence[Any], group_responses)
        )
        normalized_group = SimpleNamespace(
            **{
                **group.__dict__,
                "status": status,
                "enabled": enabled,
                "deleted_at": deleted_at,
                "updated_at": max(
                    [group.updated_at]
                    + [
                        variant.updated_at
                        for variant in variants_by_group.get(group.id, [])
                    ]
                    + [response.updated_at for response in group_responses]
                ),
            }
        )
        payload = _build_document_payload(
            group=normalized_group,
            variants=variants_by_group.get(group.id, []),
            responses=group_responses,
        )
        if payload is None:
            continue
        documents.append(payload)
        trigger_group_id = int(cast(int | str, payload["trigger_group_id"]))
        for side, key in (
            ("trigger", "trigger_image_keys"),
            ("response", "response_image_keys"),
        ):
            for image_id in parse_image_keys(str(payload[key])):
                image_map_rows.append(
                    {
                        "trigger_group_id": trigger_group_id,
                        "side": side,
                        "canonical_image_id": image_id,
                    }
                )

    connection.execute("DELETE FROM wordbank_search_document")
    connection.execute("DELETE FROM wordbank_search_image_map")
    connection.execute("DELETE FROM wordbank_search_trigger_fts")
    connection.execute("DELETE FROM wordbank_search_response_fts")
    if documents:
        connection.executemany(
            """
            INSERT INTO wordbank_search_document (
                trigger_group_id,
                status,
                enabled,
                group_id,
                created_by,
                deleted_at,
                scope,
                probability,
                weight,
                trigger_text,
                trigger_exact_md5,
                trigger_structure_key,
                trigger_image_keys,
                response_text,
                response_preview_json,
                response_count,
                active_response_count,
                response_image_keys,
                trigger_tokens,
                response_tokens,
                updated_at
            ) VALUES (
                :trigger_group_id,
                :status,
                :enabled,
                :group_id,
                :created_by,
                :deleted_at,
                :scope,
                :probability,
                :weight,
                :trigger_text,
                :trigger_exact_md5,
                :trigger_structure_key,
                :trigger_image_keys,
                :response_text,
                :response_preview_json,
                :response_count,
                :active_response_count,
                :response_image_keys,
                :trigger_tokens,
                :response_tokens,
                :updated_at
            )
            """,
            documents,
        )
        connection.executemany(
            """
            INSERT INTO wordbank_search_trigger_fts(rowid, tokens)
            VALUES (:trigger_group_id, :trigger_tokens)
            """,
            documents,
        )
        connection.executemany(
            """
            INSERT INTO wordbank_search_response_fts(rowid, tokens)
            VALUES (:trigger_group_id, :response_tokens)
            """,
            documents,
        )
    if image_map_rows:
        connection.executemany(
            """
            INSERT INTO wordbank_search_image_map (
                trigger_group_id,
                side,
                canonical_image_id
            ) VALUES (
                :trigger_group_id,
                :side,
                :canonical_image_id
            )
            """,
            image_map_rows,
        )


def _normalize_variant_snapshot(payload: Mapping[str, object]) -> dict[str, str | int]:
    return {
        "id": _coerce_int(payload["id"], field="id"),
        "trigger_group_id": _coerce_int(
            payload["trigger_group_id"],
            field="trigger_group_id",
        ),
        "trigger_text": str(payload.get("trigger_text") or ""),
        "message_json": str(payload.get("message_json") or "[]"),
        "exact_md5": str(payload.get("exact_md5") or ""),
        "structure_key": str(payload.get("structure_key") or ""),
        "search_text": str(payload.get("search_text") or ""),
        "search_tokens": str(payload.get("search_tokens") or ""),
        "image_keys": str(payload.get("image_keys") or ""),
    }


def _variant_payload_from_shape(shape: MessageShape) -> dict[str, str]:
    fingerprint = fingerprint_shape(shape)
    return {
        "trigger_text": fingerprint.summary_text,
        "message_json": shape_to_payload(shape),
        "exact_md5": fingerprint.exact_md5,
        "structure_key": fingerprint.structure_key,
        "search_text": fingerprint.search_text,
        "search_tokens": fingerprint.search_tokens,
        "image_keys": fingerprint.image_keys,
    }


def _load_plan(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("fix plan root must be an object")
    version = int(payload.get("version", 0) or 0)
    if version != PLAN_VERSION:
        raise ValueError(f"unsupported fix plan version: {version}")
    operations = payload.get("operations")
    if not isinstance(operations, list):
        raise ValueError("fix plan missing operations")
    return payload


def _row_variant_snapshot(row: sqlite3.Row) -> dict[str, str | int]:
    return {
        "id": int(row["id"]),
        "trigger_group_id": int(row["trigger_group_id"]),
        "trigger_text": str(row["trigger_text"] or ""),
        "message_json": str(row["message_json"] or "[]"),
        "exact_md5": str(row["exact_md5"] or ""),
        "structure_key": str(row["structure_key"] or ""),
        "search_text": str(row["search_text"] or ""),
        "search_tokens": str(row["search_tokens"] or ""),
        "image_keys": str(row["image_keys"] or ""),
    }


def _snapshot_matches(
    current: Mapping[str, str | int],
    expected: Mapping[str, str | int],
) -> bool:
    return all(current[key] == expected[key] for key in current)


def _variant_matches_desired(
    current: Mapping[str, str | int],
    desired: Mapping[str, str],
) -> bool:
    return all(
        str(current[field_name]) == desired[field_name]
        for field_name in VARIANT_FIELD_NAMES
    )


def _group_fix_is_safe(
    current_variants: Sequence[Mapping[str, str | int]],
    desired: Mapping[str, str],
) -> bool:
    return all(
        str(variant[field_name]) == desired[field_name]
        for variant in current_variants
        for field_name in GROUP_SAFE_MATCH_FIELDS
    )


def _desired_variant_payload(
    payload: Mapping[str, object],
) -> dict[str, str]:
    desired: dict[str, str] = {}
    for field_name in VARIANT_FIELD_NAMES:
        if field_name not in payload:
            raise ValueError(f"desired_variant missing field: {field_name}")
        desired[field_name] = str(payload.get(field_name) or "")
    return desired


def _append_unique_int(target: list[int], value: object) -> None:
    try:
        parsed = _coerce_int(value, field="legacy_id")
    except MigrationError:
        return
    if parsed not in target:
        target.append(parsed)


def build_trigger_fix_plan(
    *,
    db_path: Path,
    legacy_rows: Sequence[Mapping[str, Any]],
    image_root: Path,
    mapping_path: Path | None,
) -> tuple[dict[str, object], TriggerFixExportReport]:
    report = TriggerFixExportReport(scanned_rows=len(legacy_rows))
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        current_image_ids = _current_image_canonical_ids(connection)
        responses_by_key, variants_by_group = _load_current_rows(connection)
        image_catalog = build_legacy_image_catalog(image_root, mapping_path)

        operations_by_group: dict[int, dict[str, object]] = {}
        desired_by_group: dict[int, dict[str, str]] = {}
        conflicting_group_ids: set[int] = set()
        already_matching_group_ids: set[int] = set()

        for row in legacy_rows:
            try:
                group_id, trigger_shape = _load_target_group_id(
                    row=row,
                    image_catalog=image_catalog,
                    current_image_ids=current_image_ids,
                    responses_by_key=responses_by_key,
                )
            except MigrationError as exc:
                report.add_skip(str(exc))
                continue

            report.matched_rows += 1
            desired_variant = _variant_payload_from_shape(trigger_shape)

            existing_desired = desired_by_group.get(group_id)
            if existing_desired is not None and existing_desired != desired_variant:
                operations_by_group.pop(group_id, None)
                desired_by_group.pop(group_id, None)
                already_matching_group_ids.discard(group_id)
                conflicting_group_ids.add(group_id)
                report.add_skip(f"group {group_id}: conflicting_pg_targets")
                continue
            if group_id in conflicting_group_ids:
                report.add_skip(f"group {group_id}: conflicting_pg_targets")
                continue
            desired_by_group[group_id] = desired_variant

            current_rows = variants_by_group.get(group_id, [])
            if not current_rows:
                report.add_skip(f"group {group_id}: no_current_variants")
                continue

            current_snapshots = [_row_variant_snapshot(item) for item in current_rows]
            if all(
                _variant_matches_desired(snapshot, desired_variant)
                for snapshot in current_snapshots
            ):
                already_matching_group_ids.add(group_id)
                continue

            if not _group_fix_is_safe(current_snapshots, desired_variant):
                report.add_skip(f"group {group_id}: incompatible_current_variants")
                continue

            operation = operations_by_group.get(group_id)
            if operation is None:
                operation = {
                    "trigger_group_id": group_id,
                    "expected_variants": current_snapshots,
                    "desired_variant": desired_variant,
                    "legacy_response_ids": [],
                    "legacy_trigger_ids": [],
                }
                operations_by_group[group_id] = operation

            _append_unique_int(
                cast(list[int], operation["legacy_response_ids"]),
                row.get("response_id"),
            )
            _append_unique_int(
                cast(list[int], operation["legacy_trigger_ids"]),
                row.get("trigger_id"),
            )

        operations = [
            operations_by_group[group_id]
            for group_id in sorted(operations_by_group)
            if group_id not in conflicting_group_ids
        ]
        for operation in operations:
            cast(list[int], operation["legacy_response_ids"]).sort()
            cast(list[int], operation["legacy_trigger_ids"]).sort()

    report.finalize(
        operations=operations,
        already_matching_group_ids=already_matching_group_ids,
    )
    return (
        {
            "version": PLAN_VERSION,
            "generated_at": get_current_time(),
            "summary": report.to_dict(),
            "operations": operations,
        },
        report,
    )


def apply_trigger_fix_plan(
    *,
    db_path: Path,
    plan_payload: Mapping[str, object],
    dry_run: bool = False,
) -> TriggerFixApplyReport:
    operations_raw = plan_payload.get("operations")
    if not isinstance(operations_raw, list):
        raise ValueError("fix plan missing operations")

    report = TriggerFixApplyReport(scanned_operations=len(operations_raw))
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        repair_time = get_current_time()
        changed = False
        for operation_raw in operations_raw:
            if not isinstance(operation_raw, Mapping):
                report.add_skip("operation_not_object")
                continue

            try:
                group_id = int(operation_raw["trigger_group_id"])
                expected_variants_raw = operation_raw["expected_variants"]
                desired_variant_raw = operation_raw["desired_variant"]
            except Exception as exc:
                report.add_skip(f"invalid_operation: {type(exc).__name__}")
                continue

            if not isinstance(expected_variants_raw, list) or not isinstance(
                desired_variant_raw,
                Mapping,
            ):
                report.add_skip("invalid_operation_shape")
                continue

            expected_variants = [
                _normalize_variant_snapshot(
                    cast(Mapping[str, object], item),
                )
                for item in expected_variants_raw
                if isinstance(item, Mapping)
            ]
            if not expected_variants:
                report.add_skip(f"group {group_id}: expected_variants_empty")
                continue
            if len(expected_variants) != len(expected_variants_raw):
                report.add_skip(f"group {group_id}: invalid_expected_variants")
                continue

            try:
                desired_variant = _desired_variant_payload(desired_variant_raw)
            except ValueError as exc:
                report.add_skip(f"group {group_id}: {exc}")
                continue

            variant_ids = [int(item["id"]) for item in expected_variants]
            rows = connection.execute(
                f"""
                SELECT *
                FROM wordbank_trigger_variant
                WHERE id IN ({",".join("?" for _ in variant_ids)})
                ORDER BY id ASC
                """,
                variant_ids,
            ).fetchall()
            if len(rows) != len(variant_ids):
                report.add_skip(f"group {group_id}: variant_count_mismatch")
                continue

            rows_by_id = {int(row["id"]): row for row in rows}
            expected_by_id = {int(item["id"]): item for item in expected_variants}
            invalid_group = False
            current_matches_expected = True
            current_matches_desired = True
            for variant_id in variant_ids:
                row = rows_by_id[variant_id]
                if int(row["trigger_group_id"]) != group_id:
                    invalid_group = True
                    break
                current_snapshot = _row_variant_snapshot(row)
                expected_snapshot = expected_by_id[variant_id]
                if not _snapshot_matches(current_snapshot, expected_snapshot):
                    current_matches_expected = False
                if not _variant_matches_desired(current_snapshot, desired_variant):
                    current_matches_desired = False
            if invalid_group:
                report.add_skip(f"group {group_id}: variant_group_mismatch")
                continue
            if current_matches_desired:
                report.add_already_applied()
                continue
            if not current_matches_expected:
                report.add_skip(f"group {group_id}: current_state_mismatch")
                continue

            if not dry_run:
                for variant_id in variant_ids:
                    connection.execute(
                        """
                        UPDATE wordbank_trigger_variant
                        SET
                            trigger_text = ?,
                            message_json = ?,
                            exact_md5 = ?,
                            structure_key = ?,
                            search_text = ?,
                            search_tokens = ?,
                            image_keys = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            desired_variant["trigger_text"],
                            desired_variant["message_json"],
                            desired_variant["exact_md5"],
                            desired_variant["structure_key"],
                            desired_variant["search_text"],
                            desired_variant["search_tokens"],
                            desired_variant["image_keys"],
                            repair_time,
                            variant_id,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE wordbank_trigger_group
                    SET updated_at = ?
                    WHERE id = ?
                    """,
                    (repair_time, group_id),
                )
                changed = True
            report.add_update(group_id, len(variant_ids))

        if changed and not dry_run:
            connection.commit()
            _rebuild_search_index(connection)
            connection.commit()
    return report


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path).resolve()
    input_path = Path(args.input).resolve()
    report_path = Path(args.report).resolve()
    plan_payload = _load_plan(input_path)

    logger.info(
        "[wordbank-trigger-fix] starting "
        + json.dumps(
            {
                "db_path": str(db_path),
                "input": str(input_path),
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )

    report = apply_trigger_fix_plan(
        db_path=db_path,
        plan_payload=plan_payload,
        dry_run=args.dry_run,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.success(
        "[wordbank-trigger-fix] " + json.dumps(report.to_dict(), ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
