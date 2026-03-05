"""Water 被动事件处理。"""

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
)

from src.database.consts import WritePolicy
from src.logger import logger
from src.plugins.water.database import water_repo
from src.plugins.water.services import matrix_suggestion_service


async def handle_water_record(bot: Bot, event: GroupMessageEvent) -> None:
    group_id = str(event.group_id)
    user_id = str(event.user_id)

    await water_repo.save_message(
        group_id=group_id,
        user_id=user_id,
        created_at=event.time,
        policy=WritePolicy.BUFFERED,
    )

    try:
        await matrix_suggestion_service.maybe_suggest_on_first_record(bot, group_id)
    except Exception as e:
        logger.warning(f"[Water] first-record suggestion skipped: {e}")


async def handle_group_increase_notice(
    bot: Bot,
    event: GroupIncreaseNoticeEvent,
) -> None:
    if str(event.user_id) == str(bot.self_id):
        return

    try:
        await matrix_suggestion_service.maybe_suggest_on_new_member(
            bot=bot,
            group_id=str(event.group_id),
            user_id=str(event.user_id),
        )
    except Exception as e:
        logger.warning(f"[Water] newcomer suggestion skipped: {e}")
