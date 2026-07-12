"""Water 成就查询命令处理。"""

import arrow
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.matcher import Matcher

from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import finish_with_message
from src.lib.utils.common import get_current_time
from src.plugins.water.database import water_repo
from src.plugins.water.services.achievement import achievement_service


async def build_my_achievements_message(
    event: GroupMessageEvent,
    locale: LocaleCode,
) -> str:
    user_id = str(event.user_id)
    group_id = str(event.group_id)
    matrix_id = await water_repo.get_or_create_group_matrix_id(group_id)
    record_date = int(arrow.get(get_current_time()).format("YYYYMMDD"))
    return await achievement_service.build_user_achievement_message(
        user_id=user_id,
        matrix_id=matrix_id,
        record_date=record_date,
        locale=locale,
    )


async def handle_my_achievements(
    matcher: Matcher,
    event: GroupMessageEvent,
    locale: LocaleCode,
) -> None:
    await finish_with_message(
        getattr(matcher, "bot", None),
        matcher,
        event=event,
        message=await build_my_achievements_message(event, locale),
        source_kind="water_achievement",
    )
