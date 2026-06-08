"""事件驱动成就服务。"""

from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import arrow

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.plugins.water.database import water_repo
from src.plugins.water.database.types import WaterAchievementPayload

AchievementChecker = Callable[[str, str, int, int], Awaitable[bool]]


@dataclass(frozen=True)
class AchievementDef:
    id: str
    name: str
    desc: str
    motivation: str
    context: str
    track_type: Literal["permanent", "seasonal"] = "permanent"


ACHIEVEMENT_RULES: dict[str, AchievementDef] = {
    "FIRST_BLOOD": AchievementDef(
        id="FIRST_BLOOD",
        name="萌新起步",
        desc="首次产生水王流水记录",
        motivation="先发一条消息点亮你的成就墙。",
        context="first message observed",
        track_type="permanent",
    ),
    "NIGHT_OWL": AchievementDef(
        id="NIGHT_OWL",
        name="夜猫子",
        desc="凌晨 2:00-5:00 连续 3 天活跃",
        motivation="连续三晚在线，拿下稀有夜行者徽章。",
        context="night activity streak 3 days",
        track_type="seasonal",
    ),
    "MATRIX_PIONEER": AchievementDef(
        id="MATRIX_PIONEER",
        name="星环先锋",
        desc="全局等级率先达到 Lv10",
        motivation="冲到 Lv10，抢下全服唯一先驱称号。",
        context="first global level 10",
        track_type="permanent",
    ),
    "STEADY_COMPANION": AchievementDef(
        id="STEADY_COMPANION",
        name="长情陪伴",
        desc="单一矩阵连续活跃 30 天",
        motivation="坚持一个月不间断活跃，解锁长期主义徽章。",
        context="30-day matrix streak",
        track_type="seasonal",
    ),
}


