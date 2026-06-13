"""Water 三维榜单查询服务。"""

from __future__ import annotations

import asyncio
from time import perf_counter

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.logger import logger
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
        combo = f"{subject}/{scope}/{period}"
        total_started = perf_counter()
        if not is_valid_rank_combo(subject, scope):
            return Message(tr(locale, "water.query.unsupported"))
        if period == "day":
            data_started = perf_counter()
            data = await self._build_day_rank_data(
                subject=subject,
                scope=scope,
                group_id=group_id,
                locale=locale,
                limit=limit,
            )
            data_elapsed = (perf_counter() - data_started) * 1000
            if data is None:
                logger.debug(
                    "[Water][RankQuery] combo={} stage=day_data empty=1 "
                    "elapsed_ms={:.2f}",
                    combo,
                    data_elapsed,
                )
                return Message(tr(locale, "water.rank.empty"))
            render_started = perf_counter()
            image = await build_water_day_rank_image(data, locale)
            render_elapsed = (perf_counter() - render_started) * 1000
            if image is None:
                logger.debug(
                    "[Water][RankQuery] combo={} stage=day_render empty=1 "
                    "elapsed_ms={:.2f}",
                    combo,
                    render_elapsed,
                )
                return Message(tr(locale, "water.rank.empty"))
            total_elapsed = (perf_counter() - total_started) * 1000
            logger.debug(
                "[Water][RankQuery] combo={} type=day data_ms={:.2f} "
                "render_ms={:.2f} total_ms={:.2f} bytes={}",
                combo,
                data_elapsed,
                render_elapsed,
                total_elapsed,
                len(image),
            )
            return Message(MessageSegment.image(image))
        else:
            data_started = perf_counter()
            data = await water_rank_service.build_natural_period_rank_data(
                subject=subject,
                scope=scope,
                period=period,
                group_id=group_id,
                locale=locale,
                limit=limit,
            )
            data_elapsed = (perf_counter() - data_started) * 1000
            if data is None:
                logger.debug(
                    "[Water][RankQuery] combo={} stage=period_data empty=1 "
                    "elapsed_ms={:.2f}",
                    combo,
                    data_elapsed,
                )
                return Message(tr(locale, "water.rank.empty"))
            render_started = perf_counter()
            image = await build_water_period_rank_image(data, locale)
            render_elapsed = (perf_counter() - render_started) * 1000
            if image is None:
                logger.debug(
                    "[Water][RankQuery] combo={} stage=period_render empty=1 "
                    "elapsed_ms={:.2f}",
                    combo,
                    render_elapsed,
                )
                return Message(tr(locale, "water.rank.empty"))
            total_elapsed = (perf_counter() - total_started) * 1000
            logger.debug(
                "[Water][RankQuery] combo={} type=period data_ms={:.2f} "
                "render_ms={:.2f} total_ms={:.2f} bytes={}",
                combo,
                data_elapsed,
                render_elapsed,
                total_elapsed,
                len(image),
            )
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
