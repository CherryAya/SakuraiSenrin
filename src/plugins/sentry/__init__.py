"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-25 01:39:00
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 17:26:54
Description: sentry 异常记录插件
"""

import asyncio
from pathlib import Path
from typing import cast

from nonebot import get_bot
from nonebot.adapters.onebot.v11 import Bot
import sentry_sdk
from sentry_sdk.types import Event, Hint

from src.config import config
from src.database.core.consts import Permission
from src.lib.admin_notifications import deliver_admin_notification_i18n
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger

name = tr("zh-CN", "plugin.sentry.name")
description = tr("zh-CN", "plugin.sentry.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#8D2CBD",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "i18n": {
            "name_key": "plugin.sentry.name",
            "description_key": "plugin.sentry.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="system",
            order=30,
            source=DOCS_SOURCE,
        ),
    },
)

background_tasks: set[asyncio.Task] = set()


def _should_drop_event(hint: Hint) -> bool:
    exc_info = hint.get("exc_info")
    if not exc_info:
        return False

    exc_type, exc_value, exc_traceback = exc_info
    if exc_type is not AssertionError:
        return False
    if exc_traceback is None:
        return False
    if str(exc_value):
        return False

    while exc_traceback is not None:
        frame = exc_traceback.tb_frame
        if (
            frame.f_code.co_name == "_drain_helper"
            and frame.f_globals.get("__name__") == "websockets.legacy.protocol"
        ):
            return True
        exc_traceback = exc_traceback.tb_next
    return False


async def notify_admin(error_message: str) -> None:
    try:
        await deliver_admin_notification_i18n(
            cast(Bot, get_bot()),
            locale="zh-CN",
            key="sentry.alert",
            source_kind="sentry_alert",
            error_message=error_message,
        )
    except Exception as e:
        logger.error(f"Sentry 报警发送失败: {e}")


def before_send_handler(event: Event, hint: Hint) -> Event | None:
    if _should_drop_event(hint):
        logger.debug("Skip reporting websocket keepalive AssertionError noise.")
        return None

    if "exc_info" in hint:
        exc_type, exc_value, _ = hint["exc_info"]
        error_msg = f"Type: {exc_type.__name__}\nValue: {exc_value}"
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(notify_admin(error_msg))
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
        except RuntimeError:
            pass

    return event


sentry_sdk.init(
    dsn=config.SENTRY_DSN,
    send_default_pii=True,
    before_send=before_send_handler,
    enable_logs=True,
)
