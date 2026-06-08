"""活动赛季查询服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.water.database import water_repo
from src.plugins.water.database.repo import WaterActivitySeasonRecord
from src.plugins.water.renderers.season_overview import render_season_overview
from src.repositories import user_repo
from src.services.info import resolve_group_name

SeasonSubject = Literal["personal", "group", "matrix"]
SeasonView = Literal["overview", "score", "rank", "achievement"]


@dataclass(frozen=True)
class SeasonOverviewCardData:
    season: WaterActivitySeasonRecord
    subject: SeasonSubject
    user_id: str
    group_id: str


class SeasonRankService:
    async def build_message(
        self,
        *,
        season: WaterActivitySeasonRecord,
        subject: SeasonSubject,
        view: SeasonView,
        user_id: str,
        group_id: str,
        locale: LocaleCode,
    ) -> str:
        if view == "rank":
            return await self._build_rank_message(
                season=season,
                subject=subject,
                user_id=user_id,
                group_id=group_id,
                locale=locale,
            )
        if view == "score":
            return await self._build_score_message(
                season=season,
                subject=subject,
                user_id=user_id,
                group_id=group_id,
                locale=locale,
            )
        if view == "achievement":
            return await self._build_achievement_message(
                season=season,
                user_id=user_id,
                locale=locale,
            )
        return await self._build_overview_message(
            season=season,
            subject=subject,
            user_id=user_id,
            group_id=group_id,
            locale=locale,
        )

    async def _build_overview_message(
        self,
        *,
        season: WaterActivitySeasonRecord,
        subject: SeasonSubject,
        user_id: str,
        group_id: str,
        locale: LocaleCode,
    ) -> str:
        score = await self._build_score_lines(
            season=season,
            subject=subject,
            user_id=user_id,
            group_id=group_id,
            locale=locale,
        )
        rank = await self._build_rank_lines(
            season=season,
            subject=subject,
            user_id=user_id,
            group_id=group_id,
            locale=locale,
        )
        achievement = await self._build_achievement_lines(
            season=season, user_id=user_id, locale=locale
        )
        return render_season_overview(season, score + rank + achievement, locale)

    async def _build_score_message(
        self,
        *,
        season: WaterActivitySeasonRecord,
        subject: SeasonSubject,
        user_id: str,
        group_id: str,
        locale: LocaleCode,
    ) -> str:
        return render_season_overview(
            season,
            await self._build_score_lines(
                season=season,
                subject=subject,
                user_id=user_id,
                group_id=group_id,
                locale=locale,
            ),
            locale,
        )

    async def _build_rank_message(
        self,
        *,
        season: WaterActivitySeasonRecord,
        subject: SeasonSubject,
        user_id: str,
        group_id: str,
        locale: LocaleCode,
    ) -> str:
        return render_season_overview(
            season,
            await self._build_rank_lines(
                season=season,
                subject=subject,
                user_id=user_id,
                group_id=group_id,
                locale=locale,
            ),
            locale,
        )

    async def _build_achievement_message(
        self,
        *,
        season: WaterActivitySeasonRecord,
        user_id: str,
        locale: LocaleCode,
    ) -> str:
        return render_season_overview(
            season,
            await self._build_achievement_lines(
                season=season,
                user_id=user_id,
                locale=locale,
            ),
            locale,
        )

    async def _build_score_lines(
        self,
        *,
        season: WaterActivitySeasonRecord,
        subject: SeasonSubject,
        user_id: str,
        group_id: str,
        locale: LocaleCode,
    ) -> list[str]:
        if subject == "group":
            rankings = await water_repo.get_group_season_rankings(
                season.start_date,
                season.end_date,
            )
            own = next((item for item in rankings if item.group_id == group_id), None)
            group_name = await resolve_group_name(None, group_id)
            if own is None:
                return [
                    tr(
                        locale,
                        "water.query.season.score.group.empty",
                        name=group_name or group_id,
                    )
                ]
            return [
                tr(
                    locale,
                    "water.query.season.score.group.title",
                    name=group_name or group_id,
                ),
                tr(
                    locale,
                    "water.query.season.score.msg_count",
                    msg_count=own.msg_count,
                ),
                tr(
                    locale,
                    "water.query.season.score.active_days",
                    active_days=own.active_days,
                ),
                tr(
                    locale,
                    "water.query.season.score.active_hours",
                    active_hours=own.active_hours,
                ),
            ]
        if subject == "matrix":
            matrix_id = await water_repo.get_or_create_group_matrix_id(group_id)
            rankings = await water_repo.get_matrix_season_rankings(
                season.start_date,
                season.end_date,
            )
            own = next((item for item in rankings if item.matrix_id == matrix_id), None)
            if own is None:
                return [
                    tr(
                        locale,
                        "water.query.season.score.matrix.empty",
                        name=matrix_id,
                    )
                ]
            return [
                tr(locale, "water.query.season.score.matrix.title", name=matrix_id),
                tr(
                    locale,
                    "water.query.season.score.msg_count",
                    msg_count=own.msg_count,
                ),
                tr(
                    locale,
                    "water.query.season.score.active_days",
                    active_days=own.active_days,
                ),
                tr(
                    locale,
                    "water.query.season.score.active_hours",
                    active_hours=own.active_hours,
                ),
            ]
        rankings = await water_repo.get_user_season_rankings(
            season.start_date,
            season.end_date,
        )
        own = next((item for item in rankings if item.user_id == user_id), None)
        name = await user_repo.get_name_by_uid(user_id)
        if own is None:
            return [
                tr(
                    locale,
                    "water.query.season.score.personal.empty",
                    name=name or user_id,
                )
            ]
        return [
            tr(locale, "water.query.season.score.personal.title", name=name or user_id),
            tr(locale, "water.query.season.score.msg_count", msg_count=own.msg_count),
            tr(
                locale,
                "water.query.season.score.active_days",
                active_days=own.active_days,
            ),
            tr(
                locale,
                "water.query.season.score.active_hours",
                active_hours=own.active_hours,
            ),
        ]

    async def _build_rank_lines(
        self,
        *,
        season: WaterActivitySeasonRecord,
        subject: SeasonSubject,
        user_id: str,
        group_id: str,
        locale: LocaleCode,
    ) -> list[str]:
        if subject == "group":
            rankings = await water_repo.get_group_season_rankings(
                season.start_date,
                season.end_date,
            )
            top = rankings[:10]
            own = next((item for item in rankings if item.group_id == group_id), None)
            names = {
                item.group_id: await resolve_group_name(None, item.group_id)
                for item in top
            }
            lines = [tr(locale, "water.query.season.rank.group.title")]
            for item in top:
                lines.append(
                    tr(
                        locale,
                        "water.query.season.rank.item",
                        rank=item.rank,
                        name=names.get(item.group_id) or item.group_id,
                        msg_count=item.msg_count,
                    )
                )
            if own is not None:
                lines.append(
                    tr(
                        locale,
                        "water.query.season.rank.group.current",
                        rank=own.rank,
                    )
                )
            return lines
        if subject == "matrix":
            rankings = await water_repo.get_matrix_season_rankings(
                season.start_date,
                season.end_date,
            )
            top = rankings[:10]
            matrix_id = await water_repo.get_or_create_group_matrix_id(group_id)
            own = next((item for item in rankings if item.matrix_id == matrix_id), None)
            lines = [tr(locale, "water.query.season.rank.matrix.title")]
            for item in top:
                lines.append(
                    tr(
                        locale,
                        "water.query.season.rank.item",
                        rank=item.rank,
                        name=item.matrix_id,
                        msg_count=item.msg_count,
                    )
                )
            if own is not None:
                lines.append(
                    tr(
                        locale,
                        "water.query.season.rank.matrix.current",
                        rank=own.rank,
                    )
                )
            return lines
        rankings = await water_repo.get_user_season_rankings(
            season.start_date,
            season.end_date,
        )
        top = rankings[:10]
        names = {
            item.user_id: await user_repo.get_name_by_uid(item.user_id) for item in top
        }
        own = next((item for item in rankings if item.user_id == user_id), None)
        lines = [tr(locale, "water.query.season.rank.personal.title")]
        for item in top:
            lines.append(
                tr(
                    locale,
                    "water.query.season.rank.item",
                    rank=item.rank,
                    name=names.get(item.user_id) or item.user_id,
                    msg_count=item.msg_count,
                )
            )
        if own is not None:
            lines.append(
                tr(
                    locale,
                    "water.query.season.rank.personal.current",
                    rank=own.rank,
                )
            )
        return lines

    async def _build_achievement_lines(
        self,
        *,
        season: WaterActivitySeasonRecord,
        user_id: str,
        locale: LocaleCode,
    ) -> list[str]:
        unlocked_items = await water_repo.get_user_achievement_items(user_id)
        unlocked = [
            achievement_id
            for achievement_id, track_type, season_id, _ in unlocked_items
            if track_type == "seasonal" and season_id == season.season_id
        ]
        if not unlocked:
            return [tr(locale, "water.query.season.achievement.empty")]
        return [
            tr(locale, "water.query.season.achievement.title"),
            *[
                tr(
                    locale,
                    "water.query.season.achievement.item",
                    achievement_id=achievement_id,
                )
                for achievement_id in unlocked
            ],
        ]


season_rank_service = SeasonRankService()
