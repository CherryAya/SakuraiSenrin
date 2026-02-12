from src.lib.db.connectors import ShardedDB, StaticDB

COREDB = StaticDB(filename="core.db")
LOGDB = ShardedDB(prefix="log", fmt="%Y%m")
SNAPSHOTDB = ShardedDB(prefix="snapshot", fmt="%Y%m")
