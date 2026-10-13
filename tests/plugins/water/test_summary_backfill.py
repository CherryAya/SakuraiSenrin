"""热窗滑出前的日汇总回填契约。

历史故障：日汇总只落 core.db，每天 00:35 的 prune 删除 ≥90 天前的行，
回填分片一直靠人工脚本跑，季榜/年榜出现空洞。修复后：summary 归档 job
先回填窗口外行，再走归档与 prune。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import arrow
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from src.plugins.water.database import water_repo
from src.plugins.water.database.instances import water_core_db, water_summary
from src.plugins.water.database.ops import WaterSummaryOps
from src.plugins.water.database.tables import (
    WaterArchivedDailySummary,
    WaterDailySummary,
)
from src.plugins.water.database.types import WaterSummaryPayload


async def _seed_core_summary(payload: WaterSummaryPayload) -> None:
    """直接写 core.db 的日汇总。

    save_summary_batch 按真实当前时间分流冷热，历史日期会直接路由进分片，
    绕开「core.db 中即将过期的行」这一被测状态。"""
    async with water_core_db.session(commit=True) as session:
        await WaterSummaryOps(session).bulk_upsert_summary([payload])


@pytest_asyncio.fixture(autouse=True)
async def _reset_water_runtime() -> AsyncIterator[None]:
    await water_repo.init_all_tables()
    await water_repo.reset_runtime_data(preserve_seasons=False)
    yield
    await water_repo.init_all_tables()
    await water_repo.reset_runtime_data(preserve_seasons=False)


def _summary_payload(
    *,
    record_date: int,
    group_id: str = "20001",
    user_id: str = "10001",
    msg_count: int = 5,
) -> WaterSummaryPayload:
    return {
        "group_id": group_id,
        "user_id": user_id,
        "record_date": record_date,
        "msg_count": msg_count,
        "active_hours": 1,
        "hourly_counts": [0] * 24,
        "created_at": 1,
        "updated_at": 1,
    }


async def _core_summary_row(record_date: int) -> WaterDailySummary | None:
    async with water_core_db.session(commit=False) as session:
        return await session.get(
            WaterDailySummary,
            {
                "group_id": "20001",
                "user_id": "10001",
                "record_date": record_date,
            },
        )


@pytest.mark.asyncio
async def test_backfill_expiring_summaries_copies_rows_to_shards() -> None:
    """窗口外行必须被复制进对应月份的分片（幂等 upsert）。"""
    today_ts = arrow.get("2026-06-30", "YYYY-MM-DD").int_timestamp
    hot_start = water_repo._hot_summary_start_date(today_ts)
    assert hot_start == 20260402

    # 一行滑出热窗（回填目标），一行留在窗内（原样保留）。
    await _seed_core_summary(_summary_payload(record_date=20260301, msg_count=7))
    await _seed_core_summary(_summary_payload(record_date=20260601, msg_count=2))

    result = await water_repo.backfill_expiring_summaries(today_ts=today_ts)

    assert result["copied"] == 1
    assert result["shards"] == {"2026_03": 1}

    # 分片里能读到该行，且数值与源行一致。
    archived = await water_repo.get_summaries_in_window(
        20260301,
        20260301,
        group_ids=["20001"],
        user_id="10001",
    )
    assert len(archived) == 1
    assert archived[0].msg_count == 7
    assert archived[0].record_date == 20260301


@pytest.mark.asyncio
async def test_backfill_expiring_summaries_is_idempotent() -> None:
    """重复回填幂等：upsert 覆盖写，计数保持一份。"""
    today_ts = arrow.get("2026-06-30", "YYYY-MM-DD").int_timestamp
    await _seed_core_summary(_summary_payload(record_date=20260301))

    first = await water_repo.backfill_expiring_summaries(today_ts=today_ts)
    second = await water_repo.backfill_expiring_summaries(today_ts=today_ts)

    assert first["copied"] == 1
    assert second["copied"] == 1
    archived = await water_repo.get_summaries_in_window(
        20260301,
        20260301,
        group_ids=["20001"],
        user_id="10001",
    )
    assert len(archived) == 1
    assert archived[0].msg_count == 5


@pytest.mark.asyncio
async def test_backfill_then_prune_keeps_rows_queryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回填后 prune 仍可跨热/冷两段读到该行。

    旧实现顺序是 prune 直接删，长周期查询此后出现空洞；
    修复后 job 先回填再 prune。"""
    from src.plugins.water.database import repo as repo_module

    today_ts = arrow.get("2026-06-30", "YYYY-MM-DD").int_timestamp
    monkeypatch.setattr(repo_module, "get_current_time", lambda: today_ts)
    await _seed_core_summary(_summary_payload(record_date=20260301, msg_count=4))
    await _seed_core_summary(_summary_payload(record_date=20260629, msg_count=3))

    await water_repo.backfill_expiring_summaries(today_ts=today_ts)
    pruned = await water_repo.prune_hot_summaries(today_ts)

    assert pruned == 1
    assert await _core_summary_row(20260301) is None

    # 跨段查询：冷段（分片）返回回填行，热段返回窗内行。
    rows = await water_repo.get_summaries_in_window(
        20260301,
        20260629,
        group_ids=["20001"],
        user_id="10001",
    )
    by_date = {row.record_date: row.msg_count for row in rows}
    assert by_date == {20260301: 4, 20260629: 3}