class AchievementService:
    def __init__(self) -> None:
        self._checkers: dict[str, str] = {
            "FIRST_BLOOD": "_check_first_blood",
            "NIGHT_OWL": "_check_night_owl",
            "MATRIX_PIONEER": "_check_matrix_pioneer",
            "STEADY_COMPANION": "_check_steady_companion",
        }

    async def check_and_unlock(
        self,
        user_id: str,
        matrix_id: str,
        record_date: int,
        today_msg_count: int,
        season_id: str = "",
    ) -> list[str]:
        unlocked_items = await water_repo.get_user_achievement_items(user_id)
        unlocked_forever = {
            achievement_id
            for achievement_id, track_type, _, _ in unlocked_items
            if track_type == "permanent"
        }
        unlocked_in_season = {
            (achievement_id, season_id)
            for achievement_id, track_type, season_id, _ in unlocked_items
            if track_type == "seasonal"
        }
        now_ts = get_current_time()
        new_unlocks: list[WaterAchievementPayload] = []

        for achievement_id, rule in ACHIEVEMENT_RULES.items():
            if rule.track_type == "permanent" and achievement_id in unlocked_forever:
                continue
            if (
                rule.track_type == "seasonal"
                and season_id
                and (achievement_id, season_id) in unlocked_in_season
            ):
                continue
            checker_name = self._checkers.get(achievement_id)
            if checker_name is None:
                continue
            checker = getattr(self, checker_name, None)
            if checker is None:
                continue
            if await checker(user_id, matrix_id, record_date, today_msg_count):
                new_unlocks.append(
                    {
                        "user_id": user_id,
                        "achievement_id": achievement_id,
                        "track_type": rule.track_type,
                        "season_id": season_id if rule.track_type == "seasonal" else "",
                        "unlocked_at": now_ts,
                        "context": rule.context,
                    }
                )

        if not new_unlocks:
            return []
        await water_repo.unlock_achievements(new_unlocks)
        return [item["achievement_id"] for item in new_unlocks]

    async def build_user_achievement_message(
        self,
        user_id: str,
        matrix_id: str,
        record_date: int,
        locale: LocaleCode,
    ) -> str:
        unlocked_items = await water_repo.get_user_achievement_items(user_id)
        unlocked_permanent = {
            achievement_id
            for achievement_id, track_type, _, _ in unlocked_items
            if track_type == "permanent"
        }
        unlocked_seasonal = {
            achievement_id
            for achievement_id, track_type, season_id, _ in unlocked_items
            if track_type == "seasonal" and season_id
        }
        unlocked_ids = unlocked_permanent | unlocked_seasonal
        total = len(ACHIEVEMENT_RULES)
        unlocked_count = len(unlocked_ids)

        lines = [
            tr(locale, "water.achievement.title"),
            tr(
                locale,
                "water.achievement.unlocked_progress",
                unlocked_count=unlocked_count,
                total=total,
            ),
        ]

        if unlocked_items:
            lines.append(tr(locale, "water.achievement.unlocked.section"))
            for achievement_id, track_type, season_id, unlocked_at in unlocked_items:
                rule = ACHIEVEMENT_RULES.get(achievement_id)
                if rule is None:
                    continue
                unlocked_text = (
                    arrow.get(unlocked_at)
                    .to("Asia/Shanghai")
                    .format("YYYY-MM-DD HH:mm")
                )
                if track_type == "seasonal":
                    tag = tr(
                        locale,
                        "water.achievement.tag.seasonal",
                        season_id=season_id,
                    )
                else:
                    tag = tr(locale, "water.achievement.tag.permanent")
                lines.append(
                    tr(
                        locale,
                        "water.achievement.unlocked.item",
                        name=rule.name,
                        achievement_id=rule.id,
                        tag=tag,
                    )
                )
                lines.append(
                    tr(
                        locale,
                        "water.achievement.unlocked.desc",
                        desc=rule.desc,
                    )
                )
                lines.append(
                    tr(
                        locale,
                        "water.achievement.unlocked.time",
                        unlocked_text=unlocked_text,
                    )
                )
        else:
            lines.extend(
                [
                    tr(locale, "water.achievement.unlocked.section"),
                    tr(locale, "water.achievement.unlocked.none"),
                ]
            )

        locked_ids = [aid for aid in ACHIEVEMENT_RULES if aid not in unlocked_ids]
        lines.append(tr(locale, "water.achievement.next.section"))
        if not locked_ids:
            lines.append(tr(locale, "water.achievement.next.all_done"))
            return "\n".join(lines)

        for achievement_id in locked_ids[:2]:
            rule = ACHIEVEMENT_RULES[achievement_id]
            progress = await self._progress_text(
                achievement_id,
                user_id=user_id,
                matrix_id=matrix_id,
                record_date=record_date,
                locale=locale,
            )
            lines.append(
                tr(
                    locale,
                    "water.achievement.next.item",
                    name=rule.name,
                    achievement_id=rule.id,
                )
            )
            lines.append(tr(locale, "water.achievement.unlocked.desc", desc=rule.desc))
            lines.append(
                tr(
                    locale,
                    "water.achievement.next.progress",
                    progress=progress,
                )
            )
            lines.append(
                tr(
                    locale,
                    "water.achievement.next.motivation",
                    motivation=rule.motivation,
                )
            )

        if len(locked_ids) > 2:
            lines.append(
                tr(
                    locale,
                    "water.achievement.next.remaining",
                    count=len(locked_ids) - 2,
                )
            )
        return "\n".join(lines)

    async def _check_first_blood(
        self,
        user_id: str,
        matrix_id: str,
        record_date: int,
        today_msg_count: int,
    ) -> bool:
        _ = (user_id, matrix_id, record_date)
        return today_msg_count > 0

    async def _check_night_owl(
        self,
        user_id: str,
        matrix_id: str,
        record_date: int,
        today_msg_count: int = 0,
    ) -> bool:
        _ = today_msg_count
        end_day = arrow.get(str(record_date), "YYYYMMDD")
        start_day = end_day.shift(days=-2)
        summaries = await water_repo.get_user_recent_summaries(
            user_id=user_id,
            matrix_id=matrix_id,
            start_date=int(start_day.format("YYYYMMDD")),
            end_date=record_date,
        )
        merged = self._merge_summaries_by_date(summaries)
        if len(merged) < 3:
            return False

        day_to_hourly = merged
        for i in range(3):
            cur_day = int(start_day.shift(days=i).format("YYYYMMDD"))
            if cur_day not in day_to_hourly:
                return False
            hourly = day_to_hourly[cur_day]
            if sum(hourly[2:5]) <= 0:
                return False
        return True

    async def _check_matrix_pioneer(
        self,
        user_id: str,
        matrix_id: str,
        record_date: int,
        today_msg_count: int = 0,
    ) -> bool:
        _ = (matrix_id, record_date, today_msg_count)
        level_info = await water_repo.get_user_global_level(user_id)
        if level_info is None or level_info[2] < 10:
            return False
        has_predecessor = await water_repo.exists_other_global_lv10(user_id)
        return not has_predecessor

    async def _check_steady_companion(
        self,
        user_id: str,
        matrix_id: str,
        record_date: int,
        today_msg_count: int = 0,
    ) -> bool:
        _ = today_msg_count
        end_day = arrow.get(str(record_date), "YYYYMMDD")
        start_day = end_day.shift(days=-29)
        summaries = await water_repo.get_user_recent_summaries(
            user_id=user_id,
            matrix_id=matrix_id,
            start_date=int(start_day.format("YYYYMMDD")),
            end_date=record_date,
        )
        merged = self._merge_summaries_by_date(summaries)
        if len(merged) < 30:
            return False
        summary_days = set(merged)
        for i in range(30):
            cur_day = int(start_day.shift(days=i).format("YYYYMMDD"))
            if cur_day not in summary_days:
                return False
        return True

    async def _progress_text(
        self,
        achievement_id: str,
        user_id: str,
        matrix_id: str,
        record_date: int,
        locale: LocaleCode,
    ) -> str:
        if achievement_id == "FIRST_BLOOD":
            return tr(locale, "water.achievement.progress.first_blood")
        if achievement_id == "NIGHT_OWL":
            streak = await self._night_owl_streak(user_id, matrix_id, record_date)
            return tr(locale, "water.achievement.progress.night_owl", streak=streak)
        if achievement_id == "STEADY_COMPANION":
            streak = await self._steady_streak(user_id, matrix_id, record_date)
            return tr(
                locale,
                "water.achievement.progress.steady_companion",
                streak=streak,
            )
        if achievement_id == "MATRIX_PIONEER":
            level_info = await water_repo.get_user_global_level(user_id)
            lv = level_info[2] if level_info is not None else 0
            if lv >= 10:
                has_predecessor = await water_repo.exists_other_global_lv10(user_id)
                if has_predecessor:
                    return tr(
                        locale,
                        "water.achievement.progress.matrix_pioneer.blocked",
                    )
                return tr(locale, "water.achievement.progress.matrix_pioneer.ready")
            return tr(
                locale,
                "water.achievement.progress.matrix_pioneer.level",
                level=lv,
            )
        return tr(locale, "water.achievement.progress.pending")

    async def _night_owl_streak(
        self,
        user_id: str,
        matrix_id: str,
        record_date: int,
    ) -> int:
        end_day = arrow.get(str(record_date), "YYYYMMDD")
        start_day = end_day.shift(days=-2)
        summaries = await water_repo.get_user_recent_summaries(
            user_id=user_id,
            matrix_id=matrix_id,
            start_date=int(start_day.format("YYYYMMDD")),
            end_date=record_date,
        )
        day_to_night = {
            record_day: sum(hourly[2:5]) > 0
            for record_day, hourly in self._merge_summaries_by_date(summaries).items()
        }

        streak = 0
        for i in range(2, -1, -1):
            day = int(start_day.shift(days=i).format("YYYYMMDD"))
            if day_to_night.get(day, False):
                streak += 1
            else:
                break
        return streak

    async def _steady_streak(
        self,
        user_id: str,
        matrix_id: str,
        record_date: int,
    ) -> int:
        end_day = arrow.get(str(record_date), "YYYYMMDD")
        start_day = end_day.shift(days=-29)
        summaries = await water_repo.get_user_recent_summaries(
            user_id=user_id,
            matrix_id=matrix_id,
            start_date=int(start_day.format("YYYYMMDD")),
            end_date=record_date,
        )
        summary_days = set(self._merge_summaries_by_date(summaries))
        streak = 0
        for i in range(29, -1, -1):
            day = int(start_day.shift(days=i).format("YYYYMMDD"))
            if day in summary_days:
                streak += 1
            else:
                break
        return streak

    @staticmethod
    def _merge_summaries_by_date(
        summaries: Sequence[Any],
    ) -> dict[int, list[int]]:
        merged: dict[int, list[int]] = defaultdict(lambda: [0] * 24)
        for item in summaries:
            hourly = list((item.hourly_counts or [0] * 24)[:24])
            if len(hourly) < 24:
                hourly.extend([0] * (24 - len(hourly)))
            bucket = merged[item.record_date]
            for idx, count in enumerate(hourly):
                bucket[idx] += count
        return dict(merged)


achievement_service = AchievementService()
