"""Audit water storage layout and benchmark log index baseline."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from typing import Any

import zstandard as zstd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lib.utils.common import get_current_time

DEFAULT_DB_ROOT = Path("./data/db")
DEFAULT_NAMESPACE = "water_db"
CORE_FILENAME = "core.db"
LOG_PREFIX = "logs"
SUMMARY_PREFIX = "summary"
EXPECTED_LOG_INDEXES = {
    "idx_water_hourly_counter_group_date_hour",
    "idx_water_hourly_counter_user_date",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Water storage slimming and log index baseline",
    )
    parser.add_argument(
        "--db-root",
        default=str(DEFAULT_DB_ROOT),
        help="database root directory",
    )
    parser.add_argument(
        "--namespace",
        default=DEFAULT_NAMESPACE,
        help="water sharedDB namespace directory",
    )
    parser.add_argument(
        "--report",
        default="./data/db/water-storage-audit.json",
        help="where to write the audit report JSON",
    )
    parser.add_argument(
        "--benchmark-iterations",
        type=int,
        default=20,
        help="number of iterations for each log query benchmark",
    )
    parser.add_argument(
        "--inspect-archives",
        action="store_true",
        help="inspect .db.zst archives by temporary decompression",
    )
    parser.add_argument(
        "--strict-indexes",
        action="store_true",
        help="exit non-zero when the observed log indexes diverge from baseline",
    )
    return parser.parse_args()


def build_water_storage_audit_report(
    db_root: Path,
    *,
    namespace: str = DEFAULT_NAMESPACE,
    benchmark_iterations: int = 20,
    inspect_archives: bool = False,
) -> dict[str, Any]:
    namespace_dir = db_root / namespace
    core_path = namespace_dir / CORE_FILENAME
    return {
        "db_root": str(db_root),
        "namespace": namespace,
        "generated_at": get_current_time(),
        "core": _inspect_core_db(core_path),
        "logs": _inspect_segment_store(
            namespace_dir=namespace_dir,
            prefix=LOG_PREFIX,
            table_name="water_hourly_counter",
            benchmark_iterations=benchmark_iterations,
            inspect_archives=inspect_archives,
        ),
        "summary": _inspect_segment_store(
            namespace_dir=namespace_dir,
            prefix=SUMMARY_PREFIX,
            table_name="water_daily_summary",
            benchmark_iterations=benchmark_iterations,
            inspect_archives=inspect_archives,
        ),
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _inspect_core_db(core_path: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "path": str(core_path),
        "exists": core_path.exists(),
        "size_bytes": core_path.stat().st_size if core_path.exists() else 0,
        "page_count": 0,
        "page_size": 0,
        "top_objects": [],
        "hot_summary": {
            "table_present": False,
            "rows": 0,
            "total_msg_count": 0,
            "avg_hourly_blob_bytes": 0.0,
            "total_hourly_blob_bytes": 0,
            "plain_rows": 0,
            "sparse_rows": 0,
            "unknown_rows": 0,
        },
    }
    if not core_path.exists():
        return report

    with sqlite3.connect(core_path) as conn:
        report["page_count"] = int(_scalar(conn, "PRAGMA page_count") or 0)
        report["page_size"] = int(_scalar(conn, "PRAGMA page_size") or 0)
        report["top_objects"] = _query_dbstat(conn)
        if _table_exists(conn, "water_daily_summary"):
            report["hot_summary"] = {
                "table_present": True,
                **_summary_table_metrics(conn, "water_daily_summary"),
            }
    return report


def _inspect_segment_store(
    *,
    namespace_dir: Path,
    prefix: str,
    table_name: str,
    benchmark_iterations: int,
    inspect_archives: bool,
) -> dict[str, Any]:
    manifest_path = namespace_dir / f"{prefix}_manifest.json"
    manifest_entries = _load_manifest_entries(manifest_path)
    shards = _discover_shards(namespace_dir, prefix, manifest_entries)
    inspected_shards: list[dict[str, Any]] = []
    expected_missing: dict[str, list[str]] = {}
    unexpected_indexes: dict[str, list[str]] = {}

    for shard in shards:
        should_inspect = not shard["is_archive"] or inspect_archives
        if not should_inspect:
            continue
        if table_name == "water_hourly_counter":
            shard_report = _inspect_log_shard(
                shard["path"],
                is_archive=bool(shard["is_archive"]),
                benchmark_iterations=benchmark_iterations,
                shard_key=str(shard["shard_key"]),
            )
            missing = shard_report["missing_indexes"]
            unexpected = shard_report["unexpected_indexes"]
            if missing:
                expected_missing[str(shard["shard_key"])] = missing
            if unexpected:
                unexpected_indexes[str(shard["shard_key"])] = unexpected
        else:
            shard_report = _inspect_summary_shard(
                shard["path"],
                is_archive=bool(shard["is_archive"]),
                shard_key=str(shard["shard_key"]),
            )
        inspected_shards.append(shard_report)

    total_size_bytes = sum(int(shard["size_bytes"]) for shard in shards)
    report: dict[str, Any] = {
        "base_dir": str(namespace_dir),
        "prefix": prefix,
        "manifest_path": str(manifest_path),
        "manifest_present": manifest_path.exists(),
        "total_size_bytes": total_size_bytes,
        "online_shards": sum(1 for shard in shards if not shard["is_archive"]),
        "archived_shards": sum(1 for shard in shards if shard["is_archive"]),
        "shards": shards,
        "inspected_shards": inspected_shards,
    }
    if table_name == "water_hourly_counter":
        report["index_baseline"] = {
            "expected_indexes": sorted(EXPECTED_LOG_INDEXES),
            "inspected_shard_count": len(inspected_shards),
            "ok": not expected_missing and not unexpected_indexes,
            "missing_indexes": expected_missing,
            "unexpected_indexes": unexpected_indexes,
        }
    return report


def _load_manifest_entries(manifest_path: Path) -> dict[str, dict[str, Any]]:
    if not manifest_path.exists():
        return {}
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_entries = payload.get("segments", {})
    return {
        str(shard_key): dict(entry)
        for shard_key, entry in raw_entries.items()
        if isinstance(entry, dict)
    }


def _discover_shards(
    namespace_dir: Path,
    prefix: str,
    manifest_entries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    shards: dict[str, dict[str, Any]] = {}
    for pattern, is_archive in (
        (f"{prefix}_*.db", False),
        (f"{prefix}_*.db.zst", True),
    ):
        for file_path in sorted(namespace_dir.glob(pattern)):
            shard_key = file_path.name.removeprefix(f"{prefix}_")
            shard_key = shard_key.removesuffix(".db.zst").removesuffix(".db")
            entry = manifest_entries.get(shard_key, {})
            shards[shard_key] = {
                "shard_key": shard_key,
                "path": str(file_path),
                "is_archive": is_archive,
                "size_bytes": file_path.stat().st_size,
                "state": entry.get("state", "cold" if is_archive else "hot"),
                "manifest_row_count": int(entry.get("row_count", 0) or 0),
                "manifest_size_bytes": int(entry.get("size_bytes", 0) or 0),
                "last_access_at": int(entry.get("last_access_at", 0) or 0),
                "hydrated_at": int(entry.get("hydrated_at", 0) or 0),
            }
    for shard_key, entry in manifest_entries.items():
        shards.setdefault(
            shard_key,
            {
                "shard_key": shard_key,
                "path": str(entry.get("archive_path") or entry.get("path") or ""),
                "is_archive": str(entry.get("state", "cold")).lower() == "cold",
                "size_bytes": int(entry.get("size_bytes", 0) or 0),
                "state": entry.get("state", "unknown"),
                "manifest_row_count": int(entry.get("row_count", 0) or 0),
                "manifest_size_bytes": int(entry.get("size_bytes", 0) or 0),
                "last_access_at": int(entry.get("last_access_at", 0) or 0),
                "hydrated_at": int(entry.get("hydrated_at", 0) or 0),
            },
        )
    return [shards[key] for key in sorted(shards)]


@contextmanager
def _open_sqlite(path: str, *, is_archive: bool) -> Any:
    source = Path(path)
    if not is_archive:
        conn = sqlite3.connect(source)
        try:
            yield conn
        finally:
            conn.close()
        return

    with tempfile.TemporaryDirectory(prefix="water-audit-") as tmp_dir:
        hydrated_path = Path(tmp_dir) / source.name.removesuffix(".zst")
        dctx = zstd.ZstdDecompressor()
        with source.open("rb") as src, hydrated_path.open("wb") as dst:
            dctx.copy_stream(src, dst)
        conn = sqlite3.connect(hydrated_path)
        try:
            yield conn
        finally:
            conn.close()


def _inspect_log_shard(
    path: str,
    *,
    is_archive: bool,
    benchmark_iterations: int,
    shard_key: str,
) -> dict[str, Any]:
    with _open_sqlite(path, is_archive=is_archive) as conn:
        index_names = _list_table_indexes(conn, "water_hourly_counter")
        row_count = int(_scalar(conn, "SELECT COUNT(*) FROM water_hourly_counter") or 0)
        sample = _pick_log_sample(conn)
        benchmarks = (
            _run_log_benchmarks(
                conn,
                sample=sample,
                iterations=benchmark_iterations,
            )
            if sample is not None
            else []
        )
    current_indexes = set(index_names)
    unexpected = sorted(
        index_name
        for index_name in current_indexes
        if index_name not in EXPECTED_LOG_INDEXES
    )
    missing = sorted(EXPECTED_LOG_INDEXES - current_indexes)
    return {
        "shard_key": shard_key,
        "path": path,
        "is_archive": is_archive,
        "row_count": row_count,
        "index_names": index_names,
        "missing_indexes": missing,
        "unexpected_indexes": unexpected,
        "sample": sample,
        "benchmarks": benchmarks,
    }


def _inspect_summary_shard(
    path: str,
    *,
    is_archive: bool,
    shard_key: str,
) -> dict[str, Any]:
    with _open_sqlite(path, is_archive=is_archive) as conn:
        metrics = _summary_table_metrics(conn, "water_daily_summary")
    return {
        "shard_key": shard_key,
        "path": path,
        "is_archive": is_archive,
        **metrics,
    }


def _summary_table_metrics(conn: sqlite3.Connection, table_name: str) -> dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS rows,
            COALESCE(SUM(msg_count), 0) AS total_msg_count,
            COALESCE(AVG(LENGTH(hourly_counts)), 0) AS avg_hourly_blob_bytes,
            COALESCE(SUM(LENGTH(hourly_counts)), 0) AS total_hourly_blob_bytes,
            COALESCE(
                SUM(CASE WHEN substr(hourly_counts, 1, 1) = x'00' THEN 1 ELSE 0 END),
                0
            ) AS plain_rows,
            COALESCE(
                SUM(CASE WHEN substr(hourly_counts, 1, 1) = x'01' THEN 1 ELSE 0 END),
                0
            ) AS sparse_rows
        FROM {table_name}
        """
    ).fetchone()
    assert row is not None
    rows = int(row[0] or 0)
    plain_rows = int(row[4] or 0)
    sparse_rows = int(row[5] or 0)
    return {
        "rows": rows,
        "total_msg_count": int(row[1] or 0),
        "avg_hourly_blob_bytes": round(float(row[2] or 0.0), 2),
        "total_hourly_blob_bytes": int(row[3] or 0),
        "plain_rows": plain_rows,
        "sparse_rows": sparse_rows,
        "unknown_rows": max(0, rows - plain_rows - sparse_rows),
    }


