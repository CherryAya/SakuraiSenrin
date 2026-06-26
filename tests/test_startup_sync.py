from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from nonebot.adapters.onebot.v11 import Bot
import pytest

from src.services import startup_sync as startup_sync_module


class _Snapshot:
    def __init__(self, *, snapshot_id: str, short_id: str, time: str) -> None:
        self.id = snapshot_id
        self.short_id = short_id
        self.time = time


@pytest.mark.asyncio
async def test_startup_sync_notifies_superuser_when_remote_is_newer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[int, str]] = []

    class _Bot:
        async def send_private_msg(
            self, *, user_id: int, message: str
        ) -> dict[str, int]:
            sent.append((user_id, message))
            return {"message_id": 9001}

    class _Service:
        local_root = Path("data/backup")

        async def list_snapshots(self) -> list[object]:
            return [
                _Snapshot(
                    snapshot_id="snap-1",
                    short_id="snap-1",
                    time="2026-06-27T09:00:00+08:00",
                )
            ]

    monkeypatch.setattr(startup_sync_module.config, "SUPERUSERS", {"1"})
    monkeypatch.setattr(
        startup_sync_module,
        "build_backup_service_from_config",
        lambda: _Service(),
    )

    async def _local_latest() -> int:
        return 1_700_000_000

    monkeypatch.setattr(
        startup_sync_module, "_get_local_latest_data_mtime", _local_latest
    )
    monkeypatch.setattr(startup_sync_module, "_startup_check_completed", False)
    startup_sync_module._pending_restore_by_prompt.clear()

    await startup_sync_module.run_startup_backup_freshness_check(cast(Bot, _Bot()))

    assert sent
    assert sent[0][0] == 1
    assert "远端备份比本地新" in sent[0][1]
    assert "snap-1" in sent[0][1]
    assert "9001" not in sent[0][1]
    assert "9001" in startup_sync_module._pending_restore_by_prompt


@pytest.mark.asyncio
async def test_startup_sync_logs_when_local_is_newer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []

    class _Bot:
        async def send_private_msg(
            self, *, user_id: int, message: str
        ) -> dict[str, int]:
            _ = (user_id, message)
            raise AssertionError("should not notify when local is newer")

    class _Service:
        local_root = Path("data/backup")

        async def list_snapshots(self) -> list[object]:
            return [
                _Snapshot(
                    snapshot_id="snap-1",
                    short_id="snap-1",
                    time="2026-06-27T09:00:00+08:00",
                )
            ]

    monkeypatch.setattr(startup_sync_module.config, "SUPERUSERS", {"1"})
    monkeypatch.setattr(
        startup_sync_module,
        "build_backup_service_from_config",
        lambda: _Service(),
    )

    async def _local_latest() -> int:
        return 1_900_000_000

    monkeypatch.setattr(
        startup_sync_module, "_get_local_latest_data_mtime", _local_latest
    )
    monkeypatch.setattr(startup_sync_module.logger, "warning", warnings.append)
    monkeypatch.setattr(startup_sync_module, "_startup_check_completed", False)

    await startup_sync_module.run_startup_backup_freshness_check(cast(Bot, _Bot()))

    assert warnings
    assert "Please run a backup soon" in warnings[0]


@pytest.mark.asyncio
async def test_startup_sync_reply_yes_runs_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restored: list[str] = []

    class _Bot:
        async def send_private_msg(
            self, *, user_id: int, message: str
        ) -> dict[str, int]:
            _ = (user_id, message)
            return {"message_id": 1}

    startup_sync_module._pending_restore_by_prompt.clear()
    startup_sync_module._pending_restore_by_prompt["123"] = (
        startup_sync_module.PendingStartupRestore(
            snapshot_id="snap-123",
            remote_latest_at=2,
            local_latest_at=1,
            prompt_message_id="123",
        )
    )

    async def _restore(*, snapshot_id: str) -> None:
        restored.append(snapshot_id)

    monkeypatch.setattr(
        startup_sync_module, "restore_latest_remote_snapshot_into_local", _restore
    )

    result = await startup_sync_module.handle_startup_sync_reply(
        cast(Bot, _Bot()),
        reply_message_id="123",
        text="y",
    )

    assert restored == ["snap-123"]
    assert result is not None
    assert "已恢复到本地" in result


