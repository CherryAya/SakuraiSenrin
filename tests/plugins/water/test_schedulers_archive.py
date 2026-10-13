"""归档 job 的进程内执行与诚实上报契约。

历史故障：归档走子进程，Windows 上句柄释放被 WinError 32 挡死，异常被宽
except 吞掉后清单照写 success。修复后的契约：两个归档 job 在进程内直接
调用编排器；报告含失败时 job 记 ERROR 而非 success。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from src.lib.db.connectors import (
    SegmentArchiveFailure,
    SegmentArchiveOutcome,
    SegmentArchiveReport,
)
from src.logger import logger


def _report_failed() -> SegmentArchiveReport:
    return SegmentArchiveReport(
        failures=[
            SegmentArchiveFailure(
                shard_key="2026_04",
                outcome=SegmentArchiveOutcome.FAILED,
                reason="[WinError 32] file in use",
            )
        ]
    )


@pytest.mark.asyncio
async def test_message_archive_job_runs_in_process_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """归档失败必须记 ERROR：in-process 执行，日志走 exception。"""
    from src.plugins.water import schedulers

    subprocess_mock = AsyncMock(side_effect=AssertionError("子进程不应被调用"))
    success_mock = Mock()
    exception_mock = Mock()

    monkeypatch.setattr(
        schedulers.water_repo,
        "archive_message_shards",
        AsyncMock(return_value=_report_failed()),
    )
    monkeypatch.setattr(schedulers, "run_water_subprocess_job", subprocess_mock)
    monkeypatch.setattr(logger, "success", success_mock)
    monkeypatch.setattr(logger, "exception", exception_mock)

    await schedulers._water_message_archive_job()

    subprocess_mock.assert_not_awaited()
    success_mock.assert_not_called()
    exception_mock.assert_called()


@pytest.mark.asyncio
async def test_summary_archive_job_runs_in_process_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """summary 归档失败同样记 ERROR，prune 照常执行。"""
    from src.plugins.water import schedulers

    subprocess_mock = AsyncMock(side_effect=AssertionError("子进程不应被调用"))
    success_mock = Mock()
    exception_mock = Mock()

    monkeypatch.setattr(
        schedulers.water_repo,
        "archive_summary_shards",
        AsyncMock(return_value=_report_failed()),
    )
    prune_mock = AsyncMock(return_value=3)
    monkeypatch.setattr(schedulers.water_repo, "prune_hot_summaries", prune_mock)
    monkeypatch.setattr(schedulers, "run_water_subprocess_job", subprocess_mock)
    monkeypatch.setattr(logger, "success", success_mock)
    monkeypatch.setattr(logger, "exception", exception_mock)

    await schedulers._water_summary_archive_job()

    subprocess_mock.assert_not_awaited()
    success_mock.assert_not_called()
    exception_mock.assert_called()
    prune_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_message_archive_job_reports_success_when_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """归档正常时照常打 success 日志。"""
    from src.plugins.water import schedulers

    success_mock = Mock()
    monkeypatch.setattr(
        schedulers.water_repo,
        "archive_message_shards",
        AsyncMock(return_value=SegmentArchiveReport(archived=["2026_04"])),
    )
    monkeypatch.setattr(logger, "success", success_mock)

    await schedulers._water_message_archive_job()

    success_mock.assert_called()
