"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-21 01:00:56
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-22 18:40:04
Description: 群聊通知处理
"""

import asyncio
from dataclasses import dataclass

import arrow
from nonebot import on_notice
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupBanNoticeEvent,
    GroupDecreaseNoticeEvent,
    GroupIncreaseNoticeEvent,
    NotifyEvent,
)
from nonebot.exception import ActionFailed
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule, is_type, to_me

from src.config import config
from src.database.core.consts import GroupStatus, Permission
from src.lib.consts import GLOBAL_GROUP_SCOPE, TriggerType
from src.lib.utils import AlertTemplate
from src.repositories import blacklist_repo, group_repo, member_repo
from src.services.info import resolve_group_name
from src.services.sync import sync_members_from_api

name = "群组事件处理"
description = """
群组事件处理:
  被禁言自动退群拉黑
  更新群组状态
  进群同步群组成员

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
        "version": "0.1.0",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "no_check": True,
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
        group_id=GLOBAL_GROUP_SCOPE,
        operator_id=str(ctx.bot.self_id),
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
            msg += f"连坐退群：{member.group_id} {member.group.group_name}\n"
        except ActionFailed:
            msg += f"连坐退群失败：{member.group_id} {member.group.group_name}\n"

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
        await bot.send_private_msg(
            user_id=int(superuser),
            message=AlertTemplate.build_tip_notification(
                event_name="群组被踢出",
                event_details=(
                    "不好，被扔出来了，已自动拉黑\n"
                    f"群组：{event.group_id}\n"
                    f"群名：{group_name}\n"
                    f"操作者：{event.operator_id}\n"
                    f"{msg}"
                ).strip(),
            ),
        )
        await asyncio.sleep(1)


@on_notice(
    priority=5, rule=is_type(GroupBanNoticeEvent) & to_me(), block=False
).handle()
async def _(
    bot: Bot,
    event: GroupBanNoticeEvent,
) -> None:
    group_id = str(event.group_id)
    group = await group_repo.get_group(group_id)
    on_all_shut = event.sub_type == "ban" and event.user_id == 0
    off_all_shut = event.sub_type == "lift_ban" and event.user_id == 0

    if on_all_shut and group and group.status == GroupStatus.AUTHORIZED:
        group.set_all_shut(True)
    if off_all_shut and group and group.status == GroupStatus.AUTHORIZED:
        group.set_all_shut(False)

    await bot.set_group_leave(group_id=event.group_id)
    msg = await ban_user_and_cleanup_groups(
        AdminNoticeContext(
            bot,
            str(event.group_id),
            str(event.operator_id),
            "恶意禁言凛凛",
        )
    )
    ban_duration = (
        arrow.now()
        .shift(seconds=event.duration)
        .humanize(
            locale="zh",
            only_distance=True,
        )
    )
    group_name = await resolve_group_name(bot, str(event.group_id))

    for superuser in config.SUPERUSERS:
        await bot.send_private_msg(
            user_id=int(superuser),
            message=AlertTemplate.build_tip_notification(
                event_name="群组禁言",
                event_details=(
                    "检测到禁言行为，已自动退出群聊\n"
                    f"群组：{event.group_id}\n"
                    f"群名：{group_name}\n"
                    f"操作者：{event.operator_id}\n"
                    f"禁言时长：{ban_duration}\n"
                    f"{msg}"
                ).strip(),
            ),
        )
        await asyncio.sleep(1)


@on_notice(priority=5, rule=is_rename_group(), block=False).handle()
async def _(event: NotifyEvent) -> None:
    if not event.model_extra:
        return
    group_id = str(event.group_id)
    new_name = event.model_extra["name_new"]
    await group_repo.update_name(group_id, new_name)
