"""绝对时间范围查询服务。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.water.services.rank import PeriodType, water_rank_service
from src.plugins.water.services.rank_query import water_rank_query_service


class AbsoluteRankService:
    async def build_group_day_rank(
        self,
        group_id: str,
        locale: LocaleCode,
    ) -> Message:
        return await water_rank_query_service.build_rank_message(
            subject="user",
            scope="group",
            period="day",
            group_id=group_id,
            locale=locale,
        )

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