def _pick_log_sample(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT record_date, group_id, user_id
        FROM water_hourly_counter
        ORDER BY record_date DESC, msg_count DESC, group_id ASC, user_id ASC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return {
        "record_date": int(row[0]),
        "group_id": str(row[1]),
        "user_id": str(row[2]),
    }


def _run_log_benchmarks(
    conn: sqlite3.Connection,
    *,
    sample: dict[str, Any],
    iterations: int,
) -> list[dict[str, Any]]:
    record_date = int(sample["record_date"])
    group_id = str(sample["group_id"])
    user_id = str(sample["user_id"])
    queries = [
        (
            "leaderboard_by_group_day",
            """
            SELECT user_id, SUM(msg_count) AS total
            FROM water_hourly_counter
            WHERE group_id = ? AND record_date = ?
            GROUP BY user_id
            ORDER BY total DESC
            LIMIT 20
            """,
            (group_id, record_date),
        ),
        (
            "user_hourly_distribution",
            """
            SELECT user_id, hour, msg_count
            FROM water_hourly_counter
            WHERE group_id = ? AND user_id = ? AND record_date = ?
            ORDER BY hour ASC
            """,
            (group_id, user_id, record_date),
        ),
        (
            "daily_group_user_aggregate",
            """
            SELECT
                group_id,
                user_id,
                SUM(msg_count) AS msg_count,
                COUNT(hour) AS active_hours
            FROM water_hourly_counter
            WHERE record_date = ?
            GROUP BY group_id, user_id
            """,
            (record_date,),
        ),
    ]
    return [
        _benchmark_query(conn, name=name, sql=sql, params=params, iterations=iterations)
        for name, sql, params in queries
    ]


def _benchmark_query(
    conn: sqlite3.Connection,
    *,
    name: str,
    sql: str,
    params: tuple[Any, ...],
    iterations: int,
) -> dict[str, Any]:
    explain_rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    start = time.perf_counter()
    row_count = 0
    for _ in range(max(1, iterations)):
        row_count = len(conn.execute(sql, params).fetchall())
    elapsed = time.perf_counter() - start
    return {
        "name": name,
        "iterations": max(1, iterations),
        "row_count": row_count,
        "avg_ms": round((elapsed / max(1, iterations)) * 1000, 4),
        "plan": [str(row[3]) for row in explain_rows],
    }


def _list_table_indexes(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA index_list('{table_name}')").fetchall()
    names = [
        str(row[1]) for row in rows if not str(row[1]).startswith("sqlite_autoindex")
    ]
    return sorted(names)


def _query_dbstat(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT name, SUM(pgsize) AS size_bytes
            FROM dbstat
            GROUP BY name
            ORDER BY size_bytes DESC, name ASC
            LIMIT 20
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {"name": str(name), "size_bytes": int(size_bytes or 0)}
        for name, size_bytes in rows
    ]


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return bool(
        conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            LIMIT 1
            """,
            (table_name,),
        ).fetchone()
    )


def _scalar(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def main() -> None:
    args = parse_args()
    report = build_water_storage_audit_report(
        Path(args.db_root),
        namespace=args.namespace,
        benchmark_iterations=args.benchmark_iterations,
        inspect_archives=args.inspect_archives,
    )
    write_report(Path(args.report), report)
    baseline = report["logs"].get("index_baseline", {})
    if args.strict_indexes and not baseline.get("ok", False):
        raise SystemExit("water log index baseline check failed")


if __name__ == "__main__":
    main()
