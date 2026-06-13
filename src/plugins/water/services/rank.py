"""Water 自然榜单服务。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import ClassVar, Literal, cast

import arrow
from pil_utils import BuildImage

from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.logger import logger
from src.plugins.water.database import water_repo
from src.plugins.water.database.repo import NaturalRankItem
from src.plugins.water.img import (
    WaterPeriodRankCardData,
    WaterRankCardItem,
    build_water_period_rank_image,
)
from src.plugins.water.services.rank_types import (
    PERIOD_LABELS,
    SCOPE_LABELS,
    SUBJECT_LABELS,
    WaterRankPeriod,
    WaterRankScope,
    WaterRankSubject,
)
from src.repositories import user_repo
from src.services.info import resolve_group_name

PeriodType = Literal["week", "month", "season", "year"]


@dataclass(frozen=True)
class RankWindow:
    period: WaterRankPeriod
    locale: LocaleCode
    title: str
    badge: str
    current_start: arrow.Arrow
    current_end: arrow.Arrow
    previous_start: arrow.Arrow
    previous_end: arrow.Arrow
    anchor_day: arrow.Arrow

    @property
    def start_date(self) -> int:
        return int(self.current_start.format("YYYYMMDD"))

    @property
    def end_date(self) -> int:
        return int(self.current_end.format("YYYYMMDD"))

    @property
    def previous_start_date(self) -> int:
        return int(self.previous_start.format("YYYYMMDD"))

    @property
    def previous_end_date(self) -> int:
        return int(self.previous_end.format("YYYYMMDD"))

    @property
    def range_text(self) -> str:
        return (
            f"{self.current_start.format('YYYY.MM.DD')}"
            f" - {self.current_end.format('YYYY.MM.DD')}"
        )

    @property
    def compare_text(self) -> str:
        return tr(
            self.locale,
            "water.rank.compare_text",
            start=self.previous_start.format("YYYY.MM.DD"),
            end=self.previous_end.format("YYYY.MM.DD"),
        )


class WaterRankService:
    PERIOD_TITLE_KEYS: ClassVar[dict[PeriodType, MessageKey]] = {
        "week": "water.rank.title.week",
        "month": "water.rank.title.month",
        "season": "water.rank.title.season",
        "year": "water.rank.title.year",
    }

    async def build_period_rank_image(
        self,
        period: PeriodType,
        locale: LocaleCode,
        limit: int = 10,
    ) -> bytes | None:
        data = await self.build_natural_period_rank_data(
            subject="user",
            scope="global",
            period=period,
            group_id="",
            locale=locale,
            limit=limit,
        )
        if data is None:
            return None
        return await build_water_period_rank_image(data, locale)

    async def build_natural_period_rank_data(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        period: WaterRankPeriod,
        group_id: str,
        locale: LocaleCode,
        limit: int = 10,
    ) -> WaterPeriodRankCardData | None:
        combo = f"{subject}/{scope}/{period}"
        total_started = perf_counter()
        window_started = perf_counter()
        window = await self._resolve_period_window(
            subject=subject,
            scope=scope,
            period=period,
            group_id=group_id,
            locale=locale,
        )
        window_elapsed = (perf_counter() - window_started) * 1000
        query_started = perf_counter()
        snapshot = await water_repo.get_natural_period_snapshot(
            subject=subject,
            scope=scope,
            group_id=group_id,
            start_date=window.start_date,
            end_date=window.end_date,
            previous_start_date=window.previous_start_date,
            previous_end_date=window.previous_end_date,
            limit=limit,
        )
        query_elapsed = (perf_counter() - query_started) * 1000
        top_items = snapshot.leaderboard
        overview = snapshot.overview
        if not top_items or overview.total_msg_count <= 0:
            logger.debug(
                "[Water][RankData] combo={} window_ms={:.2f} query_ms={:.2f} "
                "empty=1 start={} end={}",
                combo,
                window_elapsed,
                query_elapsed,
                window.start_date,
                window.end_date,
            )
            return None

        hydrate_started = perf_counter()
        view_items = await self._build_view_items(subject, top_items, locale)
        hydrate_elapsed = (perf_counter() - hydrate_started) * 1000
        champion = view_items[0]
        runner_up_count = view_items[1].msg_count if len(view_items) > 1 else 0
        display_meta = self._build_display_meta(subject, scope)
        normalized_period = cast(
            Literal["week", "month", "season", "year", "total"],
            "total" if period == "total" else period,
        )
        title = (
            f"{SUBJECT_LABELS[subject]} · {SCOPE_LABELS[scope]}{PERIOD_LABELS[period]}"
        )
        card_data = WaterPeriodRankCardData(
            period=normalized_period,
            title=title,
            badge=window.badge,
            range_text=window.range_text,
            compare_text=window.compare_text,
            generated_at=get_current_time(),
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
        logger.debug(
            "[Water][RankData] combo={} window_ms={:.2f} query_ms={:.2f} "
            "hydrate_ms={:.2f} total_ms={:.2f} rows={} active={}",
            combo,
            window_elapsed,
            query_elapsed,
            hydrate_elapsed,
            (perf_counter() - total_started) * 1000,
            len(top_items),
            overview.active_entity_count,
        )
        return card_data

    async def build_total_rank_lines(
        self,
        locale: LocaleCode,
        limit: int = 10,
    ) -> list[str]:
        data = await self.build_natural_period_rank_data(
            subject="user",
            scope="global",
            period="total",
            group_id="",
            locale=locale,
            limit=limit,
        )
        if data is None:
            return [
                tr(locale, "water.rank.total.title"),
                tr(locale, "water.rank.total.empty"),
            ]
        lines = [tr(locale, "water.rank.total.title")]
        for item in data.top_items:
            lines.append(
                tr(
                    locale,
                    "water.rank.total.item",
                    rank=item.current_rank,
                    name=item.display_name,
                    msg_count=item.msg_count,
                    active_days=item.active_days,
                )
            )
        return lines

    async def _resolve_period_window(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        period: WaterRankPeriod,
        group_id: str,
        locale: LocaleCode,
    ) -> RankWindow:
        state = await water_repo.get_settlement_state()
        last_success = int(state["last_success_record_date"])
        if last_success > 0:
            anchor = arrow.get(str(last_success), "YYYYMMDD").floor("day")
        else:
            anchor = (
                arrow.get(get_current_time())
                .to("Asia/Shanghai")
                .shift(days=-1)
                .floor("day")
            )

        if period == "total":
            first_date = await water_repo.get_first_summary_record_date(
                subject=subject,
                scope=scope,
                group_id=group_id,
            )
            if first_date is None:
                current_start = anchor
            else:
                current_start = arrow.get(str(first_date), "YYYYMMDD").floor("day")
            day_count = max(1, (anchor.date() - current_start.date()).days + 1)
            previous_end = current_start.shift(days=-1).floor("day")
            previous_start = previous_end.shift(days=-(day_count - 1)).floor("day")
            return RankWindow(
                period=period,
                locale=locale,
                title=f"{SUBJECT_LABELS[subject]} · {SCOPE_LABELS[scope]}总榜",
                badge=f"{day_count}d",
                current_start=current_start,
                current_end=anchor,
                previous_start=previous_start,
                previous_end=previous_end,
                anchor_day=anchor,
            )

        period_key = cast(PeriodType, period)
        current_start = self._floor_period(anchor, period_key)
        day_count = max(1, (anchor.date() - current_start.date()).days + 1)
        previous_end = current_start.shift(days=-1).floor("day")
        previous_start = previous_end.shift(days=-(day_count - 1)).floor("day")
        return RankWindow(
            period=period,
            locale=locale,
            title=tr(locale, self.PERIOD_TITLE_KEYS[period_key]),
            badge=self._build_badge(anchor, period_key),
            current_start=current_start,
            current_end=anchor,
            previous_start=previous_start,
            previous_end=previous_end,
            anchor_day=anchor,
        )

    @staticmethod
    def _floor_period(day: arrow.Arrow, period: PeriodType) -> arrow.Arrow:
        if period == "week":
            return day.floor("week")
        if period == "month":
            return day.floor("month")
        if period == "year":
            return day.floor("year")

        quarter = (day.month - 1) // 3
        return day.shift(months=-(day.month - (quarter * 3 + 1))).floor("month")

    @staticmethod
    def _build_badge(day: arrow.Arrow, period: PeriodType) -> str:
        if period == "week":
            week = day.isocalendar()[1]
            return f"{day.year} W{week:02d}"
        if period == "month":
            return day.format("YYYY.MM")
        if period == "year":
            return day.format("YYYY")
        quarter = (day.month - 1) // 3 + 1
        return f"{day.year} S{quarter}"

    async def _build_view_items(
        self,
        subject: WaterRankSubject,
        items: list[NaturalRankItem],
        locale: LocaleCode,
    ) -> list[WaterRankCardItem]:
        started = perf_counter()
        names, secondary_labels, avatars = await asyncio.gather(
            asyncio.gather(
                *(
                    self._resolve_display_name(subject, item.entity_id, locale)
                    for item in items
                )
            ),
            asyncio.gather(
                *(
                    self._resolve_secondary_label(
                        subject,
                        item.entity_id,
                        item.group_count,
                        locale,
                    )
                    for item in items
                )
            ),
            asyncio.gather(
                *(self._resolve_avatar(subject, item.entity_id) for item in items),
                return_exceptions=True,
            ),
        )
        view_items: list[WaterRankCardItem] = []
        for idx, item in enumerate(items):
            avatar = avatars[idx]
            avatar_image: BuildImage | None = None
            if not isinstance(avatar, BaseException):
                avatar_image = avatar
            view_items.append(
                WaterRankCardItem(
                    entity_id=item.entity_id,
                    display_name=names[idx],
                    secondary_label=secondary_labels[idx],
                    avatar=avatar_image,
                    msg_count=item.msg_count,
                    active_days=item.active_days,
                    active_hours=item.active_hours,
                    hourly_counts=item.hourly_counts,
                    current_rank=item.current_rank,
                    trend=item.trend,
                    group_count=item.group_count,
                )
            )
        logger.debug(
            "[Water][RankHydrate] subject={} items={} elapsed_ms={:.2f}",
            subject,
            len(items),
            (perf_counter() - started) * 1000,
        )
        return view_items

    def _build_display_meta(
        self,
        subject: WaterRankSubject,
        scope: WaterRankScope,
    ) -> dict[str, str]:
        _ = scope
        if subject == "group":
            return {
                "entity_label": "群聊",
                "champion_summary_label": (
                    "总消息 {msg_count} 条 · 活跃 {active_days} 天"
                ),
                "board_title": "TOP 10 群聊榜",
                "board_summary_label": "{msg_count} 条 · 活跃 {active_days} 天",
                "board_active_hours_label": "活跃时段覆盖 {active_hours} 小时",
                "overview_title": "群聊活跃画像",
            }
        if subject == "matrix":
            return {
                "entity_label": "矩阵",
                "champion_summary_label": (
                    "总消息 {msg_count} 条 · "
                    "活跃 {active_days} 天 · 覆盖 {group_count} 群"
                ),
                "board_title": "TOP 10 矩阵榜",
                "board_summary_label": (
                    "{msg_count} 条 · {active_days} 天 · 覆盖 {group_count} 群"
                ),
                "board_active_hours_label": "活跃时段覆盖 {active_hours} 小时",
                "overview_title": "矩阵活跃画像",
            }
        return {
            "entity_label": "用户",
            "champion_summary_label": "总消息 {msg_count} 条 · 活跃 {active_days} 天",
            "board_title": "TOP 10 用户榜",
            "board_summary_label": (
                "{msg_count} 条 · {active_days} 天 · 日均 {avg_daily}"
            ),
            "board_active_hours_label": "活跃时段覆盖 {active_hours} 小时",
            "overview_title": "用户活跃画像",
        }

    @staticmethod
    async def _resolve_display_name(
        subject: WaterRankSubject,
        entity_id: str,
        locale: LocaleCode,
    ) -> str:
        if subject == "user":
            name = await user_repo.get_name_by_uid(entity_id)
            if name:
                return name
            return tr(locale, "water.rank.user_fallback", tail=entity_id[-4:])
        if subject == "group":
            return await resolve_group_name(None, entity_id)
        return entity_id

    @staticmethod
    async def _resolve_secondary_label(
        subject: WaterRankSubject,
        entity_id: str,
        group_count: int,
        locale: LocaleCode,
    ) -> str:
        if subject == "group":
            return f"群号 {entity_id}"
        if subject == "matrix":
            return f"矩阵 {entity_id} · {group_count} 群"
        return tr(locale, "water.image.day_rank.member_fallback", tail=entity_id[-4:])

    @staticmethod
    async def _resolve_avatar(
        subject: WaterRankSubject,
        entity_id: str,
    ) -> BuildImage:
        from src.lib.utils.img import QQAvatar

        if subject == "group":
            return await QQAvatar.fetch_group(entity_id)
        if subject == "user":
            return await QQAvatar.fetch_user(entity_id, size=256)
        raise RuntimeError("matrix has no remote avatar")


water_rank_service = WaterRankService()
