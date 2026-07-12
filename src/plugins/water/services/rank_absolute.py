"""绝对时间范围查询服务。"""

from __future__ import annotations

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import MessagePlanInput
from src.plugins.water.message_support import (
    build_image_plan_entry,
    build_text_plan_entry,
)
from src.plugins.water.services.rank import PeriodType, water_rank_service
from src.plugins.water.services.rank_query import water_rank_query_service


class AbsoluteRankService:
    async def build_group_day_rank(
        self,
        group_id: str,
        locale: LocaleCode,
    ) -> MessagePlanInput:
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
    ) -> MessagePlanInput:
        res = await water_rank_service.build_period_rank_image(period, locale)
        if res:
            return build_image_plan_entry(res)
        return build_text_plan_entry(tr(locale, "water.rank.empty"))

    async def build_total_rank(self, locale: LocaleCode) -> MessagePlanInput:
        rows = await water_rank_service.build_total_rank_lines(locale)
        return build_text_plan_entry("\n".join(rows))


absolute_rank_service = AbsoluteRankService()
