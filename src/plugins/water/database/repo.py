"""Water 仓储层。"""

import asyncio
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from heapq import nsmallest
from math import floor, sqrt
import os
from secrets import token_hex
from time import perf_counter
import unicodedata

import arrow
from sqlalchemy import delete
from sqlalchemy.engine.row import Row
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.consts import WritePolicy
from src.lib.db.connectors import ColdPolicy
from src.lib.db.manager import db_manager
from src.lib.utils.common import get_current_time, split_list
from src.logger import logger
from src.plugins.water.services.rank_types import WaterRankScope, WaterRankSubject

from .instances import water_core_db, water_message, water_summary
from .ops import (
    WaterAchievementOps,
    WaterActivitySeasonOps,
    WaterArchivedSummaryOps,
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
    WaterMessageBase,
    WaterPenaltyLog,
    WaterSettlementJob,
    WaterSummaryBase,
    WaterUserAchievement,
)
from .types import (
    WaterAchievementPayload,
    WaterActivitySeasonPayload,
    WaterGroupTotalPayload,
    WaterGroupUserTotalPayload,
    WaterMatrixExpPayload,
    WaterMessagePayload,
    WaterMessageWritePayload,
    WaterPenaltyPayload,
    WaterSummaryPayload,
    WaterSummaryRecord,
    WaterUserExpPayload,
)
from .writers import water_writer

SETTLEMENT_STALE_SECONDS = 60 * 30
GLOBAL_EXP_DECAY_WEIGHTS = (1.0, 0.5, 0.2)
SUMMARY_HOT_WINDOW_DAYS = 90


@dataclass
class RankItem:
    user_id: str
    msg_count: int
    current_rank: int
    trend: int | None


@dataclass
class GlobalPeriodRankItem:
    user_id: str
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    current_rank: int
    trend: int | None


@dataclass(frozen=True)
class NaturalRankItem:
    entity_id: str
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    current_rank: int
    trend: int | None
    group_count: int = 0


@dataclass
class GlobalPeriodOverview:
    total_msg_count: int
    active_user_count: int
    hourly_counts: list[int]
    previous_total_msg_count: int

    @property
    def delta_total_msg_count(self) -> int:
        return self.total_msg_count - self.previous_total_msg_count

    @property
    def peak_hour(self) -> int:
        if not any(self.hourly_counts):
            return 0
        return max(range(24), key=lambda hour: self.hourly_counts[hour])


@dataclass(frozen=True)
class NaturalRankOverview:
    total_msg_count: int
    active_entity_count: int
    hourly_counts: list[int]
    previous_total_msg_count: int

    @property
    def peak_hour(self) -> int:
        if not any(self.hourly_counts):
            return 0
        return max(range(24), key=lambda hour: self.hourly_counts[hour])


@dataclass(frozen=True)
class NaturalPeriodRankSnapshot:
    leaderboard: list[NaturalRankItem]
    overview: NaturalRankOverview


@dataclass(frozen=True)
class WaterGroupReportMember:
    user_id: str
    msg_count: int
    active_hours: int
    hourly_counts: list[int]
    current_rank: int
    trend: int | None


@dataclass(frozen=True)
class WaterGroupDailyRankItem:
    group_id: str
    msg_count: int
    active_user_count: int
    active_hours: int
    hourly_counts: list[int]
    current_rank: int
    trend: int | None


@dataclass(frozen=True)
class WaterGroupDailyRankSnapshot:
    focus_group_id: str
    record_date: int
    total_groups: int
    focus_rank: int
    focus_trend: int | None
    leaderboard: list[WaterGroupDailyRankItem]
    has_hidden_before: bool
    has_hidden_after: bool


@dataclass(frozen=True)
class WaterGroupReportSnapshot:
    group_id: str
    record_date: int
    total_msg_count: int
    active_user_count: int
    active_hours: int
    hourly_counts: list[int]
    previous_total_msg_count: int
    previous_active_user_count: int
    previous_active_hours: int
    previous_hourly_counts: list[int]
    leaderboard: list[WaterGroupReportMember]

    @property
    def peak_hour(self) -> int:
        if not any(self.hourly_counts):
            return 0
        return max(range(24), key=lambda hour: self.hourly_counts[hour])

    @property
    def delta_total_msg_count(self) -> int:
        return self.total_msg_count - self.previous_total_msg_count

    @property
    def delta_active_user_count(self) -> int:
        return self.active_user_count - self.previous_active_user_count

    @property
    def activity_score(self) -> int:
        return self.total_msg_count + 20 * self.active_user_count


@dataclass(frozen=True)
class WaterDailyReportCandidate:
    group_id: str
    record_date: int
    total_msg_count: int
    active_user_count: int
    active_hours: int
    activity_score: int


@dataclass(frozen=True)
class WaterActivitySeasonRecord:
    season_id: str
    name: str
    normalized_name: str
    description: str
    start_date: int
    end_date: int
    status: str
    published_at: int | None
    created_by: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class SeasonUserAggregate:
    user_id: str
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    rank: int


@dataclass(frozen=True)
class SeasonGroupAggregate:
    group_id: str
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    rank: int


@dataclass(frozen=True)
class SeasonMatrixAggregate:
    matrix_id: str
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    rank: int


@dataclass
class _PeriodAggregateBucket:
    msg_count: int
    active_days: set[int]
    active_hours: int
    hourly_counts: list[int]
    group_ids: set[str]


@dataclass
class WaterMessageContext:
    group_id: str
    user_id: str
    created_at: int

    def to_payload(self) -> WaterMessagePayload:
        dt = arrow.get(self.created_at).to("Asia/Shanghai")
        return {
            "group_id": self.group_id,
            "user_id": self.user_id,
            "record_date": int(dt.format("YYYYMMDD")),
            "hour": int(dt.format("H")),
            "msg_count": 1,
        }

    def to_write_payload(self) -> WaterMessageWritePayload:
        payload = self.to_payload()
        return {
            **payload,
            "created_at": self.created_at,
        }


@dataclass
class DailyAggregateItem:
    matrix_id: str
    group_id: str
    user_id: str
    msg_count: int
    active_hours: int
    hourly_counts: list[int]


def calc_personal_delta_exp(msg_count: int, active_hours: int) -> int:
    return floor(10 * sqrt(msg_count) + 5 * active_hours)


def calc_weighted_global_exp(gains: Sequence[int]) -> int:
    total = 0
    ordered = sorted((gain for gain in gains if gain > 0), reverse=True)
    for idx, gain in enumerate(ordered):
        weight = (
            GLOBAL_EXP_DECAY_WEIGHTS[idx]
            if idx < len(GLOBAL_EXP_DECAY_WEIGHTS)
            else 0.0
        )
        total += floor(gain * weight)
    return total


