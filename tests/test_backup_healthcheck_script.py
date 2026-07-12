from __future__ import annotations

import argparse
from typing import Any

import pytest

from scripts import check_backup as check_backup_script


def test_check_backup_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["check_backup.py"])

    args = check_backup_script.parse_args()

    assert args.limit == 3
    assert args.profile is None


@pytest.mark.asyncio
async def test_check_backup_main_lists_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = argparse.Namespace(limit=2, profile="dev")
    captured: dict[str, Any] = {"info": []}

    class _Snapshot:
        def __init__(
            self,
            *,
            snapshot_id: str,
            short_id: str,
            time: str,
            hostname: str,
            total_files_processed: int,
            total_bytes_processed: int,
        ) -> None:
            self.id = snapshot_id
            self.short_id = short_id
            self.time = time
            self.hostname = hostname
            self.total_files_processed = total_files_processed
            self.total_bytes_processed = total_bytes_processed

    class _Service:
        async def list_snapshots(self) -> list[object]:
            return [
                _Snapshot(
                    snapshot_id="id-1",
                    short_id="snap-1",
                    time="2026-06-12T21:35:21+08:00",
                    hostname="host-a",
                    total_files_processed=75,
                    total_bytes_processed=1234,
                ),
                _Snapshot(
                    snapshot_id="id-2",
                    short_id="snap-2",
                    time="2026-06-11T21:35:21+08:00",
                    hostname="host-b",
                    total_files_processed=76,
                    total_bytes_processed=5678,
                ),
                _Snapshot(
                    snapshot_id="id-3",
                    short_id="snap-3",
                    time="2026-06-10T21:35:21+08:00",
                    hostname="host-c",
                    total_files_processed=77,
                    total_bytes_processed=9999,
                ),
            ]

    monkeypatch.setattr(check_backup_script.nonebot, "init", lambda: None)
    monkeypatch.setattr(check_backup_script, "parse_args", lambda: args)
    monkeypatch.setattr(
        check_backup_script.logger,
        "success",
        lambda message: captured.__setitem__("success", message),
    )
    monkeypatch.setattr(
        check_backup_script.logger,
        "info",
        lambda message: captured["info"].append(message),
    )

    from src.services import backup as backup_module

    monkeypatch.setattr(
        backup_module,
        "build_backup_service_from_config",
        lambda profile_name=None: _Service(),
    )

    await check_backup_script.main()

    assert "backup healthcheck ok: 3 remote snapshots" in str(captured["success"])
    assert "profile=" in str(captured["success"])
    assert len(captured["info"]) == 2
    assert "snapshot: snap-1" in str(captured["info"][0])
    assert "snapshot: snap-2" in str(captured["info"][1])
