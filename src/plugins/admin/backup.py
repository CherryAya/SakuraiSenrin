from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11.bot import Bot
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
from src.lib.long_task import (
    CompositeProgressSink,
    LoggerProgressSink,
    LongTaskRunner,
    LongTaskSpec,
    MessageEventProgressSink,
)
from src.lib.message_plan import MessagePlanInput, finish_with_message
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_doc_demo_plan_entry,
    build_readme_docs_plan_entry,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.services.backup import (
    BackupResult,
    ResticSnapshotInfo,
    build_backup_service_from_config,
    build_default_backup_plan,
    resolve_default_backup_profile_name,
)
from src.services.startup_sync import restore_remote_snapshot_into_local

name = tr("zh-CN", "plugin.admin_backup.name")
description = tr("zh-CN", "plugin.admin_backup.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "backup" / "README.MD"


def build_docs(ctx: DocsRenderContext | None = None) -> MessagePlanInput:
    return build_readme_docs_plan_entry(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        ctx=ctx,
    )


def _build_error_demo(
    locale: LocaleCode,
    message: str,
    feature_query: str | None,
) -> MessagePlanInput:
    return build_doc_demo_plan_entry(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        locale=locale,
        feature_query=feature_query,
        prefix_text=message,
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


def _format_run_result(
    locale: LocaleCode,
    result: BackupResult,
    *,
    profile_name: str,
) -> str:
    return "\n".join(
        [
            tr(locale, "admin.backup.run.completed"),
            tr(locale, "admin.backup.profile", profile=profile_name),
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


def _parse_restore_snapshot(raw: str | None) -> str:
    snapshot = (raw or "").strip()
    if not snapshot:
        raise ValueError("admin.backup.restore.snapshot_required")
    return snapshot


def _parse_restore_args(args: list[str]) -> tuple[str, str | None, bool]:
    snapshot = _parse_restore_snapshot(args[1] if len(args) > 1 else None)
    profile_name: str | None = None
    confirm_production_restore = False
    for extra in args[2:]:
        token = extra.strip()
        if not token:
            continue
        if token in {"confirm-production", "--confirm-production-restore"}:
            confirm_production_restore = True
            continue
        if profile_name is None:
            profile_name = token
            continue
    return snapshot, profile_name, confirm_production_restore


def _build_progress_sink(bot: Bot, event: MessageEvent) -> CompositeProgressSink:
    return CompositeProgressSink(
        LoggerProgressSink(),
        MessageEventProgressSink(bot, event),
    )


@admin_backup.handle()
async def _(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    docs = build_docs(DocsRenderContext(locale=locale))
    docs_text = str(docs)
    args = arg.extract_plain_text().strip().split()
    if not args:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=docs,
            source_kind="admin_backup",
        )

    action = args[0].lower()

    try:
        if action in {"help", "帮助"}:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=docs,
                source_kind="admin_backup",
            )
            return

        if action == "check":
            service = build_backup_service_from_config()
            snapshots = await service.list_snapshots()
            if not snapshots:
                await finish_with_message(
                    bot,
                    matcher,
                    event=event,
                    message=tr(locale, "admin.backup.check.empty"),
                    source_kind="admin_backup",
                )
                return
            latest = snapshots[0]
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message="\n".join(
                    [
                        tr(locale, "admin.backup.check.ok", count=len(snapshots)),
                        tr(
                            locale,
                            "admin.backup.profile",
                            profile=service.profile_name,
                        ),
                        tr(locale, "admin.backup.check.latest"),
                        _format_snapshot(locale, latest),
                    ]
                ),
                source_kind="admin_backup",
            )
            return

        if action == "snapshots":
            try:
                limit = _parse_limit(args[1] if len(args) > 1 else None)
            except ValueError:
                await finish_with_message(
                    bot,
                    matcher,
                    event=event,
                    message=_build_error_demo(
                        locale,
                        tr(locale, "admin.backup.limit.invalid"),
                        "snapshots",
                    ),
                    source_kind="admin_backup",
                )
                return
            service = build_backup_service_from_config()
            snapshots = await service.list_snapshots()
            if not snapshots:
                await finish_with_message(
                    bot,
                    matcher,
                    event=event,
                    message=tr(locale, "admin.backup.snapshots.empty"),
                    source_kind="admin_backup",
                )
                return
            lines = [
                tr(locale, "admin.backup.profile", profile=service.profile_name),
                tr(
                    locale,
                    "admin.backup.snapshots.title",
                    count=min(limit, len(snapshots)),
                ),
            ]
            for snapshot in snapshots[:limit]:
                lines.append(_format_snapshot(locale, snapshot))
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message="\n".join(lines),
                source_kind="admin_backup",
            )
            return

        if action == "run":
            service = build_backup_service_from_config()
            plan = build_default_backup_plan()
            async with LongTaskRunner(
                LongTaskSpec(
                    task_name="admin.backup.run",
                    source_kind="admin_backup_run",
                    prompt=tr(locale, "admin.backup.run.running"),
                ),
                sink=_build_progress_sink(bot, event),
            ) as long_task:
                await long_task.advance("archiving")
                result = await service.run(plan, force=True)
            if result is None:
                await finish_with_message(
                    bot,
                    matcher,
                    event=event,
                    message=tr(locale, "admin.backup.run.skipped"),
                    source_kind="admin_backup",
                )
                return
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=_format_run_result(
                    locale,
                    result,
                    profile_name=service.profile_name,
                ),
                source_kind="admin_backup",
            )
            return

        if action == "restore":
            try:
                (
                    snapshot,
                    profile_name,
                    confirm_production_restore,
                ) = _parse_restore_args(args)
            except ValueError:
                await finish_with_message(
                    bot,
                    matcher,
                    event=event,
                    message=_build_error_demo(
                        locale,
                        tr(locale, "admin.backup.restore.snapshot_required"),
                        "restore",
                    ),
                    source_kind="admin_backup",
                )
                return
            async with LongTaskRunner(
                LongTaskSpec(
                    task_name="admin.backup.restore",
                    source_kind="admin_backup_restore",
                    prompt=tr(locale, "admin.backup.restore.running"),
                ),
                sink=_build_progress_sink(bot, event),
            ) as long_task:
                default_profile_name = resolve_default_backup_profile_name()
                resolved_profile = profile_name or default_profile_name
                await long_task.advance(
                    "restoring",
                    metadata={
                        "snapshot": snapshot,
                        "profile": resolved_profile,
                    },
                )
                await restore_remote_snapshot_into_local(
                    snapshot=snapshot,
                    profile_name=resolved_profile,
                    confirm_production_restore=confirm_production_restore,
                )
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message="\n".join(
                    [
                        tr(locale, "admin.backup.restore.completed"),
                        tr(
                            locale,
                            "admin.backup.profile",
                            profile=profile_name or default_profile_name,
                        ),
                        tr(
                            locale,
                            "admin.backup.restore.snapshot",
                            snapshot_id=snapshot,
                        ),
                    ]
                ),
                source_kind="admin_backup",
            )
            return

        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=_build_error_demo(
                locale,
                tr(
                    locale,
                    "admin.backup.unknown_command",
                    action=action,
                    docs=docs_text,
                ),
                None,
            ),
            source_kind="admin_backup",
        )
    except FinishedException:
        raise
    except Exception as exc:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=tr(locale, "admin.backup.failed", message=str(exc)),
            source_kind="admin_backup",
        )
