"""Wordbank database instances."""

from src.lib.db.connectors import ShardedDB, StaticDB

from .patches import build_wordbank_patch_registry

wordbank_main_db = StaticDB(
    namespace="wordbank_db",
    filename="wordbank_main.db",
)

wordbank_log_db = ShardedDB(
    namespace="wordbank_db",
    prefix="wordbank_logs",
    fmt="%Y_%m",
    active_window_months=2,
)

wordbank_main_db.patch_registry = build_wordbank_patch_registry()
