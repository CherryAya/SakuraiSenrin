"""绝对时间范围查询服务。"""

from __future__ import annotations

from typing import Literal

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.water.img import build_water_rank_image
from src.plugins.water.services.rank import PeriodType, water_rank_service

AbsoluteScope = Literal["day", "month", "season", "year", "total"]


class AbsoluteRankService:
    async def build_group_day_rank(
        self,
        group_id: str,
        locale: LocaleCode,
    ) -> Message:
        res = await build_water_rank_image(group_id, locale)
        if res:
            return Message(MessageSegment.image(res))
        return Message(tr(locale, "water.rank.absolute.none"))

    async def build_period_rank(
        self,
        period: PeriodType,
        locale: LocaleCode,
    ) -> Message:
        res = await water_rank_service.build_period_rank_image(period, locale)
        if res:
            return Message(MessageSegment.image(res))
        return Message(tr(locale, "water.rank.empty"))

    async def build_total_rank(self, locale: LocaleCode) -> Message:
        rows = await water_rank_service.build_total_rank_lines(locale)
        return Message("\n".join(rows))


absolute_rank_service = AbsoluteRankService()
