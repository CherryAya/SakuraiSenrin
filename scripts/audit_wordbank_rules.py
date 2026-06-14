"""Audit wordbank rules and find invalid call_count windows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_pkg(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(path)]  # type: ignore[attr-defined]
    sys.modules[name] = pkg


_ensure_pkg("src.plugins.wordbank", ROOT / "src" / "plugins" / "wordbank")
_ensure_pkg(
    "src.plugins.wordbank.services",
    ROOT / "src" / "plugins" / "wordbank" / "services",
)

from src.lib.consts import GLOBAL_DB_ROOT
from src.lib.utils.common import get_current_time
from src.logger import logger
from src.plugins.wordbank.services.rules import MAX_CALL_COUNT_WINDOW_SECONDS

DEFAULT_REPORT = "./data/db/wordbank-rule-audit-report.json"
DEFAULT_MIGRATION_CALL_WINDOW_SECONDS = 60 * 60 * 24 * 90


def default_db_path() -> Path:
    return GLOBAL_DB_ROOT / "wordbank_db" / "wordbank_main.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit wordbank rules")
    parser.add_argument(
        "--db-path",
        default=str(default_db_path()),
        help="path to wordbank_main.db",
    )
    parser.add_argument(
        "--report",
        default=DEFAULT_REPORT,
        help="where to write the audit report JSON",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply safe fixes by removing invalid call_count constraints",
    )
    return parser.parse_args()


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def _load_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    query = """
    SELECT
        ri.id AS response_id,
        ri.trigger_group_id,
        tg.group_id AS chat_id,
        tg.created_by,
        ri.status,
        ri.enabled,
        ri.scope,
        ri.rule,
        GROUP_CONCAT(tv.trigger_text, ' || ') AS trigger_texts
    FROM wordbank_response_item ri
    JOIN wordbank_trigger_group tg ON tg.id = ri.trigger_group_id
    LEFT JOIN wordbank_trigger_variant tv ON tv.trigger_group_id = tg.id
    GROUP BY
        ri.id,
        ri.trigger_group_id,
        tg.group_id,
        tg.created_by,
        ri.status,
        ri.enabled,
        ri.scope,
        ri.rule
    ORDER BY ri.id
    """
    return list(connection.execute(query).fetchall())


def _normalize_rule(raw_rule: object) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(raw_rule, dict):
        return dict(raw_rule), None
    if raw_rule in (None, ""):
        return {}, None
    try:
        loaded = json.loads(str(raw_rule))
    except json.JSONDecodeError:
        return None, "rule_json_invalid"
    if not isinstance(loaded, dict):
        return None, "rule_not_object"
    return dict(loaded), None


def _find_rule_issues(rule: dict[str, Any]) -> list[str]:
    call_count = rule.get("call_count")
    if call_count is None:
        return []
    if not isinstance(call_count, dict):
        return ["call_count_not_object"]

    try:
        window_seconds = int(call_count.get("window_seconds", 0))
        min_count = int(call_count.get("min", 0))
        max_count = int(call_count.get("max", 0))
    except (TypeError, ValueError):
        return ["call_count_non_integer"]

    issues: list[str] = []
    if window_seconds <= 0:
        issues.append("call_count_window_non_positive")
    if window_seconds > MAX_CALL_COUNT_WINDOW_SECONDS:
        issues.append("call_count_window_too_large")
    if min_count < 0 or max_count < 0:
        issues.append("call_count_negative_bounds")
    if max_count and min_count > max_count:
        issues.append("call_count_min_gt_max")
    return issues


def _build_suggested_rule(
    rule: dict[str, Any],
    issues: list[str],
) -> dict[str, Any] | None:
    if not issues:
        return None
    if not any(issue.startswith("call_count_") for issue in issues):
        return None
    suggested = dict(rule)
    call_count = suggested.get("call_count")
    if not isinstance(call_count, dict):
        suggested.pop("call_count", None)
        return suggested
    suggested["call_count"] = {
        "window_seconds": DEFAULT_MIGRATION_CALL_WINDOW_SECONDS,
        "min": max(int(call_count.get("min", 0)), 0),
        "max": max(int(call_count.get("max", 0)), 0),
    }
    return suggested


def _build_update_sql(
    *,
    response_id: int,
    suggested_rule: dict[str, Any] | None,
) -> str | None:
    if suggested_rule is None:
        return None
    escaped_rule = json.dumps(
        suggested_rule,
        ensure_ascii=False,
        sort_keys=True,
    ).replace("'", "''")
    return (
        "UPDATE wordbank_response_item "
        f"SET rule = '{escaped_rule}' "
        f"WHERE id = {response_id};"
    )


def audit_wordbank_rules(db_path: Path) -> dict[str, Any]:
    rows_report: list[dict[str, Any]] = []
    issue_counts: dict[str, int] = {}
    scanned = 0
    with _connect(db_path) as connection:
        for row in _load_rows(connection):
            scanned += 1
            rule, parse_issue = _normalize_rule(row["rule"])
            issues = [parse_issue] if parse_issue else []
            if rule is not None:
                issues.extend(_find_rule_issues(rule))
            if not issues:
                continue
            for issue in issues:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
            suggested_rule = _build_suggested_rule(rule or {}, issues)
            rows_report.append(
                {
                    "response_id": int(row["response_id"]),
                    "trigger_group_id": int(row["trigger_group_id"]),
                    "chat_id": str(row["chat_id"] or ""),
                    "created_by": str(row["created_by"] or ""),
                    "status": str(row["status"] or ""),
                    "enabled": int(row["enabled"] or 0),
                    "scope": str(row["scope"] or ""),
                    "trigger_texts": str(row["trigger_texts"] or ""),
                    "rule": row["rule"] if isinstance(row["rule"], str) else rule or {},
                    "issues": issues,
                    "suggested_rule": suggested_rule,
                    "suggested_update_sql": _build_update_sql(
                        response_id=int(row["response_id"]),
                        suggested_rule=suggested_rule,
                    ),
                }
            )
    return {
        "generated_at": get_current_time(),
        "db_path": str(db_path),
        "max_call_count_window_seconds": MAX_CALL_COUNT_WINDOW_SECONDS,
        "scanned": scanned,
        "issue_counts": issue_counts,
        "rows": rows_report,
    }


def apply_suggested_fixes(db_path: Path, report: dict[str, Any]) -> int:
    statements = [
        row["suggested_update_sql"]
        for row in report["rows"]
        if isinstance(row.get("suggested_update_sql"), str)
        and row["suggested_update_sql"]
    ]
    if not statements:
        return 0
    with _connect(db_path) as connection:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    return len(statements)


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path).resolve()
    report_path = Path(args.report).resolve()
    report = audit_wordbank_rules(db_path)
    applied = 0
    if args.apply:
        applied = apply_suggested_fixes(db_path, report)
        report["applied_fix_count"] = applied
    write_report(report_path, report)
    logger.info(
        "[wordbank-rule-audit] "
        + json.dumps(
            {
                "db_path": str(db_path),
                "report": str(report_path),
                "scanned": report["scanned"],
                "issue_counts": report["issue_counts"],
                "applied_fix_count": applied,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
