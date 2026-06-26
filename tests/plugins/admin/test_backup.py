from __future__ import annotations

from pathlib import Path
import sys

import nonebot
from nonebot.adapters.onebot.v11 import Bot
from nonebug import App
import pytest

nonebot.init(
    SUPERUSERS={"1"},
    IGNORED_USERS=set(),
    MAIN_GROUP_ID="10001",
    GITHUB_TOKEN="test-token",
    GITHUB_REPO="owner/repo",
    GITHUB_BRANCH="main",
    WORDBANK_MEDIA_PROVIDER="local",
    command_start={"#", "/"},
    command_sep={"."},
)
if nonebot.get_plugin("admin") is None:
    sys.modules.pop("src.plugins.admin", None)
    nonebot.load_plugin("src.plugins.admin")
if nonebot.get_plugin("help") is None:
    nonebot.load_plugin("src.plugins.help")

SUPERUSER_ID = int(next(iter(nonebot.get_driver().config.superusers)))

from src.lib.i18n.runtime import tr
from src.plugins.admin.backup import _build_error_demo, admin_backup
from src.plugins.help import (
    _iter_docs_entries,
    _resolve_actor_permission,
    _resolve_docs_message,
    help_matcher,
)
from tests.plugins.water.helpers import (
    build_group_message_event,
    build_private_message_event,
)


class _Snapshot:
    def __init__(
        self,
        *,
        snapshot_id: str,
        short_id: str,
        time: str,
        hostname: str,
        files: int,
        bytes_total: int,
    ) -> None:
        self.id = snapshot_id
        self.short_id = short_id
        self.time = time
        self.hostname = hostname
        self.total_files_processed = files
        self.total_bytes_processed = bytes_total


def _freeze_error_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.lib.plugin_docs.render_feature_deep_dive",
        lambda *args, **kwargs: b"feature-demo",
    )


@pytest.mark.asyncio
async def test_admin_backup_check_returns_latest_snapshot(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event(
        "#admin.backup check",
        user_id=SUPERUSER_ID,
    )

    class _Service:
        async def list_snapshots(self) -> list[object]:
            return [
                _Snapshot(
                    snapshot_id="id-1",
                    short_id="snap-1",
                    time="2026-06-12T21:35:21+08:00",
                    hostname="host-a",
                    files=75,
                    bytes_total=1234,
                ),
                _Snapshot(
                    snapshot_id="id-2",
                    short_id="snap-2",
                    time="2026-06-11T21:35:21+08:00",
                    hostname="host-b",
                    files=74,
                    bytes_total=5678,
                ),
            ]

    from src.plugins.admin import backup as backup_plugin

    monkeypatch.setattr(
        backup_plugin,
        "build_backup_service_from_config",
        lambda: _Service(),
    )
    expected = "\n".join(
        [
            tr("zh-CN", "admin.backup.check.ok", count=2),
            tr("zh-CN", "admin.backup.check.latest"),
            tr("zh-CN", "admin.backup.snapshot.id", snapshot_id="snap-1"),
            tr(
                "zh-CN",
                "admin.backup.snapshot.time",
                time="2026-06-12T21:35:21+08:00",
            ),
            tr("zh-CN", "admin.backup.snapshot.hostname", hostname="host-a"),
            tr("zh-CN", "admin.backup.snapshot.files", count=75),
            tr("zh-CN", "admin.backup.snapshot.bytes", count=1234),
        ]
    )

    async with app.test_matcher(admin_backup) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            expected,
            bot=bot,
        )


@pytest.mark.asyncio
async def test_admin_backup_snapshots_rejects_invalid_limit(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_error_demo(monkeypatch)
    event = build_private_message_event(
        "#admin.backup snapshots 0",
        user_id=SUPERUSER_ID,
    )

    async with app.test_matcher(admin_backup) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            _build_error_demo(
                "zh-CN",
                tr("zh-CN", "admin.backup.limit.invalid"),
                "snapshots",
            ),
            bot=bot,
        )


@pytest.mark.asyncio
async def test_admin_backup_run_returns_backup_result(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event("#admin.backup run", user_id=SUPERUSER_ID)

    class _Manifest:
        def __init__(self) -> None:
            self.files = [object(), object()]
            self.bytes_total = 2048

    class _Result:
        run_id = "backup-1"
        manifest = _Manifest()
        manifest_path = Path("data/backup/manifests/backup-1.json")
        restic_snapshot_id = "snap-1"

    class _Service:
        async def run(self, plan: object, *, force: bool = False) -> object:
            assert force is True
            return _Result()

    from src.plugins.admin import backup as backup_plugin

    monkeypatch.setattr(
        backup_plugin,
        "build_backup_service_from_config",
        lambda: _Service(),
    )
    monkeypatch.setattr(backup_plugin, "build_default_backup_plan", lambda: object())
    expected = "\n".join(
        [
            tr("zh-CN", "admin.backup.run.completed"),
            tr("zh-CN", "admin.backup.run.run_id", run_id="backup-1"),
            tr(
                "zh-CN",
                "admin.backup.run.manifest",
                path="data/backup/manifests/backup-1.json",
            ),
            tr("zh-CN", "admin.backup.run.snapshot", snapshot_id="snap-1"),
            tr("zh-CN", "admin.backup.run.files", count=2),
            tr("zh-CN", "admin.backup.run.bytes", count=2048),
        ]
    )

    async with app.test_matcher(admin_backup) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            expected,
            bot=bot,
        )


@pytest.mark.asyncio
async def test_admin_backup_restore_reloads_local_runtime_state(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event(
        "#admin.backup restore latest", user_id=SUPERUSER_ID
    )

    from src.plugins.admin import backup as backup_plugin

    restored: list[str] = []

    async def _restore_remote_snapshot_into_local(*, snapshot: str) -> None:
        restored.append(snapshot)

    monkeypatch.setattr(
        backup_plugin,
        "restore_remote_snapshot_into_local",
        _restore_remote_snapshot_into_local,
    )
    expected = "\n".join(
        [
            tr("zh-CN", "admin.backup.restore.completed"),
            tr("zh-CN", "admin.backup.restore.snapshot", snapshot_id="latest"),
        ]
    )

    async with app.test_matcher(admin_backup) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            expected,
            bot=bot,
        )

    assert restored == ["latest"]


@pytest.mark.asyncio
async def test_help_can_find_admin_backup_docs(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_group_message_event("#help 备份管理模块", user_id=SUPERUSER_ID)
    entries = _iter_docs_entries("zh-CN")
    backup_entry = next(
        entry for entry in entries if entry.display_name == "备份管理模块"
    )
    monkeypatch.setattr(
        "src.plugins.help.render_plugin_guide", lambda *args, **kwargs: b"guide-demo"
    )

    async with app.test_matcher(help_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        actor_permission = await _resolve_actor_permission(bot, event)
        expected = await _resolve_docs_message(
            backup_entry,
            "zh-CN",
            actor_permission=actor_permission,
            all_entries=entries,
        )
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, bot=bot)
        ctx.should_finished(help_matcher)