def test_find_restore_manifest_path_supports_nested_restore_layout(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "host" / "tmp" / "staging"
    nested.mkdir(parents=True)
    manifest = nested / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    resolved = startup_sync_module._find_restore_manifest_path(tmp_path)

    assert resolved == manifest


def test_ensure_restore_not_in_progress_raises_when_restore_is_running() -> None:
    original = startup_sync_module._restore_in_progress
    startup_sync_module._restore_in_progress = True
    try:
        with pytest.raises(RuntimeError, match="restore in progress: test-source"):
            startup_sync_module.ensure_restore_not_in_progress(source="test-source")
    finally:
        startup_sync_module._restore_in_progress = original


@pytest.mark.asyncio
async def test_reload_runtime_state_after_restore_reloads_core_wordbank_and_water(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    import sys
    import types

    fake_plugins = types.ModuleType("src.plugins")
    fake_plugins.__path__ = []  # type: ignore[attr-defined]

    fake_wordbank = types.ModuleType("src.plugins.wordbank")
    fake_wordbank._wordbank_initialized = True  # type: ignore[attr-defined]
    setattr(
        fake_wordbank,
        "wordbank_service",
        types.SimpleNamespace(
            _rebuild_task=None,
            _dirty_group_ids={1, 2},
            _call_count_cache={(1, 60): object()},
            _initialized=True,
        ),
    )
    setattr(fake_wordbank, "wordbank_media_service", types.SimpleNamespace())

    fake_water = types.ModuleType("src.plugins.water")
    fake_water._water_plugin_initialized = True  # type: ignore[attr-defined]
    setattr(
        fake_water,
        "matrix_suggestion_service",
        types.SimpleNamespace(_first_record_seen_cache={"20001"}),
    )
    fake_water_services = types.ModuleType("src.plugins.water.services")
    fake_water_services.__path__ = []  # type: ignore[attr-defined]
    fake_water_report = types.ModuleType("src.plugins.water.services.report")
    setattr(
        fake_water_report,
        "water_report_service",
        types.SimpleNamespace(
            clear_today_report_cooldowns=lambda: events.append(
                "water-report-cooldown-clear"
            )
        ),
    )
    setattr(
        fake_water,
        "water_repo",
        types.SimpleNamespace(
            _group_matrix_cache={"20001": "mtx_1"},
            _group_matrix_locks={},
            _merge_state_locks={},
        ),
    )

    monkeypatch.setitem(sys.modules, "src.plugins", fake_plugins)
    monkeypatch.setitem(sys.modules, "src.plugins.wordbank", fake_wordbank)
    monkeypatch.setitem(sys.modules, "src.plugins.water", fake_water)
    monkeypatch.setitem(sys.modules, "src.plugins.water.services", fake_water_services)
    monkeypatch.setitem(
        sys.modules,
        "src.plugins.water.services.report",
        fake_water_report,
    )

    async def _warm_user() -> None:
        events.append("user")

    async def _warm_group() -> None:
        events.append("group")

    async def _warm_member() -> None:
        events.append("member")

    async def _warm_blacklist() -> None:
        events.append("blacklist")

    async def _init_wordbank() -> None:
        events.append("wordbank")

    async def _media_rebuild() -> None:
        events.append("wordbank-media")

    async def _init_water() -> None:
        events.append("water")

    monkeypatch.setattr(startup_sync_module.user_repo, "warm_up", _warm_user)
    monkeypatch.setattr(startup_sync_module.group_repo, "warm_up", _warm_group)
    monkeypatch.setattr(startup_sync_module.member_repo, "warm_up", _warm_member)
    monkeypatch.setattr(startup_sync_module.blacklist_repo, "warm_up", _warm_blacklist)
    setattr(fake_wordbank, "initialize_wordbank_plugin", _init_wordbank)
    cast(Any, fake_wordbank.wordbank_media_service).rebuild_cache = _media_rebuild  # type: ignore[attr-defined]
    setattr(fake_water, "initialize_water_plugin", _init_water)
    setattr(
        fake_water,
        "clear_water_query_cooldowns",
        lambda: events.append("water-cooldown-clear"),
    )

    await startup_sync_module._reload_runtime_state_after_restore()

    assert events == [
        "user",
        "group",
        "member",
        "blacklist",
        "wordbank",
        "wordbank-media",
        "water-cooldown-clear",
        "water-report-cooldown-clear",
        "water",
    ]
    assert fake_wordbank.wordbank_service._dirty_group_ids == set()
    assert fake_wordbank.wordbank_service._call_count_cache == {}
    assert fake_wordbank.wordbank_service._initialized is False
    assert fake_water.matrix_suggestion_service._first_record_seen_cache == set()
    assert fake_water.water_repo._group_matrix_cache == {}
