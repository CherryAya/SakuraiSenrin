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

from src.lib.plugin_docs import DocsRenderContext
from src.plugins.admin.backup import admin_backup, build_docs
from src.plugins.help import help_matcher
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


@pytest.mark.asyncio
async def test_admin_backup_check_returns_latest_snapshot(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event(
        "#admin backup check",
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

    async with app.test_matcher(admin_backup) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            (
                "远端备份仓库可用，共 2 个快照。\n"
                "最新快照：\n"
                "- 快照: snap-1\n"
                "  时间: 2026-06-12T21:35:21+08:00\n"
                "  主机: host-a\n"
                "  文件数: 75\n"
                "  字节数: 1234"
            ),
            bot=bot,
        )


@pytest.mark.asyncio
async def test_admin_backup_snapshots_rejects_invalid_limit(
    app: App,
) -> None:
    event = build_private_message_event(
        "#admin backup snapshots 0",
        user_id=SUPERUSER_ID,
    )

    async with app.test_matcher(admin_backup) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "快照数量必须是大于 0 的整数。", bot=bot)


@pytest.mark.asyncio
async def test_admin_backup_run_returns_backup_result(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_private_message_event("#admin backup run", user_id=SUPERUSER_ID)

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

    async with app.test_matcher(admin_backup) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            (
                "备份已完成。\n"
                "run_id: backup-1\n"
                "manifest: data/backup/manifests/backup-1.json\n"
                "snapshot: snap-1\n"
                "files: 2\n"
                "bytes: 2048"
            ),
            bot=bot,
        )


@pytest.mark.asyncio
async def test_help_can_find_admin_backup_docs(app: App) -> None:
    event = build_group_message_event("#help 备份管理模块")
    expected = build_docs(
        DocsRenderContext(
            locale="zh-CN",
            view="plugin",
        )
    )

    async with app.test_matcher(help_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, bot=bot)
        ctx.should_finished(help_matcher)
