"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-21 01:00:56
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-02 19:26:30
Description: 群聊通知处理
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from nonebot import on_notice
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupBanNoticeEvent,
    GroupDecreaseNoticeEvent,
    GroupIncreaseNoticeEvent,
    NotifyEvent,
)
from nonebot.adapters.onebot.v11.message import Message
from nonebot.exception import ActionFailed
from nonebot.rule import Rule, is_type, to_me

from src.config import config
from src.database.core.consts import GroupStatus, Permission
from src.lib.consts import GLOBAL_GROUP_FLAG, PERMANENT_BAN_FLAG, TriggerType
from src.lib.i18n.runtime import (
    format_duration,
    resolve_locale,
    send_private_i18n,
    tr,
)
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_readme_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.repositories import blacklist_repo, group_repo, member_repo
from src.services.info import resolve_group_name
from src.services.sync import sync_members_from_api

name = tr("zh-CN", "plugin.notice_group.name")
description = tr("zh-CN", "plugin.notice_group.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "group" / "README.MD"


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    return build_readme_docs(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.PASSIVE,
        permission=Permission.SUPERUSER,
        ctx=ctx,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "no_check": True,
        "i18n": {
            "name_key": "plugin.notice_group.name",
            "description_key": "plugin.notice_group.description",
        },
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="system",
            order=110,
            source=DOCS_SOURCE,
        ),
    },
)


@dataclass
class AdminNoticeContext:
    bot: Bot
    group_id: str
    user_id: str
    reason: str


def is_rename_group() -> Rule:
    async def _(event: NotifyEvent) -> bool:
        return event.sub_type == "group_name"

    return Rule(_)


async def ban_user_and_cleanup_groups(ctx: AdminNoticeContext) -> str:
    msg = ""
    await group_repo.update_status(group_id=ctx.group_id, status=GroupStatus.BANNED)
    await blacklist_repo.add_ban(
        target_user_id=ctx.user_id,
        group_id=GLOBAL_GROUP_FLAG,
        operator_id=str(ctx.bot.self_id),
        duration=PERMANENT_BAN_FLAG,
        reason=ctx.reason,
    )
    for member in await member_repo.get_admin_member_by_uid(ctx.user_id):
        await group_repo.update_status(
            group_id=member.group_id,
            status=GroupStatus.BANNED,
        )
        if member.group_id == ctx.group_id:
            continue
        try:
            await ctx.bot.set_group_leave(group_id=int(member.group_id))
            msg += (
                tr(
                    "zh-CN",
                    "notice.group.leave_success",
                    group_id=member.group_id,
                    group_name=member.group.group_name,
                )
                + "\n"
            )
        except ActionFailed:
            msg += (
                tr(
                    "zh-CN",
                    "notice.group.leave_failed",
                    group_id=member.group_id,
                    group_name=member.group.group_name,
                )
                + "\n"
            )

    return msg


@on_notice(
    priority=5,
    rule=is_type(GroupIncreaseNoticeEvent) & to_me(),
    block=False,
).handle()
async def _(
    bot: Bot,
    event: GroupIncreaseNoticeEvent,
) -> None:
    group_id = str(event.group_id)
    await sync_members_from_api(bot, group_id)


@on_notice(
    priority=5, rule=is_type(GroupDecreaseNoticeEvent) & to_me(), block=False
).handle()
async def _(
    bot: Bot,
    event: GroupDecreaseNoticeEvent,
) -> None:
    if event.sub_type != "kick_me":
        return

    msg = await ban_user_and_cleanup_groups(
        AdminNoticeContext(
            bot,
            str(event.group_id),
            str(event.operator_id),
            "恶意踢出凛凛",
        )
    )
    group_name = await resolve_group_name(bot, str(event.group_id))

    for superuser in config.SUPERUSERS:
        await send_private_i18n(
            bot,
            int(superuser),
            "notice.group.kick.details",
            locale_group_id=str(event.group_id),
            group_id=str(event.group_id),
            group_name=group_name,
            operator_id=event.operator_id,
            extra=msg.strip(),
        )
        await asyncio.sleep(1)


@on_notice(priority=5, rule=is_type(GroupBanNoticeEvent), block=False).handle()
async def _(
    bot: Bot,
    event: GroupBanNoticeEvent,
) -> None:
    group_id = str(event.group_id)
    group = await group_repo.get_group(group_id)
    on_all_shut = event.sub_type == "ban" and event.user_id == 0
    off_all_shut = event.sub_type == "lift_ban" and event.user_id == 0

    if on_all_shut and group:
        group_repo.update_all_shut(group_id, True)
    if off_all_shut and group:
        group_repo.update_all_shut(group_id, False)
    if not event.is_tome():
        return

    await bot.set_group_leave(group_id=event.group_id)
    msg = await ban_user_and_cleanup_groups(
        AdminNoticeContext(
            bot,
            str(event.group_id),
            str(event.operator_id),
            "恶意禁言凛凛",
        )
    )
    locale = await resolve_locale(group_id)
    ban_duration = format_duration(locale, event.duration)
    group_name = await resolve_group_name(bot, str(event.group_id))

    for superuser in config.SUPERUSERS:
        await send_private_i18n(
            bot,
            int(superuser),
            "notice.group.ban.details",
            locale_group_id=str(event.group_id),
            group_id=str(event.group_id),
            group_name=group_name,
            operator_id=event.operator_id,
            ban_duration=ban_duration,
            extra=msg.strip(),
        )
        await asyncio.sleep(1)


@on_notice(priority=5, rule=is_rename_group(), block=False).handle()
async def _(event: NotifyEvent) -> None:
    if not event.model_extra:
        return
    group_id = str(event.group_id)
    new_name = event.model_extra["name_new"]
    await group_repo.update_name(group_id, new_name)
