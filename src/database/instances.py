from src.lib.db.connectors import ShardedDB, StaticDB

core_db = StaticDB(filename="core.db")
log_db = ShardedDB(prefix="log", fmt="%Y%m")
snapshot_db = ShardedDB(prefix="snapshot", fmt="%Y%m")
