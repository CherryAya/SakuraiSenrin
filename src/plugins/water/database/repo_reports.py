"""Water repository report and realtime snapshot helpers."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from heapq import nsmallest
from time import perf_counter
from typing import TYPE_CHECKING, cast

import arrow
from sqlalchemy.engine.row import Row
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.connectors import ColdPolicy
from src.lib.utils.common import get_current_time
from src.logger import logger
from src.plugins.water.services.rank_types import WaterRankScope, WaterRankSubject

from .instances import water_message
from .ops import WaterMessageOps
from .types import WaterSummaryRecord
from .writers import water_writer

if TYPE_CHECKING:
    from .repo import WaterRepository
    from .repo_models import (
        NaturalPeriodRankSnapshot,
        NaturalRankOverview,
        WaterDailyReportCandidate,
        WaterGroupDailyRankSnapshot,
        WaterGroupReportSnapshot,
    )

from .repo_models import (
    NaturalPeriodRankSnapshot,
    WaterDailyReportCandidate,
    WaterDailyReportPreviewItem,
    WaterGroupDailyRankItem,
    WaterGroupDailyRankSnapshot,
    WaterGroupReportMember,
    WaterGroupReportSnapshot,
)


class WaterRepositoryReportsMixin:
    async def get_natural_day_snapshot(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        group_id: str,
        limit: int = 10,
    ) -> "NaturalPeriodRankSnapshot":
        repo_self = cast("WaterRepository", self)
        started = perf_counter()
        await water_writer.flush_now()
        now = arrow.get(get_current_time()).to("Asia/Shanghai")
        record_date = int(now.format("YYYYMMDD"))
        current_rows, previous_rows = await asyncio.gather(
            repo_self._collect_realtime_daily_rows(
                scope=scope,
                group_id=group_id,
                record_date=record_date,
            ),
            repo_self._resolve_previous_day_rows(
                scope=scope,
                group_id=group_id,
                record_date=record_date,
            ),
        )
        snapshot = await repo_self._build_natural_period_snapshot_from_rows(
            subject=subject,
            current_rows=current_rows,
            previous_rows=previous_rows,
            limit=limit,
        )
        logger.debug(
            "[Water][RankRepo] scope={} subject={} day_record={} current_rows={} "
            "previous_rows={} top={} active={} elapsed_ms={:.2f}",
            scope,
            subject,
            record_date,
            len(current_rows),
            len(previous_rows),
            len(snapshot.leaderboard),
            snapshot.overview.active_entity_count,
            (perf_counter() - started) * 1000,
        )
        return snapshot

    async def get_natural_day_leaderboard(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        group_id: str,
        limit: int = 10,
    ) -> Sequence[object]:
        snapshot = await self.get_natural_day_snapshot(
            subject=subject,
            scope=scope,
            group_id=group_id,
            limit=limit,
        )
        return snapshot.leaderboard

    async def get_natural_day_overview(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        group_id: str,
    ) -> "NaturalRankOverview":
        snapshot = await self.get_natural_day_snapshot(
            subject=subject,
            scope=scope,
            group_id=group_id,
        )
        return snapshot.overview

    async def list_daily_report_candidates(
        self,
        *,
        record_date: int,
        min_activity_score: int,
        working_group_ids: Sequence[str],
    ) -> list["WaterDailyReportCandidate"]:
        repo_self = cast("WaterRepository", self)
        if not working_group_ids:
            return []
        rows = await repo_self.get_summaries_in_window(
            record_date,
            record_date,
            group_ids=list(working_group_ids),
        )
        aggregates = repo_self._build_entity_period_aggregates(
            rows,
            lambda item: item.group_id,
        )
        ordered = sorted(
            (
                WaterDailyReportCandidate(
                    group_id=group_id,
                    record_date=record_date,
                    total_msg_count=msg_count,
                    active_user_count=active_users,
                    active_hours=active_hours,
                    activity_score=msg_count + 20 * active_users,
                )
                for group_id, (
                    msg_count,
                    active_users,
                    active_hours,
                    _hourly_counts,
                    _group_count,
                ) in aggregates.items()
                if msg_count + 20 * active_users >= min_activity_score
            ),
            key=lambda item: (
                -item.activity_score,
                -item.total_msg_count,
                item.group_id,
            ),
        )
        logger.debug(
            "[Water][ReportRepo] date={} working_groups={} candidates={} threshold={}",
            record_date,
            len(working_group_ids),
            len(ordered),
            min_activity_score,
        )
        return ordered

    async def list_daily_report_preview_items(
        self,
        *,
        record_date: int,
        working_group_ids: Sequence[str],
        live: bool = False,
    ) -> list["WaterDailyReportPreviewItem"]:
        repo_self = cast("WaterRepository", self)
        if not working_group_ids:
            return []

        rows: Sequence[WaterSummaryRecord]
        if live:
            await water_writer.flush_now()
            rows = await repo_self._collect_realtime_daily_rows(
                scope="global",
                group_id="",
                record_date=record_date,
            )
        else:
            rows = await repo_self.get_summaries_in_window(
                record_date,
                record_date,
                group_ids=list(working_group_ids),
            )

        aggregates = repo_self._build_entity_period_aggregates(
            rows,
            lambda item: item.group_id,
        )
        ordered = sorted(
            (
                WaterDailyReportPreviewItem(
                    group_id=group_id,
                    record_date=record_date,
                    total_msg_count=msg_count,
                    active_user_count=active_users,
                    active_hours=active_hours,
                    activity_score=msg_count + 20 * active_users,
                )
                for group_id in working_group_ids
                for (
                    msg_count,
                    active_users,
                    active_hours,
                    _hourly_counts,
                    _group_count,
                ) in (aggregates.get(group_id, (0, 0, 0, [0] * 24, 0)),)
            ),
            key=lambda item: (
                -item.activity_score,
                -item.total_msg_count,
                item.group_id,
            ),
        )
        logger.debug(
            "[Water][ReportRepo] preview date={} working_groups={} items={} live={}",
            record_date,
            len(working_group_ids),
            len(ordered),
            live,
        )
        return ordered

    async def get_group_report_summary_snapshot(
        self,
        *,
        group_id: str,
        record_date: int,
        limit: int = 10,
    ) -> "WaterGroupReportSnapshot | None":
        repo_self = cast("WaterRepository", self)
        current_rows, previous_rows = await asyncio.gather(
            repo_self.get_summaries_in_window(
                record_date, record_date, group_ids=[group_id]
            ),
            repo_self.get_summaries_in_window(
                repo_self._previous_date(record_date),
                repo_self._previous_date(record_date),
                group_ids=[group_id],
            ),
        )
        return repo_self._build_group_report_snapshot_from_rows(
            group_id=group_id,
            record_date=record_date,
            current_rows=current_rows,
            previous_rows=previous_rows,
            limit=limit,
        )

    async def get_group_report_live_snapshot(
        self,
        *,
        group_id: str,
        now_ts: int | None = None,
        limit: int = 10,
    ) -> "WaterGroupReportSnapshot | None":
        repo_self = cast("WaterRepository", self)
        await water_writer.flush_now()
        now = arrow.get(now_ts or get_current_time()).to("Asia/Shanghai")
        record_date = int(now.format("YYYYMMDD"))
        current_rows, previous_rows = await asyncio.gather(
            repo_self._collect_realtime_daily_rows(
                scope="group",
                group_id=group_id,
                record_date=record_date,
            ),
            repo_self.get_summaries_in_window(
                repo_self._previous_date(record_date),
                repo_self._previous_date(record_date),
                group_ids=[group_id],
            ),
        )
        return repo_self._build_group_report_snapshot_from_rows(
            group_id=group_id,
            record_date=record_date,
            current_rows=current_rows,
            previous_rows=previous_rows,
            limit=limit,
        )

    async def get_group_daily_rank_snapshot(
        self,
        *,
        group_id: str,
        record_date: int,
        radius: int = 2,
        min_window_size: int = 0,
        live: bool = False,
    ) -> "WaterGroupDailyRankSnapshot | None":
        repo_self = cast("WaterRepository", self)
        previous_date = repo_self._previous_date(record_date)
        if live:
            await water_writer.flush_now()
            current_rows, previous_rows = await asyncio.gather(
                repo_self._collect_realtime_daily_rows(
                    scope="global",
                    group_id=group_id,
                    record_date=record_date,
                ),
                repo_self.get_summaries_in_window(previous_date, previous_date),
            )
        else:
            current_rows, previous_rows = await asyncio.gather(
                repo_self.get_summaries_in_window(record_date, record_date),
                repo_self.get_summaries_in_window(previous_date, previous_date),
            )
        return repo_self._build_group_daily_rank_snapshot_from_rows(
            focus_group_id=group_id,
            record_date=record_date,
            current_rows=current_rows,
            previous_rows=previous_rows,
            radius=radius,
            min_window_size=min_window_size,
        )

    async def get_group_daily_rank_history(
        self,
        *,
        group_ids: Sequence[str],
        end_record_date: int,
        days: int = 30,
        live: bool = False,
    ) -> dict[str, list[tuple[int, int | None]]]:
        repo_self = cast("WaterRepository", self)
        if not group_ids or days <= 0:
            return {}

        end_day = arrow.get(str(end_record_date), "YYYYMMDD").to("Asia/Shanghai")
        start_day = end_day.shift(days=-(days - 1))
        start_record_date = int(start_day.format("YYYYMMDD"))
        summary_end_record_date = (
            repo_self._previous_date(end_record_date) if live else end_record_date
        )

        summary_rows: list[WaterSummaryRecord] = []
        if start_record_date <= summary_end_record_date:
            summary_rows = list(
                await repo_self.get_summaries_in_window(
                    start_record_date,
                    summary_end_record_date,
                )
            )

        all_rows = list(summary_rows)
        if live:
            await water_writer.flush_now()
            current_rows = await repo_self._collect_realtime_daily_rows(
                scope="global",
                group_id="",
                record_date=end_record_date,
            )
            all_rows.extend(current_rows)

        grouped_by_date: dict[int, list[WaterSummaryRecord]] = defaultdict(list)
        for row in all_rows:
            grouped_by_date[int(row.record_date)].append(row)

        history_dates = [
            int(start_day.shift(days=offset).format("YYYYMMDD"))
            for offset in range(days)
        ]
        history: dict[str, list[tuple[int, int | None]]] = {
            group_id: [(record_date, None) for record_date in history_dates]
            for group_id in group_ids
        }
        for index, record_date in enumerate(history_dates):
            rows = grouped_by_date.get(record_date)
            if not rows:
                continue
            current_aggregates = repo_self._build_entity_period_aggregates(
                rows,
                lambda item: item.group_id,
            )
            ordered_current = sorted(
                (
                    (
                        entity_id,
                        msg_count,
                        active_days,
                        active_hours,
                        hourly_counts,
                        group_count,
                    )
                    for entity_id, (
                        msg_count,
                        active_days,
                        active_hours,
                        hourly_counts,
                        group_count,
                    ) in current_aggregates.items()
                ),
                key=repo_self._natural_rank_sort_key,
            )
            ranks = {
                entity_id: current_rank
                for current_rank, (entity_id, *_rest) in enumerate(ordered_current, 1)
            }
            for group_id in group_ids:
                history[group_id][index] = (record_date, ranks.get(group_id))
        return history

    async def get_group_daily_distribution_items(
        self,
        *,
        record_date: int,
        live: bool = False,
    ) -> list[WaterGroupDailyRankItem]:
        repo_self = cast("WaterRepository", self)
        if live:
            await water_writer.flush_now()
            rows = await repo_self._collect_realtime_daily_rows(
                scope="global",
                group_id="",
                record_date=record_date,
            )
        else:
            rows = await repo_self.get_summaries_in_window(record_date, record_date)
        return repo_self._build_group_distribution_items_from_rows(rows)

    def _build_group_report_snapshot_from_rows(
        self,
        *,
        group_id: str,
        record_date: int,
        current_rows: Sequence[WaterSummaryRecord],
        previous_rows: Sequence[WaterSummaryRecord],
        limit: int,
    ) -> "WaterGroupReportSnapshot | None":
        repo_self = cast("WaterRepository", self)
        if not current_rows:
            return None
        current_aggregates = repo_self._build_entity_period_aggregates(
            current_rows,
            lambda item: item.user_id,
        )
        previous_aggregates = repo_self._build_entity_period_aggregates(
            previous_rows,
            lambda item: item.user_id,
        )
        ordered_current = nsmallest(
            limit,
            (
                (
                    user_id,
                    msg_count,
                    active_days,
                    active_hours,
                    hourly_counts,
                    group_count,
                )
                for user_id, (
                    msg_count,
                    active_days,
                    active_hours,
                    hourly_counts,
                    group_count,
                ) in current_aggregates.items()
            ),
            key=repo_self._natural_rank_sort_key,
        )
        previous_ranks = repo_self._build_previous_rank_map(
            previous_aggregates,
            [user_id for user_id, *_rest in ordered_current],
        )
        leaderboard = [
            WaterGroupReportMember(
                user_id=user_id,
                msg_count=msg_count,
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
                _active_days,
                active_hours,
                hourly_counts,
                _group_count,
            ) in enumerate(ordered_current, 1)
        ]
        current_hourly = repo_self._sum_hourly(current_rows)
        previous_hourly = repo_self._sum_hourly(previous_rows)
        return WaterGroupReportSnapshot(
            group_id=group_id,
            record_date=record_date,
            total_msg_count=sum(int(row.msg_count) for row in current_rows),
            active_user_count=len(current_aggregates),
            active_hours=sum(1 for count in current_hourly if count > 0),
            hourly_counts=current_hourly,
            previous_total_msg_count=sum(int(row.msg_count) for row in previous_rows),
            previous_active_user_count=len(previous_aggregates),
            previous_active_hours=sum(1 for count in previous_hourly if count > 0),
            previous_hourly_counts=previous_hourly,
            leaderboard=leaderboard,
        )

    def _build_group_daily_rank_snapshot_from_rows(
        self,
        *,
        focus_group_id: str,
        record_date: int,
        current_rows: Sequence[WaterSummaryRecord],
        previous_rows: Sequence[WaterSummaryRecord],
        radius: int,
        min_window_size: int = 0,
    ) -> "WaterGroupDailyRankSnapshot | None":
        repo_self = cast("WaterRepository", self)
        if not current_rows:
            return None

        current_aggregates = repo_self._build_entity_period_aggregates(
            current_rows,
            lambda item: item.group_id,
        )
        if focus_group_id not in current_aggregates:
            return None

        group_active_users: dict[str, set[str]] = defaultdict(set)
        group_hourly_counts: dict[str, list[int]] = defaultdict(lambda: [0] * 24)
        for row in current_rows:
            current_group_id = str(row.group_id)
            group_active_users[current_group_id].add(str(row.user_id))
            hourly_counts = group_hourly_counts[current_group_id]
            for hour, count in enumerate(row.hourly_counts):
                hourly_counts[hour] += int(count)

        previous_aggregates = repo_self._build_entity_period_aggregates(
            previous_rows,
            lambda item: item.group_id,
        )

        ordered_current = sorted(
            (
                (
                    entity_id,
                    msg_count,
                    active_days,
                    active_hours,
                    hourly_counts,
                    group_count,
                )
                for entity_id, (
                    msg_count,
                    active_days,
                    active_hours,
                    hourly_counts,
                    group_count,
                ) in current_aggregates.items()
            ),
            key=repo_self._natural_rank_sort_key,
        )
        previous_ranks = repo_self._build_previous_rank_map(
            previous_aggregates,
            [entity_id for entity_id, *_rest in ordered_current],
        )
        items: list[WaterGroupDailyRankItem] = []
        for current_rank, (
            entity_id,
            msg_count,
            _active_days,
            _active_hours,
            aggregate_hourly_counts,
            _group_count,
        ) in enumerate(ordered_current, 1):
            entity_hourly_counts = group_hourly_counts.get(
                entity_id, aggregate_hourly_counts
            )
            items.append(
                WaterGroupDailyRankItem(
                    group_id=entity_id,
                    msg_count=msg_count,
                    active_user_count=len(group_active_users.get(entity_id, set())),
                    active_hours=sum(1 for count in entity_hourly_counts if count),
                    hourly_counts=entity_hourly_counts,
                    current_rank=current_rank,
                    trend=(
                        previous_ranks[entity_id] - current_rank
                        if entity_id in previous_ranks
                        else None
                    ),
                )
            )
        focus_index = next(
            (
                index
                for index, item in enumerate(items)
                if item.group_id == focus_group_id
            ),
            None,
        )
        if focus_index is None:
            return None

        window_size = max(radius * 2 + 1, min_window_size)
        window_size = min(len(items), window_size)
        before_count = min(radius, window_size - 1)
        start = focus_index - before_count
        start = max(0, min(start, len(items) - window_size))
        end = min(len(items), start + window_size)
        focus_item = items[focus_index]
        return WaterGroupDailyRankSnapshot(
            focus_group_id=focus_group_id,
            record_date=record_date,
            total_groups=len(items),
            total_msg_count=sum(item.msg_count for item in items),
            focus_rank=focus_item.current_rank,
            focus_trend=focus_item.trend,
            leaderboard=items[start:end],
            has_hidden_before=start > 0,
            has_hidden_after=end < len(items),
        )

    def _build_group_distribution_items_from_rows(
        self,
        rows: Sequence[WaterSummaryRecord],
    ) -> list[WaterGroupDailyRankItem]:
        repo_self = cast("WaterRepository", self)
        if not rows:
            return []
        current_aggregates = repo_self._build_entity_period_aggregates(
            rows,
            lambda item: item.group_id,
        )
        group_active_users: dict[str, set[str]] = defaultdict(set)
        group_hourly_counts: dict[str, list[int]] = defaultdict(lambda: [0] * 24)
        for row in rows:
            current_group_id = str(row.group_id)
            group_active_users[current_group_id].add(str(row.user_id))
            hourly_counts = group_hourly_counts[current_group_id]
            for hour, count in enumerate(row.hourly_counts):
                hourly_counts[hour] += int(count)

        ordered_current = sorted(
            (
                (
                    entity_id,
                    msg_count,
                    active_days,
                    active_hours,
                    hourly_counts,
                    group_count,
                )
                for entity_id, (
                    msg_count,
                    active_days,
                    active_hours,
                    hourly_counts,
                    group_count,
                ) in current_aggregates.items()
            ),
            key=repo_self._natural_rank_sort_key,
        )
        return [
            WaterGroupDailyRankItem(
                group_id=entity_id,
                msg_count=msg_count,
                active_user_count=len(group_active_users.get(entity_id, set())),
                active_hours=sum(
                    1 for count in group_hourly_counts[entity_id] if count
                ),
                hourly_counts=group_hourly_counts[entity_id],
                current_rank=current_rank,
                trend=None,
            )
            for current_rank, (
                entity_id,
                msg_count,
                _active_days,
                _active_hours,
                _aggregate_hourly_counts,
                _group_count,
            ) in enumerate(ordered_current, 1)
        ]

    async def _collect_realtime_daily_rows(
        self,
        *,
        scope: WaterRankScope,
        group_id: str,
        record_date: int,
    ) -> list[WaterSummaryRecord]:
        repo_self = cast("WaterRepository", self)
        started = perf_counter()
        now = arrow.get(str(record_date), "YYYYMMDD").to("Asia/Shanghai")
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp

        async def _stats_in_shard(
            session: AsyncSession,
        ) -> Sequence[Row[tuple[str, str, int, int]]]:
            return await WaterMessageOps(session).aggregate_daily_stats(
                start_ts, end_ts
            )

        async def _hourly_in_shard(
            session: AsyncSession,
        ) -> Sequence[tuple[str, str, int, int]]:
            return await WaterMessageOps(session).aggregate_daily_hourly_stats(
                start_ts, end_ts
            )

        stats_per_shard = await water_message.map_reduce(
            now.datetime,
            now.datetime,
            _stats_in_shard,
            cold_policy=ColdPolicy.HYDRATE,
        )
        hourly_per_shard = await water_message.map_reduce(
            now.datetime,
            now.datetime,
            _hourly_in_shard,
            cold_policy=ColdPolicy.HYDRATE,
        )
        allowed_group_ids: set[str] | None = None
        if scope == "group":
            allowed_group_ids = {group_id}
        elif scope == "matrix":
            matrix_id = await repo_self.get_or_create_group_matrix_id(group_id)
            group_ids = await repo_self.get_groups_by_matrix_id(matrix_id)
            allowed_group_ids = set(group_ids or [group_id])

        merged_stats: dict[tuple[str, str], tuple[int, int]] = {}
        for shard_rows in stats_per_shard:
            for row_group_id, user_id, msg_count, active_hours in shard_rows:
                if (
                    allowed_group_ids is not None
                    and row_group_id not in allowed_group_ids
                ):
                    continue
                key = (str(row_group_id), str(user_id))
                old_msg, old_hours = merged_stats.get(key, (0, 0))
                merged_stats[key] = (
                    old_msg + int(msg_count),
                    old_hours + int(active_hours),
                )

        merged_hourly: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * 24)
        for shard_rows in hourly_per_shard:
            for row_group_id, user_id, hour, count in shard_rows:
                if (
                    allowed_group_ids is not None
                    and row_group_id not in allowed_group_ids
                ):
                    continue
                merged_hourly[(str(row_group_id), str(user_id))][int(hour)] += int(
                    count
                )

        rows = [
            WaterSummaryRecord(
                group_id=row_group_id,
                user_id=user_id,
                record_date=record_date,
                msg_count=msg_count,
                active_hours=active_hours,
                hourly_counts=merged_hourly[(row_group_id, user_id)],
                created_at=0,
                updated_at=0,
            )
            for (row_group_id, user_id), (
                msg_count,
                active_hours,
            ) in merged_stats.items()
        ]
        logger.debug(
            "[Water][RankRepo] realtime scope={} group_id={} record_date={} rows={} "
            "merged_hourly_keys={} elapsed_ms={:.2f}",
            scope,
            group_id,
            record_date,
            len(rows),
            len(merged_hourly),
            (perf_counter() - started) * 1000,
        )
        return rows

    async def _resolve_previous_day_rows(
        self,
        *,
        scope: WaterRankScope,
        group_id: str,
        record_date: int,
    ) -> list[WaterSummaryRecord]:
        repo_self = cast("WaterRepository", self)
        previous_date = repo_self._previous_date(record_date)
        return await repo_self._resolve_rank_scope_summaries(
            subject="user",
            scope=scope,
            group_id=group_id,
            start_date=previous_date,
            end_date=previous_date,
        )
