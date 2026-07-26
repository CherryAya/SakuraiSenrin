from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sys
import tempfile
from time import perf_counter

from src.logger import logger

from .worker_jobs import (
    WaterWorkerJobName,
    WaterWorkerManifest,
    build_water_job_id,
    load_water_worker_manifest,
)

ROOT = Path(__file__).resolve().parents[4]
WATER_JOB_SCRIPT = ROOT / "scripts" / "run_water_job.py"
WATER_JOB_OUTPUT_ROOT = Path(tempfile.gettempdir()) / "sakurai-water-jobs"
DEFAULT_WATER_JOB_TIMEOUT_SECONDS = 900
DAILY_REPORT_PREPARE_TIMEOUT_SECONDS = 1800


@dataclass(slots=True, frozen=True)
class WaterSubprocessResult:
    job_name: WaterWorkerJobName
    job_id: str
    output_dir: Path
    exit_code: int
    elapsed_ms: float
    stdout: str
    stderr: str
    timed_out: bool
    manifest: WaterWorkerManifest | None = None

    @property
    def manifest_path(self) -> Path:
        return self.output_dir / "manifest.json"

    @property
    def succeeded(self) -> bool:
        return (
            not self.timed_out
            and self.exit_code == 0
            and self.manifest is not None
            and self.manifest.status in {"success", "skipped", "partial"}
        )


async def run_water_subprocess_job(
    job_name: WaterWorkerJobName,
    *,
    record_date: int | None = None,
    force: bool = False,
    locale: str | None = None,
    timeout_seconds: int | None = None,
) -> WaterSubprocessResult:
    resolved_timeout_seconds = (
        timeout_seconds
        if timeout_seconds is not None
        else (
            DAILY_REPORT_PREPARE_TIMEOUT_SECONDS
            if job_name == "daily_report_prepare"
            else DEFAULT_WATER_JOB_TIMEOUT_SECONDS
        )
    )
    job_id = build_water_job_id(job_name)
    output_dir = WATER_JOB_OUTPUT_ROOT / job_id
    await asyncio.to_thread(shutil.rmtree, output_dir, True)
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(WATER_JOB_SCRIPT),
        "--job",
        job_name,
        "--job-id",
        job_id,
        "--output-dir",
        str(output_dir),
        "--timeout-seconds",
        str(resolved_timeout_seconds),
    ]
    if record_date is not None:
        command.extend(["--record-date", str(record_date)])
    if force:
        command.append("--force")
    if locale:
        command.extend(["--locale", locale])

    logger.info(
        "[Water][Worker] start job={} job_id={} timeout={}s output_dir={}",
        job_name,
        job_id,
        resolved_timeout_seconds,
        output_dir,
    )
    env = dict(os.environ)
    env["SAKURAI_WATER_WORKER"] = "1"
    started = perf_counter()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=resolved_timeout_seconds,
        )
    except TimeoutError:
        timed_out = True
        logger.warning(
            "[Water][Worker] timeout job={} job_id={} timeout={}s",
            job_name,
            job_id,
            resolved_timeout_seconds,
        )
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
    elapsed_ms = (perf_counter() - started) * 1000
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    await asyncio.to_thread(
        (output_dir / "stdout.log").write_text,
        stdout,
        encoding="utf-8",
    )
    await asyncio.to_thread(
        (output_dir / "stderr.log").write_text,
        stderr,
        encoding="utf-8",
    )

    manifest: WaterWorkerManifest | None = None
    manifest_path = output_dir / "manifest.json"
    if await asyncio.to_thread(manifest_path.is_file):
        manifest = await asyncio.to_thread(load_water_worker_manifest, manifest_path)
    result = WaterSubprocessResult(
        job_name=job_name,
        job_id=job_id,
        output_dir=output_dir,
        exit_code=process.returncode if process.returncode is not None else -1,
        elapsed_ms=elapsed_ms,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        manifest=manifest,
    )
    logger.info(
        (
            "[Water][Worker] done job={} job_id={} exit_code={} timed_out={} "
            "elapsed_ms={:.2f} manifest={}"
        ),
        job_name,
        job_id,
        result.exit_code,
        result.timed_out,
        result.elapsed_ms,
        manifest.status if manifest is not None else "missing",
    )
    return result
