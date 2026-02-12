from src.lib.consts import GLOBAL_SCOPE
from src.lib.db.batch import BatchWriter

from .core.ops import GroupOps, UserOps
from .core.types import (
    GroupPayload,
    GroupUpdateNamePayload,
    MemberPayload,
    UserPayload,
    UserUpdateNamePayload,
)
from .instances import COREDB, SNAPSHOTDB
from .snapshot.consts import SnapshotEventType
from .snapshot.ops import GroupSnapshotOps, UserSnapshotOps
from .snapshot.types import GroupSnapshotPayload, UserSnapshotPayload


async def _flush_create_user(batch_data: list[UserPayload]) -> None:
    unique_data = {item["user_id"]: item for item in batch_data}.values()
    final_data = list(unique_data)
    if not final_data:
        return

    async with COREDB.session() as session:
        await UserOps(session).bulk_upsert_users(final_data)

    async with SNAPSHOTDB.session() as session:
        snapshots: list[UserSnapshotPayload] = [
            {
                "user_id": d["user_id"],
                "group_id": GLOBAL_SCOPE,
                "event_type": SnapshotEventType.USERNAME,
                "content": d["user_name"],
            }
            for d in final_data
        ]
        await UserSnapshotOps(session).bulk_create_user_snapshots(snapshots)


async def _flush_update_user_name(batch_data: list[UserUpdateNamePayload]) -> None:
    unique_data = {item["user_id"]: item for item in batch_data}.values()
    final_data = list(unique_data)
    if not final_data:
        return

    async with COREDB.session() as session:
        await UserOps(session).bulk_update_names(final_data)

    async with SNAPSHOTDB.session() as session:
        snapshots: list[UserSnapshotPayload] = [
            {
                "user_id": d["user_id"],
                "group_id": GLOBAL_SCOPE,
                "event_type": SnapshotEventType.USERNAME,
                "content": d["user_name"],
            }
            for d in final_data
        ]
        await UserSnapshotOps(session).bulk_create_user_snapshots(snapshots)


async def _flush_create_group(batch_data: list[GroupPayload]) -> None:
    unique_data = {item["group_id"]: item for item in batch_data}.values()
    final_data = list(unique_data)
    if not final_data:
        return

    async with COREDB.session() as session:
        await GroupOps(session).bulk_upsert_groups(final_data)

    async with SNAPSHOTDB.session() as session:
        snapshots: list[GroupSnapshotPayload] = [
            {
                "group_id": d["group_id"],
                "event_type": SnapshotEventType.GROUPNAME,
                "content": d["group_name"],
            }
            for d in final_data
        ]
        await GroupSnapshotOps(session).bulk_create_group_snapshots(snapshots)


async def _flush_update_group_name(batch_data: list[GroupUpdateNamePayload]) -> None:
    unique_data = {item["group_id"]: item for item in batch_data}.values()
    final_data = list(unique_data)
    if not final_data:
        return

    async with COREDB.session() as session:
        await GroupOps(session).bulk_update_names(final_data)

    async with SNAPSHOTDB.session() as session:
        snapshots: list[GroupSnapshotPayload] = [
            {
                "group_id": d["group_id"],
                "event_type": SnapshotEventType.GROUPNAME,
                "content": d["group_name"],
            }
            for d in final_data
        ]
        await GroupSnapshotOps(session).bulk_create_group_snapshots(snapshots)


async def _flush_create_member(batch_data: list[MemberPayload]) -> None:
    unique_data = {
        (item["group_id"], item["user_id"]): item for item in batch_data
    }.values()
    final_data = list(unique_data)

    if not final_data:
        return
    # async with COREDB.session() as session:
    # 这里可能还需要顺便 Upsert User 和 Group 表，防止外键报错
    # ... logic to upsert users/groups ...
    # await MemberOps(session).bulk_upsert_members(final_data)

    # # 批量写入 Monitor (Insert 快照)
    # async with MONITOR.session() as session:
    #     # 转换为 Snapshot 模型需要的格式
    #     snapshots = [
    #         {
    #             "user_id": d["user_id"],
    #             "group_id": d["group_id"],
    #             "event_type": "card",
    #             "content": d["card"],
    #         }
    #         for d in final_data
    #     ]
    #     await UserSnapshotOps(session).bulk_create(snapshots)


user_create_writer = BatchWriter[UserPayload](
    flush_callback=_flush_create_user,
    batch_size=50,
    flush_interval=3.0,
)
user_update_name_writer = BatchWriter[UserUpdateNamePayload](
    flush_callback=_flush_update_user_name,
    batch_size=50,
    flush_interval=3.0,
)
group_create_writer = BatchWriter[GroupPayload](
    flush_callback=_flush_create_group,
    batch_size=50,
    flush_interval=3.0,
)
group_update_name_writer = BatchWriter[GroupUpdateNamePayload](
    flush_callback=_flush_update_group_name,
    batch_size=50,
    flush_interval=3.0,
)

member_create_writer = BatchWriter[MemberPayload](
    flush_callback=_flush_create_member,
    batch_size=50,
    flush_interval=3.0,
)
