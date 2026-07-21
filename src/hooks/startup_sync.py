"""Handle startup backup-sync confirmation replies from superusers."""

from __future__ import annotations

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER

from src.lib.message_plan import finish_with_message
from src.services.message_asset_startup import (
    handle_message_asset_startup_reply,
    is_message_asset_startup_reply_text,
)
from src.services.startup_sync import (
    handle_startup_sync_reply,
    is_startup_sync_reply_text,
)


async def _is_startup_sync_reply(event: MessageEvent) -> bool:
    if event.reply is None:
        return False
    plain_text = event.message.extract_plain_text()
    return is_startup_sync_reply_text(
        plain_text
    ) or is_message_asset_startup_reply_text(plain_text)


startup_sync_reply = on_message(
    priority=5,
    block=False,
    permission=SUPERUSER,
    rule=_is_startup_sync_reply,
)


@startup_sync_reply.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    reply = event.reply
    if reply is None:
        return
    message_id = getattr(reply, "message_id", None)
    if message_id is None:
        return
    result = await handle_startup_sync_reply(
        bot,
        reply_message_id=str(message_id),
        text=event.message.extract_plain_text(),
    )
    if result is None:
        result = await handle_message_asset_startup_reply(
            bot,
            reply_message_id=str(message_id),
            text=event.message.extract_plain_text(),
        )
    if result is not None:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=result,
            source_kind="startup_sync",
        )
