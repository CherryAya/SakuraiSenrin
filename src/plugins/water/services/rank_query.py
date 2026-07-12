"""Water 三维榜单查询服务。"""

from __future__ import annotations

from time import perf_counter

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import MessagePlanInput
from src.logger import logger
from src.plugins.water.database import water_repo
from src.plugins.water.img import build_water_day_rank_image
from src.plugins.water.message_support import (
    build_image_plan_entry,
    build_text_plan_entry,
)
from src.plugins.water.renderers.models import WaterDayRankCardData
from src.plugins.water.renderers.report import (
    build_water_period_rank_image,
)
from src.plugins.water.services.rank import water_rank_service
from src.plugins.water.services.rank_types import (
    WaterRankPeriod,
    WaterRankScope,
    WaterRankSubject,
    is_valid_rank_combo,
)
from src.services.info import resolve_group_name


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
    ) -> MessagePlanInput:
        combo = f"{subject}/{scope}/{period}"
        total_started = perf_counter()
        if not is_valid_rank_combo(subject, scope):
            return build_text_plan_entry(tr(locale, "water.query.unsupported"))
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
                return build_text_plan_entry(tr(locale, "water.rank.empty"))
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
                return build_text_plan_entry(tr(locale, "water.rank.empty"))
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
            return build_image_plan_entry(image)
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
                return build_text_plan_entry(tr(locale, "water.rank.empty"))
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
                return build_text_plan_entry(tr(locale, "water.rank.empty"))
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
            return build_image_plan_entry(image)

    async def _build_day_rank_data(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        group_id: str,
        locale: LocaleCode,
        limit: int,
    ) -> WaterDayRankCardData | None:
        snapshot = await water_repo.get_natural_day_snapshot(
            subject=subject,
            scope=scope,
            group_id=group_id,
            limit=limit,
        )
        top_items = snapshot.leaderboard
        overview = snapshot.overview
        if not top_items or overview.total_msg_count <= 0:
            return None
        view_items = await water_rank_service._build_view_items(
            subject,
            top_items,
            locale,
        )
        champion = view_items[0]
        title = water_rank_service.build_rank_title(locale, subject, scope, "day")
        group_name = await resolve_group_name(None, group_id)
        scope_label = water_rank_service.build_scope_label(locale, scope)
        subject_label = water_rank_service.build_subject_label(locale, subject)
        return WaterDayRankCardData(
            title=title,
            group_id=group_id,
            group_name=group_name,
            scope_label=tr(
                locale,
                "water.rank.day.scope_label",
                scope=scope_label,
                subject=subject_label,
            ),
            subject_label=subject_label,
            leader_name=champion.display_name,
            leader_rank_label=tr(
                locale,
                "water.rank.day.leader_rank",
                rank=champion.current_rank,
            ),
            generated_at=0,
            top_items=view_items,
            summary_label=tr(
                locale,
                "water.rank.day.summary",
                leader_name=champion.display_name,
                msg_count=champion.msg_count,
                active_entity_count=overview.active_entity_count,
            ),
            footer_label=tr(locale, "water.rank.day.footer"),
        )


water_rank_query_service = WaterRankQueryService()
