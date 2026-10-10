# 测试/脚本需直接验证内部不变量，故关闭跨模块私有符号告警
# pyright: reportPrivateUsage=false
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
        self_id = "99999"

        async def call_api(self, api: str, **kwargs: Any) -> dict[str, int]:
            assert api == "send_private_msg"
            sent.append((int(kwargs["user_id"]), str(kwargs["message"])))
            return {"message_id": 9001}

    class _Service:
        local_root = Path("data/backup")
        restic = type(
            "_Restic",
            (),
            {
                "profile_name": "dev",
                "allow_restore": True,
                "allowed_app_envs_for_restore": ("development",),
            },
        )()

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
        lambda profile_name=None: _Service(),
    )
    monkeypatch.setattr(
        startup_sync_module,
        "resolve_default_backup_profile_name",
        lambda: "dev",
    )
    monkeypatch.setattr(
        startup_sync_module,
        "resolve_default_backup_profile_name",
        lambda: "dev",
    )
    monkeypatch.setattr(
        startup_sync_module,
        "resolve_default_backup_profile_name",
        lambda: "dev",
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
        self_id = "99999"

        async def call_api(self, api: str, **kwargs: Any) -> dict[str, int]:
            _ = (api, kwargs)
            raise AssertionError("should not notify when local is newer")

    class _Service:
        local_root = Path("data/backup")
        restic = type(
            "_Restic",
            (),
            {
                "profile_name": "dev",
                "allow_restore": True,
                "allowed_app_envs_for_restore": ("development",),
            },
        )()

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
        lambda profile_name=None: _Service(),
    )
    monkeypatch.setattr(
        startup_sync_module,
        "resolve_default_backup_profile_name",
        lambda: "dev",
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
        self_id = "99999"

        async def call_api(self, api: str, **kwargs: Any) -> dict[str, int]:
            _ = (api, kwargs)
            return {"message_id": 1}

    startup_sync_module._pending_restore_by_prompt.clear()
    startup_sync_module._pending_restore_by_prompt["123"] = (
        startup_sync_module.PendingStartupRestore(
            snapshot_id="snap-123",
            remote_latest_at=2,
            local_latest_at=1,
            prompt_message_id="123",
            profile_name="dev",
        )
    )

    async def _restore(
        *,
        snapshot: str,
        profile_name: str | None = None,
    ) -> None:
        restored.append(snapshot)

    monkeypatch.setattr(
        startup_sync_module, "restore_remote_snapshot_into_local", _restore
    )

    result = await startup_sync_module.handle_startup_sync_reply(
        cast(Bot, _Bot()),
        reply_message_id="123",
        text="y",
    )

    assert restored == ["snap-123"]
    assert result is not None
    assert "已恢复到本地" in result


def test_assert_restore_allowed_rejects_production_restore_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Service:
        restic = type(
            "_Restic",
            (),
            {
                "allow_restore": True,
                "allowed_app_envs_for_restore": ("production", "development"),
            },
        )()

    monkeypatch.setattr(startup_sync_module, "resolve_app_env", lambda: "production")
    monkeypatch.setattr(
        startup_sync_module,
        "build_backup_service_from_config",
        lambda profile_name=None: _Service(),
    )
    monkeypatch.setattr(
        startup_sync_module,
        "resolve_default_backup_profile_name",
        lambda: "prod",
    )

    with pytest.raises(RuntimeError, match="explicit confirmation"):
        startup_sync_module.assert_restore_allowed(profile_name="prod")


def test_assert_restore_allowed_rejects_cross_profile_restore_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Service:
        restic = type(
            "_Restic",
            (),
            {
                "allow_restore": True,
                "allowed_app_envs_for_restore": ("production", "development"),
            },
        )()

    monkeypatch.setattr(startup_sync_module, "resolve_app_env", lambda: "production")
    monkeypatch.setattr(
        startup_sync_module,
        "build_backup_service_from_config",
        lambda profile_name=None: _Service(),
    )
    monkeypatch.setattr(
        startup_sync_module,
        "resolve_default_backup_profile_name",
        lambda: "prod",
    )

    with pytest.raises(RuntimeError, match="only restore from its default"):
        startup_sync_module.assert_restore_allowed(
            profile_name="dev",
            confirm_production_restore=True,
        )


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
@pytest.mark.asyncio
async def test_reload_runtime_state_after_restore_warms_repos_then_reloads_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复后的重载顺序：先预热核心仓储，再触发各插件注册的重载回调。

    插件不再由本层点名，而是通过 src.lib.runtime_reload 自注册，
    因此这里注册两个回调来代表插件侧。
    """
    events: list[str] = []

    async def _warm(name: str) -> None:
        events.append(name)

    for repo_name in ("user_repo", "group_repo", "member_repo", "blacklist_repo"):
        monkeypatch.setattr(
            getattr(startup_sync_module, repo_name),
            "warm_up",
            lambda _n=repo_name: _warm(_n.split("_")[0]),
        )

    from src.lib import runtime_reload as reload_module

    async def _plugin_a() -> None:
        events.append("plugin-a-reload")

    async def _plugin_b() -> None:
        events.append("plugin-b-reload")

    monkeypatch.setattr(reload_module, "_reload_callbacks", {})
    reload_module.register_runtime_reload("plugin-a", _plugin_a)
    reload_module.register_runtime_reload("plugin-b", _plugin_b)

    await startup_sync_module._reload_runtime_state_after_restore()

    assert events == [
        "user",
        "group",
        "member",
        "blacklist",
        "plugin-a-reload",
        "plugin-b-reload",
    ]
