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

from nonebot.adapters.onebot.v11 import ActionFailed
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import FriendRequestEvent
from nonebot.plugin import on_request

from src.database.consts import WritePolicy
from src.database.core.consts import Permission
from src.lib.admin_notifications import deliver_admin_notification_i18n
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
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


@on_request(priority=5).handle()
async def _(
    bot: Bot,
    event: FriendRequestEvent,
) -> None:
    user_id = str(event.user_id)
    # 延迟等待期间事件上下文可能失效，先做有效性校验，避免把非法 user_id 传给 API。
    if not user_id.isdigit() or int(user_id) < 1:
        logger.warning(
            f"[NoticeUser] friend request skipped reason=invalid_user_id "
            f"user_id={user_id or '-'} flag={event.flag}"
        )
        return

    await asyncio.sleep(random.randint(10, 20))
    await bot.set_friend_add_request(flag=event.flag, approve=True)

    user_name = ""
    try:
        stranger_info = await bot.get_stranger_info(user_id=int(user_id))
        user_name = str(stranger_info.get("nickname", "") or "")
    except ActionFailed as exc:
        # 拉取资料失败不应中断后续入库与通报。
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
