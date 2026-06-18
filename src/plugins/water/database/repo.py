"""Water 仓储层。"""

import asyncio
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from heapq import nsmallest
import os
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
from .repo_models import (
    DailyAggregateItem,
    GlobalPeriodOverview,
    GlobalPeriodRankItem,
    NaturalPeriodRankSnapshot,
    NaturalRankItem,
    NaturalRankOverview,
    RankItem,
    SeasonGroupAggregate,
    SeasonMatrixAggregate,
    SeasonUserAggregate,
    WaterActivitySeasonRecord,
    WaterDailyReportCandidate,
    WaterGroupDailyRankItem,
    WaterGroupDailyRankSnapshot,
    WaterGroupReportMember,
    WaterGroupReportSnapshot,
    WaterMessageContext,
    _PeriodAggregateBucket,
    calc_personal_delta_exp,
    calc_weighted_global_exp,
    gen_matrix_id,
)
from .repo_admin import WaterRepositoryAdminMixin
from .repo_reports import WaterRepositoryReportsMixin
from .repo_ranks import WaterRepositoryRanksMixin
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


def _repo_module():
    from . import repo as repo_module

    return repo_module


class WaterRepository(
    WaterRepositoryReportsMixin,
    WaterRepositoryRanksMixin,
    WaterRepositoryAdminMixin,
):
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
        repo_module = _repo_module()
        month_keys = self._iter_month_keys(start_date, end_date)

        async def _query_for_key(shard_key: str) -> list[WaterSummaryRecord]:
            time_ctx = arrow.get(shard_key, "YYYY_MM").datetime
            async with water_summary.read_session(
                time_ctx=time_ctx,
                cold_policy=ColdPolicy.HYDRATE,
            ) as session:
                return await repo_module.WaterArchivedSummaryOps(
                    session
                ).get_summaries_in_window(
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
        repo_module = _repo_module()
        if end_date < 19000101:
            return None
        month_keys = self._iter_month_keys(19000101, end_date)
        for shard_key in month_keys:
            time_ctx = arrow.get(shard_key, "YYYY_MM").datetime
            async with water_summary.read_session(
                time_ctx=time_ctx,
                cold_policy=ColdPolicy.HYDRATE,
            ) as session:
                first_date = await repo_module.WaterArchivedSummaryOps(
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
        return gen_matrix_id()

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
