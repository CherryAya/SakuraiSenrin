"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-21 01:51:01
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-03 12:24:46
Description: 邀请通知处理
"""

import asyncio
import random

from nonebot import on_notice, on_request
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupIncreaseNoticeEvent,
    GroupRequestEvent,
)
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata
from nonebot.rule import is_type, to_me

from src.config import config
from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.utils.common import AlertTemplate
from src.repositories import group_repo, invite_repo
from src.services.info import resolve_group_name

name = "群组邀请处理"
description = """
群组邀请处理:
  邀请事件上报

""".strip()

usage = """

""".strip()

__plugin_meta__ = PluginMetadata(
    name=name,
    description=description,
    usage=usage,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "no_check": True,
    },
)


async def is_invite_request(event: GroupIncreaseNoticeEvent) -> bool:
    return event.sub_type == "invite"


# [notice.group_increase.invite]


@on_notice(
    priority=5,
    rule=is_type(GroupIncreaseNoticeEvent) & to_me(),
    block=False,
).handle()
@on_request(
    priority=5,
    rule=is_type(GroupRequestEvent) & is_invite_request,
    block=False,
).handle()
async def _(
    bot: Bot,
    event: GroupRequestEvent | GroupIncreaseNoticeEvent,
    matcher: Matcher,
) -> None:
    # fmt: off
    inviter_id = str(
        event.user_id
        if isinstance(event, GroupRequestEvent)
        else event.operator_id
    )
    # fmt: on
    group_id = str(event.group_id)
    group = await group_repo.get_group(group_id)
    group_name = await resolve_group_name(bot, group_id)
    flag = event.flag if isinstance(event, GroupRequestEvent) else None
    invitation = await invite_repo.create_invitation(
        group_id=group_id,
        inviter_id=inviter_id,
        flag=flag,
    )

    if group and group.status.is_working:
        if isinstance(event, GroupRequestEvent):
            await bot.set_group_add_request(
                flag=event.flag,
                sub_type=event.sub_type,
                approve=False,
            )
        await matcher.finish()

    elif group and group.status.is_banned:
        if isinstance(event, GroupIncreaseNoticeEvent):
            await bot.set_group_leave(group_id=event.group_id)
        else:
            await bot.set_group_add_request(
                flag=event.flag,
                sub_type=event.sub_type,
                approve=False,
            )
        await bot.send_private_msg(
            user_id=int(inviter_id),
            message=(
                "🚫 自动拒绝\n"
                f"群号：{event.group_id}\n"
                f"群名：{group_name}\n"
                f"邀请者：{inviter_id}\n"
                "群聊已被拉黑，凛凛不想加入此群组。\n"
                f"如有异议，请及时加入反馈群「{config.MAIN_GROUP_ID}」并联系群管【加入白名单】"
            ),
        )
        for superuser in config.SUPERUSERS:
            await bot.send_private_msg(
                user_id=int(superuser),
                message=AlertTemplate.build_tip_notification(
                    event_name="自动拒绝",
                    event_details=(
                        "黑名单群组发起邀请，已自动拒绝\n"
                        f"群号：{event.group_id}\n"
                        f"群名：{group_name}\n"
                        f"邀请者：{inviter_id}"
                    ),
                ),
            )
            await asyncio.sleep(1)
        await matcher.finish()

    await bot.send_private_msg(
        user_id=int(inviter_id),
        message=(
            "📩 谢谢您对凛凛发起的邀请 ^_^\n"
            f"群号：{group_id}\n"
            f"群名：{group_name}\n"
            f"邀请者：{inviter_id}\n\n"
            "======重要提示======\n"
            f"请及时加入反馈群「{config.MAIN_GROUP_ID}」并联系群管【加入白名单】\n"
            f"请及时加入反馈群「{config.MAIN_GROUP_ID}」并联系群管【加入白名单】\n"
            f"请及时加入反馈群「{config.MAIN_GROUP_ID}」并联系群管【加入白名单】\n"
            "===================\n\n"
            "否则凛凛将无法在您的群聊中发送消息哦~\n"
            "另外，任何形式的禁言是不被允许的！如需要凛凛退出群聊，切勿直接移除，还请发送【#remove】指令。\n"
            "祝旅途愉快，每一种境遇都是命运的付赠品，还请好好珍惜，也希望能和凛凛相处的开心。\n"
            "—— 来自 SakuraiSenrin (•◡•) /💕"
        ),
    )

    report_message = (
        f"📩 新的邀请事件通知\n"
        f"群号：{group_id}\n"
        f"群名：{group_name}\n"
        f"邀请者：{inviter_id}\n"
        f"邀请 flag：{flag}\n\n"
        "回复 y 以同意，发送 n 以拒绝。"
    )
    for super_user_id in config.SUPERUSERS:
        message_id = (
            await bot.send_private_msg(
                user_id=int(super_user_id),
                message=AlertTemplate.build_tip_notification(
                    matcher.plugin_name, report_message
                ),
            )
        )["message_id"]
        if not message_id:
            continue

        await invite_repo.add_message_record(
            invitation_id=invitation.id,
            message_id=str(message_id),
        )
        await asyncio.sleep(random.randint(1, 3))
