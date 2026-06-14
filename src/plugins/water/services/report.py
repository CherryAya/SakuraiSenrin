"""Water 群日报服务。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic, perf_counter
from typing import Literal

import arrow
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11.bot import Bot
from pil_utils import BuildImage

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.logger import logger
from src.plugins.water.database import water_repo
from src.plugins.water.database.repo import (
    WaterDailyReportCandidate,
    WaterGroupReportSnapshot,
)
from src.plugins.water.img import (
    WaterPeriodRankCardData,
    WaterRankCardItem,
    build_water_period_rank_image,
)
from src.plugins.water.services.rank import water_rank_service
from src.repositories import group_repo
from src.services.info import resolve_group_name

WaterGroupReportWindow = Literal["today_live", "yesterday_settled"]

REPORT_ACTIVITY_SCORE_FACTOR = 20
REPORT_ACTIVITY_SCORE_THRESHOLD = 300
REPORT_PUSH_INTERVAL_SECONDS = 8.0
TODAY_REPORT_COOLDOWN_SECONDS = 60
_today_report_group_cooldowns: dict[str, float] = {}


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
        now = monotonic()
        expires_at = _today_report_group_cooldowns.get(group_id, 0.0)
        if expires_at > now:
            remain = max(1, int(expires_at - now))
            return False, remain
        _today_report_group_cooldowns[group_id] = now + TODAY_REPORT_COOLDOWN_SECONDS
        return True, 0

    def clear_today_report_cooldowns(self) -> None:
        _today_report_group_cooldowns.clear()

    async def build_group_report_message(
        self,
        *,
        window: WaterGroupReportWindow,
        group_id: str,
        locale: LocaleCode,
        now_ts: int | None = None,
    ) -> Message:
        snapshot = await self._get_snapshot(
            window=window,
            group_id=group_id,
            now_ts=now_ts,
        )
        if snapshot is None or snapshot.total_msg_count <= 0:
            return Message(tr(locale, "water.report.empty"))
        data = await self._build_card_data(window, snapshot, locale)
        image = await build_water_period_rank_image(data, locale)
        if image is None:
            return Message(tr(locale, "water.report.empty"))
        return Message(MessageSegment.image(image))

    async def run_daily_group_report_push(
        self,
        *,
        bot: Bot,
        locale: LocaleCode = "zh-CN",
        record_date: int | None = None,
    ) -> WaterDailyReportBatchResult:
        started = perf_counter()
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

        async def _render(
            candidate: WaterDailyReportCandidate,
        ) -> tuple[WaterDailyReportCandidate, Message | None]:
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

        rendered = await asyncio.gather(
            *[_render(candidate) for candidate in candidates]
        )
        rendered_items = [
            (candidate, message)
            for candidate, message in rendered
            if message is not None
        ]
        sent_groups = 0
        failed_groups = 0
        for candidate, message in rendered_items:
            send_started = perf_counter()
            try:
                await bot.send_group_msg(
                    group_id=int(candidate.group_id),
                    message=message,
                )
                sent_groups += 1
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
    ) -> WaterPeriodRankCardData:
        top_items = await self._build_view_items(snapshot, locale)
        champion = top_items[0]
        group_name = await resolve_group_name(None, snapshot.group_id)
        title = (
            tr(locale, "water.report.title.today")
            if window == "today_live"
            else tr(locale, "water.report.title.yesterday")
        )
        badge = group_name
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
        )
        return WaterPeriodRankCardData(
            period="total",
            title=f"{group_name} · {title}",
            badge=badge,
            range_text=range_text,
            compare_text=compare_text,
            generated_at=now_ts_or_current(now_ts=None),
            total_msg_count=snapshot.total_msg_count,
            active_entity_count=snapshot.active_user_count,
            hourly_counts=snapshot.hourly_counts,
            peak_hour=snapshot.peak_hour,
            previous_total_msg_count=snapshot.previous_total_msg_count,
            top_items=top_items,
            champion_gap=max(
                0,
                champion.msg_count
                - (top_items[1].msg_count if len(top_items) > 1 else 0),
            ),
            champion_share=(
                champion.msg_count / snapshot.total_msg_count
                if snapshot.total_msg_count > 0
                else 0.0
            ),
            entity_label=tr(locale, "water.report.entity_label"),
            champion_summary_label=tr(locale, "water.report.champion.summary"),
            board_title=tr(locale, "water.report.board.title"),
            board_summary_label=tr(locale, "water.report.board.summary"),
            board_active_hours_label=tr(locale, "water.report.board.active_hours"),
            overview_title=tr(locale, "water.report.overview.title"),
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


def now_ts_or_current(now_ts: int | None) -> int:
    return int(now_ts or get_current_time())


water_report_service = WaterReportService()
