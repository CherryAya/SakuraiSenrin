"""Water 三维榜单查询服务。"""

from __future__ import annotations

import asyncio

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.water.database import water_repo
from src.plugins.water.img import (
    WaterPeriodRankCardData,
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
    ) -> WaterPeriodRankCardData | None:
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
        display_meta = water_rank_service._build_display_meta(subject, scope)
        champion = view_items[0]
        runner_up_count = view_items[1].msg_count if len(view_items) > 1 else 0
        title = (
            f"{SUBJECT_LABELS[subject]} · {SCOPE_LABELS[scope]}{PERIOD_LABELS['day']}"
        )
        return WaterPeriodRankCardData(
            period="week",
            title=title,
            badge="TODAY",
            range_text="今日实时累计",
            compare_text="对比区间 昨日全天",
            generated_at=0,
            total_msg_count=overview.total_msg_count,
            active_entity_count=overview.active_entity_count,
            hourly_counts=overview.hourly_counts,
            peak_hour=overview.peak_hour,
            previous_total_msg_count=overview.previous_total_msg_count,
            top_items=view_items,
            champion_gap=max(0, champion.msg_count - runner_up_count),
            champion_share=(
                champion.msg_count / overview.total_msg_count
                if overview.total_msg_count > 0
                else 0.0
            ),
            entity_label=display_meta["entity_label"],
            champion_summary_label=display_meta["champion_summary_label"],
            board_title=display_meta["board_title"],
            board_summary_label=display_meta["board_summary_label"],
            board_active_hours_label=display_meta["board_active_hours_label"],
            overview_title=display_meta["overview_title"],
        )


water_rank_query_service = WaterRankQueryService()
