from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest
import zstandard as zstd

from scripts import audit_water_storage as audit_script


def _create_log_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE water_hourly_counter (
                record_date INTEGER NOT NULL,
                hour INTEGER NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                msg_count INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX idx_water_hourly_counter_group_date_hour
            ON water_hourly_counter (group_id, record_date, hour)
            """
        )
        conn.execute(
            """
            CREATE INDEX idx_water_hourly_counter_user_date
            ON water_hourly_counter (user_id, record_date)
            """
        )
        conn.executemany(
            """
            INSERT INTO water_hourly_counter
            (record_date, hour, group_id, user_id, msg_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (20260611, 8, "20001", "10001", 2),
                (20260611, 9, "20001", "10001", 3),
                (20260611, 8, "20001", "10002", 1),
            ],
        )


def _create_summary_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE water_daily_summary (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                record_date INTEGER NOT NULL,
                msg_count INTEGER NOT NULL,
                active_hours INTEGER NOT NULL,
                hourly_counts BLOB NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO water_daily_summary
            (group_id, user_id, record_date, msg_count, active_hours, hourly_counts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "20001",
                "10001",
                20260501,
                5,
                2,
                bytes([1, 2, 8, 0, 9, 1, 0]),
            ),
        )


def _compress_to_zstd(source: Path, target: Path) -> None:
    cctx = zstd.ZstdCompressor(level=3)
    with source.open("rb") as src, target.open("wb") as dst:
        cctx.copy_stream(src, dst)


def test_audit_water_storage_parse_args_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["audit_water_storage.py"])

    args = audit_script.parse_args()

    assert Path(args.db_root) == Path("./data/db")
    assert args.namespace == "water_db"
    assert args.report == "./data/db/water-storage-audit.json"
    assert args.benchmark_iterations == 20
    assert args.inspect_archives is False
    assert args.strict_indexes is False


def test_build_water_storage_audit_report_reads_logs_and_summary(
    tmp_path: Path,
) -> None:
    namespace_dir = tmp_path / "water_db"
    namespace_dir.mkdir(parents=True)
    core_path = namespace_dir / "core.db"
    log_path = namespace_dir / "logs_2026_06.db"
    summary_path = namespace_dir / "summary_2026_05.db"

    _create_summary_db(core_path)
    _create_log_db(log_path)
    _create_summary_db(summary_path)

    report = audit_script.build_water_storage_audit_report(
        tmp_path,
        benchmark_iterations=2,
    )

    assert report["core"]["exists"] is True
    assert report["core"]["hot_summary"]["rows"] == 1
    assert report["logs"]["online_shards"] == 1
    assert report["logs"]["index_baseline"]["ok"] is True
    assert report["logs"]["inspected_shards"][0]["benchmarks"]
    assert report["summary"]["online_shards"] == 1
    assert report["summary"]["inspected_shards"][0]["rows"] == 1
    assert report["summary"]["inspected_shards"][0]["sparse_rows"] == 1


def test_build_water_storage_audit_report_inspects_archived_summary_shards(
    tmp_path: Path,
) -> None:
    namespace_dir = tmp_path / "water_db"
    namespace_dir.mkdir(parents=True)
    log_path = namespace_dir / "logs_2026_06.db"
    raw_summary_path = namespace_dir / "summary_2026_05.db"
    archive_summary_path = namespace_dir / "summary_2026_05.db.zst"

    _create_log_db(log_path)
    _create_summary_db(raw_summary_path)
    _compress_to_zstd(raw_summary_path, archive_summary_path)
    raw_summary_path.unlink()
    (namespace_dir / "summary_manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "segments": {
                    "2026_05": {
                        "segment_id": "2026_05",
                        "state": "cold",
                        "path": str(raw_summary_path),
                        "archive_path": str(archive_summary_path),
                        "row_count": 0,
                        "size_bytes": archive_summary_path.stat().st_size,
                        "last_access_at": 0,
                        "hydrated_at": 0,
                        "updated_at": 0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = audit_script.build_water_storage_audit_report(
        tmp_path,
        inspect_archives=True,
        benchmark_iterations=1,
    )

    assert report["summary"]["archived_shards"] == 1
    assert report["summary"]["inspected_shards"][0]["is_archive"] is True
    assert report["summary"]["inspected_shards"][0]["rows"] == 1


def test_main_strict_indexes_exits_on_baseline_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "report.json"
    args = type(
        "Args",
        (),
        {
            "db_root": str(tmp_path),
            "namespace": "water_db",
            "report": str(report_path),
            "benchmark_iterations": 1,
            "inspect_archives": False,
            "strict_indexes": True,
        },
    )()

    monkeypatch.setattr(audit_script, "parse_args", lambda: args)
    monkeypatch.setattr(
        audit_script,
        "build_water_storage_audit_report",
        lambda *args, **kwargs: {
            "logs": {"index_baseline": {"ok": False}},
        },
    )

    with pytest.raises(SystemExit, match="water log index baseline check failed"):
        audit_script.main()
