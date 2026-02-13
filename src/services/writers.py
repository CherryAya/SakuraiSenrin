from src.database.core.ops import GroupOps, MemberOps, UserOps
from src.database.core.types import (
    GroupPayload,
    GroupUpdateNamePayload,
    MemberPayload,
    MemberUpdateCardPayload,
    MemberUpdatePermPayload,
    UserPayload,
    UserUpdateNamePayload,
)
from src.database.instances import core_db, snapshot_db
from src.database.snapshot.ops import (
    GroupSnapshotOps,
    MemberSnapshotOps,
    UserSnapshotOps,
)
from src.database.snapshot.types import (
    GroupSnapshotPayload,
    MemberSnapshotPayload,
    UserSnapshotPayload,
)
from src.lib.db.batch import BatchWriter


async def _flush_create_user(batch_data: list[UserPayload]) -> None:
    unique_data = {item["user_id"]: item for item in batch_data}.values()
    final_data = list(unique_data)
    if not final_data:
        return

    async with core_db.session() as session:
        await UserOps(session).bulk_upsert_users(final_data)

    async with snapshot_db.session() as session:
        snapshots: list[UserSnapshotPayload] = [
            {
                "user_id": d["user_id"],
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

    async with core_db.session() as session:
        await UserOps(session).bulk_upsert_names(final_data)

    async with snapshot_db.session() as session:
        snapshots: list[UserSnapshotPayload] = [
            {
                "user_id": d["user_id"],
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

    async with core_db.session() as session:
        await GroupOps(session).bulk_upsert_groups(final_data)

    async with snapshot_db.session() as session:
        snapshots: list[GroupSnapshotPayload] = [
            {
                "group_id": d["group_id"],
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

    async with core_db.session() as session:
        await GroupOps(session).bulk_upsert_names(final_data)

    async with snapshot_db.session() as session:
        snapshots: list[GroupSnapshotPayload] = [
            {
                "group_id": d["group_id"],
                "content": d["group_name"],
            }
            for d in final_data
        ]
        await GroupSnapshotOps(session).bulk_create_group_snapshots(snapshots)


async def _flush_create_member(batch_data: list[MemberPayload]) -> None:
    unique_data = {
        (item["group_id"], item["user_id"]): item for item in batch_data
    }.values()
    members_data: list[MemberPayload] = list(unique_data)
    if not members_data:
        return

    users_data: list[UserPayload] = [
        {
            "user_id": item["user_id"],
            "user_name": "",
        }
        for item in members_data
    ]
    groups_data: list[GroupPayload] = [
        {
            "group_id": item["group_id"],
            "group_name": "",
        }
        for item in members_data
    ]

    async with core_db.session() as session:
        await UserOps(session).bulk_insert_ignore(users_data)
        await GroupOps(session).bulk_insert_ignore(groups_data)
        await MemberOps(session).bulk_upsert_members(members_data)

    async with snapshot_db.session() as session:
        snapshots: list[MemberSnapshotPayload] = [
            {
                "user_id": d["user_id"],
                "group_id": d["group_id"],
                "content": d["group_card"],
            }
            for d in members_data
        ]
        await MemberSnapshotOps(session).bulk_create_member_snapshots(snapshots)


async def _flush_update_member_card(batch_data: list[MemberUpdateCardPayload]) -> None:
    pass


async def _flush_update_member_permission(
    batch_data: list[MemberUpdatePermPayload],
) -> None:
    pass


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
member_update_card_writer = BatchWriter[MemberUpdateCardPayload](
    flush_callback=_flush_update_member_card,
    batch_size=50,
    flush_interval=3.0,
)
member_update_perm_writer = BatchWriter[MemberUpdatePermPayload](
    flush_callback=_flush_update_member_permission,
    batch_size=50,
    flush_interval=3.0,
)
