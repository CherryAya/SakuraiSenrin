"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-21 01:50:57
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-22 18:40:44
Description: 好友通知处理
"""

import asyncio
from pathlib import Path
import random

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import FriendRequestEvent
from nonebot.exception import ActionFailed, NetworkError
from nonebot.plugin import on_request
from nonebot.rule import is_type

from src.database.consts import WritePolicy
from src.database.core.consts import Permission
from src.lib.admin_notifications import deliver_admin_notification_i18n
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.lib.request_flags import is_valid_request_flag
from src.lib.types import as_str, is_object_mapping
from src.logger import logger
from src.repositories import user_repo

name = tr("zh-CN", "plugin.notice_user.name")
description = tr("zh-CN", "plugin.notice_user.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "user" / "README.MD"


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
            "name_key": "plugin.notice_user.name",
            "description_key": "plugin.notice_user.description",
        },
        "docs": create_docs_meta(
            visible=False,
            category="system",
            order=120,
            source=DOCS_SOURCE,
            slug="notice.user",
            parent_slug="notice",
            aliases=("用户事件处理", "notice.user"),
        ),
    },
)


async def is_friend_request(event: FriendRequestEvent) -> bool:
    if event.user_id < 1:
        logger.warning(
            f"[NoticeUser] friend request skipped reason=invalid_user_id "
            f"user_id={event.user_id}"
        )
        return False
    if not is_valid_request_flag(event.flag):
        logger.warning(
            "[NoticeUser] friend request skipped reason=invalid_flag "
            f"user_id={event.user_id}"
        )
        return False
    return True


async def _wait_before_accept() -> None:
    await asyncio.sleep(random.randint(10, 20))


friend_request = on_request(
    priority=5, rule=is_type(FriendRequestEvent) & is_friend_request
)


@friend_request.handle()
async def _(
    bot: Bot,
    event: FriendRequestEvent,
) -> None:
    if not await is_friend_request(event):
        return
    user_id = str(event.user_id)
    await _wait_before_accept()
    try:
        await bot.set_friend_add_request(flag=event.flag, approve=True)
    except (ActionFailed, NetworkError) as exc:
        logger.warning(
            f"[NoticeUser] friend approval unconfirmed user_id={user_id} error={exc}"
        )
        await deliver_admin_notification_i18n(
            bot,
            locale=await resolve_locale(None),
            key="notice.user.friend_request_failed",
            source_kind="notice_user_friend_request_failed",
            user_id=user_id,
        )
        return

    user_name = ""
    try:
        stranger_info: object = await bot.get_stranger_info(user_id=int(user_id))
        if is_object_mapping(stranger_info):
            user_name = as_str(stranger_info.get("nickname"))
        else:
            logger.warning(f"[NoticeUser] invalid stranger info user_id={user_id}")
    except (ActionFailed, NetworkError) as exc:
        # 拉取资料失败仍继续入库与通报。
        logger.warning(
            f"[NoticeUser] get_stranger_info failed user_id={user_id} "
            f"error={type(exc).__name__}: {exc}"
        )

    if not await user_repo.get_user(user_id):
        await user_repo.save_user(
            user_id=user_id,
            user_name=user_name,
            permission=Permission.NORMAL,
            policy=WritePolicy.IMMEDIATE,
        )
    await user_repo.ensure_persisted(user_id, user_name)

    await deliver_admin_notification_i18n(
        bot,
        locale=await resolve_locale(None),
        key="notice.user.friend_request",
        source_kind="notice_user_friend_request",
        inter_target_delay_seconds=1,
        user_id=str(event.user_id),
    )