class WaterRepository:
    def __init__(self) -> None:
        self._group_matrix_cache: dict[str, str] = {}
        self._group_matrix_locks: dict[str, asyncio.Lock] = {}
        self._merge_state_locks: dict[str, asyncio.Lock] = {}

    @classmethod
    async def init_all_tables(cls) -> None:
        await water_message.init(WaterMessageBase)
        await water_summary.init(WaterSummaryBase)
        await water_core_db.init(WaterCoreBase)

    @staticmethod
    def _hot_summary_start_date(today_ts: int | None = None) -> int:
        anchor = arrow.get(today_ts or get_current_time()).floor("day")
        return int(anchor.shift(days=-(SUMMARY_HOT_WINDOW_DAYS - 1)).format("YYYYMMDD"))

    @classmethod
    def _is_hot_summary_date(
        cls,
        record_date: int,
        today_ts: int | None = None,
    ) -> bool:
        return record_date >= cls._hot_summary_start_date(today_ts)

    @staticmethod
    def _previous_date(record_date: int) -> int:
        return int(
            arrow.get(str(record_date), "YYYYMMDD").shift(days=-1).format("YYYYMMDD")
        )

    @staticmethod
    def _iter_month_keys(start_date: int, end_date: int) -> list[str]:
        if start_date > end_date:
            return []
        start_key = (
            arrow.get(str(start_date), "YYYYMMDD").floor("month").format("YYYY_MM")
        )
        end_key = arrow.get(str(end_date), "YYYYMMDD").floor("month").format("YYYY_MM")
        keys: set[str] = set()
        for pattern in (
            f"{water_summary.prefix}_*.db",
            f"{water_summary.prefix}_*.db.zst",
        ):
            for file_path in water_summary.base_dir.glob(pattern):
                shard_key = file_path.name.removeprefix(f"{water_summary.prefix}_")
                if shard_key.endswith(".db.zst"):
                    shard_key = shard_key.removesuffix(".db.zst")
                else:
                    shard_key = shard_key.removesuffix(".db")
                if start_key <= shard_key <= end_key:
                    keys.add(shard_key)
        return sorted(keys)

    @staticmethod
    def _merge_summary_records(
        *groups: Sequence[WaterSummaryRecord],
    ) -> list[WaterSummaryRecord]:
        merged: dict[tuple[str, str, int], WaterSummaryRecord] = {}
        for rows in groups:
            for row in rows:
                key = (row.group_id, row.user_id, row.record_date)
                existing = merged.get(key)
                if existing is None:
                    merged[key] = row
                    continue
                hourly_counts = [0] * 24
                for hour in range(24):
                    hourly_counts[hour] = int(existing.hourly_counts[hour]) + int(
                        row.hourly_counts[hour]
                    )
                merged[key] = WaterSummaryRecord(
                    group_id=row.group_id,
                    user_id=row.user_id,
                    record_date=row.record_date,
                    msg_count=int(existing.msg_count) + int(row.msg_count),
                    active_hours=int(existing.active_hours) + int(row.active_hours),
                    hourly_counts=hourly_counts,
                    created_at=min(int(existing.created_at), int(row.created_at)),
                    updated_at=max(int(existing.updated_at), int(row.updated_at)),
                )
        return sorted(
            merged.values(),
            key=lambda item: (item.record_date, item.group_id, item.user_id),
        )

    async def _fetch_archived_summaries_in_window(
        self,
        start_date: int,
        end_date: int,
        *,
        group_ids: list[str] | None = None,
        user_id: str | None = None,
        preserve_order: bool = True,
    ) -> list[WaterSummaryRecord]:
        month_keys = self._iter_month_keys(start_date, end_date)

        async def _query_for_key(shard_key: str) -> list[WaterSummaryRecord]:
            time_ctx = arrow.get(shard_key, "YYYY_MM").datetime
            async with water_summary.read_session(
                time_ctx=time_ctx,
                cold_policy=ColdPolicy.HYDRATE,
            ) as session:
                return await WaterArchivedSummaryOps(session).get_summaries_in_window(
                    start_date=start_date,
                    end_date=end_date,
                    group_ids=group_ids,
                    user_id=user_id,
                    preserve_order=preserve_order,
                )

        results = await asyncio.gather(*(_query_for_key(key) for key in month_keys))
        return self._merge_summary_records(*results)

    async def _get_archived_first_summary_record_date(
        self,
        *,
        end_date: int,
        group_ids: list[str] | None = None,
    ) -> int | None:
        if end_date < 19000101:
            return None
        month_keys = self._iter_month_keys(19000101, end_date)
        for shard_key in month_keys:
            time_ctx = arrow.get(shard_key, "YYYY_MM").datetime
            async with water_summary.read_session(
                time_ctx=time_ctx,
                cold_policy=ColdPolicy.HYDRATE,
            ) as session:
                first_date = await WaterArchivedSummaryOps(
                    session
                ).get_first_summary_record_date(group_ids=group_ids)
            if first_date is not None:
                return first_date
        return None

    @staticmethod
    def _build_period_aggregates(
        summaries: Sequence[WaterSummaryRecord],
    ) -> dict[str, tuple[int, int, int, list[int]]]:
        by_user: dict[str, _PeriodAggregateBucket] = {}
        for item in summaries:
            bucket = by_user.setdefault(
                item.user_id,
                _PeriodAggregateBucket(
                    msg_count=0,
                    active_days=set(),
                    active_hours=0,
                    hourly_counts=[0] * 24,
                    group_ids=set(),
                ),
            )
            bucket.msg_count += int(item.msg_count)
            bucket.active_days.add(int(item.record_date))
            bucket.active_hours += int(item.active_hours)
            bucket.group_ids.add(str(item.group_id))
            hourly_counts = bucket.hourly_counts
            for hour, count in enumerate(item.hourly_counts):
                hourly_counts[hour] += int(count)
        return {
            user_id: (
                data.msg_count,
                len(data.active_days),
                data.active_hours,
                data.hourly_counts,
            )
            for user_id, data in by_user.items()
            if data.msg_count > 0
        }

    @staticmethod
    def _build_entity_period_aggregates(
        summaries: Sequence[WaterSummaryRecord],
        entity_key_resolver: Callable[[WaterSummaryRecord], str],
    ) -> dict[str, tuple[int, int, int, list[int], int]]:
        by_entity: dict[str, _PeriodAggregateBucket] = {}
        for item in summaries:
            entity_id = str(entity_key_resolver(item))
            bucket = by_entity.setdefault(
                entity_id,
                _PeriodAggregateBucket(
                    msg_count=0,
                    active_days=set(),
                    active_hours=0,
                    hourly_counts=[0] * 24,
                    group_ids=set(),
                ),
            )
            bucket.msg_count += int(item.msg_count)
            bucket.active_days.add(int(item.record_date))
            bucket.active_hours += int(item.active_hours)
            bucket.group_ids.add(str(item.group_id))
            for hour, count in enumerate(item.hourly_counts):
                bucket.hourly_counts[hour] += int(count)
        return {
            entity_id: (
                data.msg_count,
                len(data.active_days),
                data.active_hours,
                data.hourly_counts,
                len(data.group_ids),
            )
            for entity_id, data in by_entity.items()
            if data.msg_count > 0
        }

    @staticmethod
    def normalize_season_name(name: str) -> str:
        normalized = unicodedata.normalize("NFKC", name).strip().casefold()
        return " ".join(normalized.split())

    @staticmethod
    def _to_season_record(row: WaterActivitySeason) -> WaterActivitySeasonRecord:
        return WaterActivitySeasonRecord(
            season_id=row.season_id,
            name=row.name,
            normalized_name=row.normalized_name,
            description=row.description,
            start_date=row.start_date,
            end_date=row.end_date,
            status=row.status,
            published_at=row.published_at,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _sum_hourly(items: Sequence[WaterSummaryRecord]) -> list[int]:
        hourly = [0] * 24
        for item in items:
            source = list((item.hourly_counts or [0] * 24)[:24])
            if len(source) < 24:
                source.extend([0] * (24 - len(source)))
            for idx, count in enumerate(source):
                hourly[idx] += int(count)
        return hourly

    @staticmethod
    def _natural_rank_sort_key(
        item: tuple[str, int, int, int, list[int], int] | tuple[str, int, int, int],
    ) -> tuple[int, int, int, str]:
        entity_id, msg_count, active_days, active_hours, *_rest = item
        return (-msg_count, -active_days, -active_hours, entity_id)

    @classmethod
    def _build_previous_rank_map(
        cls,
        aggregates: Mapping[str, tuple[int, int, int, list[int], int]]
        | Mapping[str, tuple[int, int, int, list[int]]],
        target_entity_ids: Sequence[str],
    ) -> dict[str, int]:
        target_keys: dict[str, tuple[int, int, int, str]] = {}
        for entity_id in target_entity_ids:
            aggregate = aggregates.get(entity_id)
            if aggregate is None:
                continue
            msg_count, active_days, active_hours, *_rest = aggregate
            target_keys[entity_id] = cls._natural_rank_sort_key(
                (entity_id, msg_count, active_days, active_hours)
            )
        if not target_keys:
            return {}

        previous_ranks = dict.fromkeys(target_keys, 1)
        for candidate_id, aggregate in aggregates.items():
            msg_count, active_days, active_hours, *_rest = aggregate
            candidate_key = cls._natural_rank_sort_key(
                (candidate_id, msg_count, active_days, active_hours)
            )
            for entity_id, target_key in target_keys.items():
                if candidate_key < target_key:
                    previous_ranks[entity_id] += 1
        return previous_ranks

    @staticmethod
    def _build_natural_overview_from_aggregates(
        current_aggregates: Mapping[str, tuple[int, int, int, list[int], int]],
        previous_aggregates: Mapping[str, tuple[int, int, int, list[int], int]],
    ) -> NaturalRankOverview:
        hourly_counts = [0] * 24
        total_msg_count = 0
        for (
            msg_count,
            _active_days,
            _active_hours,
            entity_hourly,
            _group_count,
        ) in current_aggregates.values():
            total_msg_count += msg_count
            for hour, count in enumerate(entity_hourly):
                hourly_counts[hour] += int(count)

        previous_total_msg_count = sum(
            msg_count
            for msg_count, _active_days, _active_hours, _hourly, _group_count in (
                previous_aggregates.values()
            )
        )
        return NaturalRankOverview(
            total_msg_count=total_msg_count,
            active_entity_count=len(current_aggregates),
            hourly_counts=hourly_counts,
            previous_total_msg_count=previous_total_msg_count,
        )

    @staticmethod
    def _build_user_season_rank(
        summaries: Sequence[WaterSummaryRecord],
    ) -> list[SeasonUserAggregate]:
        by_user: dict[str, list[WaterSummaryRecord]] = defaultdict(list)
        for item in summaries:
            by_user[item.user_id].append(item)
        ordered = sorted(
            (
                (
                    user_id,
                    sum(int(row.msg_count) for row in rows),
                    len({row.record_date for row in rows}),
                    sum(int(row.active_hours) for row in rows),
                    WaterRepository._sum_hourly(rows),
                )
                for user_id, rows in by_user.items()
            ),
            key=lambda item: (-item[1], -item[2], -item[3], item[0]),
        )
        return [
            SeasonUserAggregate(
                user_id=user_id,
                msg_count=msg_count,
                active_days=active_days,
                active_hours=active_hours,
                hourly_counts=hourly_counts,
                rank=index,
            )
            for index, (
                user_id,
                msg_count,
                active_days,
                active_hours,
                hourly_counts,
            ) in enumerate(ordered, 1)
            if msg_count > 0
        ]

    @staticmethod
    def _build_group_season_rank(
        summaries: Sequence[WaterSummaryRecord],
    ) -> list[SeasonGroupAggregate]:
        by_group: dict[str, list[WaterSummaryRecord]] = defaultdict(list)
        for item in summaries:
            by_group[item.group_id].append(item)
        ordered = sorted(
            (
                (
                    group_id,
                    sum(int(row.msg_count) for row in rows),
                    len({row.record_date for row in rows}),
                    sum(int(row.active_hours) for row in rows),
                    WaterRepository._sum_hourly(rows),
                )
                for group_id, rows in by_group.items()
            ),
            key=lambda item: (-item[1], -item[2], -item[3], item[0]),
        )
        return [
            SeasonGroupAggregate(
                group_id=group_id,
                msg_count=msg_count,
                active_days=active_days,
                active_hours=active_hours,
                hourly_counts=hourly_counts,
                rank=index,
            )
            for index, (
                group_id,
                msg_count,
                active_days,
                active_hours,
                hourly_counts,
            ) in enumerate(ordered, 1)
            if msg_count > 0
        ]

    async def _build_matrix_season_rank(
        self,
        summaries: Sequence[WaterSummaryRecord],
    ) -> list[SeasonMatrixAggregate]:
        if not summaries:
            return []
        group_ids = sorted({item.group_id for item in summaries})
        group_map = await self.get_or_create_group_matrix_ids(group_ids)
        by_matrix: dict[str, list[WaterSummaryRecord]] = defaultdict(list)
        for item in summaries:
            matrix_id = group_map.get(item.group_id)
            if matrix_id:
                by_matrix[matrix_id].append(item)
        ordered = sorted(
            (
                (
                    matrix_id,
                    sum(int(row.msg_count) for row in rows),
                    len({row.record_date for row in rows}),
                    sum(int(row.active_hours) for row in rows),
                    self._sum_hourly(rows),
                )
                for matrix_id, rows in by_matrix.items()
            ),
            key=lambda item: (-item[1], -item[2], -item[3], item[0]),
        )
        return [
            SeasonMatrixAggregate(
                matrix_id=matrix_id,
                msg_count=msg_count,
                active_days=active_days,
                active_hours=active_hours,
                hourly_counts=hourly_counts,
                rank=index,
            )
            for index, (
                matrix_id,
                msg_count,
                active_days,
                active_hours,
                hourly_counts,
            ) in enumerate(ordered, 1)
            if msg_count > 0
        ]

    @staticmethod
    def _gen_matrix_id() -> str:
        return f"mtx_{token_hex(4)}"

    def _get_group_matrix_lock(self, group_id: str) -> asyncio.Lock:
        lock = self._group_matrix_locks.get(group_id)
        if lock is None:
            lock = asyncio.Lock()
            self._group_matrix_locks[group_id] = lock
        return lock

    def _get_merge_state_lock(self, group_id: str) -> asyncio.Lock:
        lock = self._merge_state_locks.get(group_id)
        if lock is None:
            lock = asyncio.Lock()
            self._merge_state_locks[group_id] = lock
        return lock

    async def warm_up_group_matrix_cache(self) -> None:
        async with water_core_db.session(commit=False) as session:
            self._group_matrix_cache = await WaterGroupMatrixMapOps(
                session
            ).get_all_mappings()

    async def get_or_create_group_matrix_id(self, group_id: str) -> str:
        if group_id in self._group_matrix_cache:
            return self._group_matrix_cache[group_id]

        async with self._get_group_matrix_lock(group_id):
            if group_id in self._group_matrix_cache:
                return self._group_matrix_cache[group_id]

            now_ts = get_current_time()
            async with water_core_db.session(commit=True) as session:
                mapping_ops = WaterGroupMatrixMapOps(session)
                matrix_id = await mapping_ops.get_matrix_id_by_group(group_id)
                if matrix_id is None:
                    # mtx_ + 8位hex，碰撞概率极低；做轻量重试避免误并矩阵。
                    all_matrix_ids = set(
                        (await mapping_ops.get_all_mappings()).values()
                    )
                    matrix_id = self._gen_matrix_id()
                    retry = 0
                    while matrix_id in all_matrix_ids and retry < 5:
                        matrix_id = self._gen_matrix_id()
                        retry += 1
                    await mapping_ops.upsert_mapping(
                        {
                            "group_id": group_id,
                            "matrix_id": matrix_id,
                            "created_at": now_ts,
                            "updated_at": now_ts,
                        }
                    )
                self._group_matrix_cache[group_id] = matrix_id
                return matrix_id

    async def get_or_create_group_matrix_ids(
        self,
        group_ids: list[str],
    ) -> dict[str, str]:
        if not group_ids:
            return {}

        result: dict[str, str] = {}
        missing = [
            group_id
            for group_id in group_ids
            if group_id not in self._group_matrix_cache
        ]
        for group_id in group_ids:
            if group_id in self._group_matrix_cache:
                result[group_id] = self._group_matrix_cache[group_id]

        if missing:
            async with water_core_db.session(commit=False) as session:
                db_map = await WaterGroupMatrixMapOps(session).get_mappings_by_groups(
                    missing
                )
            for group_id, matrix_id in db_map.items():
                self._group_matrix_cache[group_id] = matrix_id
                result[group_id] = matrix_id

            really_missing = [
                group_id for group_id in missing if group_id not in db_map
            ]
            for group_id in really_missing:
                result[group_id] = await self.get_or_create_group_matrix_id(group_id)

        return result

    async def map_group_to_matrix(self, group_id: str, matrix_id: str) -> None:
        old_matrix_id = await self.get_or_create_group_matrix_id(group_id)
        if old_matrix_id == matrix_id:
            return

        now_ts = get_current_time()
        async with water_core_db.session(commit=True) as session:
            await WaterGroupMatrixMapOps(session).upsert_mapping(
                {
                    "group_id": group_id,
                    "matrix_id": matrix_id,
                    "created_at": now_ts,
                    "updated_at": now_ts,
                }
            )
        self._group_matrix_cache[group_id] = matrix_id

    async def _save_buffered(self, ctx: WaterMessageContext) -> None:
        await water_writer.add(ctx.to_write_payload())

    async def _save_immediate(self, ctx: WaterMessageContext) -> None:
        dt = arrow.get(ctx.created_at).to("Asia/Shanghai").datetime
        time_ctx = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        async with water_message.write_session(time_ctx=time_ctx) as session:
            await WaterMessageOps(session).bulk_insert_water_message([ctx.to_payload()])

    async def save_message(
        self,
        group_id: str,
        user_id: str,
        created_at: int,
        policy: WritePolicy = WritePolicy.BUFFERED,
    ) -> None:
        ctx = WaterMessageContext(
            group_id=group_id,
            user_id=user_id,
            created_at=created_at,
        )

        if policy == WritePolicy.BUFFERED:
            await self._save_buffered(ctx)
        elif policy == WritePolicy.IMMEDIATE:
            await self._save_immediate(ctx)

    async def save_summary_batch(self, summaries: list[WaterSummaryPayload]) -> None:
        if not summaries:
            return
        routed: dict[str, list[WaterSummaryPayload]] = defaultdict(list)
        hot_payloads: list[WaterSummaryPayload] = []
        for item in summaries:
            route_ctx = (
                arrow.get(str(item["record_date"]), "YYYYMMDD")
                .to("Asia/Shanghai")
                .floor("month")
            )
            routed[route_ctx.format("YYYY_MM")].append(item)
            if self._is_hot_summary_date(int(item["record_date"])):
                hot_payloads.append(item)

        for route_key, chunk in routed.items():
            route_ctx = arrow.get(route_key, "YYYY_MM").datetime
            async with water_summary.write_session(time_ctx=route_ctx) as session:
                await WaterArchivedSummaryOps(session).bulk_upsert_summary(chunk)

        if hot_payloads:
            async with water_core_db.session(commit=True) as session:
                await WaterSummaryOps(session).bulk_upsert_summary(hot_payloads)

    async def import_message_batch(
        self,
        messages: list[WaterMessagePayload],
    ) -> int:
        if not messages:
            return 0

        routed: dict[str, list[WaterMessagePayload]] = defaultdict(list)
        for item in messages:
            route_ctx = (
                arrow.get(str(item["record_date"]), "YYYYMMDD")
                .to("Asia/Shanghai")
                .floor("month")
            )
            routed[route_ctx.format("YYYY_MM")].append(item)

        inserted = 0
        for route_key, chunk in routed.items():
            route_ctx = arrow.get(route_key, "YYYY_MM").datetime
            async with water_message.write_session(time_ctx=route_ctx) as session:
                inserted += await WaterMessageOps(session).bulk_insert_water_message(
                    chunk
                )
        return inserted

    async def reset_runtime_data(self, *, preserve_seasons: bool = True) -> None:
        async with water_core_db.session(commit=True) as session:
            tables: list[type[WaterCoreBase]] = [
                WaterDailySummary,
                WaterPenaltyLog,
                WaterSettlementJob,
                WaterMatrixMergeState,
            ]
            if not preserve_seasons:
                tables.append(WaterActivitySeason)

            for model in tables:
                await session.execute(delete(model))

            for model in (
                WaterGroupMatrixMap,
                WaterGroupUserTotal,
                WaterGroupTotal,
                WaterMatrixLevel,
                WaterGlobalLevel,
                WaterMatrixTotalLevel,
                WaterUserAchievement,
            ):
                await session.execute(delete(model))

        for file_path in water_message.base_dir.glob(f"{water_message.prefix}_*"):
            if "".join(file_path.suffixes) not in {".db", ".db.zst"}:
                continue
            full_path = str(file_path)
            await db_manager.dispose(full_path)
            if await asyncio.to_thread(os.path.exists, full_path):
                await asyncio.to_thread(os.remove, full_path)
        if await asyncio.to_thread(water_message.manifest_path.exists):
            await asyncio.to_thread(os.remove, water_message.manifest_path)
        for file_path in water_summary.base_dir.glob(f"{water_summary.prefix}_*"):
            if "".join(file_path.suffixes) not in {".db", ".db.zst"}:
                continue
            full_path = str(file_path)
            await db_manager.dispose(full_path)
            if await asyncio.to_thread(os.path.exists, full_path):
                await asyncio.to_thread(os.remove, full_path)
        if await asyncio.to_thread(water_summary.manifest_path.exists):
            await asyncio.to_thread(os.remove, water_summary.manifest_path)

        self._group_matrix_cache.clear()
        self._group_matrix_locks.clear()
        self._merge_state_locks.clear()
        water_message._initialized_shards.clear()
        water_message._manifest = None
        water_summary._initialized_shards.clear()
        water_summary._manifest = None

    async def get_today_leaderboard(
        self, group_id: str, limit: int = 20
    ) -> list[RankItem]:
        await water_writer.flush_now()

        now = arrow.get(get_current_time()).to("Asia/Shanghai")
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp
        yesterday_int = int(now.shift(days=-1).format("YYYYMMDD"))

        async def _fetch_today() -> Sequence[Row[tuple[str, int]]]:
            async with water_message.read_session(
                time_ctx=now.datetime,
            ) as session:
                return await WaterMessageOps(session).get_top_users(
                    group_id,
                    start_ts,
                    end_ts,
                    limit,
                )

        async def _fetch_yesterday() -> dict[str, int]:
            async with water_core_db.session(commit=False) as session:
                return await WaterSummaryOps(session).get_ranks_by_date(
                    group_id,
                    yesterday_int,
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

        now = arrow.get(get_current_time()).to("Asia/Shanghai")
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp

        async with water_message.read_session(
            time_ctx=now.datetime,
        ) as session:
            return await WaterMessageOps(session).get_today_group_rank(
                group_id, start_ts, end_ts
            )

    async def get_users_hourly_distribution(
        self, group_id: str, user_ids: list[str]
    ) -> dict[str, list[int]]:
        if not user_ids:
            return {}

        await water_writer.flush_now()

        now = arrow.get(get_current_time()).to("Asia/Shanghai")
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp

        async with water_message.read_session(
            time_ctx=now.datetime,
        ) as session:
            raw_timestamps = await WaterMessageOps(session).get_users_timestamps(
                group_id, user_ids, start_ts, end_ts
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
    ) -> list[GlobalPeriodRankItem]:
        current_rows = await self.get_summaries_in_window(start_date, end_date)
        previous_rows = await self.get_summaries_in_window(
            previous_start_date,
            previous_end_date,
        )
        current_aggregates = self._build_period_aggregates(current_rows)
        previous_aggregates = self._build_period_aggregates(previous_rows)
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
    ) -> GlobalPeriodOverview:
        current_rows, previous_rows = await asyncio.gather(
            self.get_summaries_in_window(start_date, end_date),
            self.get_summaries_in_window(previous_start_date, previous_end_date),
        )
        current_hourly = self._sum_hourly(current_rows)
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
        group_ids: list[str] | None = None
        if scope == "group":
            group_ids = [group_id]
        elif scope == "matrix":
            matrix_id = await self.get_or_create_group_matrix_id(group_id)
            matrix_group_ids = await self.get_groups_by_matrix_id(matrix_id)
            group_ids = matrix_group_ids or [group_id]

        hot_start_date = self._hot_summary_start_date()
        first_archived_date = await self._get_archived_first_summary_record_date(
            end_date=self._previous_date(hot_start_date),
            group_ids=group_ids,
        )
        if first_archived_date is not None:
            return first_archived_date

        async with water_core_db.session(commit=False) as session:
            return await WaterSummaryOps(session).get_first_summary_record_date(
                group_ids=group_ids
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
    ) -> list[NaturalRankItem]:
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
    ) -> NaturalPeriodRankSnapshot:
        started = perf_counter()
        current_rows, previous_rows = await asyncio.gather(
            self._resolve_rank_scope_summaries(
                subject=subject,
                scope=scope,
                group_id=group_id,
                start_date=start_date,
                end_date=end_date,
            ),
            self._resolve_rank_scope_summaries(
                subject=subject,
                scope=scope,
                group_id=group_id,
                start_date=previous_start_date,
                end_date=previous_end_date,
            ),
        )
        snapshot = await self._build_natural_period_snapshot_from_rows(
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
    ) -> NaturalPeriodRankSnapshot:
        current_aggregates = await self._build_rank_entity_aggregates(
            subject, current_rows
        )
        previous_aggregates = await self._build_rank_entity_aggregates(
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
                )
                for entity_id, (
                    msg_count,
                    active_days,
                    active_hours,
                    hourly_counts,
                    group_count,
                ) in current_aggregates.items()
            ),
            key=self._natural_rank_sort_key,
        )
        previous_ranks = self._build_previous_rank_map(
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
            )
            for current_rank, (
                entity_id,
                msg_count,
                active_days,
                active_hours,
                hourly_counts,
                group_count,
            ) in enumerate(ordered_current, 1)
        ]
        overview = self._build_natural_overview_from_aggregates(
            current_aggregates,
            previous_aggregates,
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
    ) -> NaturalRankOverview:
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

    async def get_natural_day_snapshot(
        self,
        *,
        subject: WaterRankSubject,
        scope: WaterRankScope,
        group_id: str,
        limit: int = 10,
    ) -> NaturalPeriodRankSnapshot:
        started = perf_counter()
        await water_writer.flush_now()
        now = arrow.get(get_current_time()).to("Asia/Shanghai")
        record_date = int(now.format("YYYYMMDD"))
        current_rows, previous_rows = await asyncio.gather(
            self._collect_realtime_daily_rows(
                scope=scope,
                group_id=group_id,
                record_date=record_date,
            ),
            self._resolve_previous_day_rows(
                scope=scope,
                group_id=group_id,
                record_date=record_date,
            ),
        )
        snapshot = await self._build_natural_period_snapshot_from_rows(
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
    ) -> list[NaturalRankItem]:
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
    ) -> NaturalRankOverview:
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
    ) -> list[WaterDailyReportCandidate]:
        if not working_group_ids:
            return []
        rows = await self.get_summaries_in_window(
            record_date,
            record_date,
            group_ids=list(working_group_ids),
        )
        aggregates = self._build_entity_period_aggregates(
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

    async def get_group_report_summary_snapshot(
        self,
        *,
        group_id: str,
        record_date: int,
        limit: int = 10,
    ) -> WaterGroupReportSnapshot | None:
        current_rows, previous_rows = await asyncio.gather(
            self.get_summaries_in_window(
                record_date,
                record_date,
                group_ids=[group_id],
            ),
            self.get_summaries_in_window(
                self._previous_date(record_date),
                self._previous_date(record_date),
                group_ids=[group_id],
            ),
        )
        return self._build_group_report_snapshot_from_rows(
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
    ) -> WaterGroupReportSnapshot | None:
        await water_writer.flush_now()
        now = arrow.get(now_ts or get_current_time()).to("Asia/Shanghai")
        record_date = int(now.format("YYYYMMDD"))
        current_rows, previous_rows = await asyncio.gather(
            self._collect_realtime_daily_rows(
                scope="group",
                group_id=group_id,
                record_date=record_date,
            ),
            self.get_summaries_in_window(
                self._previous_date(record_date),
                self._previous_date(record_date),
                group_ids=[group_id],
            ),
        )
        return self._build_group_report_snapshot_from_rows(
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
        working_group_ids: Sequence[str],
        radius: int = 2,
    ) -> WaterGroupDailyRankSnapshot | None:
        if not working_group_ids or group_id not in working_group_ids:
            return None
        previous_date = self._previous_date(record_date)
        current_rows, previous_rows = await asyncio.gather(
            self.get_summaries_in_window(
                record_date,
                record_date,
                group_ids=list(working_group_ids),
            ),
            self.get_summaries_in_window(
                previous_date,
                previous_date,
                group_ids=list(working_group_ids),
            ),
        )
        return self._build_group_daily_rank_snapshot_from_rows(
            focus_group_id=group_id,
            record_date=record_date,
            current_rows=current_rows,
            previous_rows=previous_rows,
            radius=radius,
        )

    def _build_group_report_snapshot_from_rows(
        self,
        *,
        group_id: str,
        record_date: int,
        current_rows: Sequence[WaterSummaryRecord],
        previous_rows: Sequence[WaterSummaryRecord],
        limit: int,
    ) -> WaterGroupReportSnapshot | None:
        if not current_rows:
            return None
        current_aggregates = self._build_entity_period_aggregates(
            current_rows,
            lambda item: item.user_id,
        )
        previous_aggregates = self._build_entity_period_aggregates(
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
            key=self._natural_rank_sort_key,
        )
        previous_ranks = self._build_previous_rank_map(
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
        current_hourly = self._sum_hourly(current_rows)
        previous_hourly = self._sum_hourly(previous_rows)
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
    ) -> WaterGroupDailyRankSnapshot | None:
        if not current_rows:
            return None

        current_aggregates = self._build_entity_period_aggregates(
            current_rows,
            lambda item: item.group_id,
        )
        if focus_group_id not in current_aggregates:
            return None

        group_active_users: dict[str, set[str]] = defaultdict(set)
        group_hourly_counts: dict[str, list[int]] = defaultdict(lambda: [0] * 24)
        for row in current_rows:
            group_id = str(row.group_id)
            group_active_users[group_id].add(str(row.user_id))
            hourly_counts = group_hourly_counts[group_id]
            for hour, count in enumerate(row.hourly_counts):
                hourly_counts[hour] += int(count)

        previous_aggregates = self._build_entity_period_aggregates(
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
            key=self._natural_rank_sort_key,
        )
        previous_ranks = self._build_previous_rank_map(
            previous_aggregates,
            [entity_id for entity_id, *_rest in ordered_current],
        )
        items = [
            WaterGroupDailyRankItem(
                group_id=entity_id,
                msg_count=msg_count,
                active_user_count=len(group_active_users.get(entity_id, set())),
                active_hours=sum(
                    1 for count in group_hourly_counts.get(entity_id, [0] * 24) if count
                ),
                hourly_counts=group_hourly_counts.get(entity_id, hourly_counts),
                current_rank=current_rank,
                trend=(
                    previous_ranks[entity_id] - current_rank
                    if entity_id in previous_ranks
                    else None
                ),
            )
            for current_rank, (
                entity_id,
                msg_count,
                _active_days,
                _active_hours,
                hourly_counts,
                _group_count,
            ) in enumerate(ordered_current, 1)
        ]
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

        start = max(0, focus_index - radius)
        end = min(len(items), focus_index + radius + 1)
        focus_item = items[focus_index]
        return WaterGroupDailyRankSnapshot(
            focus_group_id=focus_group_id,
            record_date=record_date,
            total_groups=len(items),
            focus_rank=focus_item.current_rank,
            focus_trend=focus_item.trend,
            leaderboard=items[start:end],
            has_hidden_before=start > 0,
            has_hidden_after=end < len(items),
        )

    async def _build_rank_entity_aggregates(
        self,
        subject: WaterRankSubject,
        summaries: Sequence[WaterSummaryRecord],
    ) -> dict[str, tuple[int, int, int, list[int], int]]:
        if subject == "user":
            return self._build_entity_period_aggregates(
                summaries,
                lambda item: item.user_id,
            )
        if subject == "group":
            return self._build_entity_period_aggregates(
                summaries, lambda item: item.group_id
            )
        if not summaries:
            return {}
        group_map = await self.get_or_create_group_matrix_ids(
            sorted({item.group_id for item in summaries})
        )
        return self._build_entity_period_aggregates(
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
        started = perf_counter()
        _ = subject
        if scope == "global":
            rows = await self.get_summaries_in_window(
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
            rows = await self.get_summaries_in_window(
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
        matrix_id = await self.get_or_create_group_matrix_id(group_id)
        matrix_group_ids = await self.get_groups_by_matrix_id(matrix_id)
        if not matrix_group_ids:
            matrix_group_ids = [group_id]
        rows = await self.get_summaries_in_window(
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

    async def _collect_realtime_daily_rows(
        self,
        *,
        scope: WaterRankScope,
        group_id: str,
        record_date: int,
    ) -> list[WaterSummaryRecord]:
        started = perf_counter()
        now = arrow.get(str(record_date), "YYYYMMDD").to("Asia/Shanghai")
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp

        async def _stats_in_shard(
            session: AsyncSession,
        ) -> Sequence[Row[tuple[str, str, int, int]]]:
            return await WaterMessageOps(session).aggregate_daily_stats(
                start_ts,
                end_ts,
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
            matrix_id = await self.get_or_create_group_matrix_id(group_id)
            group_ids = await self.get_groups_by_matrix_id(matrix_id)
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
        previous_date = self._previous_date(record_date)
        return await self._resolve_rank_scope_summaries(
            subject="user",
            scope=scope,
            group_id=group_id,
            start_date=previous_date,
            end_date=previous_date,
        )

    async def collect_daily_aggregates(
        self,
        target_date: arrow.Arrow,
    ) -> list[DailyAggregateItem]:
        day_start = target_date.floor("day")
        day_end = target_date.ceil("day")
        start_ts = day_start.int_timestamp
        end_ts = day_end.int_timestamp

        async def _stats_in_shard(
            session: AsyncSession,
        ) -> Sequence[Row[tuple[str, str, int, int]]]:
            return await WaterMessageOps(session).aggregate_daily_stats(
                start_ts,
                end_ts,
            )

        async def _hourly_in_shard(
            session: AsyncSession,
        ) -> Sequence[tuple[str, str, int, int]]:
            return await WaterMessageOps(session).aggregate_daily_hourly_stats(
                start_ts,
                end_ts,
            )

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
        stale_after: int = SETTLEMENT_STALE_SECONDS,
    ) -> tuple[bool, str]:
        now_ts = get_current_time()
        async with water_core_db.session(commit=True) as session:
            ops = WaterSettlementJobOps(session)
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
        now_ts = get_current_time()
        async with water_core_db.session(commit=True) as session:
            await WaterSettlementJobOps(session).mark_success(record_date, now_ts)

    async def mark_settlement_failed(self, record_date: int, error: str) -> None:
        now_ts = get_current_time()
        async with water_core_db.session(commit=True) as session:
            await WaterSettlementJobOps(session).mark_failed(record_date, now_ts, error)

    async def apply_daily_settlement(
        self,
        target_date: arrow.Arrow,
        aggregates: list[DailyAggregateItem],
        chunk_size: int = 500,
        chunk_pause_seconds: float = 0.1,
        prune_after_settlement: bool = True,
    ) -> None:
        _ = prune_after_settlement
        if not aggregates:
            return

        now_ts = get_current_time()
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
            group_stats_ops = WaterGroupStatsOps(session)
            level_ops = WaterLevelOps(session)
            penalty_ops = WaterPenaltyOps(session)

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
            return await WaterAchievementOps(session).bulk_unlock(payloads)

    async def get_user_achievement_items(
        self,
        user_id: str,
    ) -> list[tuple[str, str, str, int]]:
        async with water_core_db.session(commit=False) as session:
            return await WaterAchievementOps(session).get_unlocked_items(user_id)

    async def create_activity_season(
        self,
        payload: WaterActivitySeasonPayload,
    ) -> int:
        async with water_core_db.session(commit=True) as session:
            return await WaterActivitySeasonOps(session).create(payload)

    async def get_activity_season(
        self,
        season_id: str,
    ) -> WaterActivitySeasonRecord | None:
        async with water_core_db.session(commit=False) as session:
            row = await WaterActivitySeasonOps(session).get_by_season_id(season_id)
        if row is None:
            return None
        return self._to_season_record(row)

    async def update_activity_season(
        self,
        season_id: str,
        **values: object,
    ) -> bool:
        async with water_core_db.session(commit=True) as session:
            affected = await WaterActivitySeasonOps(session).update(season_id, **values)
        return affected > 0

    async def delete_activity_season(self, season_id: str) -> bool:
        async with water_core_db.session(commit=True) as session:
            affected = await WaterActivitySeasonOps(session).delete(season_id)
        return affected > 0

    async def list_activity_seasons(
        self,
        statuses: list[str] | None = None,
    ) -> list[WaterActivitySeasonRecord]:
        async with water_core_db.session(commit=False) as session:
            rows = await WaterActivitySeasonOps(session).list_by_status(statuses)
        return [self._to_season_record(row) for row in rows]

    async def list_current_activity_seasons(
        self,
        today: int,
    ) -> list[WaterActivitySeasonRecord]:
        async with water_core_db.session(commit=False) as session:
            rows = await WaterActivitySeasonOps(session).list_current_published(today)
        return [self._to_season_record(row) for row in rows]

    async def search_published_activity_seasons(
        self,
        keyword: str,
    ) -> list[WaterActivitySeasonRecord]:
        async with water_core_db.session(commit=False) as session:
            rows = await WaterActivitySeasonOps(session).search_published_candidates(
                keyword
            )
        return [self._to_season_record(row) for row in rows]

    async def get_penalty_log(self, penalty_id: int) -> WaterPenaltyLog | None:
        async with water_core_db.session(commit=False) as session:
            return await WaterPenaltyOps(session).get_penalty_by_id(penalty_id)

    async def get_user_recent_summaries(
        self,
        user_id: str,
        matrix_id: str,
        start_date: int,
        end_date: int,
    ) -> list[WaterSummaryRecord]:
        hot_start_date = self._hot_summary_start_date()
        async with water_core_db.session(commit=False) as session:
            group_ids = await WaterGroupMatrixMapOps(session).get_groups_by_matrix(
                matrix_id
            )
            if not group_ids:
                return []

            hot_rows: list[WaterSummaryRecord] = []
            if end_date >= hot_start_date:
                hot_rows = await WaterSummaryOps(session).get_user_recent_summaries(
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
        hot_start_date = self._hot_summary_start_date()
        hot_rows: list[WaterSummaryRecord] = []
        async with water_core_db.session(commit=False) as session:
            if end_date >= hot_start_date:
                hot_rows = await WaterSummaryOps(session).get_summaries_in_window(
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
    ) -> list[SeasonUserAggregate]:
        summaries = await self.get_summaries_in_window(start_date, end_date)
        return self._build_user_season_rank(summaries)

    async def get_group_season_rankings(
        self,
        start_date: int,
        end_date: int,
    ) -> list[SeasonGroupAggregate]:
        summaries = await self.get_summaries_in_window(start_date, end_date)
        return self._build_group_season_rank(summaries)

    async def get_matrix_season_rankings(
        self,
        start_date: int,
        end_date: int,
    ) -> list[SeasonMatrixAggregate]:
        summaries = await self.get_summaries_in_window(start_date, end_date)
        return await self._build_matrix_season_rank(summaries)

    async def get_user_global_level(self, user_id: str) -> tuple[int, int, int] | None:
        async with water_core_db.session(commit=False) as session:
            return await WaterLevelOps(session).get_global_level(user_id)

    async def get_user_global_rank(self, user_id: str) -> int | None:
        async with water_core_db.session(commit=False) as session:
            return await WaterLevelOps(session).get_user_global_rank(user_id)

    async def get_groups_by_matrix_id(self, matrix_id: str) -> list[str]:
        async with water_core_db.session(commit=False) as session:
            return await WaterGroupMatrixMapOps(session).get_groups_by_matrix(matrix_id)

    async def get_user_matrix_level(
        self,
        user_id: str,
        matrix_id: str,
    ) -> tuple[int, int, int] | None:
        async with water_core_db.session(commit=False) as session:
            return await WaterLevelOps(session).get_matrix_level(matrix_id, user_id)

    async def get_user_matrix_rank(self, user_id: str, matrix_id: str) -> int | None:
        async with water_core_db.session(commit=False) as session:
            return await WaterLevelOps(session).get_user_matrix_rank(matrix_id, user_id)

    async def get_group_user_rank(self, group_id: str, user_id: str) -> int | None:
        async with water_core_db.session(commit=False) as session:
            return await WaterGroupStatsOps(session).get_group_user_rank(
                group_id,
                user_id,
            )

    async def get_group_activity_rank(self, group_id: str) -> int | None:
        async with water_core_db.session(commit=False) as session:
            return await WaterGroupStatsOps(session).get_group_activity_rank(group_id)

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
            return await WaterLevelOps(session).get_matrix_rank(matrix_id)

    async def get_matrix_total_level(
        self, matrix_id: str
    ) -> tuple[int, int, int] | None:
        async with water_core_db.session(commit=False) as session:
            return await WaterLevelOps(session).get_matrix_total(matrix_id)

    async def exists_other_global_lv10(self, user_id: str) -> bool:
        async with water_core_db.session(commit=False) as session:
            return await WaterLevelOps(session).exists_other_global_lv10(user_id)

    async def ignore_matrix_suggestion(self, group_id: str) -> bool:
        now_ts = get_current_time()
        async with water_core_db.session(commit=True) as session:
            return await WaterMatrixMergeStateOps(session).set_ignored(group_id, now_ts)

    async def get_ignored_matrix_suggestions(self) -> set[str]:
        async with water_core_db.session(commit=False) as session:
            return await WaterMatrixMergeStateOps(session).get_ignored_groups()

    async def mark_group_first_record_seen(self, group_id: str) -> bool:
        now_ts = get_current_time()
        async with water_core_db.session(commit=True) as session:
            return await WaterMatrixMergeStateOps(session).mark_first_seen(
                group_id,
                now_ts,
            )

    async def get_marked_first_record_groups(self) -> set[str]:
        async with water_core_db.session(commit=False) as session:
            return await WaterMatrixMergeStateOps(session).get_first_seen_groups()

    async def has_matrix_merge_decision(self, group_id: str) -> bool:
        async with water_core_db.session(commit=False) as session:
            state = await WaterMatrixMergeStateOps(session).get_state(group_id)
            if state is None:
                return False
            return state.status in {"merge", "reject"}

    async def get_pending_matrix_suggestion(self, group_id: str) -> dict | None:
        async with water_core_db.session(commit=False) as session:
            state = await WaterMatrixMergeStateOps(session).get_state(group_id)
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
        now_ts = get_current_time()
        async with self._get_merge_state_lock(group_id):
            async with water_core_db.session(commit=True) as session:
                ops = WaterMatrixMergeStateOps(session)
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
        """
        首次合并意向直接生效，后续不可覆盖:
        - 只保留单条状态，状态机: "" -> pending -> merge/reject。
        - 如已是 merge/reject，拒绝覆盖，需超管介入。
        """
        now_ts = get_current_time()
        stale_target_corrected = False
        merge_applied = False
        async with self._get_merge_state_lock(group_id):
            async with water_core_db.session(commit=True) as session:
                ops = WaterMatrixMergeStateOps(session)
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
                    target_groups = await WaterGroupMatrixMapOps(
                        session
                    ).count_groups_by_matrix(target_matrix_id)
                    # 目标矩阵已失效(无群绑定)时，纠偏为当前矩阵，
                    # 避免 A/B 二次同意导致矩阵互换。
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
            merge_ops = WaterMatrixMergeStateOps(session)
            job_ops = WaterSettlementJobOps(session)
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
        now_ts = get_current_time()
        async with water_core_db.session(commit=True) as session:
            penalty_ops = WaterPenaltyOps(session)
            level_ops = WaterLevelOps(session)
            summary_ops = WaterSummaryOps(session)
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

            affected = await penalty_ops.revoke_penalty(penalty_id, now_ts)
            return affected > 0
