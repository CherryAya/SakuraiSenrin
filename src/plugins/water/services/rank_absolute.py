"""绝对时间范围查询服务。"""

from __future__ import annotations

from typing import Literal

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins.water.img import build_water_rank_image
from src.plugins.water.services.rank import PeriodType, water_rank_service

AbsoluteScope = Literal["day", "month", "season", "year", "total"]


class AbsoluteRankService:
    async def build_group_day_rank(self, group_id: str) -> Message:
        res = await build_water_rank_image(group_id)
        if res:
            return Message(MessageSegment.image(res))
        return Message("凛凛，凛凛凛凛！无水无水！🏳️")

    async def build_period_rank(self, period: PeriodType) -> Message:
        res = await water_rank_service.build_period_rank_image(period)
        if res:
            return Message(MessageSegment.image(res))
        return Message("凛凛翻了翻账本，这个周期还没有可用结算数据喔。")

    async def build_total_rank(self) -> Message:
        rows = await water_rank_service.build_total_rank_lines()
        return Message("\n".join(rows))


absolute_rank_service = AbsoluteRankService()
