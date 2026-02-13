from src.database.core.tables import CoreBase
from src.database.instances import core_db, log_db, snapshot_db
from src.database.log.tables import LogBase
from src.database.snapshot.tables import SnapshotBase


async def init_db() -> None:
    await core_db.init(CoreBase)
    await log_db.init(LogBase)
    await snapshot_db.init(SnapshotBase)
