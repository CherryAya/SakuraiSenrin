from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from src.plugins.water.services.cron_orchestrator import run_water_subprocess_job
from src.plugins.water.services.worker_jobs import (
    WaterWorkerManifest,
    write_water_worker_manifest,
)


@pytest.mark.asyncio
async def test_run_water_subprocess_job_reads_manifest_and_passes_args(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from src.plugins.water.services import cron_orchestrator as orchestrator_module

    output_dir = tmp_path / "job-1"
    captured: dict[str, object] = {}

    class _FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            write_water_worker_manifest(
                output_dir / "manifest.json",
                WaterWorkerManifest(
                    job_name="daily_report_prepare",
                    job_id="job-1",
                    started_at=1,
                    finished_at=2,
                    status="success",
                    record_date=20260718,
                    metrics={"candidate_groups": 2},
                ),
            )
            return b"stdout", b"stderr"

        def kill(self) -> None:
            self.returncode = -9

    async def _fake_create_subprocess_exec(
        *command: str,
        **kwargs: object,
    ) -> _FakeProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(orchestrator_module, "WATER_JOB_OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(orchestrator_module, "WATER_JOB_SCRIPT", tmp_path / "run.py")
    monkeypatch.setattr(orchestrator_module, "build_water_job_id", lambda _job: "job-1")
    monkeypatch.setattr(
        orchestrator_module.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await run_water_subprocess_job(
        "daily_report_prepare",
        record_date=20260718,
        force=True,
        locale="zh-CN",
        timeout_seconds=120,
    )

    command = cast(tuple[str, ...], captured["command"])
    assert result.succeeded is True
    assert result.manifest is not None
    assert result.manifest.record_date == 20260718
    assert command[0] == orchestrator_module.sys.executable
    assert command[1] == str(tmp_path / "run.py")
    assert "--record-date" in command
    assert "20260718" in command
    assert "--force" in command
    assert "--locale" in command
    assert "zh-CN" in command


@pytest.mark.asyncio
async def test_run_water_subprocess_job_uses_extended_report_timeout_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from src.plugins.water.services import cron_orchestrator as orchestrator_module

    output_dir = tmp_path / "job-2"
    captured: dict[str, object] = {}

    class _FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            write_water_worker_manifest(
                output_dir / "manifest.json",
                WaterWorkerManifest(
                    job_name="daily_report_prepare",
                    job_id="job-2",
                    started_at=1,
                    finished_at=2,
                    status="success",
                    record_date=20260725,
                    metrics={"candidate_groups": 0},
                ),
            )
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9

    async def _fake_create_subprocess_exec(
        *command: str,
        **kwargs: object,
    ) -> _FakeProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(orchestrator_module, "WATER_JOB_OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(orchestrator_module, "WATER_JOB_SCRIPT", tmp_path / "run.py")
    monkeypatch.setattr(orchestrator_module, "build_water_job_id", lambda _job: "job-2")
    monkeypatch.setattr(
        orchestrator_module.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    await run_water_subprocess_job("daily_report_prepare")

    command = cast(tuple[str, ...], captured["command"])
    assert "--timeout-seconds" in command
    timeout_index = command.index("--timeout-seconds")
    assert command[timeout_index + 1] == "1800"
