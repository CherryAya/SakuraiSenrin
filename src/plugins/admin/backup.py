from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import CommandGroup

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs import DocsRenderContext, build_readme_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.services.backup import (
    BackupResult,
    ResticSnapshotInfo,
    build_backup_service_from_config,
    build_default_backup_plan,
)

name = tr("zh-CN", "plugin.admin_backup.name")
description = tr("zh-CN", "plugin.admin_backup.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "backup" / "README.MD"


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    return build_readme_docs(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        ctx=ctx,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.SUPERUSER,
        "i18n": {
            "name_key": "plugin.admin_backup.name",
            "description_key": "plugin.admin_backup.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="admin",
            order=130,
            source=DOCS_SOURCE,
            slug="admin.backup",
            parent_slug="admin",
            aliases=("备份管理模块", "数据库备份管理", "admin.backup"),
        ),
    },
)

admin_command_group = CommandGroup("admin")
admin_backup = admin_command_group.command(
    "backup",
    aliases={"备份管理"},
    permission=SUPERUSER,
    priority=5,
    block=False,
)


def _format_snapshot(locale: LocaleCode, snapshot: ResticSnapshotInfo) -> str:
    return "\n".join(
        [
            tr(
                locale,
                "admin.backup.snapshot.id",
                snapshot_id=snapshot.short_id or snapshot.id,
            ),
            tr(locale, "admin.backup.snapshot.time", time=snapshot.time or "-"),
            tr(
                locale,
                "admin.backup.snapshot.hostname",
                hostname=snapshot.hostname or "-",
            ),
            tr(
                locale,
                "admin.backup.snapshot.files",
                count=snapshot.total_files_processed or 0,
            ),
            tr(
                locale,
                "admin.backup.snapshot.bytes",
                count=snapshot.total_bytes_processed or 0,
            ),
        ]
    )


def _format_run_result(locale: LocaleCode, result: BackupResult) -> str:
    return "\n".join(
        [
            tr(locale, "admin.backup.run.completed"),
            tr(locale, "admin.backup.run.run_id", run_id=result.run_id),
            tr(locale, "admin.backup.run.manifest", path=result.manifest_path),
            tr(
                locale,
                "admin.backup.run.snapshot",
                snapshot_id=result.restic_snapshot_id or "-",
            ),
            tr(locale, "admin.backup.run.files", count=len(result.manifest.files)),
            tr(locale, "admin.backup.run.bytes", count=result.manifest.bytes_total),
        ]
    )


def _parse_limit(raw: str | None) -> int:
    if raw is None:
        return 5
    limit = int(raw)
    if limit <= 0:
        raise ValueError("admin.backup.limit.invalid")
    return limit


@admin_backup.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg()) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    docs = build_docs(DocsRenderContext(locale=locale))
    docs_text = str(docs)
    args = arg.extract_plain_text().strip().split()
    if not args:
        await matcher.finish(docs)

    action = args[0].lower()
    service = build_backup_service_from_config()

    try:
        if action in {"help", "帮助"}:
            await matcher.finish(docs)
            return

        if action == "check":
            snapshots = await service.list_snapshots()
            if not snapshots:
                await matcher.finish(tr(locale, "admin.backup.check.empty"))
                return
            latest = snapshots[0]
            await matcher.finish(
                "\n".join(
                    [
                        tr(locale, "admin.backup.check.ok", count=len(snapshots)),
                        tr(locale, "admin.backup.check.latest"),
                        _format_snapshot(locale, latest),
                    ]
                )
            )
            return

        if action == "snapshots":
            try:
                limit = _parse_limit(args[1] if len(args) > 1 else None)
            except ValueError:
                await matcher.finish(tr(locale, "admin.backup.limit.invalid"))
                return
            snapshots = await service.list_snapshots()
            if not snapshots:
                await matcher.finish(tr(locale, "admin.backup.snapshots.empty"))
                return
            lines = [
                tr(
                    locale,
                    "admin.backup.snapshots.title",
                    count=min(limit, len(snapshots)),
                )
            ]
            for snapshot in snapshots[:limit]:
                lines.append(_format_snapshot(locale, snapshot))
            await matcher.finish("\n".join(lines))
            return

        if action == "run":
            plan = build_default_backup_plan()
            result = await service.run(plan, force=True)
            if result is None:
                await matcher.finish(tr(locale, "admin.backup.run.skipped"))
                return
            await matcher.finish(_format_run_result(locale, result))
            return

        await matcher.finish(
            tr(locale, "admin.backup.unknown_command", action=action, docs=docs_text)
        )
    except FinishedException:
        raise
    except Exception as exc:
        await matcher.finish(tr(locale, "admin.backup.failed", message=str(exc)))
