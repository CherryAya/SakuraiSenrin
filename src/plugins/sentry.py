"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-25 01:39:00
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 17:26:54
Description: sentry 异常记录插件
"""

import asyncio

from nonebot import get_bot
from nonebot.adapters.onebot.v11.message import Message
import sentry_sdk
from sentry_sdk.types import Event, Hint

from src.config import config
from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import build_static_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger

name = "Sentry"
description = """
发送错误日志到 Sentry
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
        "version": "0.1.0",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="system",
            order=30,
        ),
    },
)

background_tasks: set[asyncio.Task] = set()


async def notify_admin(error_message: str) -> None:
    try:
        bot = get_bot()
        for user_id in config.SUPERUSERS:
            await bot.send_private_msg(
                user_id=int(user_id),
                message=f"[Sentry Alert] 捕获到未处理异常:\n{error_message}",
            )
    except Exception as e:
        logger.error(f"Sentry 报警发送失败: {e}")


def before_send_handler(event: Event, hint: Hint) -> Event:
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
