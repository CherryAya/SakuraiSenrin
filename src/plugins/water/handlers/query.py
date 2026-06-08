"""Water 统一查询命令处理。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.matcher import Matcher

from src.lib.i18n.types import LocaleCode
from src.plugins.water.services.query_router import water_query_router


async def handle_water_query(
    matcher: Matcher,
    event: GroupMessageEvent,
    arg: Message,
    locale: LocaleCode,
) -> None:
    spec = water_query_router.parse(arg.extract_plain_text())
    message = await water_query_router.execute(
        spec=spec,
        user_id=str(event.user_id),
        group_id=str(event.group_id),
        locale=locale,
    )
    await matcher.finish(message)
