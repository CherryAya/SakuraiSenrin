"""Wordbank database instances."""

from src.lib.db.connectors import ColdPolicy, EventStore, StateStore

from .patches import build_wordbank_log_patch_registry

wordbank_main_db = StateStore(
    namespace="wordbank_db",
    filename="wordbank_main.db",
)

wordbank_log_db = EventStore(
    namespace="wordbank_db",
    prefix="wordbank_logs",
    fmt="%Y_%m",
    active_window_months=2,
    cold_policy=ColdPolicy.HYDRATE,
)
wordbank_log_db.patch_registry = build_wordbank_log_patch_registry()

wordbank_message_route_db = StateStore(
    namespace="wordbank_db",
    filename="wordbank_message_route.db",
)

wordbank_message_ref_db = EventStore(
    namespace="wordbank_db",
    prefix="wordbank_message_ref",
    fmt="%Y_%m",
    active_window_months=2,
    cold_policy=ColdPolicy.HYDRATE,
)
