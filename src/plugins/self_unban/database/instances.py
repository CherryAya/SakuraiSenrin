from src.lib.backup import register_backup_database
from src.lib.db.connectors import StateStore

self_unban_db = StateStore(
    namespace="self_unban_db",
    filename="self_unban.db",
)
register_backup_database(self_unban_db)

__all__ = ["self_unban_db"]
