from src.database.core.tables import CoreBase
from src.database.instances import COREDB, LOGDB, SNAPSHOTDB
from src.database.log.tables import LogBase
from src.database.snapshot.tables import SnapshotBase


async def init_db() -> None:
    await COREDB.init(CoreBase)
    await LOGDB.init(LogBase)
    await SNAPSHOTDB.init(SnapshotBase)
