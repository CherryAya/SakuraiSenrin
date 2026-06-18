"""Water repository settlement and administrative helpers."""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING

import arrow
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.manager import db_manager
from src.lib.utils.common import get_current_time, split_list

from .instances import water_core_db, water_message, water_summary
from .ops import (
    WaterAchievementOps,
    WaterActivitySeasonOps,
    WaterGroupMatrixMapOps,
    WaterGroupStatsOps,
    WaterLevelOps,
    WaterMatrixMergeStateOps,
    WaterMessageOps,
    WaterPenaltyOps,
    WaterSettlementJobOps,
    WaterSummaryOps,
)
from .tables import (
    WaterActivitySeason,
    WaterCoreBase,
    WaterDailySummary,
    WaterGlobalLevel,
    WaterGroupMatrixMap,
    WaterGroupTotal,
    WaterGroupUserTotal,
    WaterMatrixLevel,
    WaterMatrixMergeState,
    WaterMatrixTotalLevel,
    WaterPenaltyLog,
    WaterSettlementJob,
    WaterUserAchievement,
)
from .types import (
    WaterAchievementPayload,
    WaterActivitySeasonPayload,
    WaterGroupTotalPayload,
    WaterGroupUserTotalPayload,
    WaterMatrixExpPayload,
    WaterPenaltyPayload,
    WaterSummaryPayload,
    WaterSummaryRecord,
    WaterUserExpPayload,
)

if TYPE_CHECKING:
    from .repo import DailyAggregateItem, SeasonGroupAggregate, SeasonMatrixAggregate, SeasonUserAggregate, WaterActivitySeasonRecord


def _repo_module():
    from . import repo as repo_module
    return repo_module


class _WaterRepositoryAdminSupport:
    async def get_or_create_group_matrix_ids(
        self,
        group_ids: list[str],
    ) -> dict[str, str]: ...

    async def save_summary_batch(
        self,
        summaries: list[WaterSummaryPayload],
    ) -> None: ...

    @staticmethod
    def _to_season_record(row: WaterActivitySeason) -> "WaterActivitySeasonRecord": ...

    @staticmethod
    def _hot_summary_start_date(today_ts: int | None = None) -> int: ...

    async def _fetch_archived_summaries_in_window(
        self,
        start_date: int,
        end_date: int,
        *,
        group_ids: list[str] | None = None,
        user_id: str | None = None,
        preserve_order: bool = True,
    ) -> list[WaterSummaryRecord]: ...

    @staticmethod
    def _previous_date(record_date: int) -> int: ...

    @staticmethod
    def _merge_summary_records(
        *groups: Sequence[WaterSummaryRecord],
    ) -> list[WaterSummaryRecord]: ...

    @staticmethod
    def _build_user_season_rank(
        summaries: Sequence[WaterSummaryRecord],
    ) -> list["SeasonUserAggregate"]: ...

    @staticmethod
    def _build_group_season_rank(
        summaries: Sequence[WaterSummaryRecord],
    ) -> list["SeasonGroupAggregate"]: ...

    async def _build_matrix_season_rank(
        self,
        summaries: Sequence[WaterSummaryRecord],
    ) -> list["SeasonMatrixAggregate"]: ...

    def _get_merge_state_lock(self, group_id: str) -> asyncio.Lock: ...

    async def get_or_create_group_matrix_id(self, group_id: str) -> str: ...

    async def map_group_to_matrix(self, group_id: str, matrix_id: str) -> None: ...


if TYPE_CHECKING:
    _WaterRepositoryAdminMixinBase = _WaterRepositoryAdminSupport
else:
    _WaterRepositoryAdminMixinBase = object


