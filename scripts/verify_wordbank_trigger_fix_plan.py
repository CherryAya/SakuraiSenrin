"""Verify that a wordbank trigger-fix plan has been fully applied to SQLite."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fix_wordbank_trigger_migration import (
    DEFAULT_DB_PATH,
    DEFAULT_PLAN_PATH,
    PLAN_VERSION,
    VARIANT_FIELD_NAMES,
    _coerce_int,
    _desired_variant_payload,
    _load_plan,
    _row_variant_snapshot,
)
from src.logger import logger

DEFAULT_REPORT_PATH = ROOT / ".devtest" / "wordbank-trigger-fix-verify-report.json"


@dataclass(slots=True)
class TriggerFixVerifyReport:
    scanned_operations: int = 0
    verified_groups: int = 0
    verified_variants: int = 0
    failed_groups: int = 0
    failed_reasons: Counter[str] = field(default_factory=Counter)
    verified_group_ids: list[int] = field(default_factory=list)
    failed_group_ids: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed_groups == 0

    def add_verified(self, group_id: int, variant_count: int) -> None:
        self.verified_groups += 1
        self.verified_variants += variant_count
        self.verified_group_ids.append(group_id)

    def add_failed(self, group_id: int, reason: str) -> None:
        self.failed_groups += 1
        self.failed_reasons[reason] += 1
        self.failed_group_ids.append(group_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "scanned_operations": self.scanned_operations,
            "verified_groups": self.verified_groups,
            "verified_variants": self.verified_variants,
            "failed_groups": self.failed_groups,
            "failed_reasons": dict(self.failed_reasons),
            "verified_group_ids": self.verified_group_ids,
            "failed_group_ids": self.failed_group_ids,
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
        help="where to write the verify report JSON",
    )
    return parser.parse_args()


def _variant_matches_desired(
    current: Mapping[str, str | int],
    desired: Mapping[str, str],
) -> bool:
    return all(
        str(current[field_name]) == desired[field_name]
        for field_name in VARIANT_FIELD_NAMES
    )


def verify_trigger_fix_plan(
    *,
    db_path: Path,
    plan_payload: Mapping[str, object],
) -> TriggerFixVerifyReport:
    operations_raw = plan_payload.get("operations")
    if not isinstance(operations_raw, list):
        raise ValueError("fix plan missing operations")

    report = TriggerFixVerifyReport(scanned_operations=len(operations_raw))
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        for operation_raw in operations_raw:
            if not isinstance(operation_raw, Mapping):
                report.add_failed(-1, "operation_not_object")
                continue

            try:
                group_id = int(operation_raw["trigger_group_id"])
                expected_variants_raw = operation_raw["expected_variants"]
                desired_variant_raw = operation_raw["desired_variant"]
            except Exception as exc:
                report.add_failed(-1, f"invalid_operation: {type(exc).__name__}")
                continue

            if not isinstance(expected_variants_raw, list) or not isinstance(
                desired_variant_raw,
                Mapping,
            ):
                report.add_failed(group_id, "invalid_operation_shape")
                continue

            try:
                desired_variant = _desired_variant_payload(desired_variant_raw)
            except ValueError as exc:
                report.add_failed(group_id, str(exc))
                continue

            variant_ids = [
                int(item["id"])
                for item in expected_variants_raw
                if isinstance(item, Mapping) and "id" in item
            ]
            if not variant_ids or len(variant_ids) != len(expected_variants_raw):
                report.add_failed(group_id, "invalid_expected_variants")
                continue

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
                report.add_failed(group_id, "variant_count_mismatch")
                continue

            all_match = True
            for row in rows:
                if int(row["trigger_group_id"]) != group_id:
                    report.add_failed(group_id, "variant_group_mismatch")
                    all_match = False
                    break
                current_snapshot = _row_variant_snapshot(row)
                if not _variant_matches_desired(current_snapshot, desired_variant):
                    report.add_failed(group_id, "desired_state_mismatch")
                    all_match = False
                    break

            if all_match:
                report.add_verified(group_id, len(rows))
    return report


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path).resolve()
    input_path = Path(args.input).resolve()
    report_path = Path(args.report).resolve()
    plan_payload = _load_plan(input_path)
    version = _coerce_int(plan_payload.get("version", 0), field="version")
    if version != PLAN_VERSION:
        raise ValueError(f"unsupported fix plan version: {version}")

    logger.info(
        "[wordbank-trigger-fix-verify] starting "
        + json.dumps(
            {
                "db_path": str(db_path),
                "input": str(input_path),
            },
            ensure_ascii=False,
        )
    )

    report = verify_trigger_fix_plan(
        db_path=db_path,
        plan_payload=plan_payload,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if report.ok:
        logger.success(
            "[wordbank-trigger-fix-verify] "
            + json.dumps(report.to_dict(), ensure_ascii=False)
        )
        return

    logger.error(
        "[wordbank-trigger-fix-verify] "
        + json.dumps(report.to_dict(), ensure_ascii=False)
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
