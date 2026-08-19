"""Water repository rank and report helpers."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from heapq import nsmallest
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

import arrow
from sqlalchemy.engine.row import Row

from src.logger import logger
from src.plugins.water.services.rank_types import WaterRankScope, WaterRankSubject

from .instances import water_core_db, water_message
from .types import WaterSummaryRecord
from .writers import water_writer

if TYPE_CHECKING:
    from .repo import WaterRepository
    from .repo_models import (
        GlobalPeriodOverview,
        GlobalPeriodRankItem,
        NaturalPeriodRankSnapshot,
        NaturalRankItem,
        NaturalRankOverview,
        RankItem,
    )

from .repo_models import (
    GlobalPeriodOverview,
    GlobalPeriodRankItem,
    NaturalPeriodRankSnapshot,
    NaturalRankItem,
    RankItem,
)


def _repo_module() -> Any:
    from . import repo as repo_module

    return repo_module


def _current_time() -> int:
    return int(_repo_module().get_current_time())


class WaterRepositoryRanksMixin:
    async def get_today_leaderboard(
        self,
        group_id: str,
        limit: int = 20,
    ) -> list["RankItem"]:
        await water_writer.flush_now()

        now = arrow.get(_current_time()).to("Asia/Shanghai")
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp
        yesterday_int = int(now.shift(days=-1).format("YYYYMMDD"))

        async def _fetch_today() -> Sequence[Row[tuple[str, int]]]:
            async with water_message.read_session(time_ctx=now.datetime) as session:
                return (
                    await _repo_module()
                    .WaterMessageOps(session)
                    .get_top_users(
                        group_id,
                        start_ts,
                        end_ts,
                        limit,
                    )
                )

        async def _fetch_yesterday() -> dict[str, int]:
            async with water_core_db.session(commit=False) as session:
                return (
                    await _repo_module()
                    .WaterSummaryOps(session)
                    .get_ranks_by_date(
                        group_id,
                        yesterday_int,
                    )
                )

        today_data, yesterday_ranks = await asyncio.gather(
            _fetch_today(), _fetch_yesterday()
        )

        return [
            RankItem(
                user_id=user_id,
                msg_count=count,
                current_rank=current_rank,
                trend=(yesterday_ranks[user_id] - current_rank)
                if user_id in yesterday_ranks
                else None,
            )
            for current_rank, (user_id, count) in enumerate(today_data, 1)
        ]

    async def get_today_group_rank(self, group_id: str) -> int:
        await water_writer.flush_now()

        now = arrow.get(_current_time()).to("Asia/Shanghai")
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp

        async with water_message.read_session(time_ctx=now.datetime) as session:
            return (
                await _repo_module()
                .WaterMessageOps(session)
                .get_today_group_rank(group_id, start_ts, end_ts)
            )

    async def get_users_hourly_distribution(
        self,
        group_id: str,
        user_ids: list[str],
    ) -> dict[str, list[int]]:
        if not user_ids:
            return {}

        await water_writer.flush_now()

        now = arrow.get(_current_time()).to("Asia/Shanghai")
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp

        async with water_message.read_session(time_ctx=now.datetime) as session:
            raw_timestamps = (
                await _repo_module()
                .WaterMessageOps(session)
                .get_users_timestamps(group_id, user_ids, start_ts, end_ts)
            )

        user_hourly: dict[str, list[int]] = defaultdict(lambda: [0] * 24)
        for uid, hour, msg_count in raw_timestamps:
            user_hourly[uid][int(hour)] += int(msg_count)
        return dict(user_hourly)

    @staticmethod
    def _extract_hourly_counts(row: Row, start_idx: int) -> list[int]:
        return [int(row[idx] or 0) for idx in range(start_idx, start_idx + 24)]

    async def get_global_period_leaderboard(
        self,
        start_date: int,
        end_date: int,
        previous_start_date: int,
        previous_end_date: int,
        limit: int = 10,
    ) -> list["GlobalPeriodRankItem"]:
        repo_self = cast("WaterRepository", self)
        current_rows = await repo_self.get_summaries_in_window(start_date, end_date)
        previous_rows = await repo_self.get_summaries_in_window(
            previous_start_date,
            previous_end_date,
        )
        current_aggregates = repo_self._build_period_aggregates(current_rows)
        previous_aggregates = repo_self._build_period_aggregates(previous_rows)
        ordered_current = sorted(
            (
                (
                    user_id,
                    msg_count,
                    active_days,
                    active_hours,
                    hourly_counts,
                )
                for user_id, (
                    msg_count,
                    active_days,
                    active_hours,
                    hourly_counts,
                ) in current_aggregates.items()
            ),
            key=lambda item: (-item[1], -item[2], -item[3], item[0]),
        )[:limit]
        ordered_previous = sorted(
            (
                (user_id, msg_count, active_days, active_hours)
                for user_id, (
                    msg_count,
                    active_days,
                    active_hours,
                    _,
                ) in previous_aggregates.items()
            ),
            key=lambda item: (-item[1], -item[2], -item[3], item[0]),
        )
        previous_ranks = {
            user_id: rank for rank, (user_id, *_rest) in enumerate(ordered_previous, 1)
        }

        return [
            GlobalPeriodRankItem(
                user_id=user_id,
                msg_count=msg_count,
                active_days=active_days,
                active_hours=active_hours,
                hourly_counts=hourly_counts,
                current_rank=current_rank,
                trend=(
                    previous_ranks[user_id] - current_rank
                    if user_id in previous_ranks
                    else None
                ),
            )
            for current_rank, (
                user_id,
                msg_count,
                active_days,
                active_hours,
                hourly_counts,
            ) in enumerate(ordered_current, 1)
        ]

    async def get_global_period_overview(
        self,
        start_date: int,
        end_date: int,
        previous_start_date: int,
        previous_end_date: int,
    ) -> "GlobalPeriodOverview":
        repo_self = cast("WaterRepository", self)
        current_rows, previous_rows = await asyncio.gather(
            repo_self.get_summaries_in_window(start_date, end_date),
            repo_self.get_summaries_in_window(previous_start_date, previous_end_date),
        )
        current_hourly = repo_self._sum_hourly(current_rows)
        previous_total = sum(int(row.msg_count) for row in previous_rows)
        return GlobalPeriodOverview(
            total_msg_count=sum(int(row.msg_count) for row in current_rows),
            active_user_count=len({row.user_id for row in current_rows}),
            hourly_counts=current_hourly,
            previous_total_msg_count=previous_total,
        )

    async def get_first_summary_record_date(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        group_id: str,
    ) -> int | None:
        repo_self = cast("WaterRepository", self)
        group_ids: list[str] | None = None
        if scope == "group":
            group_ids = [group_id]
        elif scope == "matrix":
            matrix_id = await repo_self.get_or_create_group_matrix_id(group_id)
            matrix_group_ids = await repo_self.get_groups_by_matrix_id(matrix_id)
            group_ids = matrix_group_ids or [group_id]

        hot_start_date = repo_self._hot_summary_start_date()
        first_archived_date = await repo_self._get_archived_first_summary_record_date(
            end_date=repo_self._previous_date(hot_start_date),
            group_ids=group_ids,
        )
        if first_archived_date is not None:
            return first_archived_date

        async with water_core_db.session(commit=False) as session:
            return (
                await _repo_module()
                .WaterSummaryOps(session)
                .get_first_summary_record_date(group_ids=group_ids)
            )

    async def get_natural_period_leaderboard(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        group_id: str,
        start_date: int,
        end_date: int,
        previous_start_date: int,
        previous_end_date: int,
        limit: int = 10,
    ) -> list["NaturalRankItem"]:
        snapshot = await self.get_natural_period_snapshot(
            subject=subject,
            scope=scope,
            group_id=group_id,
            start_date=start_date,
            end_date=end_date,
            previous_start_date=previous_start_date,
            previous_end_date=previous_end_date,
            limit=limit,
        )
        return snapshot.leaderboard

    async def get_natural_period_snapshot(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        group_id: str,
        start_date: int,
        end_date: int,
        previous_start_date: int,
        previous_end_date: int,
        limit: int = 10,
    ) -> "NaturalPeriodRankSnapshot":
        repo_self = cast("WaterRepository", self)
        started = perf_counter()
        current_rows, previous_rows = await asyncio.gather(
            repo_self._resolve_rank_scope_summaries(
                subject=subject,
                scope=scope,
                group_id=group_id,
                start_date=start_date,
                end_date=end_date,
            ),
            repo_self._resolve_rank_scope_summaries(
                subject=subject,
                scope=scope,
                group_id=group_id,
                start_date=previous_start_date,
                end_date=previous_end_date,
            ),
        )
        snapshot = await repo_self._build_natural_period_snapshot_from_rows(
            subject=subject,
            current_rows=current_rows,
            previous_rows=previous_rows,
            limit=limit,
        )
        logger.debug(
            "[Water][RankRepo] scope={} subject={} current_rows={} previous_rows={} "
            "top={} active={} elapsed_ms={:.2f}",
            scope,
            subject,
            len(current_rows),
            len(previous_rows),
            len(snapshot.leaderboard),
            snapshot.overview.active_entity_count,
            (perf_counter() - started) * 1000,
        )
        return snapshot

    async def _build_natural_period_snapshot_from_rows(
        self,
        *,
        subject: WaterRankSubject,
        current_rows: Sequence[WaterSummaryRecord],
        previous_rows: Sequence[WaterSummaryRecord],
        limit: int,
    ) -> "NaturalPeriodRankSnapshot":
        repo_self = cast("WaterRepository", self)
        current_aggregates = await repo_self._build_rank_entity_aggregates(
            subject, current_rows
        )
        previous_aggregates = await repo_self._build_rank_entity_aggregates(
            subject, previous_rows
        )
        ordered_current = nsmallest(
            limit,
            (
                (
                    entity_id,
                    msg_count,
                    active_days,
                    active_hours,
                    hourly_counts,
                    group_count,
                    daily_msg_counts,
                )
                for entity_id, (
                    msg_count,
                    active_days,
                    active_hours,
                    hourly_counts,
                    group_count,
                    daily_msg_counts,
                ) in current_aggregates.items()
            ),
            key=repo_self._natural_rank_sort_key,
        )
        previous_ranks = repo_self._build_previous_rank_map(
            previous_aggregates,
            [entity_id for entity_id, *_rest in ordered_current],
        )
        leaderboard = [
            NaturalRankItem(
                entity_id=entity_id,
                msg_count=msg_count,
                active_days=active_days,
                active_hours=active_hours,
                hourly_counts=hourly_counts,
                current_rank=current_rank,
                trend=(
                    previous_ranks[entity_id] - current_rank
                    if entity_id in previous_ranks
                    else None
                ),
                group_count=group_count,
                daily_msg_counts=daily_msg_counts,
            )
            for current_rank, (
                entity_id,
                msg_count,
                active_days,
                active_hours,
                hourly_counts,
                group_count,
                daily_msg_counts,
            ) in enumerate(ordered_current, 1)
        ]
        overview = repo_self._build_natural_overview_from_aggregates(
            current_aggregates,
            previous_aggregates,
            daily_msg_counts=repo_self._sum_daily_msg_counts(current_rows),
        )
        return NaturalPeriodRankSnapshot(
            leaderboard=leaderboard,
            overview=overview,
        )

    async def get_natural_period_overview(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        group_id: str,
        start_date: int,
        end_date: int,
        previous_start_date: int,
        previous_end_date: int,
    ) -> "NaturalRankOverview":
        snapshot = await self.get_natural_period_snapshot(
            subject=subject,
            scope=scope,
            group_id=group_id,
            start_date=start_date,
            end_date=end_date,
            previous_start_date=previous_start_date,
            previous_end_date=previous_end_date,
        )
        return snapshot.overview

    async def _build_rank_entity_aggregates(
        self,
        subject: WaterRankSubject,
        summaries: Sequence[WaterSummaryRecord],
    ) -> dict[str, tuple[int, int, int, list[int], int, list[int]]]:
        repo_self = cast("WaterRepository", self)
        if subject == "user":
            return repo_self._build_entity_period_aggregates(
                summaries,
                lambda item: item.user_id,
            )
        if subject == "group":
            return repo_self._build_entity_period_aggregates(
                summaries, lambda item: item.group_id
            )
        if not summaries:
            return {}
        group_map = await repo_self.get_or_create_group_matrix_ids(
            sorted({item.group_id for item in summaries})
        )
        return repo_self._build_entity_period_aggregates(
            summaries, lambda item: group_map.get(item.group_id, item.group_id)
        )

    async def _resolve_rank_scope_summaries(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        group_id: str,
        start_date: int,
        end_date: int,
    ) -> list[WaterSummaryRecord]:
        repo_self = cast("WaterRepository", self)
        started = perf_counter()
        _ = subject
        if scope == "global":
            rows = await repo_self.get_summaries_in_window(
                start_date,
                end_date,
                preserve_order=False,
            )
            logger.debug(
                "[Water][RankRepo] scope=global subject={} start={} end={} rows={} "
                "elapsed_ms={:.2f}",
                subject,
                start_date,
                end_date,
                len(rows),
                (perf_counter() - started) * 1000,
            )
            return rows
        if scope == "group":
            rows = await repo_self.get_summaries_in_window(
                start_date,
                end_date,
                group_ids=[group_id],
                preserve_order=False,
            )
            logger.debug(
                "[Water][RankRepo] scope=group subject={} group_id={} start={} "
                "end={} rows={} elapsed_ms={:.2f}",
                subject,
                group_id,
                start_date,
                end_date,
                len(rows),
                (perf_counter() - started) * 1000,
            )
            return rows
        matrix_id = await repo_self.get_or_create_group_matrix_id(group_id)
        matrix_group_ids = await repo_self.get_groups_by_matrix_id(matrix_id)
        if not matrix_group_ids:
            matrix_group_ids = [group_id]
        rows = await repo_self.get_summaries_in_window(
            start_date,
            end_date,
            group_ids=matrix_group_ids,
            preserve_order=False,
        )
        logger.debug(
            "[Water][RankRepo] scope=matrix subject={} matrix_id={} groups={} "
            "start={} end={} rows={} elapsed_ms={:.2f}",
            subject,
            matrix_id,
            len(matrix_group_ids),
            start_date,
            end_date,
            len(rows),
            (perf_counter() - started) * 1000,
        )
        return rows