class WaterRepositoryAdminMixin(_WaterRepositoryAdminMixinBase):
    async def collect_daily_aggregates(
        self,
        target_date: arrow.Arrow,
    ) -> list["DailyAggregateItem"]:
        day_start = target_date.floor("day")
        day_end = target_date.ceil("day")
        start_ts = day_start.int_timestamp
        end_ts = day_end.int_timestamp

        async def _stats_in_shard(
            session: AsyncSession,
        ):
            return await WaterMessageOps(session).aggregate_daily_stats(
                start_ts,
                end_ts,
            )

        async def _hourly_in_shard(
            session: AsyncSession,
        ):
            return await WaterMessageOps(session).aggregate_daily_hourly_stats(
                start_ts,
                end_ts,
            )

        from src.lib.db.connectors import ColdPolicy

        stats_per_shard = await water_message.map_reduce(
            day_start.datetime,
            day_end.datetime,
            _stats_in_shard,
            cold_policy=ColdPolicy.HYDRATE,
        )
        hourly_per_shard = await water_message.map_reduce(
            day_start.datetime,
            day_end.datetime,
            _hourly_in_shard,
            cold_policy=ColdPolicy.HYDRATE,
        )

        merged_stats: dict[tuple[str, str], tuple[int, int]] = {}
        for shard_rows in stats_per_shard:
            for group_id, user_id, msg_count, active_hours in shard_rows:
                merged_stats[(group_id, user_id)] = (msg_count, active_hours)

        merged_hourly: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * 24)
        for shard_rows in hourly_per_shard:
            for group_id, user_id, hour, count in shard_rows:
                merged_hourly[(group_id, user_id)][hour] += count

        group_ids = sorted({group_id for group_id, _ in merged_stats})
        group_matrix_map = await self.get_or_create_group_matrix_ids(group_ids)

        from .repo import DailyAggregateItem

        return [
            DailyAggregateItem(
                matrix_id=group_matrix_map[group_id],
                group_id=group_id,
                user_id=user_id,
                msg_count=msg_count,
                active_hours=active_hours,
                hourly_counts=merged_hourly[(group_id, user_id)],
            )
            for (group_id, user_id), (
                msg_count,
                active_hours,
            ) in merged_stats.items()
        ]

    async def try_start_settlement_job(
        self,
        record_date: int,
        force: bool = False,
        stale_after: int = 60 * 30,
    ) -> tuple[bool, str]:
        repo_module = _repo_module()
        now_ts = repo_module.get_current_time()
        async with water_core_db.session(commit=True) as session:
            ops = repo_module.WaterSettlementJobOps(session)
            if force:
                started = await ops.try_start_job(
                    record_date,
                    now_ts,
                    stale_after=0,
                    force=True,
                )
                return (started, "forced") if started else (False, "already_settled")

            started = await ops.try_start_job(record_date, now_ts, stale_after)
            if started:
                return True, "started"

            job = await ops.get_job(record_date)
            if job is None:
                return False, "unknown"
            if job.status == "success":
                return False, "already_settled"
            if job.status == "running":
                return False, "running"
            if job.status == "failed":
                return False, "failed"
            return False, "pending"

    async def mark_settlement_success(self, record_date: int) -> None:
        repo_module = _repo_module()
        now_ts = repo_module.get_current_time()
        async with water_core_db.session(commit=True) as session:
            await repo_module.WaterSettlementJobOps(session).mark_success(
                record_date, now_ts
            )

    async def mark_settlement_failed(self, record_date: int, error: str) -> None:
        repo_module = _repo_module()
        now_ts = repo_module.get_current_time()
        async with water_core_db.session(commit=True) as session:
            await repo_module.WaterSettlementJobOps(session).mark_failed(
                record_date, now_ts, error
            )

    async def apply_daily_settlement(
        self,
        target_date: arrow.Arrow,
        aggregates: list["DailyAggregateItem"],
        chunk_size: int = 500,
        chunk_pause_seconds: float = 0.1,
        prune_after_settlement: bool = True,
    ) -> None:
        _ = prune_after_settlement
        if not aggregates:
            return

        from .repo import calc_personal_delta_exp, calc_weighted_global_exp

        repo_module = _repo_module()
        now_ts = repo_module.get_current_time()
        record_date = int(target_date.format("YYYYMMDD"))

        summary_payloads: list[WaterSummaryPayload] = []
        group_user_gain: dict[tuple[str, str], tuple[int, int, int]] = {}
        group_gain: dict[str, tuple[int, int, int]] = {}
        matrix_user_gain: dict[tuple[str, str], int] = defaultdict(int)
        matrix_gain: dict[str, int] = defaultdict(int)
        user_matrix_gain: dict[str, list[tuple[str, int]]] = defaultdict(list)
        penalty_logs: list[WaterPenaltyPayload] = []

        for row in aggregates:
            summary_payloads.append(
                {
                    "group_id": row.group_id,
                    "user_id": row.user_id,
                    "record_date": record_date,
                    "msg_count": row.msg_count,
                    "active_hours": row.active_hours,
                    "hourly_counts": row.hourly_counts,
                    "created_at": now_ts,
                    "updated_at": now_ts,
                }
            )
            group_user_gain[(row.group_id, row.user_id)] = (
                row.msg_count,
                1,
                row.active_hours,
            )
            current_group = group_gain.get(row.group_id, (0, 0, 0))
            group_gain[row.group_id] = (
                current_group[0] + row.msg_count,
                current_group[1] + 1,
                current_group[2] + row.active_hours,
            )

            delta = calc_personal_delta_exp(row.msg_count, row.active_hours)
            if row.msg_count > 1000 and row.active_hours <= 2:
                penalty_logs.append(
                    {
                        "created_at": now_ts,
                        "updated_at": now_ts,
                        "record_date": record_date,
                        "user_id": row.user_id,
                        "group_id": row.group_id,
                        "matrix_id": row.matrix_id,
                        "reason": "ANTI_SPAM_ZERO_PROFIT",
                        "delta_exp": -delta,
                        "is_revoked": 0,
                        "revoked_at": None,
                        "extra": {
                            "msg_count": row.msg_count,
                            "active_hours": row.active_hours,
                            "candidate_delta": delta,
                        },
                    }
                )
                continue

            matrix_user_gain[(row.matrix_id, row.user_id)] += delta
            matrix_gain[row.matrix_id] += delta
            user_matrix_gain[row.user_id].append((row.matrix_id, delta))

        user_global_gain: dict[str, int] = defaultdict(int)
        for user_id, gains in user_matrix_gain.items():
            user_global_gain[user_id] = calc_weighted_global_exp(
                [gain for _, gain in gains]
            )

        async with water_core_db.session(commit=True) as session:
            group_stats_ops = repo_module.WaterGroupStatsOps(session)
            level_ops = repo_module.WaterLevelOps(session)
            penalty_ops = repo_module.WaterPenaltyOps(session)

            group_user_keys = list(group_user_gain.keys())
            group_ids = list(group_gain.keys())
            matrix_keys = list(matrix_user_gain.keys())
            matrix_ids = list(matrix_gain.keys())
            user_ids = list(user_global_gain.keys())

            old_group_user = await group_stats_ops.get_group_user_totals(
                group_user_keys
            )
            old_group_total = await group_stats_ops.get_group_totals(group_ids)
            old_matrix = await level_ops.get_matrix_levels(matrix_keys)
            old_matrix_total = await level_ops.get_matrix_totals(matrix_ids)
            old_global = await level_ops.get_global_levels(user_ids)

            group_user_payloads: list[WaterGroupUserTotalPayload] = []
            for group_id, user_id in group_user_keys:
                msg_count, active_days, active_hours = group_user_gain[
                    (group_id, user_id)
                ]
                old_msg, old_days, old_hours = old_group_user.get(
                    (group_id, user_id),
                    (0, 0, 0),
                )
                group_user_payloads.append(
                    {
                        "group_id": group_id,
                        "user_id": user_id,
                        "msg_count": old_msg + msg_count,
                        "active_days": old_days + active_days,
                        "active_hours": old_hours + active_hours,
                        "created_at": now_ts,
                        "updated_at": now_ts,
                    }
                )

            group_total_payloads: list[WaterGroupTotalPayload] = []
            for group_id in group_ids:
                msg_count, active_days, active_hours = group_gain[group_id]
                old_msg, old_days, old_hours = old_group_total.get(group_id, (0, 0, 0))
                group_total_payloads.append(
                    {
                        "group_id": group_id,
                        "msg_count": old_msg + msg_count,
                        "active_days": old_days + active_days,
                        "active_hours": old_hours + active_hours,
                        "created_at": now_ts,
                        "updated_at": now_ts,
                    }
                )

            matrix_payloads: list[WaterUserExpPayload] = []
            for matrix_id, user_id in matrix_keys:
                gain = matrix_user_gain[(matrix_id, user_id)]
                old_exp, old_season_exp, _ = old_matrix.get(
                    (matrix_id, user_id),
                    (0, 0, 1),
                )
                new_exp = max(0, old_exp + gain)
                new_season = max(0, old_season_exp + gain)
                matrix_payloads.append(
                    {
                        "matrix_id": matrix_id,
                        "user_id": user_id,
                        "delta_exp": new_exp,
                        "delta_season_exp": new_season,
                        "created_at": now_ts,
                        "updated_at": now_ts,
                    }
                )

            global_payloads: list[WaterUserExpPayload] = []
            for user_id in user_ids:
                gain = user_global_gain[user_id]
                old_exp, old_season_exp, _ = old_global.get(user_id, (0, 0, 1))
                new_exp = max(0, old_exp + gain)
                new_season = max(0, old_season_exp + gain)
                global_payloads.append(
                    {
                        "matrix_id": "",
                        "user_id": user_id,
                        "delta_exp": new_exp,
                        "delta_season_exp": new_season,
                        "created_at": now_ts,
                        "updated_at": now_ts,
                    }
                )

            matrix_total_payloads: list[WaterMatrixExpPayload] = []
            for matrix_id in matrix_ids:
                gain = matrix_gain[matrix_id]
                old_exp, old_season_exp, _ = old_matrix_total.get(matrix_id, (0, 0, 1))
                new_exp = max(0, old_exp + gain)
                new_season = max(0, old_season_exp + gain)
                matrix_total_payloads.append(
                    {
                        "matrix_id": matrix_id,
                        "delta_exp": new_exp,
                        "delta_season_exp": new_season,
                        "created_at": now_ts,
                        "updated_at": now_ts,
                    }
                )

            for chunk in split_list(matrix_payloads, chunk_size):
                await level_ops.upsert_matrix_levels(chunk)
                if chunk_pause_seconds > 0:
                    await asyncio.sleep(chunk_pause_seconds)

            for chunk in split_list(group_user_payloads, chunk_size):
                await group_stats_ops.upsert_group_user_totals(chunk)
                if chunk_pause_seconds > 0:
                    await asyncio.sleep(chunk_pause_seconds)

            for chunk in split_list(group_total_payloads, chunk_size):
                await group_stats_ops.upsert_group_totals(chunk)
                if chunk_pause_seconds > 0:
                    await asyncio.sleep(chunk_pause_seconds)

            for chunk in split_list(global_payloads, chunk_size):
                await level_ops.upsert_global_levels(chunk)
                if chunk_pause_seconds > 0:
                    await asyncio.sleep(chunk_pause_seconds)

            for chunk in split_list(matrix_total_payloads, chunk_size):
                await level_ops.upsert_matrix_totals(chunk)
                if chunk_pause_seconds > 0:
                    await asyncio.sleep(chunk_pause_seconds)

            if penalty_logs:
                for chunk in split_list(penalty_logs, chunk_size):
                    await penalty_ops.insert_penalty_logs(chunk)

        for chunk in split_list(summary_payloads, chunk_size):
            await self.save_summary_batch(chunk)
            if chunk_pause_seconds > 0:
                await asyncio.sleep(chunk_pause_seconds)

    async def prune_old_messages(self, before_ts: int) -> int:
        _ = before_ts
        return 0

    async def archive_message_shards(self) -> None:
        await water_message.run_archiver_task()

    async def unlock_achievements(self, payloads: list[WaterAchievementPayload]) -> int:
        if not payloads:
            return 0
        async with water_core_db.session(commit=True) as session:
            return await _repo_module().WaterAchievementOps(session).bulk_unlock(payloads)

    async def get_user_achievement_items(
        self,
        user_id: str,
    ) -> list[tuple[str, str, str, int]]:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterAchievementOps(session).get_unlocked_items(
                user_id
            )

    async def create_activity_season(
        self,
        payload: WaterActivitySeasonPayload,
    ) -> int:
        async with water_core_db.session(commit=True) as session:
            return await _repo_module().WaterActivitySeasonOps(session).create(payload)

    async def get_activity_season(
        self,
        season_id: str,
    ) -> "WaterActivitySeasonRecord | None":
        async with water_core_db.session(commit=False) as session:
            row = await _repo_module().WaterActivitySeasonOps(session).get_by_season_id(
                season_id
            )
        if row is None:
            return None
        return self._to_season_record(row)

    async def update_activity_season(
        self,
        season_id: str,
        **values: object,
    ) -> bool:
        async with water_core_db.session(commit=True) as session:
            affected = await _repo_module().WaterActivitySeasonOps(session).update(
                season_id, **values
            )
        return affected > 0

    async def delete_activity_season(self, season_id: str) -> bool:
        async with water_core_db.session(commit=True) as session:
            affected = await _repo_module().WaterActivitySeasonOps(session).delete(
                season_id
            )
        return affected > 0

    async def list_activity_seasons(
        self,
        statuses: list[str] | None = None,
    ) -> list["WaterActivitySeasonRecord"]:
        async with water_core_db.session(commit=False) as session:
            rows = await _repo_module().WaterActivitySeasonOps(session).list_by_status(
                statuses
            )
        return [self._to_season_record(row) for row in rows]

    async def list_current_activity_seasons(
        self,
        today: int,
    ) -> list["WaterActivitySeasonRecord"]:
        async with water_core_db.session(commit=False) as session:
            rows = await _repo_module().WaterActivitySeasonOps(
                session
            ).list_current_published(today)
        return [self._to_season_record(row) for row in rows]

    async def search_published_activity_seasons(
        self,
        keyword: str,
    ) -> list["WaterActivitySeasonRecord"]:
        async with water_core_db.session(commit=False) as session:
            rows = await _repo_module().WaterActivitySeasonOps(
                session
            ).search_published_candidates(keyword)
        return [self._to_season_record(row) for row in rows]

    async def get_penalty_log(self, penalty_id: int):
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterPenaltyOps(session).get_penalty_by_id(
                penalty_id
            )

    async def get_user_recent_summaries(
        self,
        user_id: str,
        matrix_id: str,
        start_date: int,
        end_date: int,
    ) -> list[WaterSummaryRecord]:
        repo_module = _repo_module()
        hot_start_date = self._hot_summary_start_date()
        async with water_core_db.session(commit=False) as session:
            group_ids = await repo_module.WaterGroupMatrixMapOps(session).get_groups_by_matrix(
                matrix_id
            )
            if not group_ids:
                return []

            hot_rows: list[WaterSummaryRecord] = []
            if end_date >= hot_start_date:
                hot_rows = await repo_module.WaterSummaryOps(session).get_user_recent_summaries(
                    user_id=user_id,
                    group_ids=group_ids,
                    start_date=max(start_date, hot_start_date),
                    end_date=end_date,
                )

        archived_rows: list[WaterSummaryRecord] = []
        if start_date < hot_start_date:
            archived_rows = await self._fetch_archived_summaries_in_window(
                user_id=user_id,
                group_ids=group_ids,
                start_date=start_date,
                end_date=min(end_date, self._previous_date(hot_start_date)),
            )
        return self._merge_summary_records(archived_rows, hot_rows)

    async def get_summaries_in_window(
        self,
        start_date: int,
        end_date: int,
        *,
        group_ids: list[str] | None = None,
        user_id: str | None = None,
        preserve_order: bool = True,
    ) -> list[WaterSummaryRecord]:
        repo_module = _repo_module()
        hot_start_date = self._hot_summary_start_date()
        hot_rows: list[WaterSummaryRecord] = []
        async with water_core_db.session(commit=False) as session:
            if end_date >= hot_start_date:
                hot_rows = await repo_module.WaterSummaryOps(session).get_summaries_in_window(
                    start_date=max(start_date, hot_start_date),
                    end_date=end_date,
                    group_ids=group_ids,
                    user_id=user_id,
                    preserve_order=preserve_order,
                )

        archived_rows: list[WaterSummaryRecord] = []
        if start_date < hot_start_date:
            archived_rows = await self._fetch_archived_summaries_in_window(
                start_date=start_date,
                end_date=min(end_date, self._previous_date(hot_start_date)),
                group_ids=group_ids,
                user_id=user_id,
                preserve_order=preserve_order,
            )
        return self._merge_summary_records(archived_rows, hot_rows)

    async def get_user_season_rankings(
        self,
        start_date: int,
        end_date: int,
    ) -> list["SeasonUserAggregate"]:
        summaries = await self.get_summaries_in_window(start_date, end_date)
        return self._build_user_season_rank(summaries)

    async def get_group_season_rankings(
        self,
        start_date: int,
        end_date: int,
    ) -> list["SeasonGroupAggregate"]:
        summaries = await self.get_summaries_in_window(start_date, end_date)
        return self._build_group_season_rank(summaries)

    async def get_matrix_season_rankings(
        self,
        start_date: int,
        end_date: int,
    ) -> list["SeasonMatrixAggregate"]:
        summaries = await self.get_summaries_in_window(start_date, end_date)
        return await self._build_matrix_season_rank(summaries)

    async def get_user_global_level(self, user_id: str) -> tuple[int, int, int] | None:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterLevelOps(session).get_global_level(user_id)

    async def get_user_global_rank(self, user_id: str) -> int | None:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterLevelOps(session).get_user_global_rank(
                user_id
            )

    async def get_groups_by_matrix_id(self, matrix_id: str) -> list[str]:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterGroupMatrixMapOps(
                session
            ).get_groups_by_matrix(matrix_id)

    async def get_user_matrix_level(
        self,
        user_id: str,
        matrix_id: str,
    ) -> tuple[int, int, int] | None:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterLevelOps(session).get_matrix_level(
                matrix_id, user_id
            )

    async def get_user_matrix_rank(self, user_id: str, matrix_id: str) -> int | None:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterLevelOps(session).get_user_matrix_rank(
                matrix_id, user_id
            )

    async def get_group_user_rank(self, group_id: str, user_id: str) -> int | None:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterGroupStatsOps(session).get_group_user_rank(
                group_id,
                user_id,
            )

    async def get_group_activity_rank(self, group_id: str) -> int | None:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterGroupStatsOps(
                session
            ).get_group_activity_rank(group_id)

    async def archive_summary_shards(self) -> None:
        await water_summary.run_archiver_task()

    async def prune_hot_summaries(self, today_ts: int | None = None) -> int:
        hot_start_date = self._hot_summary_start_date(today_ts)
        async with water_core_db.session(commit=True) as session:
            result = await session.execute(
                delete(WaterDailySummary).where(
                    WaterDailySummary.record_date < hot_start_date
                )
            )
        return int(getattr(result, "rowcount", 0) or 0)

    async def get_matrix_rank(self, matrix_id: str) -> int | None:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterLevelOps(session).get_matrix_rank(matrix_id)

    async def get_matrix_total_level(
        self, matrix_id: str
    ) -> tuple[int, int, int] | None:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterLevelOps(session).get_matrix_total(matrix_id)

    async def exists_other_global_lv10(self, user_id: str) -> bool:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterLevelOps(
                session
            ).exists_other_global_lv10(user_id)

    async def ignore_matrix_suggestion(self, group_id: str) -> bool:
        repo_module = _repo_module()
        now_ts = repo_module.get_current_time()
        async with water_core_db.session(commit=True) as session:
            return await repo_module.WaterMatrixMergeStateOps(session).set_ignored(
                group_id, now_ts
            )

    async def get_ignored_matrix_suggestions(self) -> set[str]:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterMatrixMergeStateOps(
                session
            ).get_ignored_groups()

    async def mark_group_first_record_seen(self, group_id: str) -> bool:
        repo_module = _repo_module()
        now_ts = repo_module.get_current_time()
        async with water_core_db.session(commit=True) as session:
            return await repo_module.WaterMatrixMergeStateOps(session).mark_first_seen(
                group_id,
                now_ts,
            )

    async def get_marked_first_record_groups(self) -> set[str]:
        async with water_core_db.session(commit=False) as session:
            return await _repo_module().WaterMatrixMergeStateOps(
                session
            ).get_first_seen_groups()

    async def has_matrix_merge_decision(self, group_id: str) -> bool:
        async with water_core_db.session(commit=False) as session:
            state = await _repo_module().WaterMatrixMergeStateOps(session).get_state(
                group_id
            )
            if state is None:
                return False
            return state.status in {"merge", "reject"}

    async def get_pending_matrix_suggestion(self, group_id: str) -> dict | None:
        async with water_core_db.session(commit=False) as session:
            state = await _repo_module().WaterMatrixMergeStateOps(session).get_state(
                group_id
            )
            if state is None:
                return None
            if state.status != "pending" or not state.target_matrix_id:
                return None
            return {"target_matrix_id": state.target_matrix_id}

    async def set_pending_matrix_suggestion(
        self,
        group_id: str,
        target_matrix_id: str,
    ) -> None:
        repo_module = _repo_module()
        now_ts = repo_module.get_current_time()
        async with self._get_merge_state_lock(group_id):
            async with water_core_db.session(commit=True) as session:
                ops = repo_module.WaterMatrixMergeStateOps(session)
                state = await ops.get_state(group_id)
                if state is not None and state.status in {"merge", "reject", "pending"}:
                    return
                await ops.set_pending_target(group_id, target_matrix_id, now_ts)

    async def set_matrix_merge_intention_once(
        self,
        group_id: str,
        action: str,
        operator_id: str,
    ) -> tuple[bool, dict]:
        repo_module = _repo_module()
        now_ts = repo_module.get_current_time()
        stale_target_corrected = False
        merge_applied = False
        async with self._get_merge_state_lock(group_id):
            async with water_core_db.session(commit=True) as session:
                ops = repo_module.WaterMatrixMergeStateOps(session)
                state = await ops.get_state(group_id)
                if state is not None and state.status in {"merge", "reject"}:
                    return False, {
                        "action": state.status,
                        "target_matrix_id": state.target_matrix_id,
                    }

                target_matrix_id = state.target_matrix_id if state is not None else ""
                if not target_matrix_id or (
                    state is not None and state.status != "pending"
                ):
                    return False, {"action": "no_need", "target_matrix_id": ""}

                current_matrix_id = await self.get_or_create_group_matrix_id(group_id)
                resolved_target_matrix_id = target_matrix_id
                if action == "merge" and target_matrix_id != current_matrix_id:
                    target_groups = await repo_module.WaterGroupMatrixMapOps(
                        session
                    ).count_groups_by_matrix(target_matrix_id)
                    if target_groups <= 0:
                        resolved_target_matrix_id = current_matrix_id
                        stale_target_corrected = True

                ok = await ops.set_intention_once(
                    group_id,
                    action,
                    operator_id,
                    now_ts,
                    target_matrix_id=resolved_target_matrix_id,
                )
                if not ok:
                    latest = await ops.get_state(group_id)
                    if latest is None:
                        return False, {"action": "no_need", "target_matrix_id": ""}
                    return False, {
                        "action": latest.status,
                        "target_matrix_id": latest.target_matrix_id,
                    }

        if (
            action == "merge"
            and resolved_target_matrix_id
            and resolved_target_matrix_id != current_matrix_id
        ):
            await self.map_group_to_matrix(group_id, resolved_target_matrix_id)
            merge_applied = True

        return True, {
            "action": action,
            "target_matrix_id": resolved_target_matrix_id,
            "stale_target_corrected": stale_target_corrected,
            "merge_applied": merge_applied,
        }

    async def get_settlement_state(self) -> dict[str, int | str]:
        async with water_core_db.session(commit=False) as session:
            repo_module = _repo_module()
            merge_ops = repo_module.WaterMatrixMergeStateOps(session)
            job_ops = repo_module.WaterSettlementJobOps(session)
            latest_job = await job_ops.get_latest_job()
            last_success = await job_ops.get_last_success_record_date()
            ignored_count = len(await merge_ops.get_ignored_groups())

        if latest_job is None:
            return {
                "last_success_record_date": last_success,
                "latest_record_date": 0,
                "latest_status": "none",
                "latest_started_at": 0,
                "latest_finished_at": 0,
                "ignored_count": ignored_count,
            }

        return {
            "last_success_record_date": last_success,
            "latest_record_date": latest_job.record_date,
            "latest_status": latest_job.status,
            "latest_started_at": latest_job.started_at,
            "latest_finished_at": latest_job.finished_at,
            "ignored_count": ignored_count,
        }

    async def pardon_penalty(self, penalty_id: int) -> bool:
        from .repo import calc_personal_delta_exp, calc_weighted_global_exp

        repo_module = _repo_module()
        now_ts = repo_module.get_current_time()
        async with water_core_db.session(commit=True) as session:
            penalty_ops = repo_module.WaterPenaltyOps(session)
            level_ops = repo_module.WaterLevelOps(session)
            summary_ops = repo_module.WaterSummaryOps(session)
            log = await penalty_ops.get_penalty_by_id(penalty_id)
            if log is None or log.is_revoked:
                return False

            summary_rows = await summary_ops.get_user_summary_rows_by_date(
                log.user_id,
                log.record_date,
            )
            penalties = await penalty_ops.get_user_penalties_by_date(
                log.user_id,
                log.record_date,
            )
            penalties_by_group = {penalty.group_id: penalty for penalty in penalties}

            current_gains: list[int] = []
            next_gains: list[int] = []
            for group_id, msg_count, active_hours in summary_rows:
                delta = calc_personal_delta_exp(msg_count, active_hours)
                penalty = penalties_by_group.get(group_id)
                if penalty is None or penalty.is_revoked:
                    current_gains.append(delta)
                    next_gains.append(delta)
                    continue
                if penalty.id == penalty_id:
                    next_gains.append(delta)

            matrix_delta = int(log.extra.get("candidate_delta", abs(log.delta_exp)))
            global_delta = calc_weighted_global_exp(
                next_gains
            ) - calc_weighted_global_exp(current_gains)

            matrix_level = await level_ops.get_matrix_level(log.matrix_id, log.user_id)
            global_level = await level_ops.get_global_level(log.user_id)
            matrix_total = await level_ops.get_matrix_total(log.matrix_id)

            if matrix_delta > 0:
                matrix_exp = (
                    matrix_level[0] if matrix_level is not None else 0
                ) + matrix_delta
                matrix_season_exp = (
                    matrix_level[1] if matrix_level is not None else 0
                ) + matrix_delta
                await level_ops.upsert_matrix_levels(
                    [
                        {
                            "matrix_id": log.matrix_id,
                            "user_id": log.user_id,
                            "delta_exp": matrix_exp,
                            "delta_season_exp": matrix_season_exp,
                            "created_at": now_ts,
                            "updated_at": now_ts,
                        }
                    ]
                )

                matrix_total_exp = (
                    matrix_total[0] if matrix_total is not None else 0
                ) + matrix_delta
                matrix_total_season_exp = (
                    matrix_total[1] if matrix_total is not None else 0
                ) + matrix_delta
                await level_ops.upsert_matrix_totals(
                    [
                        {
                            "matrix_id": log.matrix_id,
                            "delta_exp": matrix_total_exp,
                            "delta_season_exp": matrix_total_season_exp,
                            "created_at": now_ts,
                            "updated_at": now_ts,
                        }
                    ]
                )

            if global_delta > 0:
                global_exp = (
                    global_level[0] if global_level is not None else 0
                ) + global_delta
                global_season_exp = (
                    global_level[1] if global_level is not None else 0
                ) + global_delta
                await level_ops.upsert_global_levels(
                    [
                        {
                            "matrix_id": "",
                            "user_id": log.user_id,
                            "delta_exp": global_exp,
                            "delta_season_exp": global_season_exp,
                            "created_at": now_ts,
                            "updated_at": now_ts,
                        }
                    ]
                )

            revoked = await penalty_ops.revoke_penalty(penalty_id, now_ts)
            return revoked > 0
