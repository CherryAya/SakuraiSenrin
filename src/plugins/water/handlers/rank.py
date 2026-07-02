"""Water 周期榜命令处理。"""

from __future__ import annotations

from typing import Literal

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.matcher import Matcher

from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.long_task import (
    CompositeProgressSink,
    LoggerProgressSink,
    LongTaskRunner,
    LongTaskSpec,
    MessageEventProgressSink,
)
from src.lib.message_plan import finish_with_message
from src.lib.messages import image_message
from src.plugins.water.services.rank import water_rank_service

PeriodType = Literal["week", "month", "season", "year"]

PERIOD_LABELS: dict[PeriodType, MessageKey] = {
    "week": "water.rank.period.week",
    "month": "water.rank.period.month",
    "season": "water.rank.period.season",
    "year": "water.rank.period.year",
}


async def handle_period_rank(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    period: PeriodType,
    locale: LocaleCode,
) -> None:
    async with LongTaskRunner(
        LongTaskSpec(
            task_name=f"water.rank.period.{period}",
            source_kind="water_rank_period",
            prompt=tr(
                locale,
                "water.rank.working",
                period=tr(locale, PERIOD_LABELS[period]),
            ),
        ),
        sink=CompositeProgressSink(
            LoggerProgressSink(),
            MessageEventProgressSink(bot, event),
        ),
    ) as long_task:
        await long_task.advance("rendering")
        res = await water_rank_service.build_period_rank_image(period, locale)
    if res:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=image_message(res),
            source_kind="water_rank_period",
        )
    await finish_with_message(
        bot,
        matcher,
        event=event,
        message=tr(locale, "water.rank.empty"),
        source_kind="water_rank_period",
    )
