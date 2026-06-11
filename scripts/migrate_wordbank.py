"""Migrate legacy PostgreSQL wordbank records into the new SQLite wordbank."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import types
from typing import TYPE_CHECKING, Any, cast

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
_ensure_pkg(
    "src.plugins.wordbank.database",
    ROOT / "src" / "plugins" / "wordbank" / "database",
)
_ensure_pkg("src.lib.object_storage", ROOT / "src" / "lib" / "object_storage")

from src.logger import logger
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.migration import (
    LegacyPgConfig,
    build_legacy_image_catalog,
    load_legacy_pg_config,
    migrate_legacy_rows,
)
from src.plugins.wordbank.services.media import WordbankMediaService

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TypedDict

    class LegacyWordbankRow(TypedDict):
        response_id: int
        trigger_id: int
        response_text: object
        response_rule_conditions: object
        weight: int
        priority: int
        created_by: str
        created_at: object
        response_available: bool
        trigger_text: object
        trigger_config: object
        extra_info: object
        approval_status: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy wordbank data")
    parser.add_argument(
        "--old-repo",
        default="../sakuraisenrin-old",
        help="path to the legacy repository root",
    )
    parser.add_argument(
        "--image-root",
        default="../SakuraiSenrinPic/recovered_files_smart",
        help="path to the local recovered image directory",
    )
    parser.add_argument(
        "--mapping-file",
        default="../SakuraiSenrinPic/file_mapping_smart.json",
        help="path to the legacy image mapping file",
    )
    parser.add_argument("--pg-host", help="legacy PostgreSQL host override")
    parser.add_argument("--pg-port", type=int, help="legacy PostgreSQL port override")
    parser.add_argument("--pg-user", help="legacy PostgreSQL user override")
    parser.add_argument("--pg-password", help="legacy PostgreSQL password override")
    parser.add_argument(
        "--pg-database",
        default="senrin_wordbank",
        help="legacy PostgreSQL database name",
    )
    parser.add_argument(
        "--report",
        default="./data/wordbank/migration-report.json",
        help="where to write the migration report JSON",
    )
    parser.add_argument(
        "--no-reset-target",
        action="store_true",
        help="do not clear the new wordbank before importing",
    )
    return parser.parse_args()


def build_pg_config(args: argparse.Namespace) -> LegacyPgConfig:
    defaults = load_legacy_pg_config(Path(args.old_repo))
    return LegacyPgConfig(
        host=args.pg_host or defaults.host,
        port=args.pg_port or defaults.port,
        user=args.pg_user or defaults.user,
        password=args.pg_password or defaults.password,
        database=args.pg_database or defaults.database,
    )


def fetch_legacy_rows(config: LegacyPgConfig) -> list[dict[str, object]]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        dbname=config.database,
    )
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
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
                    a.current_status AS approval_status
                FROM response AS r
                JOIN trigger AS t
                    ON t.trigger_id = r.trigger_id
                JOIN approval AS a
                    ON a.response_id = r.response_id
                ORDER BY r.response_id ASC
                """
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return [dict(cast(Any, row)) for row in rows]


async def main() -> None:
    args = parse_args()
    rows = await asyncio.to_thread(fetch_legacy_rows, build_pg_config(args))
    repository = WordbankRepository()
    media_service = WordbankMediaService(repository)
    image_catalog = build_legacy_image_catalog(
        Path(args.image_root),
        Path(args.mapping_file) if args.mapping_file else None,
    )
    report = await migrate_legacy_rows(
        cast("Sequence[LegacyWordbankRow]", rows),
        repository=repository,
        media_service=media_service,
        image_catalog=image_catalog,
        reset_target=not args.no_reset_target,
    )

    report_path = Path(args.report)
    categorized_report_path = _derived_categorized_report_path(report_path)
    await asyncio.to_thread(
        report_path.parent.mkdir,
        parents=True,
        exist_ok=True,
    )
    await asyncio.to_thread(
        report_path.write_text,
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        "utf-8",
    )
    await asyncio.to_thread(
        categorized_report_path.write_text,
        json.dumps(report.to_failure_categories_dict(), ensure_ascii=False, indent=2),
        "utf-8",
    )

    logger.success(
        json.dumps(
            {
                "total_rows": report.total_rows,
                "imported_rows": report.imported_rows,
                "imported_entries": report.imported_entries,
                "skipped_rows": report.skipped_rows,
                "status_counts": dict(report.status_counts),
                "report": str(report_path),
                "categorized_report": str(categorized_report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _derived_categorized_report_path(report_path: Path) -> Path:
    suffix = report_path.suffix or ".json"
    stem = report_path.stem if report_path.suffix else report_path.name
    return report_path.with_name(f"{stem}-by-error{suffix}")


if __name__ == "__main__":
    asyncio.run(main())
