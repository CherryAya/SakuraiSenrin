"""Water 成就查询命令处理。"""

import arrow
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.matcher import Matcher

from src.lib.utils.common import get_current_time
from src.plugins.water.database import water_repo
from src.plugins.water.services import achievement_service


async def handle_my_achievements(matcher: Matcher, event: GroupMessageEvent) -> None:
    user_id = str(event.user_id)
    group_id = str(event.group_id)
    matrix_id = await water_repo.get_or_create_group_matrix_id(group_id)
    record_date = int(arrow.get(get_current_time()).format("YYYYMMDD"))

    message = await achievement_service.build_user_achievement_message(
        user_id=user_id,
        matrix_id=matrix_id,
        record_date=record_date,
    )
    await matcher.finish(message)
