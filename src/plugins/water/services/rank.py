"""Water 周期总榜服务。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import ClassVar, Literal

import arrow
from pil_utils import BuildImage

from src.lib.utils.common import get_current_time
from src.plugins.water.database import water_repo
from src.plugins.water.img import (
    WaterPeriodRankCardData,
    WaterPeriodRankUserItem,
    build_water_period_rank_image,
)
from src.repositories import user_repo

PeriodType = Literal["week", "month", "season", "year"]


@dataclass(frozen=True)
class PeriodWindow:
    period: PeriodType
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
        return (
            f"对比区间 {self.previous_start.format('YYYY.MM.DD')}"
            f" - {self.previous_end.format('YYYY.MM.DD')}"
        )


class WaterRankService:
    PERIOD_TITLES: ClassVar[dict[PeriodType, str]] = {
        "week": "水王周榜",
        "month": "水王月榜",
        "season": "水王季榜",
        "year": "水王年榜",
    }

    async def build_period_rank_image(
        self,
        period: PeriodType,
        limit: int = 10,
    ) -> bytes | None:
        data = await self.build_period_rank_data(period, limit=limit)
        if data is None:
            return None
        return await build_water_period_rank_image(data)

    async def build_period_rank_data(
        self,
        period: PeriodType,
        limit: int = 10,
    ) -> WaterPeriodRankCardData | None:
        window = await self._resolve_period_window(period)
        top_users, overview = await asyncio.gather(
            water_repo.get_global_period_leaderboard(
                window.start_date,
                window.end_date,
                window.previous_start_date,
                window.previous_end_date,
                limit=limit,
            ),
            water_repo.get_global_period_overview(
                window.start_date,
                window.end_date,
                window.previous_start_date,
                window.previous_end_date,
            ),
        )
        if not top_users or overview.total_msg_count <= 0:
            return None

        names, avatars = await asyncio.gather(
            asyncio.gather(
                *(self._resolve_user_name(item.user_id) for item in top_users)
            ),
            asyncio.gather(
                *(self._resolve_avatar(item.user_id) for item in top_users),
                return_exceptions=True,
            ),
        )

        user_items: list[WaterPeriodRankUserItem] = []
        for idx, item in enumerate(top_users):
            avatar = avatars[idx]
            if isinstance(avatar, BaseException) or not avatar:
                avatar_image: BuildImage | None = None
            else:
                avatar_image = avatar
            user_items.append(
                WaterPeriodRankUserItem(
                    user_id=item.user_id,
                    username=names[idx],
                    avatar=avatar_image,
                    msg_count=item.msg_count,
                    active_days=item.active_days,
                    active_hours=item.active_hours,
                    hourly_counts=item.hourly_counts,
                    current_rank=item.current_rank,
                    trend=item.trend,
                )
            )

        champion = user_items[0]
        runner_up_count = user_items[1].msg_count if len(user_items) > 1 else 0
        return WaterPeriodRankCardData(
            period=period,
            title=window.title,
            badge=window.badge,
            range_text=window.range_text,
            compare_text=window.compare_text,
            generated_at=get_current_time(),
            total_msg_count=overview.total_msg_count,
            active_user_count=overview.active_user_count,
            hourly_counts=overview.hourly_counts,
            peak_hour=overview.peak_hour,
            previous_total_msg_count=overview.previous_total_msg_count,
            top_users=user_items,
            champion_gap=max(0, champion.msg_count - runner_up_count),
            champion_share=(
                champion.msg_count / overview.total_msg_count
                if overview.total_msg_count > 0
                else 0.0
            ),
        )

    async def build_total_rank_lines(self, limit: int = 10) -> list[str]:
        rows = await water_repo.get_user_season_rankings(0, 99991231)
        if not rows:
            return ["===== 水王总榜 =====", "暂无全历史数据。"]
        top = rows[:limit]
        names = await asyncio.gather(
            *(self._resolve_user_name(item.user_id) for item in top)
        )
        lines = ["===== 水王总榜 ====="]
        for item, name in zip(top, names, strict=False):
            lines.append(
                f"- #{item.rank} {name}: {item.msg_count} 条 / {item.active_days} 天"
            )
        return lines

    async def _resolve_period_window(self, period: PeriodType) -> PeriodWindow:
        state = await water_repo.get_settlement_state()
        last_success = int(state["last_success_record_date"])
        if last_success > 0:
            anchor = arrow.get(str(last_success), "YYYYMMDD").floor("day")
        else:
            anchor = arrow.get(get_current_time()).shift(days=-1).floor("day")

        current_start = self._floor_period(anchor, period)
        day_count = max(1, (anchor.date() - current_start.date()).days + 1)
        previous_end = current_start.shift(days=-1).floor("day")
        previous_start = previous_end.shift(days=-(day_count - 1)).floor("day")
        return PeriodWindow(
            period=period,
            title=self.PERIOD_TITLES[period],
            badge=self._build_badge(anchor, period),
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

    @staticmethod
    async def _resolve_user_name(user_id: str) -> str:
        name = await user_repo.get_name_by_uid(user_id)
        if name:
            return name
        return f"用户_{user_id[-4:]}"

    @staticmethod
    async def _resolve_avatar(user_id: str) -> BuildImage:
        from src.lib.utils.img import QQAvatar

        return await QQAvatar.fetch_user(user_id, size=256)


water_rank_service = WaterRankService()
