"""Wordbank database instances."""

from src.lib.db.connectors import ShardedDB, StaticDB

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
