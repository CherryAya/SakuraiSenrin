"""Water 三维榜单查询服务。"""

from __future__ import annotations

import asyncio

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.water.database import water_repo
from src.plugins.water.img import (
    WaterDayRankCardData,
    build_water_day_rank_image,
    build_water_period_rank_image,
)
from src.plugins.water.services.rank import water_rank_service
from src.plugins.water.services.rank_types import (
    PERIOD_LABELS,
    SCOPE_LABELS,
    SUBJECT_LABELS,
    WaterRankPeriod,
    WaterRankScope,
    WaterRankSubject,
    is_valid_rank_combo,
)


class WaterRankQueryService:
    async def build_rank_message(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        period: WaterRankPeriod,
        group_id: str,
        locale: LocaleCode,
        limit: int = 10,
    ) -> Message:
        if not is_valid_rank_combo(subject, scope):
            return Message(tr(locale, "water.query.unsupported"))
        if period == "day":
            data = await self._build_day_rank_data(
                subject=subject,
                scope=scope,
                group_id=group_id,
                locale=locale,
                limit=limit,
            )
            if data is None:
                return Message(tr(locale, "water.rank.empty"))
            image = await build_water_day_rank_image(data, locale)
            if image is None:
                return Message(tr(locale, "water.rank.empty"))
            return Message(MessageSegment.image(image))
        else:
            data = await water_rank_service.build_natural_period_rank_data(
                subject=subject,
                scope=scope,
                period=period,
                group_id=group_id,
                locale=locale,
                limit=limit,
            )
            if data is None:
                return Message(tr(locale, "water.rank.empty"))
            image = await build_water_period_rank_image(data, locale)
            if image is None:
                return Message(tr(locale, "water.rank.empty"))
            return Message(MessageSegment.image(image))

    async def _build_day_rank_data(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        group_id: str,
        locale: LocaleCode,
        limit: int,
    ) -> WaterDayRankCardData | None:
        top_items, overview = await asyncio.gather(
            water_repo.get_natural_day_leaderboard(
                subject=subject,
                scope=scope,
                group_id=group_id,
                limit=limit,
            ),
            water_repo.get_natural_day_overview(
                subject=subject,
                scope=scope,
                group_id=group_id,
            ),
        )
        if not top_items or overview.total_msg_count <= 0:
            return None
        view_items = await water_rank_service._build_view_items(
            subject,
            top_items,
            locale,
        )
        champion = view_items[0]
        title = (
            f"{SUBJECT_LABELS[subject]} · {SCOPE_LABELS[scope]}{PERIOD_LABELS['day']}"
        )
        return WaterDayRankCardData(
            title=title,
            scope_label=f"{SCOPE_LABELS[scope]} · {SUBJECT_LABELS[subject]}",
            subject_label=SUBJECT_LABELS[subject],
            leader_name=champion.display_name,
            leader_rank_label=f"#{champion.current_rank}",
            generated_at=0,
            top_items=view_items,
            summary_label=(
                f"今日领跑: {champion.display_name}\n"
                f"实时累计: {champion.msg_count} 条 · "
                f"上榜对象: {overview.active_entity_count}"
            ),
            footer_label="统计口径: 当天实时累计，图形为日榜瓷砖图。",
        )


water_rank_query_service = WaterRankQueryService()
