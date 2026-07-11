"""Water 群日报服务。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

import arrow
from nonebot.adapters.onebot.v11.bot import Bot
from pil_utils import BuildImage

from src.lib.cooldown import CooldownIsolateLevel, MemoryCooldown
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.long_task import LongTaskRunner
from src.lib.message_delivery import DeliveryTarget
from src.lib.message_plan import DeliveryPlan, MessagePlanInput, deliver_message_plan
from src.lib.utils.common import get_current_time
from src.lib.utils.img import QQAvatar
from src.logger import logger
from src.plugins.water.database import water_repo
from src.plugins.water.database.repo_models import (
    WaterDailyReportCandidate,
    WaterGroupDailyRankSnapshot,
    WaterGroupReportSnapshot,
)
from src.plugins.water.message_support import (
    build_image_plan_entry,
    build_text_plan_entry,
)
from src.plugins.water.renderers.models import (
    WaterGroupDailyRankCardItem,
    WaterGroupRankTrendSeries,
    WaterGroupReportImageData,
    WaterGroupShareSlice,
    WaterRankCardItem,
    WaterReportInsightItem,
)
from src.plugins.water.renderers.report import build_water_group_report_image
from src.plugins.water.renderers.report_layout import (
    pick_group_report_right_panel_tier,
)
from src.plugins.water.services.rank import water_rank_service
from src.repositories import group_repo
from src.services.info import resolve_group_name

WaterGroupReportWindow = Literal["today_live", "yesterday_settled"]

REPORT_ACTIVITY_SCORE_FACTOR = 20
REPORT_ACTIVITY_SCORE_THRESHOLD = 300
REPORT_PUSH_INTERVAL_SECONDS = 8.0
TODAY_REPORT_COOLDOWN_SECONDS = 60
_today_report_cooldown = MemoryCooldown(
    TODAY_REPORT_COOLDOWN_SECONDS,
    isolate_level=CooldownIsolateLevel.GROUP,
)


@dataclass(frozen=True)
class WaterDailyReportBatchResult:
    record_date: int
    candidate_groups: int
    rendered_groups: int
    sent_groups: int
    skipped_groups: int
    failed_groups: int
    total_elapsed_ms: float


class WaterReportService:
    def try_acquire_today_report_cooldown(self, group_id: str) -> tuple[bool, int]:
        result = _today_report_cooldown.acquire(group_id=group_id)
        return result.acquired, result.remaining_seconds

    def clear_today_report_cooldowns(self) -> None:
        _today_report_cooldown.clear()

    async def build_group_report_message(
        self,
        *,
        window: WaterGroupReportWindow,
        group_id: str,
        locale: LocaleCode,
        now_ts: int | None = None,
        task: LongTaskRunner | None = None,
    ) -> MessagePlanInput:
        if task is not None:
            await task.advance("loading_snapshot", metadata={"group_id": group_id})
        snapshot = await self._get_snapshot(
            window=window,
            group_id=group_id,
            now_ts=now_ts,
        )
        if snapshot is None or snapshot.total_msg_count <= 0:
            return build_text_plan_entry(tr(locale, "water.report.empty"))
        if task is not None:
            await task.advance(
                "building_report_data",
                metadata={"group_id": group_id},
            )
        data = await self._build_card_data(window, snapshot, locale)
        if task is not None:
            await task.advance(
                "rendering_report",
                metadata={"group_id": group_id},
            )
        image = await build_water_group_report_image(data, locale)
        if image is None:
            return build_text_plan_entry(tr(locale, "water.report.empty"))
        return build_image_plan_entry(image)

    async def run_daily_group_report_push(
        self,
        *,
        bot: Bot,
        locale: LocaleCode = "zh-CN",
        record_date: int | None = None,
        task: LongTaskRunner | None = None,
    ) -> WaterDailyReportBatchResult:
        started = perf_counter()
        if task is not None:
            await task.advance("loading_candidates")
        state = await water_repo.get_settlement_state()
        target_date = record_date or int(state["last_success_record_date"])
        if target_date <= 0 or int(state["last_success_record_date"]) < target_date:
            logger.warning(
                "[Water][ReportPush] skipped date={} reason=settlement_not_ready",
                target_date,
            )
            return WaterDailyReportBatchResult(
                record_date=target_date,
                candidate_groups=0,
                rendered_groups=0,
                sent_groups=0,
                skipped_groups=0,
                failed_groups=0,
                total_elapsed_ms=(perf_counter() - started) * 1000,
            )

        working_group_ids = await group_repo.get_working_group_ids()
        candidates = await water_repo.list_daily_report_candidates(
            record_date=target_date,
            min_activity_score=REPORT_ACTIVITY_SCORE_THRESHOLD,
            working_group_ids=working_group_ids,
        )
        logger.info(
            "[Water][ReportPush] start date={} working_groups={} candidates={}",
            target_date,
            len(working_group_ids),
            len(candidates),
        )
        if not candidates:
            return WaterDailyReportBatchResult(
                record_date=target_date,
                candidate_groups=0,
                rendered_groups=0,
                sent_groups=0,
                skipped_groups=0,
                failed_groups=0,
                total_elapsed_ms=(perf_counter() - started) * 1000,
            )

        sem = asyncio.Semaphore(4)
        if task is not None:
            await task.advance(
                "rendering_groups",
                current=0,
                total=len(candidates),
            )

        async def _render(
            candidate: WaterDailyReportCandidate,
        ) -> tuple[WaterDailyReportCandidate, MessagePlanInput | None]:
            render_started = perf_counter()
            async with sem:
                try:
                    message = await self.build_group_report_message(
                        window="yesterday_settled",
                        group_id=candidate.group_id,
                        locale=locale,
                        now_ts=arrow.get(str(candidate.record_date), "YYYYMMDD")
                        .to("Asia/Shanghai")
                        .shift(days=1)
                        .int_timestamp,
                        task=task,
                    )
                    logger.debug(
                        "[Water][ReportPush] group={} score={} msg={} users={} "
                        "stage=render elapsed_ms={:.2f}",
                        candidate.group_id,
                        candidate.activity_score,
                        candidate.total_msg_count,
                        candidate.active_user_count,
                        (perf_counter() - render_started) * 1000,
                    )
                    return candidate, message
                except Exception:
                    logger.exception(
                        "[Water][ReportPush] group={} stage=render failed",
                        candidate.group_id,
                    )
                    return candidate, None

        rendered: list[tuple[WaterDailyReportCandidate, MessagePlanInput | None]] = []
        for completed_count, render_task in enumerate(
            asyncio.as_completed(
                [asyncio.create_task(_render(candidate)) for candidate in candidates]
            ),
            start=1,
        ):
            rendered.append(await render_task)
            if task is not None:
                await task.advance(
                    "rendering_groups",
                    current=completed_count,
                    total=len(candidates),
                )
        rendered_items = [
            (candidate, message)
            for candidate, message in rendered
            if message is not None
        ]
        sent_groups = 0
        failed_groups = 0
        if task is not None and rendered_items:
            await task.advance(
                "sending_groups",
                current=0,
                total=len(rendered_items),
            )
        for candidate, message in rendered_items:
            send_started = perf_counter()
            try:
                await deliver_message_plan(
                    bot,
                    plan=DeliveryPlan(
                        messages=(message,),
                        source_kind="water_daily_report_push",
                    ),
                    target=DeliveryTarget(
                        kind="group",
                        target_id=str(candidate.group_id),
                    ),
                )
                sent_groups += 1
                if task is not None:
                    await task.advance(
                        "sending_groups",
                        current=sent_groups + failed_groups,
                        total=len(rendered_items),
                    )
                logger.debug(
                    "[Water][ReportPush] group={} score={} msg={} users={} "
                    "stage=send elapsed_ms={:.2f}",
                    candidate.group_id,
                    candidate.activity_score,
                    candidate.total_msg_count,
                    candidate.active_user_count,
                    (perf_counter() - send_started) * 1000,
                )
            except Exception:
                failed_groups += 1
                if task is not None:
                    await task.advance(
                        "sending_groups",
                        current=sent_groups + failed_groups,
                        total=len(rendered_items),
                    )
                logger.exception(
                    "[Water][ReportPush] group={} stage=send failed",
                    candidate.group_id,
                )
            await asyncio.sleep(REPORT_PUSH_INTERVAL_SECONDS)

        result = WaterDailyReportBatchResult(
            record_date=target_date,
            candidate_groups=len(candidates),
            rendered_groups=len(rendered_items),
            sent_groups=sent_groups,
            skipped_groups=len(candidates) - len(rendered_items),
            failed_groups=failed_groups,
            total_elapsed_ms=(perf_counter() - started) * 1000,
        )
        logger.info(
            "[Water][ReportPush] done date={} candidates={} rendered={} sent={} "
            "skipped={} failed={} total_ms={:.2f}",
            result.record_date,
            result.candidate_groups,
            result.rendered_groups,
            result.sent_groups,
            result.skipped_groups,
            result.failed_groups,
            result.total_elapsed_ms,
        )
        return result

    async def _get_snapshot(
        self,
        *,
        window: WaterGroupReportWindow,
        group_id: str,
        now_ts: int | None = None,
    ) -> WaterGroupReportSnapshot | None:
        if window == "today_live":
            return await water_repo.get_group_report_live_snapshot(
                group_id=group_id,
                now_ts=now_ts,
            )
        if now_ts is not None:
            record_date = int(
                arrow.get(now_ts).to("Asia/Shanghai").shift(days=-1).format("YYYYMMDD")
            )
        else:
            state = await water_repo.get_settlement_state()
            record_date = int(state["last_success_record_date"])
        if record_date <= 0:
            return None
        return await water_repo.get_group_report_summary_snapshot(
            group_id=group_id,
            record_date=record_date,
        )

    async def _build_card_data(
        self,
        window: WaterGroupReportWindow,
        snapshot: WaterGroupReportSnapshot,
        locale: LocaleCode,
    ) -> WaterGroupReportImageData:
        top_items = await self._build_view_items(snapshot, locale)
        group_rank_snapshot = await self._build_group_rank_snapshot(
            window,
            snapshot,
        )
        group_rank_items = await self._build_group_rank_items(
            group_rank_snapshot,
        )
        group_share_slices = await self._build_group_share_slices(
            window,
            snapshot,
            group_rank_snapshot,
        )
        (
            group_rank_trend_labels,
            group_rank_trend_series,
        ) = await self._build_group_rank_trend_series(
            window,
            snapshot,
            group_rank_snapshot,
        )
        group_rank_metrics = self._build_group_rank_metrics(
            group_rank_snapshot,
            snapshot,
        )
        group_rank_insights = self._build_group_rank_insights(
            group_rank_metrics,
            locale,
        )
        group_rank_has_hidden_before = (
            group_rank_snapshot.has_hidden_before
            if group_rank_snapshot is not None
            else False
        )
        group_rank_has_hidden_after = (
            group_rank_snapshot.has_hidden_after
            if group_rank_snapshot is not None
            else False
        )
        title = (
            tr(locale, "water.report.title.today")
            if window == "today_live"
            else tr(locale, "water.report.title.yesterday")
        )
        record_day = arrow.get(str(snapshot.record_date), "YYYYMMDD").to(
            "Asia/Shanghai"
        )
        range_text = (
            tr(
                locale,
                "water.report.range.today",
                date=record_day.format("YYYY.MM.DD"),
            )
            if window == "today_live"
            else tr(
                locale,
                "water.report.range.yesterday",
                date=record_day.format("YYYY.MM.DD"),
            )
        )
        compare_text = tr(
            locale,
            "water.report.compare",
            prev_date=record_day.shift(days=-1).format("YYYY.MM.DD"),
            msg_delta=self._format_delta(snapshot.delta_total_msg_count),
            user_delta=self._format_delta(snapshot.delta_active_user_count),
        )
        return WaterGroupReportImageData(
            title=title,
            badge="",
            range_text=range_text,
            compare_text=compare_text,
            generated_at=now_ts_or_current(now_ts=None),
            total_msg_count=snapshot.total_msg_count,
            active_user_count=snapshot.active_user_count,
            hourly_counts=snapshot.hourly_counts,
            previous_hourly_counts=snapshot.previous_hourly_counts,
            peak_hour=snapshot.peak_hour,
            previous_total_msg_count=snapshot.previous_total_msg_count,
            top_items=top_items,
            group_rank_title=tr(locale, "water.report.group_rank.title"),
            group_rank_summary=self._build_group_rank_summary(
                group_rank_snapshot,
                locale,
            ),
            group_rank_items=group_rank_items or [],
            group_rank_insights=group_rank_insights,
            group_share_slices=group_share_slices,
            group_rank_trend_labels=group_rank_trend_labels,
            group_rank_trend_series=group_rank_trend_series,
            right_panel_layout_tier=pick_group_report_right_panel_tier(
                user_count=len(top_items),
                rank_item_count=len(group_rank_items or []),
                has_hidden_before=group_rank_has_hidden_before,
                has_hidden_after=group_rank_has_hidden_after,
                has_trend_history=bool(group_rank_trend_series),
            ),
            group_rank_share_ratio=float(group_rank_metrics["share_ratio"] or 0.0),
            group_rank_total_msg_count=int(group_rank_metrics["total_msg_count"] or 0),
            group_rank_focus_msg_count=int(group_rank_metrics["focus_msg_count"] or 0),
            group_rank_prev_gap_msg_count=(
                int(group_rank_metrics["prev_gap_msg_count"])
                if group_rank_metrics["prev_gap_msg_count"] is not None
                else None
            ),
            group_rank_next_gap_msg_count=(
                int(group_rank_metrics["next_gap_msg_count"])
                if group_rank_metrics["next_gap_msg_count"] is not None
                else None
            ),
            group_rank_has_hidden_before=group_rank_has_hidden_before,
            group_rank_has_hidden_after=group_rank_has_hidden_after,
        )

    async def _build_view_items(
        self,
        snapshot: WaterGroupReportSnapshot,
        locale: LocaleCode,
    ) -> list[WaterRankCardItem]:
        names = await asyncio.gather(
            *(
                water_rank_service._resolve_display_name("user", item.user_id, locale)
                for item in snapshot.leaderboard
            )
        )
        secondary_labels = await asyncio.gather(
            *(
                water_rank_service._resolve_secondary_label(
                    "user",
                    item.user_id,
                    0,
                    locale,
                )
                for item in snapshot.leaderboard
            )
        )
        avatars = await asyncio.gather(
            *(
                water_rank_service._resolve_avatar("user", item.user_id)
                for item in snapshot.leaderboard
            ),
            return_exceptions=True,
        )
        return [
            WaterRankCardItem(
                entity_id=item.user_id,
                display_name=names[idx],
                secondary_label=secondary_labels[idx],
                avatar=self._normalize_avatar(avatars[idx]),
                msg_count=item.msg_count,
                active_days=1,
                active_hours=item.active_hours,
                hourly_counts=item.hourly_counts,
                current_rank=item.current_rank,
                trend=item.trend,
            )
            for idx, item in enumerate(snapshot.leaderboard)
        ]

    @staticmethod
    def _normalize_avatar(avatar: object) -> BuildImage | None:
        if isinstance(avatar, BaseException):
            return None
        return avatar if isinstance(avatar, BuildImage) else None

    @staticmethod
    def _format_delta(value: int) -> str:
        if value > 0:
            return f"+{value}"
        return str(value)

    async def _build_group_rank_snapshot(
        self,
        window: WaterGroupReportWindow,
        snapshot: WaterGroupReportSnapshot,
    ) -> WaterGroupDailyRankSnapshot | None:
        return await water_repo.get_group_daily_rank_snapshot(
            group_id=snapshot.group_id,
            record_date=snapshot.record_date,
            radius=4,
            live=window == "today_live",
        )

    async def _build_group_rank_items(
        self,
        snapshot: WaterGroupDailyRankSnapshot | None,
    ) -> list[WaterGroupDailyRankCardItem] | None:
        if snapshot is None:
            return None
        names, avatars = await asyncio.gather(
            asyncio.gather(
                *(
                    resolve_group_name(None, item.group_id)
                    for item in snapshot.leaderboard
                )
            ),
            asyncio.gather(
                *(QQAvatar.fetch_group(item.group_id) for item in snapshot.leaderboard),
                return_exceptions=True,
            ),
        )
        return [
            WaterGroupDailyRankCardItem(
                group_id=item.group_id,
                display_name=names[idx],
                avatar=self._normalize_avatar(avatars[idx]),
                msg_count=item.msg_count,
                current_rank=item.current_rank,
                trend=item.trend,
                is_focus_group=item.group_id == snapshot.focus_group_id,
            )
            for idx, item in enumerate(snapshot.leaderboard)
        ]

    async def _build_group_share_slices(
        self,
        window: WaterGroupReportWindow,
        snapshot: WaterGroupReportSnapshot,
        group_rank_snapshot: WaterGroupDailyRankSnapshot | None,
    ) -> list[WaterGroupShareSlice]:
        distribution_items = await water_repo.get_group_daily_distribution_items(
            record_date=snapshot.record_date,
            live=window == "today_live",
        )
        if not distribution_items:
            return []
        names = await asyncio.gather(
            *(resolve_group_name(None, item.group_id) for item in distribution_items)
        )
        total_msg_count = sum(item.msg_count for item in distribution_items) or 1
        focus_group_id = (
            group_rank_snapshot.focus_group_id
            if group_rank_snapshot is not None
            else snapshot.group_id
        )
        return [
            WaterGroupShareSlice(
                group_id=item.group_id,
                display_name=names[idx],
                msg_count=item.msg_count,
                share_ratio=item.msg_count / total_msg_count,
                is_focus_group=item.group_id == focus_group_id,
            )
            for idx, item in enumerate(distribution_items)
        ]

    async def _build_group_rank_trend_series(
        self,
        window: WaterGroupReportWindow,
        snapshot: WaterGroupReportSnapshot,
        group_rank_snapshot: WaterGroupDailyRankSnapshot | None,
    ) -> tuple[list[str], list[WaterGroupRankTrendSeries]]:
        trend_group_ids = self._select_trend_group_ids(group_rank_snapshot)
        if not trend_group_ids:
            return [], []
        history = await water_repo.get_group_daily_rank_history(
            group_ids=trend_group_ids,
            end_record_date=snapshot.record_date,
            days=30,
            live=window == "today_live",
        )
        if not history:
            return [], []
        first_history = next(iter(history.values()))
        labels = [
            arrow.get(str(record_date), "YYYYMMDD").format("MM/DD")
            for record_date, _rank in first_history
        ]
        names = await asyncio.gather(
            *(resolve_group_name(None, group_id) for group_id in trend_group_ids)
        )
        focus_group_id = (
            group_rank_snapshot.focus_group_id
            if group_rank_snapshot is not None
            else snapshot.group_id
        )
        series = [
            WaterGroupRankTrendSeries(
                group_id=group_id,
                display_name=names[idx],
                ranks=[rank for _record_date, rank in history[group_id]],
                is_focus_group=group_id == focus_group_id,
            )
            for idx, group_id in enumerate(trend_group_ids)
        ]
        return labels, series

    @staticmethod
    def _select_trend_group_ids(
        snapshot: WaterGroupDailyRankSnapshot | None,
        *,
        radius: int = 3,
    ) -> list[str]:
        if snapshot is None or not snapshot.leaderboard:
            return []
        focus_index = next(
            (
                index
                for index, item in enumerate(snapshot.leaderboard)
                if item.group_id == snapshot.focus_group_id
            ),
            None,
        )
        if focus_index is None:
            return []
        window_size = radius * 2 + 1
        start = max(
            0,
            min(focus_index - radius, len(snapshot.leaderboard) - window_size),
        )
        end = min(len(snapshot.leaderboard), start + window_size)
        return [item.group_id for item in snapshot.leaderboard[start:end]]

    def _build_group_rank_summary(
        self,
        snapshot: WaterGroupDailyRankSnapshot | None,
        locale: LocaleCode,
    ) -> str:
        if snapshot is None:
            return ""
        trend_value = snapshot.focus_trend
        if trend_value is None:
            trend_value = max(0, snapshot.total_groups - snapshot.focus_rank)
        trend_text = self._format_delta(trend_value)
        return tr(
            locale,
            "water.report.group_rank.summary",
            rank=snapshot.focus_rank,
            total_groups=snapshot.total_groups,
            trend=trend_text,
        )

    def _build_group_rank_metrics(
        self,
        snapshot: WaterGroupDailyRankSnapshot | None,
        report_snapshot: WaterGroupReportSnapshot,
    ) -> dict[str, int | float | None]:
        empty_metrics: dict[str, int | float | None] = {
            "share_ratio": 0.0,
            "total_msg_count": 0,
            "focus_msg_count": 0,
            "prev_gap_msg_count": None,
            "next_gap_msg_count": None,
            "peak_hour": report_snapshot.peak_hour,
        }
        if snapshot is None or not snapshot.leaderboard:
            return empty_metrics

        focus_index = next(
            (
                index
                for index, item in enumerate(snapshot.leaderboard)
                if item.group_id == snapshot.focus_group_id
            ),
            None,
        )
        if focus_index is None:
            return empty_metrics

        focus_item = snapshot.leaderboard[focus_index]
        previous_item = (
            snapshot.leaderboard[focus_index - 1] if focus_index > 0 else None
        )
        next_item = (
            snapshot.leaderboard[focus_index + 1]
            if focus_index + 1 < len(snapshot.leaderboard)
            else None
        )
        return {
            "share_ratio": (
                focus_item.msg_count / snapshot.total_msg_count
                if snapshot.total_msg_count > 0
                else 0.0
            ),
            "total_msg_count": snapshot.total_msg_count,
            "focus_msg_count": focus_item.msg_count,
            "prev_gap_msg_count": (
                previous_item.msg_count - focus_item.msg_count
                if previous_item is not None
                else None
            ),
            "next_gap_msg_count": (
                focus_item.msg_count - next_item.msg_count
                if next_item is not None
                else None
            ),
            "peak_hour": report_snapshot.peak_hour,
        }

    def _build_group_rank_insights(
        self,
        metrics: dict[str, int | float | None],
        locale: LocaleCode,
    ) -> list[WaterReportInsightItem]:
        share_ratio = float(metrics["share_ratio"] or 0.0)
        peak_hour = int(metrics["peak_hour"] or 0)
        prev_gap_msg_count = metrics["prev_gap_msg_count"]
        next_gap_msg_count = metrics["next_gap_msg_count"]
        return [
            WaterReportInsightItem(
                label=tr(locale, "water.report.group_rank.insight.share"),
                value=f"{share_ratio * 100:.1f}%",
            ),
            WaterReportInsightItem(
                label=tr(locale, "water.report.group_rank.insight.peak_hour"),
                value=f"{peak_hour:02d}:00",
            ),
            WaterReportInsightItem(
                label=tr(locale, "water.report.group_rank.insight.previous_gap"),
                value=(
                    tr(
                        locale,
                        "water.report.group_rank.count",
                        count=int(prev_gap_msg_count),
                    )
                    if prev_gap_msg_count is not None
                    else tr(locale, "water.report.group_rank.insight.top")
                ),
            ),
            WaterReportInsightItem(
                label=tr(locale, "water.report.group_rank.insight.next_gap"),
                value=(
                    tr(
                        locale,
                        "water.report.group_rank.count",
                        count=int(next_gap_msg_count),
                    )
                    if next_gap_msg_count is not None
                    else tr(locale, "water.report.group_rank.insight.bottom")
                ),
            ),
        ]


def now_ts_or_current(now_ts: int | None) -> int:
    return int(now_ts or get_current_time())


water_report_service = WaterReportService()
