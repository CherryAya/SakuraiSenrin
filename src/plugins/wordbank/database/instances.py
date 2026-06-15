"""Wordbank database instances."""

from src.lib.backup import register_backup_database
from src.lib.db.connectors import ColdPolicy, EventStore, StateStore

wordbank_main_db = StateStore(
    namespace="wordbank_db",
    filename="wordbank_main.db",
)
register_backup_database(wordbank_main_db)

wordbank_log_db = EventStore(
    namespace="wordbank_db",
    prefix="wordbank_logs",
    fmt="%Y_%m",
    active_window_months=2,
    cold_policy=ColdPolicy.HYDRATE,
)
register_backup_database(wordbank_log_db)

wordbank_message_route_db = StateStore(
    namespace="wordbank_db",
    filename="wordbank_message_route.db",
)
register_backup_database(wordbank_message_route_db)

wordbank_message_ref_db = EventStore(
    namespace="wordbank_db",
    prefix="wordbank_message_ref",
    fmt="%Y_%m",
    active_window_months=2,
    cold_policy=ColdPolicy.HYDRATE,
)
register_backup_database(wordbank_message_ref_db)
