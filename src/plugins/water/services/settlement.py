"""每日 00:05 结算引擎。"""

import asyncio
from dataclasses import dataclass

import arrow
from loguru import logger

from src.lib.trace_log import log_trace_event, new_trace_id
from src.lib.utils.common import get_current_time
from src.plugins.water.database import water_repo
from src.plugins.water.database.repo_models import DailyAggregateItem
from src.plugins.water.services.worker_jobs import WaterWorkerManifest

from .achievement import AchievementService


@dataclass(frozen=True)
class SettlementResult:
    success: bool
    skipped: bool
    record_date: int
    aggregate_rows: int
    unlocked_achievements: int
    reason: str = ""
    forced: bool = False


class WaterSettlementService:
    def __init__(self) -> None:
        self.achievement_service = AchievementService()

    async def run_daily_settlement(
        self,
        target_date: arrow.Arrow | None = None,
        force: bool = False,
        chunk_pause_seconds: float = 0.1,
        prune_after_settlement: bool = True,
    ) -> SettlementResult:
        """
        每日结算总入口，满足三道防线:
        1. 幂等锁 (water_settlement_job)。
        2. 分块落盘 + 单会话事务。
        3. 消息分片文件生命周期由独立归档任务处理。
        """
        if target_date is None:
            target = (
                arrow.get(get_current_time())
                .to("Asia/Shanghai")
                .shift(days=-1)
                .floor("day")
            )
        else:
            target = target_date.floor("day")
        record_date = int(target.format("YYYYMMDD"))
        trace_id = new_trace_id("water_settlement")
        log_trace_event(
            event_name="daily_settlement",
            source_kind="water_settlement",
            component="water.settlement",
            status="started",
            summary=f"Starting water settlement for {record_date}.",
            trace_id=trace_id,
            record_date=record_date,
            payload_json={"force": force},
        )

        started, reason = await water_repo.try_start_settlement_job(
            record_date,
            force=force,
        )
        if not started:
            logger.warning(
                f"[Water] settle skipped date={record_date}, reason={reason}"
            )
            log_trace_event(
                event_name="daily_settlement",
                source_kind="water_settlement",
                component="water.settlement",
                status="skipped",
                summary=f"Skipped water settlement for {record_date}: {reason}.",
                trace_id=trace_id,
                record_date=record_date,
                payload_json={"force": force, "reason": reason},
            )
            return SettlementResult(
                success=False,
                skipped=True,
                record_date=record_date,
                aggregate_rows=0,
                unlocked_achievements=0,
                reason=reason,
                forced=False,
            )
        try:
            aggregates = await water_repo.collect_daily_aggregates(target)
            await water_repo.apply_daily_settlement(
                target,
                aggregates,
                chunk_size=500,
                chunk_pause_seconds=chunk_pause_seconds,
                prune_after_settlement=prune_after_settlement,
            )
            unlocked = await self._trigger_achievements(record_date, aggregates)
            await water_repo.mark_settlement_success(record_date)

            logger.success(
                f"[Water] settle completed date={record_date}, rows={len(aggregates)}"
            )
            log_trace_event(
                event_name="daily_settlement",
                source_kind="water_settlement",
                component="water.settlement",
                status="success",
                summary=f"Completed water settlement for {record_date}.",
                trace_id=trace_id,
                record_date=record_date,
                batch_size=len(aggregates),
                payload_json={"unlocked_achievements": unlocked},
            )
            return SettlementResult(
                success=True,
                skipped=False,
                record_date=record_date,
                aggregate_rows=len(aggregates),
                unlocked_achievements=unlocked,
                reason="",
                forced=(reason == "forced"),
            )
        except Exception as e:
            await water_repo.mark_settlement_failed(record_date, str(e))
            log_trace_event(
                event_name="daily_settlement",
                source_kind="water_settlement",
                component="water.settlement",
                status="failed",
                summary=f"Water settlement for {record_date} failed.",
                level="ERROR",
                trace_id=trace_id,
                record_date=record_date,
                payload_json={"error": repr(e)},
            )
            raise

    async def _trigger_achievements(
        self,
        record_date: int,
        aggregates: list[DailyAggregateItem],
    ) -> int:
        # 同一个用户在同一天可能跨群，这里按当天最大 msg_count 触发一次成就检查。
        user_context: dict[str, tuple[str, int]] = {}
        for row in aggregates:
            _, msg_count = user_context.get(row.user_id, ("", 0))
            if row.msg_count >= msg_count:
                user_context[row.user_id] = (row.matrix_id, row.msg_count)

        if not user_context:
            return 0

        active_seasons = await water_repo.list_current_activity_seasons(record_date)
        sem = asyncio.Semaphore(20)
        unlocked_total = 0

        async def _task(user_id: str, matrix_id: str, msg_count: int) -> None:
            nonlocal unlocked_total
            async with sem:
                unlocked = await self.achievement_service.check_and_unlock(
                    user_id=user_id,
                    matrix_id=matrix_id,
                    record_date=record_date,
                    today_msg_count=msg_count,
                )
                unlocked_total += len(unlocked)
                for season in active_seasons:
                    seasonal_unlocks = await self.achievement_service.check_and_unlock(
                        user_id=user_id,
                        matrix_id=matrix_id,
                        record_date=record_date,
                        today_msg_count=msg_count,
                        season_id=season.season_id,
                    )
                    unlocked_total += len(seasonal_unlocks)

        await asyncio.gather(
            *[
                _task(user_id, matrix_id, msg_count)
                for user_id, (matrix_id, msg_count) in user_context.items()
            ]
        )
        return unlocked_total


def build_settlement_result_from_manifest(
    manifest: WaterWorkerManifest,
) -> SettlementResult:
    metrics = manifest.metrics
    return SettlementResult(
        success=manifest.status in {"success", "partial"},
        skipped=manifest.status == "skipped",
        record_date=manifest.record_date or 0,
        aggregate_rows=int(metrics.get("aggregate_rows", 0)),
        unlocked_achievements=int(metrics.get("unlocked_achievements", 0)),
        reason=str(metrics.get("reason", manifest.error or "")),
        forced=bool(metrics.get("forced", False)),
    )


water_settlement_service = WaterSettlementService()
