from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from src.services import backup_scheduler as scheduler_module


def test_install_backup_scheduler_skips_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, Any] = {}
    from src.config import config

    monkeypatch.setattr(config, "BACKUP_ENABLED", False)
    monkeypatch.setattr(
        scheduler_module,
        "require",
        lambda plugin: called.__setitem__("plugin", plugin),
    )

    scheduler_module.install_backup_scheduler()

    assert called == {}


def test_install_backup_scheduler_skips_existing_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import config

    class _Scheduler:
        def get_job(self, job_id: str) -> object:
            assert job_id == "database_backup_default"
            return object()

        def scheduled_job(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("scheduled_job should not be called")

    monkeypatch.setattr(config, "BACKUP_ENABLED", True)
    monkeypatch.setattr(scheduler_module, "require", lambda plugin: None)
    monkeypatch.setitem(
        sys.modules,
        "nonebot_plugin_apscheduler",
        SimpleNamespace(scheduler=_Scheduler()),
    )

    scheduler_module.install_backup_scheduler()


@pytest.mark.asyncio
async def test_install_backup_scheduler_registers_and_runs_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    from src.config import config

    class _Scheduler:
        def get_job(self, job_id: str) -> None:
            assert job_id == "database_backup_default"
            return None

        def scheduled_job(self, *args: object, **kwargs: object) -> Any:
            captured["trigger"] = args
            captured["options"] = kwargs

            def _decorator(func: object) -> object:
                captured["job"] = func
                return func

            return _decorator

    class _Service:
        async def run(self, plan: object) -> None:
            captured["plan_run"] = plan

    scheduler_plugin = ModuleType("nonebot_plugin_apscheduler")
    scheduler_plugin.scheduler = _Scheduler()  # type: ignore[attr-defined]

    monkeypatch.setattr(config, "BACKUP_ENABLED", True)
    monkeypatch.setattr(
        scheduler_module,
        "require",
        lambda plugin: captured.__setitem__("plugin", plugin),
    )
    monkeypatch.setitem(sys.modules, "nonebot_plugin_apscheduler", scheduler_plugin)

    from src.services import backup as backup_module

    monkeypatch.setattr(
        backup_module,
        "build_default_backup_plan",
        lambda: SimpleNamespace(cron_hour=5, cron_minute=40),
    )
    monkeypatch.setattr(
        backup_module,
        "build_backup_service_from_config",
        lambda: _Service(),
    )

    scheduler_module.install_backup_scheduler()

    assert captured["plugin"] == "nonebot_plugin_apscheduler"
    assert captured["trigger"] == ("cron",)
    assert captured["options"] == {
        "hour": 5,
        "minute": 40,
        "id": "database_backup_default",
        "coalesce": True,
        "misfire_grace_time": 600,
        "max_instances": 1,
    }

    await captured["job"]()

    assert captured["plan_run"].cron_hour == 5
    assert captured["plan_run"].cron_minute == 40
