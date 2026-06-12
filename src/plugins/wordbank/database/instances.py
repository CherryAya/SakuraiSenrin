"""Wordbank database instances."""

from src.lib.db.connectors import ColdPolicy, ShardedDB, StaticDB

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

wordbank_message_route_db = StaticDB(
    namespace="wordbank_db",
    filename="wordbank_message_route.db",
)

wordbank_message_ref_db = ShardedDB(
    namespace="wordbank_db",
    prefix="wordbank_message_ref",
    fmt="%Y_%m",
    active_window_months=2,
    cold_policy=ColdPolicy.HYDRATE,
)
