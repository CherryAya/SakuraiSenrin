"""Water administrative state operations."""

from typing import cast

from sqlalchemy import CursorResult, delete, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.lib.db.ops import BaseOps

from .tables import (
    WaterActivitySeason,
    WaterMatrixMergeState,
    WaterPenaltyLog,
    WaterSettlementJob,
    WaterUserAchievement,
)
from .types import (
    WaterAchievementPayload,
    WaterActivitySeasonPayload,
    WaterMatrixMergeStatePayload,
    WaterPenaltyPayload,
    WaterSettlementJobPayload,
)


class WaterPenaltyOps(BaseOps[WaterPenaltyLog]):
    async def insert_penalty_logs(self, data: list[WaterPenaltyPayload]) -> int:
        if not data:
            return 0
        stmt = sqlite_insert(WaterPenaltyLog).values(data)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_penalty_by_id(self, penalty_id: int) -> WaterPenaltyLog | None:
        return await self.session.get(WaterPenaltyLog, penalty_id)

    async def get_user_penalties_by_date(
        self,
        user_id: str,
        record_date: int,
    ) -> list[WaterPenaltyLog]:
        stmt = select(WaterPenaltyLog).where(
            WaterPenaltyLog.user_id == user_id,
            WaterPenaltyLog.record_date == record_date,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def revoke_penalty(self, penalty_id: int, revoked_at: int) -> int:
        stmt = (
            update(WaterPenaltyLog)
            .where(WaterPenaltyLog.id == penalty_id, WaterPenaltyLog.is_revoked == 0)
            .values(is_revoked=1, revoked_at=revoked_at, updated_at=revoked_at)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount


class WaterSettlementJobOps(BaseOps[WaterSettlementJob]):
    async def ensure_job(self, payload: WaterSettlementJobPayload) -> int:
        stmt = sqlite_insert(WaterSettlementJob).values(payload)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[WaterSettlementJob.record_date]
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_job(self, record_date: int) -> WaterSettlementJob | None:
        return await self.session.get(WaterSettlementJob, record_date)

    async def try_start_job(
        self,
        record_date: int,
        now_ts: int,
        stale_after: int,
        force: bool = False,
    ) -> bool:
        await self.ensure_job(
            {
                "record_date": record_date,
                "status": "pending",
                "started_at": 0,
                "finished_at": 0,
                "error": "",
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        stale_before = max(0, now_ts - stale_after)
        if force:
            stmt = (
                update(WaterSettlementJob)
                .where(WaterSettlementJob.record_date == record_date)
                .values(
                    status="running",
                    started_at=now_ts,
                    finished_at=0,
                    error="",
                    updated_at=now_ts,
                )
            )
        else:
            stmt = (
                update(WaterSettlementJob)
                .where(
                    WaterSettlementJob.record_date == record_date,
                    WaterSettlementJob.status != "success",
                    (
                        (WaterSettlementJob.status.in_(["pending", "failed"]))
                        | (
                            (WaterSettlementJob.status == "running")
                            & (WaterSettlementJob.started_at <= stale_before)
                        )
                    ),
                )
                .values(
                    status="running",
                    started_at=now_ts,
                    finished_at=0,
                    error="",
                    updated_at=now_ts,
                )
            )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0

    async def mark_success(self, record_date: int, now_ts: int) -> int:
        stmt = (
            update(WaterSettlementJob)
            .where(WaterSettlementJob.record_date == record_date)
            .values(
                status="success",
                finished_at=now_ts,
                error="",
                updated_at=now_ts,
            )
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def mark_failed(self, record_date: int, now_ts: int, error: str) -> int:
        stmt = (
            update(WaterSettlementJob)
            .where(WaterSettlementJob.record_date == record_date)
            .values(
                status="failed",
                finished_at=now_ts,
                error=error[:2000],
                updated_at=now_ts,
            )
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_latest_job(self) -> WaterSettlementJob | None:
        stmt = (
            select(WaterSettlementJob)
            .order_by(WaterSettlementJob.record_date.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def get_last_success_record_date(self) -> int:
        stmt = select(func.max(WaterSettlementJob.record_date)).where(
            WaterSettlementJob.status == "success"
        )
        result = await self.session.execute(stmt)
        return int(result.scalar() or 0)


class WaterMatrixMergeStateOps(BaseOps[WaterMatrixMergeState]):
    async def ensure_row(self, payload: WaterMatrixMergeStatePayload) -> int:
        stmt = sqlite_insert(WaterMatrixMergeState).values(payload)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[WaterMatrixMergeState.group_id]
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_state(self, group_id: str) -> WaterMatrixMergeState | None:
        return await self.session.get(WaterMatrixMergeState, group_id)

    async def mark_first_seen(self, group_id: str, now_ts: int) -> bool:
        await self.ensure_row(
            {
                "group_id": group_id,
                "first_seen_at": None,
                "is_ignored": 0,
                "status": "",
                "target_matrix_id": "",
                "operator_id": "",
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        stmt = (
            update(WaterMatrixMergeState)
            .where(
                WaterMatrixMergeState.group_id == group_id,
                WaterMatrixMergeState.first_seen_at.is_(None),
            )
            .values(first_seen_at=now_ts, updated_at=now_ts)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0

    async def get_first_seen_groups(self) -> set[str]:
        stmt = select(WaterMatrixMergeState.group_id).where(
            WaterMatrixMergeState.first_seen_at.is_not(None)
        )
        result = await self.session.execute(stmt)
        return {str(group_id) for group_id in result.scalars().all()}

    async def set_ignored(self, group_id: str, now_ts: int) -> bool:
        await self.ensure_row(
            {
                "group_id": group_id,
                "first_seen_at": None,
                "is_ignored": 0,
                "status": "",
                "target_matrix_id": "",
                "operator_id": "",
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        stmt = (
            update(WaterMatrixMergeState)
            .where(
                WaterMatrixMergeState.group_id == group_id,
                WaterMatrixMergeState.is_ignored == 0,
            )
            .values(is_ignored=1, updated_at=now_ts)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0

    async def get_ignored_groups(self) -> set[str]:
        stmt = select(WaterMatrixMergeState.group_id).where(
            WaterMatrixMergeState.is_ignored == 1
        )
        result = await self.session.execute(stmt)
        return {str(group_id) for group_id in result.scalars().all()}

    async def set_pending_target(
        self,
        group_id: str,
        target_matrix_id: str,
        now_ts: int,
    ) -> int:
        await self.ensure_row(
            {
                "group_id": group_id,
                "first_seen_at": None,
                "is_ignored": 0,
                "status": "",
                "target_matrix_id": "",
                "operator_id": "",
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        stmt = (
            update(WaterMatrixMergeState)
            .where(WaterMatrixMergeState.group_id == group_id)
            .values(
                status="pending",
                target_matrix_id=target_matrix_id,
                operator_id="",
                updated_at=now_ts,
            )
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def set_intention_once(
        self,
        group_id: str,
        action: str,
        operator_id: str,
        now_ts: int,
        target_matrix_id: str | None = None,
    ) -> bool:
        await self.ensure_row(
            {
                "group_id": group_id,
                "first_seen_at": None,
                "is_ignored": 0,
                "status": "",
                "target_matrix_id": "",
                "operator_id": "",
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        values = {
            "status": action,
            "operator_id": operator_id,
            "updated_at": now_ts,
        }
        if target_matrix_id is not None:
            values["target_matrix_id"] = target_matrix_id

        stmt = (
            update(WaterMatrixMergeState)
            .where(
                WaterMatrixMergeState.group_id == group_id,
                ~WaterMatrixMergeState.status.in_(["merge", "reject"]),
            )
            .values(**values)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0


class WaterAchievementOps(BaseOps[WaterUserAchievement]):
    async def get_unlocked_items(
        self, user_id: str
    ) -> list[tuple[str, str, str, int]]:
        stmt = (
            select(
                WaterUserAchievement.achievement_id,
                WaterUserAchievement.track_type,
                WaterUserAchievement.season_id,
                WaterUserAchievement.unlocked_at,
            )
            .where(WaterUserAchievement.user_id == user_id)
            .order_by(WaterUserAchievement.unlocked_at.asc())
        )
        result = await self.session.execute(stmt)
        return [
            (
                str(achievement_id),
                str(track_type),
                str(season_id),
                int(unlocked_at),
            )
            for achievement_id, track_type, season_id, unlocked_at in result.all()
        ]

    async def bulk_unlock(self, data: list[WaterAchievementPayload]) -> int:
        if not data:
            return 0
        stmt = sqlite_insert(WaterUserAchievement).values(data)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[
                WaterUserAchievement.user_id,
                WaterUserAchievement.achievement_id,
                WaterUserAchievement.season_id,
            ]
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount


class WaterActivitySeasonOps(BaseOps[WaterActivitySeason]):
    async def create(self, payload: WaterActivitySeasonPayload) -> int:
        stmt = sqlite_insert(WaterActivitySeason).values(payload)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_by_season_id(self, season_id: str) -> WaterActivitySeason | None:
        return await self.session.get(WaterActivitySeason, season_id)

    async def update(
        self,
        season_id: str,
        **values: object,
    ) -> int:
        stmt = (
            update(WaterActivitySeason)
            .where(WaterActivitySeason.season_id == season_id)
            .values(**values)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def delete(self, season_id: str) -> int:
        stmt = delete(WaterActivitySeason).where(
            WaterActivitySeason.season_id == season_id
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def list_by_status(
        self,
        statuses: list[str] | None = None,
    ) -> list[WaterActivitySeason]:
        stmt = select(WaterActivitySeason)
        if statuses:
            stmt = stmt.where(WaterActivitySeason.status.in_(statuses))
        stmt = stmt.order_by(
            WaterActivitySeason.start_date.desc(),
            WaterActivitySeason.season_id.asc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_current_published(self, today: int) -> list[WaterActivitySeason]:
        stmt = (
            select(WaterActivitySeason)
            .where(
                WaterActivitySeason.status == "published",
                WaterActivitySeason.start_date <= today,
                WaterActivitySeason.end_date >= today,
            )
            .order_by(
                WaterActivitySeason.start_date.asc(),
                WaterActivitySeason.season_id.asc(),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def search_published_candidates(
        self, keyword: str
    ) -> list[WaterActivitySeason]:
        like_pattern = f"%{keyword}%"
        stmt = (
            select(WaterActivitySeason)
            .where(
                WaterActivitySeason.status == "published",
                or_(
                    WaterActivitySeason.season_id == keyword,
                    WaterActivitySeason.name == keyword,
                    WaterActivitySeason.normalized_name == keyword,
                    WaterActivitySeason.season_id.like(like_pattern),
                    WaterActivitySeason.name.like(like_pattern),
                    WaterActivitySeason.normalized_name.like(like_pattern),
                ),
            )
            .order_by(
                WaterActivitySeason.start_date.desc(),
                WaterActivitySeason.season_id.asc(),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
