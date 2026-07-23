"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-21 01:00:56
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-02 19:26:30
Description: 群聊通知处理
"""

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
from nonebot.exception import ActionFailed
from nonebot.rule import Rule, is_type

from src.database.core.consts import GroupStatus, Permission
from src.lib.admin_notifications import deliver_admin_notification_i18n
from src.lib.consts import GLOBAL_GROUP_FLAG, PERMANENT_BAN_FLAG, TriggerType
from src.lib.i18n.runtime import format_duration, resolve_locale, tr
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.repositories import blacklist_repo, group_repo, member_repo
from src.services.info import resolve_group_name
from src.services.sync import sync_members_from_api

name = tr("zh-CN", "plugin.notice_group.name")
description = tr("zh-CN", "plugin.notice_group.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "group" / "README.MD"


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
            visible=False,
            category="system",
            order=110,
            source=DOCS_SOURCE,
            slug="notice.group",
            parent_slug="notice",
            aliases=("群组事件处理", "notice.group"),
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


def _should_sync_on_group_decrease(sub_type: str) -> bool:
    return sub_type in {"leave", "kick"}


group_increase_notice = on_notice(
    priority=5,
    rule=is_type(GroupIncreaseNoticeEvent),
    block=False,
)


@group_increase_notice.handle()
async def _(
    bot: Bot,
    event: GroupIncreaseNoticeEvent,
) -> None:
    group_id = str(event.group_id)
    await sync_members_from_api(
        bot,
        group_id,
        trigger_source=f"notice_group_increase:{event.sub_type}",
    )


group_decrease_notice = on_notice(
    priority=5, rule=is_type(GroupDecreaseNoticeEvent), block=False
)


@group_decrease_notice.handle()
async def _(
    bot: Bot,
    event: GroupDecreaseNoticeEvent,
) -> None:
    if _should_sync_on_group_decrease(event.sub_type):
        await sync_members_from_api(
            bot,
            str(event.group_id),
            trigger_source=f"notice_group_decrease:{event.sub_type}",
        )

    if event.sub_type != "kick_me":
        return

    msg = await ban_user_and_cleanup_groups(
        AdminNoticeContext(
            bot,
            str(event.group_id),
            str(event.operator_id),
            tr("zh-CN", "notice.group.kick.reason"),
        )
    )
    group_name = await resolve_group_name(bot, str(event.group_id))

    await deliver_admin_notification_i18n(
        bot,
        locale=await resolve_locale(str(event.group_id)),
        key="notice.group.kick.details",
        source_kind="notice_group_kick",
        inter_target_delay_seconds=1,
        locale_group_id=str(event.group_id),
        group_id=str(event.group_id),
        group_name=group_name,
        operator_id=event.operator_id,
        extra=msg.strip(),
    )


group_ban_notice = on_notice(priority=5, rule=is_type(GroupBanNoticeEvent), block=False)


@group_ban_notice.handle()
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
            tr("zh-CN", "notice.group.ban.reason"),
        )
    )
    locale = await resolve_locale(group_id)
    ban_duration = format_duration(locale, event.duration)
    group_name = await resolve_group_name(bot, str(event.group_id))

    await deliver_admin_notification_i18n(
        bot,
        locale=locale,
        key="notice.group.ban.details",
        source_kind="notice_group_ban",
        inter_target_delay_seconds=1,
        locale_group_id=str(event.group_id),
        group_id=str(event.group_id),
        group_name=group_name,
        operator_id=event.operator_id,
        ban_duration=ban_duration,
        extra=msg.strip(),
    )


group_rename_notice = on_notice(priority=5, rule=is_rename_group(), block=False)


@group_rename_notice.handle()
async def _(event: NotifyEvent) -> None:
    if not event.model_extra:
        return
    group_id = str(event.group_id)
    new_name = event.model_extra["name_new"]
    await group_repo.update_name(group_id, new_name)
