"""Migrate legacy PostgreSQL wordbank records into the current SQLite wordbank."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
import json
from pathlib import Path
import sys
import types

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
    LegacyMigrationProgressCallback,
    LegacyPgConfig,
    load_legacy_pg_config,
    migrate_legacy_wordbank,
)
from src.plugins.wordbank.services.media import WordbankMediaService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy wordbank data")
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
        "--report",
        default="./data/wordbank/migration-report.json",
        help="where to write the migration report JSON",
    )
    parser.add_argument(
        "--no-reset-target",
        action="store_true",
        help="do not clear the new wordbank before importing",
    )
    parser.add_argument(
        "--no-import-logs",
        action="store_true",
        help="skip importing legacy response/approval logs",
    )
    parser.add_argument(
        "--progress-step",
        type=int,
        default=100,
        help="log progress every N rows for each migration phase",
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


def resolve_script_paths(
    args: argparse.Namespace,
) -> tuple[Path, Path | None, Path | None]:
    old_repo_root = Path(args.old_repo).resolve()
    image_root = Path(args.image_root).resolve() if args.image_root else None
    mapping_path = Path(args.mapping_file).resolve() if args.mapping_file else None
    return old_repo_root, image_root, mapping_path


def build_progress_logger(
    step: int,
) -> LegacyMigrationProgressCallback:
    effective_step = max(1, step)

    def _callback(
        phase: str,
        current: int,
        total: int,
        detail: Mapping[str, object],
    ) -> None:
        if phase == "search_index":
            if current == 0:
                logger.info("[wordbank-migration] rebuilding search index...")
            else:
                logger.info("[wordbank-migration] search index rebuilt")
            return

        label_map = {
            "entries": "entries",
            "response_logs": "response_logs",
            "approval_refs": "approval_refs",
        }
        label = label_map.get(phase, phase)
        suffix = ""
        if detail:
            suffix = " " + json.dumps(detail, ensure_ascii=False, sort_keys=True)
        if total <= 0:
            logger.info(f"[wordbank-migration] {label}: 0/0{suffix}")
            return
        if current in {0, 1, total} or current % effective_step == 0:
            logger.info(f"[wordbank-migration] {label}: {current}/{total}{suffix}")

    return _callback


async def main() -> None:
    args = parse_args()
    old_repo_root, image_root, mapping_path = resolve_script_paths(args)
    progress = build_progress_logger(args.progress_step)

    logger.info(
        "[wordbank-migration] starting "
        + json.dumps(
            {
                "old_repo": str(old_repo_root),
                "image_root": str(image_root) if image_root is not None else "default",
                "mapping_file": (
                    str(mapping_path) if mapping_path is not None else "default"
                ),
                "reset_target": not args.no_reset_target,
                "import_logs": not args.no_import_logs,
                "progress_step": max(1, args.progress_step),
            },
            ensure_ascii=False,
        )
    )

    repository = WordbankRepository()
    media_service = WordbankMediaService(repository)
    report = await migrate_legacy_wordbank(
        old_repo_root,
        repository=repository,
        media_service=media_service,
        image_root=image_root,
        mapping_path=mapping_path,
        pg_config=build_pg_config(args),
        reset_target=not args.no_reset_target,
        import_logs=not args.no_import_logs,
        progress=progress,
        progress_every=args.progress_step,
    )

    report_path = Path(args.report)
    categorized_report_path = _derived_categorized_report_path(report_path)
    await asyncio.to_thread(report_path.parent.mkdir, parents=True, exist_ok=True)
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
                "imported_log_rows": report.imported_log_rows,
                "imported_trigger_log_rows": report.imported_trigger_log_rows,
                "imported_approval_ref_rows": report.imported_approval_ref_rows,
                "image_resolution_counts": dict(report.image_resolution_counts),
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
