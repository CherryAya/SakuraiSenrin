"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 19:16:14
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 14:13:13
Description: water db repo
"""

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import arrow
from sqlalchemy.engine.row import Row

from src.database.consts import WritePolicy
from src.lib.utils.common import get_current_time

from .instances import water_message, water_summary_db
from .ops import WaterMessageOps, WaterSummaryOps
from .tables import WaterMessageBase, WaterSummaryBase
from .types import WaterMessagePayload, WaterSummaryPayload
from .writers import water_writer


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


class WaterRepository:
    @classmethod
    async def init_all_tables(cls) -> None:
        await water_message.init(WaterMessageBase)
        await water_summary_db.init(WaterSummaryBase)

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
        """
        核心写入入口:
        1. 封装 Context
        2. 策略分流 (Buffered / Immediate)
        """
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

        async with water_summary_db.session(commit=True) as session:
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
            async with water_summary_db.session(commit=False) as session:
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
        """获取群今日活跃度全站排名"""
        now = arrow.get(get_current_time())
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp

        async with water_message.session(
            time_ctx=now.datetime, commit=False
        ) as session:
            return await WaterMessageOps(session).get_today_group_rank(
                group_id, start_ts, end_ts
            )

    async def get_users_hourly_distribution(
        self, group_id: str, user_ids: list[str]
    ) -> dict[str, list[int]]:
        """
        在 Repo 层将流水降维为 24 小时分布数组。
        返回: { "user_id": [0, 5, 12, ... 24个元素] }
        """
        if not user_ids:
            return {}

        now = arrow.get(get_current_time())
        start_ts = now.floor("day").int_timestamp
        end_ts = now.ceil("day").int_timestamp

        async with water_message.session(
            time_ctx=now.datetime, commit=False
        ) as session:
            raw_timestamps = await WaterMessageOps(session).get_users_timestamps(
                group_id, user_ids, start_ts, end_ts
            )

        user_hourly: dict[str, list[int]] = defaultdict(lambda: [0] * 24)
        for uid, ts in raw_timestamps:
            hour = arrow.get(ts).to("local").hour
            user_hourly[uid][hour] += 1

        return dict(user_hourly)
