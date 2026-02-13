import asyncio
from collections import defaultdict

from nonebot.adapters.onebot.v11.bot import Bot

from src.database.core.consts import Permission
from src.lib.cache import group_cache
from src.lib.types import UNSET
from src.repositories import group_repo, member_repo, user_repo

_locks = defaultdict(asyncio.Lock)
_ROLE_MAPPING = {
    "owner": Permission.GROUP_OWNER,
    "admin": Permission.GROUP_ADMIN,
    "member": Permission.NORMAL,
}


async def sync_users_from_api(bot: Bot) -> None:
    """调用 API 全量同步好友用户信息

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

    for info in api_list:
        await user_repo.save_user(info["user_id"], info["nickname"])


async def sync_groups_from_api(bot: Bot) -> None:
    """调用 API 全量同步群组信息

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

    for info in api_list:
        await group_repo.save_group(info["group_id"], info["group_name"])


async def sync_members_from_api(bot: Bot, group_id: str) -> None:
    """调用 API 同步群成员信息

    API Response Reference:
        Wait for `get_group_member_list()`:
        ```json
        [
            {
                "group_id": 123456789,
                "user_id": 1479559098,
                "nickname": "SakuraiCora",
                "card": "sticker_start_tag_for",
                "sex": "unknown",
                "age": 0,
                "area": "",
                "level": "100",
                "qq_level": 0,
                "join_time": 1533625923,
                "last_sent_time": 1770951669,
                "title_expire_time": 0,
                "unfriendly": False,
                "card_changeable": True,
                "is_robot": False,
                "shut_up_timestamp": 0,
                "role": "owner", // "owner", "admin", "member"
                "title": "xxx",
            },
            ...
        ]
    """
    try:
        api_list = await bot.get_group_member_list(group_id=int(group_id))
    except Exception:
        return

    for info in api_list:
        user_id = str(info["user_id"])
        if user_id == "0":
            continue

        nickname = info["nickname"]
        card = info["card"] or nickname
        role = info["role"]
        permission = _ROLE_MAPPING.get(role, Permission.NORMAL)

        await user_repo.save_user(user_id, nickname)
        await group_repo.save_group(group_id)
        await member_repo.save_member(user_id, group_id, card, permission)


async def sync_user_runtime(user_id: str, user_name: str) -> None:
    """运行时用户同步，可差量同步。

    单个事件 -> 处理 -> 立即入队"""
    if not user_id or not user_name:
        return
    await user_repo.save_user(user_id, user_name)


async def sync_group_runtime(bot: Bot, group_id: str) -> None:
    """运行时群聊同步，由于没有返回群名，仅能做增量同步。

    对于群名同步，参考 `sync_groups_from_api()`。
    """
    if group_cache.get(group_id):
        return
    async with _locks[group_id]:
        if group_cache.get(group_id):
            return

        try:
            info = await bot.get_group_info(group_id=int(group_id))
        except Exception:
            return

        group_id = str(info["group_id"])
        await group_repo.save_group(
            group_id,
            info["group_name"],
            UNSET,
            info.get("group_all_shut") == -1,
        )


async def sync_member_runtime(
    group_id: str,
    user_id: str,
    user_name: str,
    group_card: str,
    role: str,
) -> None:
    """运行时群成员同步，可差量同步。

    单个事件 -> 处理 -> 立即入队"""
    if not user_id:
        return
    permission = _ROLE_MAPPING.get(role, Permission.NORMAL)

    await user_repo.save_user(user_id, user_name)
    await group_repo.save_group(group_id)
    await member_repo.save_member(user_id, group_id, group_card, permission)
