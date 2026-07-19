"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-21 01:51:01
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-03 12:24:46
Description: 邀请通知处理
"""

import asyncio
from pathlib import Path
import random

from nonebot import on_notice, on_request
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupIncreaseNoticeEvent,
    GroupRequestEvent,
)
from nonebot.matcher import Matcher
from nonebot.rule import is_type, to_me

from src.config import config
from src.database.consts import WritePolicy
from src.database.core.consts import InvitationStatus, Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, send_private_i18n, tr
from src.lib.message_delivery import DeliveryTarget
from src.lib.message_plan import DeliveryPlan, deliver_message_plan
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.repositories import group_repo, invite_repo, user_repo
from src.services.info import resolve_group_name, resolve_user_name

name = tr("zh-CN", "plugin.notice_invite.name")
description = tr("zh-CN", "plugin.notice_invite.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "invite" / "README.MD"


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
            visible=False,
            category="system",
            order=130,
            source=DOCS_SOURCE,
            slug="notice.invite",
            parent_slug="notice",
            aliases=("群组邀请处理", "notice.invite"),
        ),
    },
)


async def is_invite_request(event: GroupIncreaseNoticeEvent) -> bool:
    return event.sub_type == "invite"


async def _ensure_invitation_dependencies(
    bot: Bot,
    *,
    inviter_id: str,
    group_id: str,
    group_name: str,
) -> None:
    inviter = await user_repo.get_user(inviter_id)
    if inviter is None:
        inviter_name = await resolve_user_name(bot, inviter_id)
        await user_repo.save_user(
            user_id=inviter_id,
            user_name=inviter_name,
            policy=WritePolicy.IMMEDIATE,
        )

    group = await group_repo.get_group(group_id)
    if group is None:
        await group_repo.save_group(
            group_id=group_id,
            group_name=group_name,
            policy=WritePolicy.IMMEDIATE,
        )


async def _ensure_request_dependencies(
    bot: Bot,
    event: GroupRequestEvent,
    *,
    group_name: str,
) -> None:
    await _ensure_invitation_dependencies(
        bot,
        inviter_id=str(event.user_id),
        group_id=str(event.group_id),
        group_name=group_name,
    )


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
    if isinstance(event, GroupRequestEvent):
        await _ensure_request_dependencies(bot, event, group_name=group_name)
    else:
        await _ensure_invitation_dependencies(
            bot,
            inviter_id=inviter_id,
            group_id=group_id,
            group_name=group_name,
        )
    group = await group_repo.get_group(group_id)
    invitation = await invite_repo.create_invitation(
        group_id=group_id,
        inviter_id=inviter_id,
        flag=flag,
    )

    if group and group.status.is_working:
        await _ensure_invitation_dependencies(
            bot,
            inviter_id=str(bot.self_id),
            group_id=group_id,
            group_name=group_name,
        )
        if isinstance(event, GroupRequestEvent):
            await bot.set_group_add_request(
                flag=event.flag,
                sub_type=event.sub_type,
                approve=True,
            )
        await invite_repo.update_status(
            invitation.id,
            status=InvitationStatus.APPROVED,
            operator_id=str(bot.self_id),
        )
        await matcher.finish()

    elif group and group.status.is_banned:
        await _ensure_invitation_dependencies(
            bot,
            inviter_id=str(bot.self_id),
            group_id=group_id,
            group_name=group_name,
        )
        if isinstance(event, GroupIncreaseNoticeEvent):
            await bot.set_group_leave(group_id=event.group_id)
        else:
            await bot.set_group_add_request(
                flag=event.flag,
                sub_type=event.sub_type,
                approve=False,
            )
        await invite_repo.update_status(
            invitation.id,
            status=InvitationStatus.REJECTED,
            operator_id=str(bot.self_id),
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
        plan_result = await deliver_message_plan(
            bot,
            plan=DeliveryPlan(
                messages=(report_message,),
                source_kind="notice_invite",
                allow_asset_reuse=False,
            ),
            target=DeliveryTarget(kind="private", target_id=str(super_user_id)),
        )
        send_result = plan_result.results[0]
        message_id = send_result.message_id
        if not message_id:
            continue

        await invite_repo.add_message_record(
            invitation_id=invitation.id,
            message_id=str(message_id),
        )
        await asyncio.sleep(random.randint(1, 3))
