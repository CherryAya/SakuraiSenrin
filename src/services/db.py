"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 02:45:56
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-10-07 12:40:00
Description: db service
"""

from src.database.core.tables import CoreBase
from src.database.instances import core_db, log_db, snapshot_db
from src.database.log.tables import LogBase
from src.database.snapshot.tables import SnapshotBase
from src.lib.db.atomic import bind_attached_schemas
from src.lib.db.manager import db_manager
from src.lib.message_assets import MessageAssetBase, message_asset_db
from src.lib.trace_log import mark_trace_logging_ready

_schema_bound = False


def bind_cross_store_schemas() -> None:
    """给 log / snapshot 的表打 attached schema 标记（进程内一次即可）。"""
    global _schema_bound
    if _schema_bound:
        return
    bind_attached_schemas(CoreBase, LogBase, SnapshotBase)
    _schema_bound = True


def register_cross_store_journal_modes() -> None:
    """把跨库事务参与者登记为 rollback journal（DELETE）库。

    必须在任何连接建立【之前】执行：journal_mode 是文件属性，存在其它连接时
    不允许切换。生产此前缺失这一步，导致 init_db 用 WAL 常驻打开了
    core/log/snapshot，之后原子路径再切 DELETE 每次都得到
    `database is locked`——所有跨库原子写（封禁/改名/审批）静默失败。

    登记按当前 GLOBAL_DB_ROOT 解析且幂等：测试会切换根目录后重新 init_db，
    一次性开关会让新根目录拿不到登记。
    """
    db_manager.register_rollback_journal_dir(core_db.base_dir)
    db_manager.register_rollback_journal_dir(log_db.base_dir)
    db_manager.register_rollback_journal_dir(snapshot_db.base_dir)


async def init_db() -> None:
    # 必须在各库 init 之前：① 跨库原子会话依赖 attached schema 前缀寻址；
    # ② 跨库事务参与者必须从第一次打开起就是 rollback journal。
    bind_cross_store_schemas()
    register_cross_store_journal_modes()
    await core_db.init(CoreBase)
    await log_db.init(LogBase)
    await snapshot_db.init(SnapshotBase)
    await message_asset_db.init(MessageAssetBase)
    mark_trace_logging_ready()
