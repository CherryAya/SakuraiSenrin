"""Backup event hooks."""

from .events import (
    BackupCallback,
    BackupEvent,
    BackupFailed,
    BackupSkipped,
    BackupStarted,
    BackupSucceeded,
    dispatch_backup_event,
    register_backup_callback,
)
from .registry import (
    ensure_backup_database_registrations_loaded,
    get_registered_backup_databases,
    register_backup_database,
)

__all__ = [
    "BackupCallback",
    "BackupEvent",
    "BackupFailed",
    "BackupSkipped",
    "BackupStarted",
    "BackupSucceeded",
    "dispatch_backup_event",
    "ensure_backup_database_registrations_loaded",
    "get_registered_backup_databases",
    "register_backup_callback",
    "register_backup_database",
]
