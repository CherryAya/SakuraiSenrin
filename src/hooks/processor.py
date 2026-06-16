"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-25 16:27:42
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-03 12:18:17
Description: 运行时同步检查 hook
"""

from pathlib import Path

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    Event,
)
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor

from src.config import config
from src.database.core.consts import Permission
from src.lib.consts import GLOBAL_GROUP_FLAG, TriggerType
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.lib.types import UNSET, is_set
from src.repositories import blacklist_repo, group_repo, member_repo, user_repo
from src.services.runtime_policy import get_group_block_reason
from src.services.sync import (
    sync_group_runtime,
    sync_member_runtime,
    sync_members_from_api,
    sync_user_runtime,
)

name = tr("zh-CN", "plugin.hook_processor.name")
description = tr("zh-CN", "plugin.hook_processor.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "processor" / "README.MD"


def _runtime_locale(group_id: str | None = None) -> LocaleCode:
    return "zh-CN"


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "impression_color": "#12B886",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "i18n": {
            "name_key": "plugin.hook_processor.name",
            "description_key": "plugin.hook_processor.description",
        },
        "docs": create_docs_meta(
            visible=False,
            category="internal",
            order=10,
            source=DOCS_SOURCE,
            slug="hook.processor",
            kind="internal",
        ),
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


async def _runtime_check(bot: Bot, event: Event, matcher: Matcher) -> None:
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

    _user_id = getattr(event, "user_id", UNSET)
    _group_id = getattr(event, "group_id", UNSET)

    is_group_event = is_set(_group_id)
    is_user_event = is_set(_user_id)
    user_id = group_id = ""

    if is_group_event:
        group_id = str(_group_id)
    if is_user_event:
        user_id = str(_user_id)

    if getattr(config, "DEBUG", False):
        allowed_users = getattr(config, "DEV_TEST_USERS", set())
        allowed_groups = getattr(config, "DEV_TEST_GROUPS", set())
        if is_user_event and user_id in allowed_users:
            return
        if is_group_event and group_id in allowed_groups:
            return
        raise IgnoredException(
            tr(_runtime_locale(group_id), "hook.processor.debug_only")
        )

    user = await user_repo.get_user(user_id) if is_user_event else None
    group = await group_repo.get_group(group_id) if is_group_event else None

    if (
        is_group_event
        and group
        and not await member_repo.get_member(str(bot.self_id), group_id)
    ):
        await sync_members_from_api(bot, group_id)
        user = await user_repo.get_user(user_id) if is_user_event else None

    plugin = matcher.plugin
    is_no_check = (
        plugin and plugin.metadata and plugin.metadata.extra.get("no_check", False)
    )
    if is_no_check:
        return

    locale = _runtime_locale(group_id or None)

    if is_user_event and user_id in config.IGNORED_USERS:
        raise IgnoredException(tr(locale, "hook.processor.user_ignored"))
    if is_user_event and user_id in config.SUPERUSERS:
        return
    if is_user_event and await blacklist_repo.is_banned(user_id, GLOBAL_GROUP_FLAG):
        raise IgnoredException(tr(locale, "hook.processor.user_global_banned"))
    if is_group_event and user and getattr(user, "is_self_ignore", False):
        raise IgnoredException(tr(locale, "hook.processor.user_self_ignored"))
    if is_group_event:
        if not group:
            raise IgnoredException(tr(locale, "hook.processor.group_cache_miss"))

        if reason := get_group_block_reason(group.status):
            raise IgnoredException(reason)

        if group.is_all_shut:
            raise IgnoredException(tr(locale, "hook.processor.group_all_shut"))

        if is_user_event and await blacklist_repo.is_banned(user_id, group_id):
            raise IgnoredException(tr(locale, "hook.processor.user_group_banned"))


@run_preprocessor
async def _runtime_action(bot: Bot, event: Event, matcher: Matcher) -> None:
    await _runtime_sync(bot, event)
    await _runtime_check(bot, event, matcher)
