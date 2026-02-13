from dataclasses import dataclass

from src.database.consts import WritePolicy
from src.database.core.consts import GroupStatus
from src.database.core.ops import GroupOps
from src.database.core.types import GroupPayload, GroupUpdateNamePayload
from src.database.instances import core_db
from src.lib.cache.field import GroupCacheItem
from src.lib.cache.impl import GroupCache
from src.lib.types import UNSET, Unset, is_set, resolve_unset
from src.services.writers import group_create_writer, group_update_name_writer


@dataclass
class GroupChangeContext:
    """
    封装群组变更请求的上下文。
    注意：is_all_shut 通常是运行时状态，但如果有持久化需求也可以包含在内。
    """

    group_id: str
    group_name: str | Unset = UNSET
    status: GroupStatus | Unset = UNSET
    is_all_shut: bool | Unset = UNSET
    is_new: bool = False

    def resolve_name(self, default: str = "") -> str:
        return resolve_unset(self.group_name, default)

    def resolve_status(
        self,
        default: GroupStatus = GroupStatus.UNAUTHORIZED,
    ) -> GroupStatus:
        return resolve_unset(self.status, default)

    def resolve_shut(self, default: bool = False) -> bool:
        return resolve_unset(self.is_all_shut, default)


class GroupRepository:
    def __init__(self, cache: GroupCache) -> None:
        self.cache = cache

    async def _save_buffered(self, ctx: GroupChangeContext) -> None:
        if ctx.is_new:
            payload: GroupPayload = {
                "group_id": ctx.group_id,
                "group_name": ctx.resolve_name(),
                "status": ctx.resolve_status(),
            }
            await group_create_writer.add(payload)
            return

        if is_set(ctx.group_name):
            payload_update: GroupUpdateNamePayload = {
                "group_id": ctx.group_id,
                "group_name": ctx.group_name,
            }
            await group_update_name_writer.add(payload_update)

    async def _save_immediate(self, ctx: GroupChangeContext) -> None:
        async with core_db.session() as session:
            ops = GroupOps(session)
            if ctx.is_new:
                payload: GroupPayload = {
                    "group_id": ctx.group_id,
                    "group_name": ctx.resolve_name(),
                    "status": ctx.resolve_status(),
                }
                await ops.upsert_group(payload)
                return

            if is_set(ctx.group_name):
                await ops.upsert_name(ctx.group_id, ctx.group_name)

            if is_set(ctx.status):
                await ops.upsert_status(ctx.group_id, ctx.status)

    async def warm_up(self) -> None:
        async with core_db.session() as session:
            db_groups = await GroupOps(session).get_all()

        self.cache.set_batch(
            {
                g.group_id: GroupCacheItem(
                    group_id=str(g.group_id),
                    name_hash=hash(g.group_name),
                    status=g.status,
                    is_all_shut=False,
                )
                for g in db_groups
            },
        )

    async def get_group(self, group_id: str) -> GroupCacheItem | None:
        if item := self.cache.get(group_id):
            return item

        async with core_db.session() as session:
            db_group = await GroupOps(session).get_by_group_id(group_id)
            if not db_group:
                return None

            self.cache.upsert_group(
                group_id=str(db_group.group_id),
                group_name=db_group.group_name,
                status=db_group.status,
                is_all_shut=False,
            )
            return self.cache.get(group_id)

    async def save_group(
        self,
        group_id: str,
        group_name: str | Unset = UNSET,
        status: GroupStatus | Unset = UNSET,
        is_all_shut: bool | Unset = UNSET,
        policy: WritePolicy = WritePolicy.BUFFERED,
    ) -> None:
        """
        Write-Behind:
        1. 更新缓存 (Source of Truth)
        2. 计算 Context (Diff)
        3. 策略分流 (Buffered / Immediate)
        """
        ctx = GroupChangeContext(group_id, group_name, status, is_all_shut)
        old_item = self.cache.get(group_id)
        ctx.is_new = old_item is None

        self.cache.upsert_group(group_id, group_name, status, is_all_shut)
        if not ctx.is_new and old_item:
            if is_set(group_name) and old_item.name_hash == hash(group_name):
                ctx.group_name = UNSET
            if is_set(status) and old_item.status == status:
                ctx.status = UNSET
            if is_set(is_all_shut) and old_item.is_all_shut == is_all_shut:
                ctx.is_all_shut = UNSET

        if policy == WritePolicy.BUFFERED:
            await self._save_buffered(ctx)
        elif policy == WritePolicy.IMMEDIATE:
            await self._save_immediate(ctx)
