from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    Event,
)
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor
from nonebot.plugin import PluginMetadata

from src.config import config
from src.database.core.consts import GroupStatus, UserStatus
from src.lib.cache import GROUP_CACHE, USER_CACHE
from src.services.sync import sync_group_runtime, sync_user_runtime

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
    """运行时同步钩子函数，主要控制自动解禁，新增用户群组等，防止出现缓存未命中的情况。
    同时也具有同步数据库的功能，用于视奸用户 id 变化

    1. 群聊信息同步，由于无法直接拿到群名，直接拿一次所有群信息。60s 冷却时间
    2. 用户信息同步

    对于缓存未命中的情况，都是创建对应的对象，立即同步数据库与 cache。
    其中 member 和 user 涉及的量可能较大，采用 BatchWriter 批量写入数据库。

    """

    user_id = str(getattr(event, "user_id", ""))
    group_id = str(getattr(event, "group_id", ""))
    user_name = str(getattr(getattr(event, "sender", object), "nickname", ""))
    await sync_user_runtime(user_id, user_name)
    await sync_group_runtime(bot, group_id)


async def _runtime_check(bot: Bot, event: Event, matcher: Matcher) -> None:
    """运行时检查钩子函数，在所有事件触发前执行。
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

    if not (user := USER_CACHE.get(user_id)):
        return
    if user.status == UserStatus.BANNED:
        raise IgnoredException("用户被封禁")
    if user.is_self_ignore and group_id:
        raise IgnoredException("用户已启用 self_ignore")

    if not (group := GROUP_CACHE.get(group_id)):
        raise IgnoredException("未命中缓存，默认阻止")
    if group.is_all_shut or group.status != GroupStatus.NORMAL:
        raise IgnoredException("群聊被全员禁言或未授权")


@run_preprocessor
async def _runtime_action(bot: Bot, event: Event, matcher: Matcher) -> None:
    await _runtime_sync(bot, event)
    await _runtime_check(bot, event, matcher)
