"""Water 周期榜命令处理。"""

from __future__ import annotations

from typing import Literal

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.matcher import Matcher

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.water.services.rank import water_rank_service

PeriodType = Literal["week", "month", "season", "year"]

PERIOD_LABELS: dict[PeriodType, str] = {
    "week": "周榜",
    "month": "月榜",
    "season": "季榜",
    "year": "年榜",
}


async def handle_period_rank(
    matcher: Matcher,
    period: PeriodType,
    locale: LocaleCode,
) -> None:
    await matcher.send(tr(locale, "water.rank.working", period=PERIOD_LABELS[period]))
    res = await water_rank_service.build_period_rank_image(period)
    if res:
        await matcher.finish(MessageSegment.image(res))
    await matcher.finish(tr(locale, "water.rank.empty"))
