"""Water 仓储层。"""

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from math import floor, sqrt
from secrets import token_hex

import arrow
from sqlalchemy.engine.row import Row
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.consts import WritePolicy
from src.lib.utils.common import get_current_time, split_list

from .instances import water_core_db, water_message
from .ops import (
    WaterAchievementOps,
    WaterGroupMatrixMapOps,
    WaterLevelOps,
    WaterMatrixMergeStateOps,
    WaterMessageOps,
    WaterPenaltyOps,
    WaterSettlementJobOps,
    WaterSummaryOps,
)
from .tables import (
    WaterCoreBase,
    WaterDailySummary,
    WaterMessageBase,
    WaterPenaltyLog,
)
from .types import (
    WaterAchievementPayload,
    WaterMatrixExpPayload,
    WaterMessagePayload,
    WaterPenaltyPayload,
    WaterSummaryPayload,
    WaterUserExpPayload,
)
from .writers import water_writer

SETTLEMENT_STALE_SECONDS = 60 * 30


@dataclass
class RankItem:
    user_id: str
    msg_count: int
    current_rank: int
    trend: int | None


@dataclass
class WaterMessageContext:
    group_id: str
    user_id: str
    created_at: int

    def to_payload(self) -> WaterMessagePayload:
        return {
            "group_id": self.group_id,
            "user_id": self.user_id,
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


class WaterRepository:
    def __init__(self) -> None:
        self._group_matrix_cache: dict[str, str] = {}
        self._group_matrix_locks: dict[str, asyncio.Lock] = {}
        self._merge_state_locks: dict[str, asyncio.Lock] = {}

    @classmethod
    async def init_all_tables(cls) -> None:
        await water_message.init(WaterMessageBase)
        await water_core_db.init(WaterCoreBase)

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
        await water_writer.add(ctx.to_payload())

    async def _save_immediate(self, ctx: WaterMessageContext) -> None:
        dt = arrow.get(ctx.created_at).datetime
        time_ctx = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        async with water_message.session(time_ctx=time_ctx, commit=True) as session:
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

        async with water_core_db.session(commit=True) as session:
            await WaterSummaryOps(session).bulk_upsert_summary(summaries)

    async def get_today_leaderboard(
        self, group_id: str, limit: int = 20
    ) -> list[RankItem]:
        now = arrow.get(get_current_time())
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp
        yesterday_int = int(now.shift(days=-1).format("YYYYMMDD"))

        async def _fetch_today() -> Sequence[Row[tuple[str, int]]]:
            async with water_message.session(
                time_ctx=now.datetime, commit=False
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
        now = arrow.get(get_current_time())
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp

        async with water_message.session(
            time_ctx=now.datetime,
            commit=False,
        ) as session:
            return await WaterMessageOps(session).get_today_group_rank(
                group_id, start_ts, end_ts
            )

    async def get_users_hourly_distribution(
        self, group_id: str, user_ids: list[str]
    ) -> dict[str, list[int]]:
        if not user_ids:
            return {}

        now = arrow.get(get_current_time())
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp

        async with water_message.session(
            time_ctx=now.datetime,
            commit=False,
        ) as session:
            raw_timestamps = await WaterMessageOps(session).get_users_timestamps(
                group_id, user_ids, start_ts, end_ts
            )

        user_hourly: dict[str, list[int]] = defaultdict(lambda: [0] * 24)
        for uid, ts in raw_timestamps:
            hour = arrow.get(ts).to("local").hour
            user_hourly[uid][hour] += 1
        return dict(user_hourly)

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
        )
        hourly_per_shard = await water_message.map_reduce(
            day_start.datetime,
            day_end.datetime,
            _hourly_in_shard,
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
    ) -> None:
        if not aggregates:
            return

        now_ts = get_current_time()
        record_date = int(target_date.format("YYYYMMDD"))

        summary_payloads: list[WaterSummaryPayload] = []
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
                        "delta_exp": 0,
                        "is_revoked": 0,
                        "revoked_at": None,
                        "extra": {
                            "msg_count": row.msg_count,
                            "active_hours": row.active_hours,
                        },
                    }
                )
                continue

            delta = calc_personal_delta_exp(row.msg_count, row.active_hours)
            matrix_user_gain[(row.matrix_id, row.user_id)] += delta
            matrix_gain[row.matrix_id] += delta
            user_matrix_gain[row.user_id].append((row.matrix_id, delta))

        user_global_gain: dict[str, int] = defaultdict(int)
        decay_weights = [1.0, 0.5, 0.2]
        for user_id, gains in user_matrix_gain.items():
            ordered = sorted(gains, key=lambda x: x[1], reverse=True)
            for idx, (_, gain) in enumerate(ordered):
                weight = decay_weights[idx] if idx < len(decay_weights) else 0.0
                user_global_gain[user_id] += floor(gain * weight)

        async with water_core_db.session(commit=True) as session:
            summary_ops = WaterSummaryOps(session)
            level_ops = WaterLevelOps(session)
            penalty_ops = WaterPenaltyOps(session)

            matrix_keys = list(matrix_user_gain.keys())
            matrix_ids = list(matrix_gain.keys())
            user_ids = list(user_global_gain.keys())

            old_matrix = await level_ops.get_matrix_levels(matrix_keys)
            old_matrix_total = await level_ops.get_matrix_totals(matrix_ids)
            old_global = await level_ops.get_global_levels(user_ids)

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

            for chunk in split_list(summary_payloads, chunk_size):
                await summary_ops.bulk_upsert_summary(chunk)
                await asyncio.sleep(0.1)

            for chunk in split_list(matrix_payloads, chunk_size):
                await level_ops.upsert_matrix_levels(chunk)
                await asyncio.sleep(0.1)

            for chunk in split_list(global_payloads, chunk_size):
                await level_ops.upsert_global_levels(chunk)
                await asyncio.sleep(0.1)

            for chunk in split_list(matrix_total_payloads, chunk_size):
                await level_ops.upsert_matrix_totals(chunk)
                await asyncio.sleep(0.1)

            if penalty_logs:
                for chunk in split_list(penalty_logs, chunk_size):
                    await penalty_ops.insert_penalty_logs(chunk)

        # 按规范执行裁剪钩子，保留最近 3 天流水。
        prune_before_ts = target_date.shift(days=-2).floor("day").int_timestamp
        await self.prune_old_messages(prune_before_ts)

    async def prune_old_messages(self, before_ts: int) -> int:
        before = arrow.get(before_ts).floor("month")
        now = arrow.get(get_current_time()).floor("month")
        total = 0
        cursor = before
        while cursor <= now:
            async with water_message.session(
                time_ctx=cursor.datetime,
                commit=True,
            ) as session:
                total += await WaterMessageOps(session).prune_before(before_ts)
            cursor = cursor.shift(months=1)
        return total

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

    async def get_penalty_log(self, penalty_id: int) -> WaterPenaltyLog | None:
        async with water_core_db.session(commit=False) as session:
            return await WaterPenaltyOps(session).get_penalty_by_id(penalty_id)

    async def get_user_recent_summaries(
        self,
        user_id: str,
        matrix_id: str,
        start_date: int,
        end_date: int,
    ) -> Sequence[WaterDailySummary]:
        async with water_core_db.session(commit=False) as session:
            all_mappings = await WaterGroupMatrixMapOps(session).get_all_mappings()
        group_ids = [
            group_id
            for group_id, mapped_matrix_id in all_mappings.items()
            if mapped_matrix_id == matrix_id
        ]
        async with water_core_db.session(commit=False) as session:
            return await WaterSummaryOps(session).get_user_recent_summaries(
                user_id=user_id,
                group_ids=group_ids,
                start_date=start_date,
                end_date=end_date,
            )

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
            return await WaterSummaryOps(session).get_group_user_rank(group_id, user_id)

    async def get_group_activity_rank(self, group_id: str) -> int | None:
        async with water_core_db.session(commit=False) as session:
            return await WaterSummaryOps(session).get_group_activity_rank(group_id)

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
            log = await penalty_ops.get_penalty_by_id(penalty_id)
            if log is None or log.is_revoked:
                return False
            await level_ops.apply_exp_compensation_matrix(
                matrix_id=log.matrix_id,
                user_id=log.user_id,
                delta=abs(log.delta_exp),
            )
            affected = await penalty_ops.revoke_penalty(penalty_id, now_ts)
            return affected > 0
