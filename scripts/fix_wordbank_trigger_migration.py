"""Repair legacy wordbank trigger rows in SQLite from the old PostgreSQL source."""

from __future__ import annotations

import argparse
import asyncio
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
    fetch_legacy_response_rows,
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
DEFAULT_REPORT_PATH = "./data/db/wordbank-trigger-fix-report.json"


@dataclass(slots=True)
class TriggerFixReport:
    scanned_rows: int = 0
    matched_rows: int = 0
    updated_groups: int = 0
    updated_variants: int = 0
    skipped_rows: int = 0
    skipped_reasons: Counter[str] = field(default_factory=Counter)
    updated_group_ids: list[int] = field(default_factory=list)

    def add_skip(self, reason: str) -> None:
        self.skipped_rows += 1
        self.skipped_reasons[reason] += 1

    def add_update(self, group_id: int, variant_count: int) -> None:
        self.updated_groups += 1
        self.updated_variants += variant_count
        self.updated_group_ids.append(group_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "scanned_rows": self.scanned_rows,
            "matched_rows": self.matched_rows,
            "updated_groups": self.updated_groups,
            "updated_variants": self.updated_variants,
            "skipped_rows": self.skipped_rows,
            "skipped_reasons": dict(self.skipped_reasons),
            "updated_group_ids": self.updated_group_ids,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--old-repo",
        default="../sakuraisenrin-old",
        help="path to the legacy repository root",
    )
    parser.add_argument(
        "--image-root",
        help=(
            "path to the recovered image directory; defaults to "
            "../SakuraiSenrinPic/recovered_files relative to --old-repo"
        ),
    )
    parser.add_argument(
        "--mapping-file",
        help=(
            "path to the legacy image mapping file; defaults to "
            "../SakuraiSenrinPic/file_mapping.json relative to --old-repo"
        ),
    )
    parser.add_argument(
        "--pg-host",
        help="legacy PostgreSQL host override",
    )
    parser.add_argument(
        "--pg-port",
        type=int,
        help="legacy PostgreSQL port override",
    )
    parser.add_argument(
        "--pg-user",
        help="legacy PostgreSQL user override",
    )
    parser.add_argument(
        "--pg-password",
        help="legacy PostgreSQL password override",
    )
    parser.add_argument(
        "--pg-database",
        default="senrin_wordbank",
        help="legacy PostgreSQL database name",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="path to the target wordbank_main.db",
    )
    parser.add_argument(
        "--report",
        default=DEFAULT_REPORT_PATH,
        help="where to write the fix report JSON",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan and report without writing changes",
    )
    return parser.parse_args()


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path | None, Path | None]:
    old_repo_root = Path(args.old_repo).resolve()
    image_root = Path(args.image_root).resolve() if args.image_root else None
    mapping_path = Path(args.mapping_file).resolve() if args.mapping_file else None
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
            row, probability=float(row["group_probability"])
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


def fix_wordbank_trigger_migration(
    *,
    db_path: Path,
    image_root: Path,
    mapping_path: Path | None,
    pg_config: LegacyPgConfig,
    dry_run: bool = False,
) -> TriggerFixReport:
    report = TriggerFixReport()
    image_catalog = build_legacy_image_catalog(image_root, mapping_path)
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        current_image_ids = _current_image_canonical_ids(connection)
        responses_by_key, variants_by_group = _load_current_rows(connection)
        legacy_rows = asyncio.run(fetch_legacy_response_rows(pg_config))
        report.scanned_rows = len(legacy_rows)
        repair_time = get_current_time()
        for row in legacy_rows:
            try:
                group_id, trigger_shape = _load_target_group_id(
                    row=row,
                    image_catalog=image_catalog,
                    current_image_ids=current_image_ids,
                    responses_by_key=responses_by_key,
                )
            except Exception as exc:
                report.add_skip(f"{type(exc).__name__}: {exc}")
                continue

            variant_rows = variants_by_group.get(group_id, [])
            if not variant_rows:
                report.add_skip(f"group {group_id}: missing trigger variants")
                continue

            desired_fingerprint = fingerprint_shape(trigger_shape)
            desired_payload = shape_to_payload(trigger_shape)
            current_payloads = {
                shape_to_payload(
                    shape_from_payload(str(variant_row["message_json"] or "[]"))
                )
                for variant_row in variant_rows
            }
            if len(current_payloads) > 1:
                report.add_skip(f"group {group_id}: multiple trigger variants differ")
                continue
            if current_payloads == {desired_payload}:
                report.matched_rows += 1
                continue

            report.matched_rows += 1
            if not dry_run:
                for variant_row in variant_rows:
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
                            desired_fingerprint.summary_text,
                            desired_payload,
                            desired_fingerprint.exact_md5,
                            desired_fingerprint.structure_key,
                            desired_fingerprint.search_text,
                            desired_fingerprint.search_tokens,
                            desired_fingerprint.image_keys,
                            repair_time,
                            int(variant_row["id"]),
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
            report.add_update(group_id, len(variant_rows))

        if not dry_run:
            connection.commit()
            _rebuild_search_index(connection)
            connection.commit()
    return report


def main() -> None:
    args = parse_args()
    old_repo_root, image_root, mapping_path = resolve_paths(args)
    image_root = image_root or _default_legacy_image_root(old_repo_root)
    mapping_path = mapping_path or _default_legacy_image_mapping_path(old_repo_root)
    db_path = Path(args.db_path).resolve()
    report_path = Path(args.report).resolve()
    pg_config = build_pg_config(args)

    logger.info(
        "[wordbank-trigger-fix] starting "
        + json.dumps(
            {
                "old_repo": str(old_repo_root),
                "image_root": str(image_root),
                "mapping_file": str(mapping_path) if mapping_path else "",
                "db_path": str(db_path),
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )

    report = fix_wordbank_trigger_migration(
        db_path=db_path,
        image_root=image_root,
        mapping_path=mapping_path,
        pg_config=pg_config,
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
