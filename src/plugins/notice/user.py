"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-21 01:50:57
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-22 18:40:44
Description: 好友通知处理
"""

import asyncio
import random

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import FriendRequestEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.plugin import on_request

from src.config import config
from src.database.consts import WritePolicy
from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import send_private_i18n
from src.lib.plugin_docs import build_static_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.repositories import user_repo

name = "好友通知事件处理"
description = """
好友通知事件处理:
  处理好友请求
""".strip()

docs_content = "被动触发"


def build_docs() -> Message:
    return build_static_docs(
        name=name,
        description=description,
        content=docs_content,
        trigger=TriggerType.PASSIVE,
        permission=Permission.SUPERUSER,
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
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="system",
            order=120,
        ),
    },
)


@on_request(priority=5).handle()
async def _(
    bot: Bot,
    event: FriendRequestEvent,
) -> None:
    await asyncio.sleep(random.randint(10, 20))
    await bot.set_friend_add_request(flag=event.flag, approve=True)
    user_name: str = (await bot.get_stranger_info(user_id=event.user_id)).get(
        "nickname", ""
    )
    user_id = str(event.user_id)
    if not user_repo.get_user(user_id):
        await user_repo.save_user(
            user_id=user_id,
            user_name=user_name,
            permission=Permission.NORMAL,
            policy=WritePolicy.IMMEDIATE,
        )

    for super_user_id in config.SUPERUSERS:
        await send_private_i18n(
            bot,
            int(super_user_id),
            "notice.user.friend_request",
            user_id=str(event.user_id),
        )
        await asyncio.sleep(1)
