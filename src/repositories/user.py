from dataclasses import dataclass

from src.database.consts import WritePolicy
from src.database.core.consts import Permission, UserStatus
from src.database.core.ops import UserOps
from src.database.core.types import UserPayload, UserUpdateNamePayload
from src.database.instances import core_db
from src.lib.cache.field import UserCacheItem
from src.lib.cache.impl import UserCache
from src.lib.types import UNSET, Unset, is_set, resolve_unset
from src.services.writers import user_create_writer, user_update_name_writer


@dataclass
class UserChangeContext:
    """
    封装用户变更请求的上下文，包含原始数据和衍生状态。
    提供 resolve_xxx 方法统一处理 Unset -> Default 的逻辑。
    """

    user_id: str
    user_name: str | Unset = UNSET
    status: UserStatus | Unset = UNSET
    permission: Permission | Unset = UNSET
    is_new: bool = False

    def resolve_name(self, default: str = "") -> str:
        return resolve_unset(self.user_name, default)

    def resolve_status(self, default: UserStatus = UserStatus.NORMAL) -> UserStatus:
        return resolve_unset(self.status, default)

    def resolve_perm(
        self,
        default: Permission = Permission.NORMAL,
    ) -> Permission:
        return resolve_unset(self.permission, default)


class UserRepository:
    def __init__(self, cache: UserCache) -> None:
        self.cache = cache

    async def _save_buffered(self, ctx: UserChangeContext) -> None:
        if ctx.is_new:
            payload: UserPayload = {
                "user_id": ctx.user_id,
                "user_name": ctx.resolve_name(),
                "status": ctx.resolve_status(),
                "permission": ctx.resolve_perm(),
            }
            await user_create_writer.add(payload)
            return
        if is_set(ctx.user_name):
            payload_update: UserUpdateNamePayload = {
                "user_id": ctx.user_id,
                "user_name": ctx.user_name,
            }
            await user_update_name_writer.add(payload_update)

    async def _save_immediate(self, ctx: UserChangeContext) -> None:
        async with core_db.session() as session:
            ops = UserOps(session)
            if ctx.is_new:
                payload: UserPayload = {
                    "user_id": ctx.user_id,
                    "user_name": ctx.resolve_name(),
                    "status": ctx.resolve_status(),
                    "permission": ctx.resolve_perm(),
                }
                await ops.upsert_user(payload)
                return
            if is_set(ctx.user_name):
                await ops.upsert_name(ctx.user_id, ctx.user_name)
            if is_set(ctx.status):
                await ops.upsert_status(ctx.user_id, ctx.status)
            if is_set(ctx.permission):
                await ops.upsert_permission(ctx.user_id, ctx.permission)

    async def warm_up(self) -> None:
        async with core_db.session() as session:
            users = await UserOps(session).get_all()
        self.cache.set_batch(
            {
                u.user_id: UserCacheItem(
                    user_id=str(u.user_id),
                    name_hash=hash(u.user_name),
                    status=u.status,
                    permission=u.permission,
                )
                for u in users
            },
        )

    async def get_user(self, user_id: str) -> UserCacheItem | None:
        if item := self.cache.get(user_id):
            return item

        async with core_db.session() as session:
            db_user = await UserOps(session).get_by_user_id(user_id)
            if not db_user:
                return None

            self.cache.upsert_user(
                user_id=str(db_user.user_id),
                user_name=db_user.user_name,
                status=db_user.status,
                permission=db_user.permission,
            )
            return self.cache.get(user_id)

    async def save_user(
        self,
        user_id: str,
        user_name: str | Unset = UNSET,
        status: UserStatus | Unset = UNSET,
        permission: Permission | Unset = UNSET,
        policy: WritePolicy = WritePolicy.BUFFERED,
    ) -> None:
        from src.config import config

        ctx = UserChangeContext(user_id, user_name, status, permission)
        old_item = self.cache.get(user_id)
        ctx.is_new = old_item is None
        self.cache.upsert_user(user_id, user_name, status, permission)

        if not ctx.is_new and old_item:
            if is_set(user_name) and old_item.name_hash == hash(user_name):
                ctx.user_name = UNSET
        if user_id in config.SUPERUSERS:
            ctx.permission = Permission.SUPERUSER

        if policy == WritePolicy.BUFFERED:
            await self._save_buffered(ctx)
        elif policy == WritePolicy.IMMEDIATE:
            await self._save_immediate(ctx)
