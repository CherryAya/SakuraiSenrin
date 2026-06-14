from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from scripts import audit_wordbank_rules as audit_script
from src.plugins.wordbank.services.rules import MAX_CALL_COUNT_WINDOW_SECONDS


def _init_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE wordbank_trigger_group (
                id INTEGER PRIMARY KEY,
                group_id TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'approved'
            );
            CREATE TABLE wordbank_trigger_variant (
                id INTEGER PRIMARY KEY,
                trigger_group_id INTEGER NOT NULL,
                trigger_text TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE wordbank_response_item (
                id INTEGER PRIMARY KEY,
                trigger_group_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'approved',
                enabled INTEGER NOT NULL DEFAULT 1,
                scope TEXT NOT NULL DEFAULT 'current_group',
                rule TEXT NOT NULL DEFAULT '{}'
            );
            """
        )


def test_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["audit_wordbank_rules.py"])

    args = audit_script.parse_args()

    assert args.db_path.endswith("data/db/wordbank_db/wordbank_main.db")
    assert args.report == audit_script.DEFAULT_REPORT
    assert args.apply is False


def test_audit_wordbank_rules_reports_oversized_call_count(tmp_path: Path) -> None:
    db_path = tmp_path / "wordbank_main.db"
    _init_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO wordbank_trigger_group (id, group_id, created_by, status) "
            "VALUES (18, '', '2219126424', 'approved')"
        )
        connection.execute(
            "INSERT INTO wordbank_trigger_variant (id, trigger_group_id, trigger_text) "
            "VALUES (18, 18, '?')"
        )
        connection.execute(
            "INSERT INTO wordbank_response_item "
            "(id, trigger_group_id, status, enabled, scope, rule) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                9914,
                18,
                "approved",
                1,
                "all_groups",
                json.dumps(
                    {
                        "roles": "member",
                        "call_count": {
                            "window_seconds": MAX_CALL_COUNT_WINDOW_SECONDS + 1,
                            "min": 3,
                            "max": 0,
                        },
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.execute(
            "INSERT INTO wordbank_response_item "
            "(id, trigger_group_id, status, enabled, scope, rule) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (9915, 18, "approved", 1, "all_groups", json.dumps({"roles": "member"})),
        )
        connection.commit()

    report = audit_script.audit_wordbank_rules(db_path)

    assert report["scanned"] == 2
    assert report["issue_counts"] == {"call_count_window_too_large": 1}
    assert len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["response_id"] == 9914
    assert row["issues"] == ["call_count_window_too_large"]
    assert row["trigger_texts"] == "?"
    assert row["suggested_rule"] == {"roles": "member"}
    assert "UPDATE wordbank_response_item" in row["suggested_update_sql"]


def test_apply_suggested_fixes_removes_invalid_call_count(tmp_path: Path) -> None:
    db_path = tmp_path / "wordbank_main.db"
    _init_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO wordbank_trigger_group (id, group_id, created_by, status) "
            "VALUES (1, '20001', '10001', 'approved')"
        )
        connection.execute(
            "INSERT INTO wordbank_trigger_variant (id, trigger_group_id, trigger_text) "
            "VALUES (1, 1, 'test')"
        )
        connection.execute(
            "INSERT INTO wordbank_response_item "
            "(id, trigger_group_id, status, enabled, scope, rule) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                1,
                1,
                "approved",
                1,
                "current_group",
                json.dumps(
                    {
                        "call_count": {
                            "window_seconds": MAX_CALL_COUNT_WINDOW_SECONDS + 99,
                            "min": 1,
                            "max": 0,
                        },
                        "roles": "admin",
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()

    report = audit_script.audit_wordbank_rules(db_path)
    applied = audit_script.apply_suggested_fixes(db_path, report)

    assert applied == 1
    with sqlite3.connect(db_path) as connection:
        stored_rule = connection.execute(
            "SELECT rule FROM wordbank_response_item WHERE id = 1"
        ).fetchone()[0]
    assert json.loads(stored_rule) == {"roles": "admin"}
