"""Water 周期榜命令处理。"""

from __future__ import annotations

from typing import Literal

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.matcher import Matcher

from src.plugins.water.services.rank import water_rank_service

PeriodType = Literal["week", "month", "season", "year"]

PERIOD_LABELS: dict[PeriodType, str] = {
    "week": "周榜",
    "month": "月榜",
    "season": "季榜",
    "year": "年榜",
}


async def handle_period_rank(matcher: Matcher, period: PeriodType) -> None:
    await matcher.send(f"凛凛统计{PERIOD_LABELS[period]}中，请稍后喔……")
    res = await water_rank_service.build_period_rank_image(period)
    if res:
        await matcher.finish(MessageSegment.image(res))
    await matcher.finish("凛凛翻了翻账本，这个周期还没有可用结算数据喔。")