@pytest.mark.asyncio
async def test_backfill_covers_rows_beyond_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回填覆盖全部窗口外行，更早的历史行同样保全。

    首版实现带 margin_days=3 下界，更早的行会被 prune 永久删除；
    现在全量回填（窗口外行数由每日 prune 约束在上限内）。"""
    today_ts = arrow.get("2026-06-30", "YYYY-MM-DD").int_timestamp
    await _seed_core_summary(_summary_payload(record_date=20251115, msg_count=9))
    await _seed_core_summary(_summary_payload(record_date=20260329, msg_count=8))

    result = await water_repo.backfill_expiring_summaries(today_ts=today_ts)

    assert result["copied"] == 2
    assert result["shards"] == {"2025_11": 1, "2026_03": 1}
    archived = await water_repo.get_summaries_in_window(
        20251115,
        20251115,
        group_ids=["20001"],
        user_id="10001",
    )
    assert [row.msg_count for row in archived] == [9]


@pytest.mark.asyncio
async def test_summary_archive_job_backfills_before_prune(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """job 顺序契约：回填 -> 归档 -> prune；回填计数进入成功日志。"""
    from src.lib.db.connectors import SegmentArchiveReport
    from src.plugins.water import schedulers

    call_order: list[str] = []

    async def _backfill(**kwargs: object) -> dict[str, object]:
        _ = kwargs
        call_order.append("backfill")
        return {"copied": 2, "shards": {"2026_03": 2}}

    async def _archive() -> SegmentArchiveReport:
        call_order.append("archive")
        return SegmentArchiveReport(archived=["2026_03"])

    async def _prune() -> int:
        call_order.append("prune")
        return 2

    monkeypatch.setattr(schedulers.water_repo, "backfill_expiring_summaries", _backfill)
    monkeypatch.setattr(schedulers.water_repo, "archive_summary_shards", _archive)
    monkeypatch.setattr(schedulers.water_repo, "prune_hot_summaries", _prune)

    await schedulers._water_summary_archive_job()

    assert call_order == ["backfill", "archive", "prune"]


@pytest.mark.asyncio
async def test_summary_shard_receives_backfilled_rows_after_archiver_runs() -> None:
    """归档巡检后分片仍可读回回填行（.db/.zst 两种物理形态一致）。"""
    from src.lib.db.connectors import ColdPolicy

    today_ts = arrow.get("2026-06-30", "YYYY-MM-DD").int_timestamp
    await _seed_core_summary(_summary_payload(record_date=20260301))

    await water_repo.backfill_expiring_summaries(today_ts=today_ts)
    await water_repo.archive_summary_shards()

    # 归档转冷：显式 HYDRATE 读回
    async with water_summary.read_session_for(
        "2026_03",
        cold_policy=ColdPolicy.HYDRATE,
    ) as session:
        count = await session.scalar(
            select(func.count()).select_from(WaterArchivedDailySummary)
        )
    assert int(count or 0) == 1


def test_hot_window_anchor_is_cst_not_utc() -> None:
    """热窗边界按业务时区（CST）取日界，且在一个 CST 日内恒定。

    旧实现按 UTC 取日界；CST 00:00-08:00 期间与读路径边界差一天，
    边界那天被读路径当作已归档而静默漏查。"""
    from src.plugins.water.database.repo import WaterRepository

    early = arrow.get("2026-07-01 00:35:00+08:00").int_timestamp
    late = arrow.get("2026-07-01 09:00:00+08:00").int_timestamp

    # 旧实现在 early 给 20260402（UTC 日界）
    assert WaterRepository._hot_summary_start_date(early) == 20260403
    # 同一 CST 日内边界恒定：job 与读路径必须切在同一处。
    assert WaterRepository._hot_summary_start_date(
        early
    ) == WaterRepository._hot_summary_start_date(late)


@pytest.mark.asyncio
async def test_bulk_upsert_summary_chunks_large_batches() -> None:
    """单次 upsert 超过 SQLite 绑定参数上限时自动分块。

    维护脚本默认 --summary-batch-size 5000，8 字段实测 4096 行即触发
    "too many SQL variables"；ops 层分块后调用方传多大都安全。"""
    from src.plugins.water.database.instances import water_core_db
    from src.plugins.water.database.ops import WaterSummaryOps

    payloads = [
        {
            "group_id": f"g{idx}",
            "user_id": f"u{idx}",
            "record_date": 20260701,
            "msg_count": 1,
            "active_hours": 1,
            "hourly_counts": [0] * 24,
            "created_at": 1,
            "updated_at": 1,
        }
        for idx in range(9000)
    ]

    async with water_core_db.session(commit=True) as session:
        written = await WaterSummaryOps(session).bulk_upsert_summary(payloads)

    # 9000 行经分块全部落库
    assert written == 9000
