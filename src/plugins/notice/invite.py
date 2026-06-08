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
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.rule import is_type, to_me

from src.config import config
from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, send_private_i18n, tr
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_static_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.repositories import group_repo, invite_repo
from src.services.info import resolve_group_name

name = tr("zh-CN", "plugin.notice_invite.name")
description = tr("zh-CN", "plugin.notice_invite.description")


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    locale = ctx.locale if ctx is not None else "zh-CN"
    return build_static_docs(
        name_key="plugin.notice_invite.name",
        description_key="plugin.notice_invite.description",
        content_key="plugin.notice_invite.docs",
        trigger=TriggerType.PASSIVE,
        permission=Permission.SUPERUSER,
        locale=locale,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "no_check": True,
        "i18n": {
            "name_key": "plugin.notice_invite.name",
            "description_key": "plugin.notice_invite.description",
        },
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="system",
            order=130,
        ),
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
        await send_private_i18n(
            bot,
            int(inviter_id),
            "notice.invite.auto_reject",
            locale_group_id=group_id,
            group_id=group_id,
            group_name=group_name,
            inviter_id=inviter_id,
            main_group_id=config.MAIN_GROUP_ID,
        )
        for superuser in config.SUPERUSERS:
            await send_private_i18n(
                bot,
                int(superuser),
                "notice.invite.auto_reject.details",
                locale_group_id=group_id,
                group_id=group_id,
                group_name=group_name,
                inviter_id=inviter_id,
            )
            await asyncio.sleep(1)
        await matcher.finish()

    await send_private_i18n(
        bot,
        int(inviter_id),
        "notice.invite.received",
        locale_group_id=group_id,
        group_id=group_id,
        group_name=group_name,
        inviter_id=inviter_id,
        main_group_id=config.MAIN_GROUP_ID,
    )

    locale = await resolve_locale(group_id)
    report_message = tr(
        locale,
        "notice.invite.report",
        group_id=group_id,
        group_name=group_name,
        inviter_id=inviter_id,
        flag=flag,
    )
    for super_user_id in config.SUPERUSERS:
        message_id = (
            await bot.send_private_msg(
                user_id=int(super_user_id),
                message=report_message,
            )
        )["message_id"]
        if not message_id:
            continue

        await invite_repo.add_message_record(
            invitation_id=invitation.id,
            message_id=str(message_id),
        )
        await asyncio.sleep(random.randint(1, 3))
