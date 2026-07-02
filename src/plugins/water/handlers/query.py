"""Water 统一查询命令处理。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.matcher import Matcher

from src.lib.i18n.types import LocaleCode
from src.lib.long_task import LongTaskRunner
from src.plugins.water.services.query_router import WaterQuerySpec, water_query_router


async def build_water_query_message(
    event: GroupMessageEvent,
    arg: Message,
    locale: LocaleCode,
    *,
    is_superuser: bool = False,
    spec: WaterQuerySpec | None = None,
    task: LongTaskRunner | None = None,
) -> Message:
    resolved_spec = spec or water_query_router.parse(arg.extract_plain_text())
    return await water_query_router.execute(
        spec=resolved_spec,
        user_id=str(event.user_id),
        group_id=str(event.group_id),
        locale=locale,
        is_superuser=is_superuser,
        task=task,
    )


async def handle_water_query(
    matcher: Matcher,
    event: GroupMessageEvent,
    arg: Message,
    locale: LocaleCode,
    *,
    is_superuser: bool = False,
    spec: WaterQuerySpec | None = None,
) -> None:
    await matcher.finish(
        await build_water_query_message(
            event,
            arg,
            locale,
            is_superuser=is_superuser,
            spec=spec,
        )
    )


async def build_my_water_profile_message(
    event: GroupMessageEvent,
    locale: LocaleCode,
) -> Message:
    return await water_query_router.build_profile_message(
        user_id=str(event.user_id),
        group_id=str(event.group_id),
        locale=locale,
        mode="full",
    )


async def handle_my_water_profile(
    matcher: Matcher,
    event: GroupMessageEvent,
    locale: LocaleCode,
) -> None:
    await matcher.finish(await build_my_water_profile_message(event, locale))
