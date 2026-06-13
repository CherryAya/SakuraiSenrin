from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from scripts import run_backup as run_backup_script
from scripts import run_restore as run_restore_script


def test_run_backup_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["run_backup.py"])

    args = run_backup_script.parse_args()

    assert args.force is False


def test_run_restore_parse_args_requires_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["run_restore.py", "latest", "--target", "./data/restore-check"],
    )

    args = run_restore_script.parse_args()

    assert args.snapshot == "latest"
    assert args.target == "./data/restore-check"


@pytest.mark.asyncio
async def test_run_backup_main_executes_force_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = argparse.Namespace(force=True)
    captured: dict[str, Any] = {"info": []}

    class _Result:
        run_id = "backup-1"
        manifest_path = tmp_path / "manifest.json"
        restic_snapshot_id = "snap-1"

    class _Service:
        async def run(
            self,
            plan: object,
            *,
            force: bool = False,
            stream_output: bool = False,
        ) -> object:
            captured["run"] = {
                "plan": plan,
                "force": force,
                "stream_output": stream_output,
            }
            return _Result()

    monkeypatch.setattr(run_backup_script.nonebot, "init", lambda: None)
    monkeypatch.setattr(run_backup_script, "parse_args", lambda: args)
    monkeypatch.setattr(
        run_backup_script.logger,
        "success",
        lambda message: captured.__setitem__("success", message),
    )
    monkeypatch.setattr(
        run_backup_script.logger,
        "info",
        lambda message: captured["info"].append(message),
    )

    from src.services import backup as backup_module

    monkeypatch.setattr(
        backup_module,
        "build_backup_service_from_config",
        lambda: _Service(),
    )
    monkeypatch.setattr(
        backup_module,
        "build_default_backup_plan",
        lambda: "plan-object",
    )

    await run_backup_script.main()

    assert captured["run"] == {
        "plan": "plan-object",
        "force": True,
        "stream_output": True,
    }
    assert "backup completed: backup-1" in str(captured["success"])
    assert any("manifest:" in str(item) for item in captured["info"])
    assert any("restic snapshot:" in str(item) for item in captured["info"])


@pytest.mark.asyncio
async def test_run_restore_main_executes_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = argparse.Namespace(snapshot="latest", target="./data/restore-check")
    captured: dict[str, Any] = {}

    class _Service:
        async def restore(self, *, snapshot: str, target: Path) -> None:
            captured["restore"] = {
                "snapshot": snapshot,
                "target": target,
            }

    monkeypatch.setattr(run_restore_script.nonebot, "init", lambda: None)
    monkeypatch.setattr(run_restore_script, "parse_args", lambda: args)
    monkeypatch.setattr(
        run_restore_script.logger,
        "success",
        lambda message: captured.__setitem__("success", message),
    )

    from src.services import backup as backup_module

    monkeypatch.setattr(
        backup_module,
        "build_backup_service_from_config",
        lambda: _Service(),
    )

    await run_restore_script.main()

    assert captured["restore"] == {
        "snapshot": "latest",
        "target": Path("./data/restore-check"),
    }
    assert "restore completed: latest -> ./data/restore-check" in str(
        captured["success"]
    )
