from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from src.lib.db.connectors import (
    SegmentArchiveFailure,
    SegmentArchiveOutcome,
    SegmentArchiveReport,
)
from src.services import shard_archiver as archiver_module


class _Scheduler:
    """记录注册过程的假调度器。"""

    def __init__(self, existing: object | None = None) -> None:
        self.existing = existing
        self.captured: dict[str, Any] = {}

    def get_job(self, job_id: str) -> object | None:
        assert job_id == "core_shard_archive"
        return self.existing

    def scheduled_job(self, *args: object, **kwargs: object) -> Any:
        self.captured["trigger"] = args
        self.captured["options"] = kwargs

        def _decorator(func: object) -> object:
            self.captured["job"] = func
            return func

        return _decorator


def _install_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    scheduler: _Scheduler,
) -> None:
    plugin = ModuleType("nonebot_plugin_apscheduler")
    plugin.scheduler = scheduler  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nonebot_plugin_apscheduler", plugin)
    monkeypatch.setattr(archiver_module, "require", lambda plugin_name: None)


def test_install_skips_existing_job(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = _Scheduler(existing=object())
    _install_scheduler(monkeypatch, scheduler)

    archiver_module.install_shard_archiver_scheduler()

    assert "job" not in scheduler.captured


@pytest.mark.asyncio
async def test_registers_and_runs_archive_for_both_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _Scheduler()
    _install_scheduler(monkeypatch, scheduler)

    calls: list[str] = []
    log_report = SegmentArchiveReport(archived=["2026_08"])
    snapshot_report = SegmentArchiveReport(archived=["2026_08"])

    async def _archive_logs() -> SegmentArchiveReport:
        calls.append("logs")
        return log_report

    async def _archive_snapshots() -> SegmentArchiveReport:
        calls.append("snapshots")
        return snapshot_report

    import src.database.instances as instances_module

    monkeypatch.setattr(instances_module.log_db, "run_archiver_task", _archive_logs)
    monkeypatch.setattr(
        instances_module.snapshot_db,
        "run_archiver_task",
        _archive_snapshots,
    )

    guard_calls: list[str] = []
    from src.services import startup_sync as startup_sync_module

    monkeypatch.setattr(
        startup_sync_module,
        "ensure_restore_not_in_progress",
        lambda *, source: guard_calls.append(source),
    )

    success_mock = SimpleNamespace(calls=[])
    monkeypatch.setattr(
        archiver_module.logger,
        "success",
        lambda message: success_mock.calls.append(message),
    )

    archiver_module.install_shard_archiver_scheduler()

    assert scheduler.captured["trigger"] == ("cron",)
    assert scheduler.captured["options"] == {
        "hour": 0,
        "minute": 45,
        "id": "core_shard_archive",
        "coalesce": True,
        "misfire_grace_time": 300,
        "max_instances": 1,
    }

    await scheduler.captured["job"]()

    assert guard_calls == ["core_shard_archive"]
    assert calls == ["logs", "snapshots"]
    assert success_mock.calls


@pytest.mark.asyncio
async def test_archive_failures_are_reported_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """归档报告含失败时记 ERROR，success 日志照旧缺席。"""
    scheduler = _Scheduler()
    _install_scheduler(monkeypatch, scheduler)

    failed = SegmentArchiveReport(
        failures=[
            SegmentArchiveFailure(
                shard_key="2026_08",
                outcome=SegmentArchiveOutcome.FAILED,
                reason="[WinError 32] file in use",
            )
        ]
    )

    async def _ok() -> SegmentArchiveReport:
        return SegmentArchiveReport(archived=["2026_08"])

    async def _failed() -> SegmentArchiveReport:
        return failed

    import src.database.instances as instances_module

    monkeypatch.setattr(instances_module.log_db, "run_archiver_task", _failed)
    monkeypatch.setattr(instances_module.snapshot_db, "run_archiver_task", _ok)

    from src.services import startup_sync as startup_sync_module

    monkeypatch.setattr(
        startup_sync_module,
        "ensure_restore_not_in_progress",
        lambda *, source: None,
    )

    exception_calls: list[str] = []
    success_calls: list[str] = []
    monkeypatch.setattr(
        archiver_module.logger,
        "exception",
        lambda message: exception_calls.append(message),
    )
    monkeypatch.setattr(
        archiver_module.logger,
        "success",
        lambda message: success_calls.append(message),
    )

    archiver_module.install_shard_archiver_scheduler()
    await scheduler.captured["job"]()

    assert exception_calls
    assert not success_calls


@pytest.mark.asyncio
async def test_job_archives_real_old_shards_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端：真实 EventStore 写入非活跃月分片后，job 产出 .zst。

    单元层 mock 契约照旧靠这次真实两阶段归档
    （rename -> 压缩 -> 删 staging）坐实。"""
    import arrow

    from src.database.log.tables import LogBase, TraceEventLog
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(
        connectors_module,
        "get_current_time",
        lambda: arrow.get("2026-10-01 12:00:00+08:00").int_timestamp,
    )

    from src.database.instances import log_db
    from src.services import startup_sync as startup_sync_module

    monkeypatch.setattr(
        startup_sync_module,
        "ensure_restore_not_in_progress",
        lambda *, source: None,
    )
    await log_db.store.init(LogBase)

    august = arrow.get("2026-08-15").datetime
    async with log_db.store.write_session(time_ctx=august) as session:
        session.add(
            TraceEventLog(
                created_at=1,
                trace_id="t1",
                source_kind="test",
                component="test",
                level="INFO",
                event_name="e",
                status="ok",
                summary="s",
                log_role="test",
                parent_trace_id=None,
                group_id=None,
                user_id=None,
                job_id=None,
                shard_key=None,
                record_date=None,
                batch_size=None,
                attempt=None,
                payload_json={},
            )
        )

    shard_path = log_db.store.base_dir / "log_202608.db"
    archive_path = log_db.store.base_dir / "log_202608.db.zst"
    assert shard_path.is_file()
    assert not archive_path.exists()

    scheduler = _Scheduler()
    _install_scheduler(monkeypatch, scheduler)
    archiver_module.install_shard_archiver_scheduler()
    await scheduler.captured["job"]()

    # 两阶段归档完成：在线分片被移走、归档产物落盘。
    assert archive_path.is_file()
    assert not shard_path.exists()
