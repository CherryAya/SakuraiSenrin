"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-26 01:28:36
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-03 14:25:30
Description: 同步逻辑
"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from time import monotonic

from nonebot.adapters.onebot.v11.bot import Bot

from src.database.core.consts import Permission
from src.lib.types import UNSET
from src.lib.utils.common import get_current_time
from src.logger import logger
from src.repositories import group_repo, member_repo, user_repo
from src.services.info import resolve_group_name

_group_runtime_locks = defaultdict(asyncio.Lock)
_member_full_sync_locks = defaultdict(asyncio.Lock)
_ROLE_MAPPING = {
    "owner": Permission.GROUP_OWNER,
    "admin": Permission.GROUP_ADMIN,
    "member": Permission.NORMAL,
}


@dataclass(slots=True, frozen=True)
class MemberSyncReport:
    group_id: str
    group_name: str
    trigger_source: str
    started_at: int
    finished_at: int
    elapsed_ms: int
    member_total: int
    synced_members: int
    ok: bool
    error_type: str = ""
    error_reason: str = ""


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


def _fallback_group_name(group_id: str) -> str:
    if not group_id:
        return "群聊"
    return f"群聊_{group_id[-4:]}"


async def _resolve_sync_group_name(bot: Bot, group_id: str) -> str:
    try:
        return await resolve_group_name(bot, group_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(
            f"[Sync] failed to resolve group name group_id={group_id}: {exc}"
        )
        return _fallback_group_name(group_id)


async def sync_members_from_api(
    bot: Bot,
    group_id: str,
    *,
    trigger_source: str = "manual",
) -> MemberSyncReport:
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
    async with _member_full_sync_locks[group_id]:
        started_at = get_current_time()
        started_monotonic = monotonic()
        group_name = await _resolve_sync_group_name(bot, group_id)
        try:
            api_list = await bot.get_group_member_list(group_id=int(group_id))
        except Exception as exc:
            finished_at = get_current_time()
            elapsed_ms = int((monotonic() - started_monotonic) * 1000)
            logger.warning(
                "[Sync] sync members from api failed "
                f"group_id={group_id} group_name={group_name} "
                f"trigger={trigger_source} error={type(exc).__name__}: {exc}"
            )
            return MemberSyncReport(
                group_id=group_id,
                group_name=group_name,
                trigger_source=trigger_source,
                started_at=started_at,
                finished_at=finished_at,
                elapsed_ms=elapsed_ms,
                member_total=0,
                synced_members=0,
                ok=False,
                error_type=type(exc).__name__,
                error_reason=str(exc),
            )

        synced_members = 0
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
            synced_members += 1

        finished_at = get_current_time()
        elapsed_ms = int((monotonic() - started_monotonic) * 1000)
        logger.debug(
            "[Sync] sync members from api finished "
            f"group_id={group_id} group_name={group_name} "
            f"trigger={trigger_source} members={synced_members} elapsed_ms={elapsed_ms}"
        )
        return MemberSyncReport(
            group_id=group_id,
            group_name=group_name,
            trigger_source=trigger_source,
            started_at=started_at,
            finished_at=finished_at,
            elapsed_ms=elapsed_ms,
            member_total=synced_members,
            synced_members=synced_members,
            ok=True,
        )


async def sync_user_runtime(user_id: str, user_name: str) -> None:
    """运行时用户同步，可差量同步。
    单个事件 -> 处理 -> 立即入队"""
    from src.config import config

    if not user_id or not user_name:
        return
    if user_id in config.SUPERUSERS:
        perm = Permission.SUPERUSER
    else:
        perm = Permission.NORMAL
    await user_repo.save_user(user_id, user_name, permission=perm)


async def sync_group_runtime(bot: Bot, group_id: str) -> None:
    """运行时群聊同步，由于没有返回群名，仅能做增量同步。

    对于群名同步，参考 `sync_groups_from_api()`。
    """
    if not group_id or await group_repo.get_group(group_id):
        return
    async with _group_runtime_locks[group_id]:
        if await group_repo.get_group(group_id):
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
    if not user_id or not user_name:
        return
    permission = _ROLE_MAPPING.get(role, Permission.NORMAL)

    await user_repo.save_user(user_id, user_name)
    await group_repo.save_group(group_id)
    await member_repo.save_member(user_id, group_id, group_card, permission)
