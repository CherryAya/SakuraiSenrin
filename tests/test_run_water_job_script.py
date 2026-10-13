# 测试直取内部符号：pyright 私有访问豁免
# pyright: reportPrivateUsage=false
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from scripts import run_water_job as run_water_job_script
from src.plugins.water.database import water_repo
from src.plugins.water.services.settlement import water_settlement_service


@pytest.mark.asyncio
async def test_run_job_initializes_water_tables_before_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def _init_all_tables() -> None:
        events.append("init")

    async def _run_daily_settlement(**kwargs: object) -> SimpleNamespace:
        events.append("settlement")
        assert kwargs["force"] is False
        target_date = cast(Any, kwargs["target_date"])
        assert target_date is not None
        assert target_date.format("YYYYMMDD") == "20260801"
        return SimpleNamespace(
            skipped=False,
            success=True,
            record_date=20260801,
            aggregate_rows=12,
            unlocked_achievements=3,
            forced=False,
            reason="",
        )

    monkeypatch.setattr(
        water_repo,
        "init_all_tables",
        _init_all_tables,
    )
    monkeypatch.setattr(
        water_settlement_service,
        "run_daily_settlement",
        _run_daily_settlement,
    )
    monkeypatch.setattr(run_water_job_script, "get_current_time", lambda: 1_785_456_000)

    manifest = await run_water_job_script._run_job(
        "settlement",
        job_id="job-1",
        output_dir=run_water_job_script.Path("/tmp/water-job-test"),
        record_date=20260801,
        locale="zh-CN",
        force=False,
    )

    assert events == ["init", "settlement"]
    assert manifest.status == "success"
    assert manifest.record_date == 20260801
    assert manifest.metrics["aggregate_rows"] == 12


@pytest.mark.asyncio
async def test_run_job_rejects_archive_job_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """归档 job 名从子进程脚本入口剔除（归档已移回主进程）。

    此前 message_archive/summary_archive 走子进程且照常上报 success，
    归档失败时 job 也报「cron archive done」。"""
    monkeypatch.setattr(run_water_job_script.sys, "argv", ["run_water_job.py"])
    with pytest.raises(SystemExit):
        run_water_job_script.parse_args()

    argv = [
        "run_water_job.py",
        "--job",
        "summary_archive",
    ]
    monkeypatch.setattr(run_water_job_script.sys, "argv", argv)
    with pytest.raises(SystemExit):
        run_water_job_script.parse_args()

    argv = [
        "run_water_job.py",
        "--job",
        "message_archive",
    ]
    monkeypatch.setattr(run_water_job_script.sys, "argv", argv)
    with pytest.raises(SystemExit):
        run_water_job_script.parse_args()
