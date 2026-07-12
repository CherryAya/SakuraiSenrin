"""Backup database registration and discovery."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import sys

from src.lib.db.connectors import BaseDB
from src.logger import logger

_registered_databases: list[BaseDB] = []
_registered_database_ids: set[int] = set()
_attempted_modules: set[str] = set()


def register_backup_database(db: BaseDB) -> None:
    db_id = id(db)
    if db_id in _registered_database_ids:
        return
    _registered_databases.append(db)
    _registered_database_ids.add(db_id)


def get_registered_backup_databases() -> tuple[BaseDB, ...]:
    return tuple(_registered_databases)


def ensure_backup_database_registrations_loaded() -> None:
    for module_name, required in _iter_registration_modules():
        _import_registration_module(module_name, required=required)


def reset_backup_database_registry_for_test() -> None:
    _registered_databases.clear()
    _registered_database_ids.clear()
    for module_name, _required in _iter_registration_modules():
        sys.modules.pop(module_name, None)
    _attempted_modules.clear()


def _import_registration_module(module_name: str, *, required: bool) -> None:
    if module_name in _attempted_modules:
        return
    _attempted_modules.add(module_name)
    try:
        import_module(module_name)
    except Exception as exc:
        if required:
            raise
        logger.debug(
            f"[Backup] backup registration discovery skipped for {module_name}: {exc}"
        )
        return


def _iter_registration_modules() -> list[tuple[str, bool]]:
    project_root = Path(__file__).resolve().parents[3]
    modules: list[tuple[str, bool]] = [("src.database.instances", True)]
    plugins_root = project_root / "src" / "plugins"
    for instances_path in sorted(plugins_root.glob("*/database/instances.py")):
        try:
            relative_path = instances_path.relative_to(project_root)
        except ValueError:
            logger.debug(
                "[Backup] skip registration discovery outside project: "
                f"{instances_path}"
            )
            continue
        module_name = ".".join(relative_path.with_suffix("").parts)
        modules.append((module_name, False))
    return modules
