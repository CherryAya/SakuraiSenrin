"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-25 16:27:42
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-21 02:38:11
Description: 运行时同步检查 hook
"""

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    Event,
)
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor
from nonebot.plugin import PluginMetadata

from src.config import config
from src.database.core.consts import GroupStatus
from src.repositories import group_repo, user_repo
from src.services.sync import sync_group_runtime, sync_member_runtime, sync_user_runtime

name = "检测服务"
description = """
黑白名单检测:
  检测用户是否在黑名单中
  检测群组是否在白名单中
  定时任务校验合法群组，对于不合法的进行退群处理
""".strip()

usage = """
被动触发
""".strip()


__plugin_meta__ = PluginMetadata(
    name=name,
    description=description,
    usage=usage,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "trigger": "Passive",
        "permission": "SUPERUSER",
    },
)


async def _runtime_sync(bot: Bot, event: Event) -> None:
    """运行时同步钩子函数。

    1. 用户信息同步，记录用户昵称变化。
    2. 群聊信息同步，记录群组名变化。
    3. 群成员信息同步，记录群名片、群权限变化。
    4. 黑名单信息同步，控制自动解禁。
    """

    user_id = str(getattr(event, "user_id", ""))
    group_id = str(getattr(event, "group_id", ""))
    user_name = str(getattr(getattr(event, "sender", object), "nickname", ""))
    group_card = str(getattr(getattr(event, "sender", object), "card", "")) or user_name
    role = str(getattr(getattr(event, "sender", object), "role", ""))
    await sync_user_runtime(user_id, user_name)
    await sync_group_runtime(bot, group_id)
    await sync_member_runtime(group_id, user_id, user_name, group_card, role)


async def _runtime_check(event: Event, matcher: Matcher) -> None:
    """运行时检查钩子函数，在所有事件触发前执行，集中处理缓存击穿。
    主要 check 的点:

    1. 插件是否开启了 no_check 标记
    2. 用户是否全局配置忽略
    3. 用户是否超级用户
    4. 用户是否被封禁
    5. 用户是否启用 self_ignore 且 event 是群组事件
    6. 群聊是否有授权
    7. 群聊是否启用全员禁言

    对于缓存未命中的情况:
    1. 用户未命中缓存，默认放行
    2. 群聊未命中缓存，默认阻止
    """
    if (
        (plugin := matcher.plugin)
        and (metadata := plugin.metadata)
        and metadata.extra.get("no_check", False)
    ):
        return

    user_id = str(getattr(event, "user_id", ""))
    group_id = str(getattr(event, "group_id", ""))

    if user_id in config.IGNORED_USERS:
        raise IgnoredException("用户已被全局配置忽略")
    if user_id in config.SUPERUSERS:
        return

    user = await user_repo.get_user(user_id)
    if not user:
        return
    if user.is_self_ignore and group_id:
        raise IgnoredException("用户已启用 self_ignore")

    group = await group_repo.get_group(group_id)
    if not group:
        raise IgnoredException("未命中缓存，默认阻止")
    if group.is_all_shut or group.status != GroupStatus.AUTHORIZED:
        raise IgnoredException("群聊被全员禁言或未授权")


@run_preprocessor
async def _runtime_action(bot: Bot, event: Event, matcher: Matcher) -> None:
    await _runtime_sync(bot, event)
    await _runtime_check(event, matcher)
