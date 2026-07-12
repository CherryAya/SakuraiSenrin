"""活动赛季成就视图服务。"""

from __future__ import annotations

from dataclasses import dataclass

from src.lib.i18n.types import LocaleCode
from src.plugins.water.services.achievement import ACHIEVEMENT_RULES


@dataclass(frozen=True)
class SeasonAchievementSummary:
    season_id: str
    unlocked: list[str]
    available: list[str]


class SeasonAchievementService:
    async def build_summary(
        self,
        *,
        season_id: str,
        unlocked_items: list[tuple[str, str, str, int]],
        locale: LocaleCode = "zh-CN",
    ) -> SeasonAchievementSummary:
        unlocked = [
            achievement_id
            for achievement_id, track_type, item_season_id, _ in unlocked_items
            if track_type == "seasonal" and item_season_id == season_id
        ]
        available = [
            rule.name(locale)
            for rule in ACHIEVEMENT_RULES.values()
            if rule.track_type == "seasonal"
        ]
        return SeasonAchievementSummary(
            season_id=season_id,
            unlocked=unlocked,
            available=available,
        )


season_achievement_service = SeasonAchievementService()
