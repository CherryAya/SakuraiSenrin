"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-22 16:55:55
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-22 17:00:11
Description: 平台相关信息获取
"""

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.log import logger

from src.repositories import group_repo, user_repo


async def resolve_user_name(bot: Bot, user_id: str) -> str:
    db_name = await user_repo.get_name_by_uid(user_id)
    if db_name:
        return db_name

    try:
        info = await bot.get_stranger_info(user_id=int(user_id))
        real_name = info.get("nickname", f"用户_{user_id}")

        await user_repo.save_user(user_id=user_id, user_name=real_name)
        return real_name
    except Exception as e:
        logger.warning(f"获取新用户 {user_id} 名字失败: {e}")
        return f"用户_{user_id[-4:]}"


async def resolve_group_name(bot: Bot, group_id: str) -> str:
    db_name = await group_repo.get_name_by_gid(group_id)
    if db_name:
        return db_name

    try:
        info = await bot.get_group_info(group_id=int(group_id))
        real_name = info.get("group_name", f"群聊_{group_id}")

        await group_repo.save_group(group_id=group_id, group_name=real_name)
        return real_name
    except Exception as e:
        logger.warning(f"获取新群聊 {group_id} 名字失败: {e}")
        return f"群聊_{group_id[-4:]}"
