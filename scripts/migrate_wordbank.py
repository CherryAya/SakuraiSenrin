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
    LegacyImageCatalog,
    LegacyMigrationProgressCallback,
    LegacyPgConfig,
    MigrationError,
    WordbankMigrationReport,
    extract_failure_details_from_categorized_report,
    load_legacy_pg_config,
    migrate_legacy_rows,
    migrate_legacy_wordbank,
    rebuild_legacy_rows_from_failure_details,
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
    parser.add_argument(
        "--append-from-report",
        help=(
            "append import from an existing migration report JSON; "
            "does not read legacy PostgreSQL"
        ),
    )
    parser.add_argument(
        "--append-from-error-report",
        help=(
            "append import from a categorized error report JSON; "
            "defaults to categories selected by --error-category"
        ),
    )
    parser.add_argument(
        "--error-category",
        action="append",
        dest="error_categories",
        help=(
            "error category to append from categorized report; "
            "repeatable, defaults to trigger_shape_empty"
        ),
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
    append_mode = bool(args.append_from_report or args.append_from_error_report)

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
                "append_from_report": args.append_from_report or "",
                "append_from_error_report": args.append_from_error_report or "",
                "error_categories": args.error_categories or ["trigger_shape_empty"],
            },
            ensure_ascii=False,
        )
    )

    repository = WordbankRepository()
    media_service = WordbankMediaService(repository)
    if append_mode:
        report = await _append_from_report(
            args,
            repository=repository,
            media_service=media_service,
            image_root=image_root,
            mapping_path=mapping_path,
            progress=progress,
        )
    else:
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


async def _append_from_report(
    args: argparse.Namespace,
    *,
    repository: WordbankRepository,
    media_service: WordbankMediaService,
    image_root: Path | None,
    mapping_path: Path | None,
    progress: LegacyMigrationProgressCallback,
) -> WordbankMigrationReport:
    if args.append_from_report and args.append_from_error_report:
        raise MigrationError(
            "cannot use --append-from-report and --append-from-error-report together"
        )
    if args.no_import_logs:
        logger.warning(
            "[wordbank-migration] append mode ignores legacy log import flags "
            "and only replays failed entry rows"
        )

    resolved_image_root = image_root or _default_append_image_root(args)
    resolved_mapping_path = (
        mapping_path if mapping_path is not None else _default_append_mapping_path(args)
    )
    payload_path = await asyncio.to_thread(
        Path(args.append_from_error_report or args.append_from_report or "").resolve
    )
    payload = json.loads(
        await asyncio.to_thread(payload_path.read_text, encoding="utf-8")
    )
    if not isinstance(payload, Mapping):
        raise MigrationError("append report payload must be a JSON object")

    if args.append_from_error_report:
        categories = args.error_categories or ["trigger_shape_empty"]
        details = extract_failure_details_from_categorized_report(
            payload,
            categories=categories,
        )
    else:
        raw_details = payload.get("failure_details")
        if not isinstance(raw_details, list):
            raise MigrationError("report missing failure_details")
        details = [dict(item) for item in raw_details if isinstance(item, Mapping)]

    rows = rebuild_legacy_rows_from_failure_details(details)
    image_catalog = _build_append_image_catalog(
        resolved_image_root,
        resolved_mapping_path,
    )
    logger.info(
        "[wordbank-migration] append replay "
        + json.dumps(
            {
                "report": str(payload_path),
                "selected_failures": len(details),
                "replay_rows": len(rows),
                "reset_target": False,
            },
            ensure_ascii=False,
        )
    )
    return await migrate_legacy_rows(
        rows,
        repository=repository,
        media_service=media_service,
        image_catalog=image_catalog,
        reset_target=False,
        progress=progress,
        progress_every=args.progress_step,
    )


def _default_append_image_root(args: argparse.Namespace) -> Path:
    old_repo_root = Path(args.old_repo).resolve()
    return old_repo_root.parent / "SakuraiSenrinPic" / "recovered_files"


def _default_append_mapping_path(args: argparse.Namespace) -> Path:
    old_repo_root = Path(args.old_repo).resolve()
    return old_repo_root.parent / "SakuraiSenrinPic" / "file_mapping.json"


def _build_append_image_catalog(
    image_root: Path,
    mapping_path: Path | None,
) -> LegacyImageCatalog:
    from src.plugins.wordbank.migration import build_legacy_image_catalog

    return build_legacy_image_catalog(image_root, mapping_path)


if __name__ == "__main__":
    asyncio.run(main())
