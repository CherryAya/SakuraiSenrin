"""Export a PostgreSQL-backed wordbank trigger-fix plan for later SQLite repair."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fix_wordbank_trigger_migration import (
    DEFAULT_DB_PATH,
    DEFAULT_PLAN_PATH,
    build_pg_config,
    build_trigger_fix_plan,
    resolve_paths,
)
from scripts.migrations.wordbank_legacy_source import fetch_legacy_response_rows
from scripts.migrations.wordbank_types import LegacyPgConfig
from src.logger import logger


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
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="path to the current wordbank_main.db",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_PLAN_PATH),
        help="where to write the exported trigger-fix plan JSON",
    )
    return parser.parse_args()


def export_trigger_fix_plan(
    *,
    db_path: Path,
    output_path: Path,
    image_root: Path,
    mapping_path: Path | None,
    pg_config: LegacyPgConfig,
) -> dict[str, object]:
    legacy_rows = asyncio.run(fetch_legacy_response_rows(pg_config))
    plan_payload, report = build_trigger_fix_plan(
        db_path=db_path,
        legacy_rows=legacy_rows,
        image_root=image_root,
        mapping_path=mapping_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(plan_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.success(
        "[wordbank-trigger-plan-export] "
        + json.dumps(report.to_dict(), ensure_ascii=False)
    )
    return report.to_dict()


def main() -> None:
    args = parse_args()
    _, image_root, mapping_path = resolve_paths(args)
    db_path = Path(args.db_path).resolve()
    output_path = Path(args.output).resolve()
    pg_config = build_pg_config(args)

    logger.info(
        "[wordbank-trigger-plan-export] starting "
        + json.dumps(
            {
                "db_path": str(db_path),
                "output": str(output_path),
                "image_root": str(image_root),
                "mapping_path": str(mapping_path) if mapping_path else "",
            },
            ensure_ascii=False,
        )
    )

    export_trigger_fix_plan(
        db_path=db_path,
        output_path=output_path,
        image_root=image_root,
        mapping_path=mapping_path,
        pg_config=pg_config,
    )


if __name__ == "__main__":
    main()
