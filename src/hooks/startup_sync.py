"""Handle startup backup-sync confirmation replies from superusers."""

from __future__ import annotations

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me

from src.lib.message_plan import finish_with_message
from src.lib.reply_router import (
    ReplyRoute,
    build_reply_rule,
    dispatch_reply_route,
    register_reply_route,
)
from src.services.startup_sync import (
    handle_startup_sync_reply_target,
    is_startup_sync_reply_text,
)

register_reply_route(
    ReplyRoute(
        name="startup_sync.restore",
        context_kinds=("startup_sync.restore",),
        text_matcher=is_startup_sync_reply_text,
        handler=lambda bot, event, target: handle_startup_sync_reply_target(
            bot,
            target=target,
            text=event.message.extract_plain_text(),
        ),
    )
)


startup_sync_reply = on_message(
    priority=5,
    block=False,
    permission=SUPERUSER,
    rule=to_me() & build_reply_rule("startup_sync.restore"),
)


@startup_sync_reply.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    result = await dispatch_reply_route("startup_sync.restore", bot, event)
    if result is not None:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=result,
            source_kind="startup_sync",
        )
