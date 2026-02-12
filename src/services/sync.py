import asyncio
from collections import defaultdict

from nonebot.adapters.onebot.v11.bot import Bot

from src.database.core.consts import GroupStatus, Permission, UserStatus
from src.database.core.ops import BlacklistOps, GroupOps, MemberOps, UserOps
from src.database.core.types import (
    GroupPayload,
    GroupUpdateNamePayload,
    UserPayload,
    UserUpdateNamePayload,
)
from src.database.instances import COREDB
from src.database.writers import (
    group_create_writer,
    group_update_name_writer,
    user_create_writer,
    user_update_name_writer,
)
from src.lib.cache import (
    BLACKLIST_CACHE,
    GROUP_CACHE,
    MEMBER_CACHE,
    USER_CACHE,
    BlacklistCacheItem,
    GroupCacheItem,
    MemberCacheItem,
    UserCacheItem,
)

_locks = defaultdict(asyncio.Lock)


async def load_users_from_db() -> None:
    """初始化用户缓存

    从数据库中加载用户信息到缓存，默认 `status` 为 `NORMAL`。
    """
    async with COREDB.session(commit=False) as session:
        users = await UserOps(session).get_all()

    USER_CACHE.set_batch(
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


async def load_groups_from_db() -> None:
    """初始化群组缓存

    从数据库中加载群组信息到缓存，默认 `is_all_shut` 为 False。
    """
    async with COREDB.session() as session:
        db_groups = await GroupOps(session).get_all()

    GROUP_CACHE.set_batch(
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


async def load_members_from_db() -> None:
    """初始化群成员缓存

    从数据库中加载群成员信息到缓存，默认 `permission` 为 `NORMAL`。
    """
    async with COREDB.session(commit=False) as session:
        members = await MemberOps(session).get_all()

    MEMBER_CACHE.set_batch(
        {
            MEMBER_CACHE._gen_key(member.user_id, member.group_id): MemberCacheItem(
                card_hash=hash(member.group_card),
                permission=member.permission,
            )
            for member in members
        },
    )


async def load_blacklist_from_db() -> None:
    """初始化黑名单缓存

    从数据库中加载黑名单信息到缓存。
    """
    async with COREDB.session(commit=False) as session:
        blacklists = await BlacklistOps(session).get_all()

    BLACKLIST_CACHE.set_batch(
        {
            BLACKLIST_CACHE._gen_key(
                item.target_user_id,
                item.group_id,
            ): BlacklistCacheItem(
                expiry=item.ban_expiry,
                reason=item.reason,
            )
            for item in blacklists
        },
    )


def _process_user_sync(
    user_id: str,
    user_name: str,
) -> tuple[UserPayload | None, UserUpdateNamePayload | None]:
    from src.config import config

    """
    核心用户同步逻辑。

    Returns:
        (insert_payload, update_payload)
        返回元组，如果有值则代表需要写入数据库，如果为 None 则无需操作。
    """
    insert_payload: UserPayload | None = None
    update_payload: UserUpdateNamePayload | None = None
    user = USER_CACHE.get(user_id)

    if not user:
        status = UserStatus.NORMAL
        permission = (
            Permission.SUPERUSER if user_id in config.SUPERUSERS else Permission.NORMAL
        )
        USER_CACHE.set_user(
            user_id=user_id,
            user_name=user_name,
            status=status,
            permission=permission,
        )
        insert_payload = {
            "user_id": user_id,
            "user_name": user_name,
            "permission": permission,
            "status": status,
        }
    elif user.name_hash != hash(user_name):
        USER_CACHE.set_user(
            user_id=user_id,
            user_name=user_name,
            status=user.status,
            permission=user.permission,
        )
        update_payload = {"user_id": user_id, "user_name": user_name}

    return insert_payload, update_payload


async def sync_users_from_api(bot: Bot) -> None:
    """调用 API 同步用户信息

    策略：
    - 内存缓存 (Cache): 新增、改名、状态变动、权限变动。
    - 数据库 (DB): 新增、改名。

    API Response Reference:
        Wait for `get_friend_list()`:
        ```
        {
            "birthday_year": 2002,
            "birthday_month": 4,
            "birthday_day": 10,
            "user_id": 1479559098,
            "age": 23,
            "phone_num": "-",
            "email": "sakuraicora@gmail.com",
            "category_id": 0,
            "nickname": "SakuraiCora",
            "remark": "",
            "sex": "female",
            "level": 64,
        }
        ```
    """
    try:
        api_list = await bot.get_friend_list()
    except Exception:
        return

    inserts = []
    updates = []

    for info in api_list:
        user_id = str(info["user_id"])
        nickname = info["nickname"]
        new_insert, new_update = _process_user_sync(user_id, nickname)
        if new_insert:
            inserts.append(new_insert)
        if new_update:
            updates.append(new_update)

    await user_create_writer.add_all(inserts)
    await user_update_name_writer.add_all(updates)


async def sync_groups_from_api(bot: Bot) -> None:
    """调用 API 同步群组信息

    策略：
    - 内存缓存 (Cache): 新增、改名、状态变动、全员禁言开关变动。
    - 数据库 (DB): 新增、改名。

    API Response Reference:
        Wait for `get_group_list()`:
        ```json
        {
            "group_id": 1107576103,
            "group_name": "❄️凛雪列車｜描摹重日冷影❄️",
            "group_all_shut": -1,  // -1: 开启全员禁言, 0: 关闭
            "member_count": 449,
            "max_member_count": 500
            ...
        }
        ```
    """
    try:
        api_list = await bot.get_group_list()
    except Exception:
        return
    inserts: list[GroupPayload] = []
    updates: list[GroupUpdateNamePayload] = []
    for info in api_list:
        group_id = str(info["group_id"])
        group_name = info["group_name"]
        is_shut = info.get("group_all_shut") == -1
        if group_id not in GROUP_CACHE:
            inserts.append({"group_id": group_id, "group_name": group_name})
        elif GROUP_CACHE.needs_update_name(group_id, group_name):
            updates.append(
                {"group_id": group_id, "group_name": group_name},
            )
        GROUP_CACHE.set_smart(group_id, group_name, is_shut)
    await group_create_writer.add_all(inserts)
    await group_update_name_writer.add_all(updates)


async def sync_group_runtime(bot: Bot, group_id: str) -> None:
    if GROUP_CACHE.get(group_id):
        return
    async with _locks[group_id]:
        if GROUP_CACHE.get(group_id):
            return

        try:
            info = await bot.get_group_info(group_id=int(group_id))
        except Exception:
            return

        group_id = str(info["group_id"])
        GROUP_CACHE.set_smart(
            group_id=group_id,
            group_name=info["group_name"],
            is_all_shut=(info.get("group_all_shut") == -1),
        )
        async with COREDB.session() as session:
            await GroupOps(session).upsert_group_status(
                group_id=group_id,
                status=GroupStatus.UNAUTHORIZED,
            )


async def sync_user_runtime(user_id: str, user_name: str) -> None:
    """运行时同步：单个事件 -> 处理 -> 立即入队"""
    if not user_id or not user_name:
        return
    new_insert, new_update = _process_user_sync(user_id, user_name)

    if new_insert:
        await user_create_writer.add(new_insert)
    if new_update:
        await user_update_name_writer.add(new_update)
