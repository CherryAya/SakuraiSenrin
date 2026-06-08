"""Water 周期榜命令处理。"""

from __future__ import annotations

from typing import Literal

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.matcher import Matcher

from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
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
    period: PeriodType,
    locale: LocaleCode,
) -> None:
    await matcher.send(
        tr(locale, "water.rank.working", period=tr(locale, PERIOD_LABELS[period]))
    )
    res = await water_rank_service.build_period_rank_image(period, locale)
    if res:
        await matcher.finish(MessageSegment.image(res))
    await matcher.finish(tr(locale, "water.rank.empty"))
