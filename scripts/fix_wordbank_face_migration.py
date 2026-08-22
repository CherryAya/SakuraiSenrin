"""Repair wordbank rows where legacy face segments became literal text."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fix_wordbank_trigger_migration import _rebuild_search_index
from src.plugins.wordbank.message_model import (
    MessageAtom,
    MessageShape,
    fingerprint_shape,
    shape_from_payload,
    shape_to_payload,
)

DEFAULT_DB_PATH = ROOT / "data" / "db" / "wordbank_db" / "wordbank_main.db"
DEFAULT_REPORT_PATH = ROOT / ".devtest" / "wordbank-face-fix-report.json"
LEGACY_FACE_TEXT_RE = re.compile(r"^\[face:(?P<face_id>[0-9]+)\]$")


@dataclass(slots=True)
class FaceFixReport:
    dry_run: bool
    scanned_variants: int = 0
    scanned_responses: int = 0
    changed_variants: int = 0
    changed_responses: int = 0
    changed_groups: set[int] = field(default_factory=set)
    converted_faces: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "scanned_variants": self.scanned_variants,
            "scanned_responses": self.scanned_responses,
            "changed_variants": self.changed_variants,
            "changed_responses": self.changed_responses,
            "changed_groups": sorted(self.changed_groups),
            "converted_faces": self.converted_faces,
        }


def _repair_shape(shape: MessageShape, report: FaceFixReport) -> MessageShape:
    atoms: list[MessageAtom] = []
    changed = False
    for atom in shape.atoms:
        if atom.kind != "text":
            atoms.append(atom)
            continue
        match = LEGACY_FACE_TEXT_RE.fullmatch(atom.text.strip())
        if match is None:
            atoms.append(atom)
            continue
        atoms.append(
            MessageAtom(
                kind="face",
                face_id=int(match.group("face_id")),
            )
        )
        report.converted_faces += 1
        changed = True
    return MessageShape(tuple(atoms)) if changed else shape


def _repair_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    report: FaceFixReport,
    apply: bool,
) -> None:
    rows = connection.execute(
        f"""
        SELECT id, trigger_group_id, message_json
        FROM {table}
        ORDER BY id ASC
        """
    ).fetchall()
    for row in rows:
        if table == "wordbank_trigger_variant":
            report.scanned_variants += 1
        else:
            report.scanned_responses += 1
        original_shape = shape_from_payload(str(row["message_json"] or "[]"))
        repaired_shape = _repair_shape(original_shape, report)
        if repaired_shape == original_shape:
            continue
        fingerprint = fingerprint_shape(repaired_shape)
        if apply and table == "wordbank_trigger_variant":
            connection.execute(
                """
                UPDATE wordbank_trigger_variant
                SET trigger_text = ?,
                    message_json = ?,
                    exact_md5 = ?,
                    structure_key = ?,
                    search_text = ?,
                    search_tokens = ?,
                    image_keys = ?
                WHERE id = ?
                """,
                (
                    fingerprint.summary_text,
                    shape_to_payload(repaired_shape),
                    fingerprint.exact_md5,
                    fingerprint.structure_key,
                    fingerprint.search_text,
                    fingerprint.search_tokens,
                    fingerprint.image_keys,
                    row["id"],
                ),
            )
            report.changed_variants += 1
        elif apply:
            connection.execute(
                """
                UPDATE wordbank_response_item
                SET text = ?,
                    message_json = ?,
                    exact_md5 = ?,
                    structure_key = ?,
                    search_text = ?,
                    search_tokens = ?,
                    image_keys = ?
                WHERE id = ?
                """,
                (
                    fingerprint.summary_text,
                    shape_to_payload(repaired_shape),
                    fingerprint.exact_md5,
                    fingerprint.structure_key,
                    fingerprint.search_text,
                    fingerprint.search_tokens,
                    fingerprint.image_keys,
                    row["id"],
                ),
            )
            report.changed_responses += 1
        else:
            if table == "wordbank_trigger_variant":
                report.changed_variants += 1
            else:
                report.changed_responses += 1
        report.changed_groups.add(int(row["trigger_group_id"]))


def repair_wordbank_face_migration(
    db_path: Path,
    *,
    dry_run: bool = True,
) -> FaceFixReport:
    report = FaceFixReport(dry_run=dry_run)
    if not db_path.is_file():
        raise FileNotFoundError(f"wordbank database not found: {db_path}")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        if not dry_run:
            connection.execute("BEGIN IMMEDIATE")
        _repair_rows(
            connection,
            table="wordbank_trigger_variant",
            report=report,
            apply=not dry_run,
        )
        _repair_rows(
            connection,
            table="wordbank_response_item",
            report=report,
            apply=not dry_run,
        )
        if not dry_run and (report.changed_variants or report.changed_responses):
            _rebuild_search_index(connection)
        if dry_run:
            pass
        else:
            connection.commit()
    except Exception:
        if not dry_run:
            connection.rollback()
        raise
    finally:
        connection.close()
    return report


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="scan and report only; this is the default",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="write repaired rows and rebuild the wordbank search index",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    report = repair_wordbank_face_migration(args.db, dry_run=not args.apply)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    sys.stdout.write(json.dumps(report.to_dict(), ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
