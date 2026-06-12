from __future__ import annotations

from pathlib import Path
import re

from nonebot import on_regex
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale
from src.lib.plugin_docs import DocsRenderContext, build_readme_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.services.backup import (
    BackupResult,
    ResticSnapshotInfo,
    build_backup_service_from_config,
    build_default_backup_plan,
)

name = "备份管理"
description = "检查远端备份状态、查看快照并手动触发数据库备份。"
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

admin_backup = on_regex(
    r"^[#＃井](?:admin(?:[.\s]+backup)?|备份管理)(?:\s+.*)?$",
    flags=re.IGNORECASE,
    permission=SUPERUSER,
    priority=5,
    block=False,
)


def _format_snapshot(snapshot: ResticSnapshotInfo) -> str:
    return "\n".join(
        [
            f"- 快照: {snapshot.short_id or snapshot.id}",
            f"  时间: {snapshot.time or '-'}",
            f"  主机: {snapshot.hostname or '-'}",
            f"  文件数: {snapshot.total_files_processed or 0}",
            f"  字节数: {snapshot.total_bytes_processed or 0}",
        ]
    )


def _format_run_result(result: BackupResult) -> str:
    return "\n".join(
        [
            "备份已完成。",
            f"run_id: {result.run_id}",
            f"manifest: {result.manifest_path}",
            f"snapshot: {result.restic_snapshot_id or '-'}",
            f"files: {len(result.manifest.files)}",
            f"bytes: {result.manifest.bytes_total}",
        ]
    )


def _parse_limit(raw: str | None) -> int:
    if raw is None:
        return 5
    limit = int(raw)
    if limit <= 0:
        raise ValueError("limit must be positive")
    return limit


def _extract_args_from_text(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    for prefix in ("#", "＃", "井"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].strip()
            break
    lowered = stripped.lower()
    for command_prefix in ("admin backup", "admin.backup", "备份管理"):
        if lowered.startswith(command_prefix):
            stripped = stripped[len(command_prefix) :].strip()
            return stripped.split() if stripped else []
    return stripped.split()


@admin_backup.handle()
async def _(matcher: Matcher, event: MessageEvent) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    docs = build_docs(DocsRenderContext(locale=locale))
    docs_text = str(docs)
    args = _extract_args_from_text(event.get_plaintext())
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
                await matcher.finish("远端备份仓库可用，但当前还没有快照。")
                return
            latest = snapshots[0]
            await matcher.finish(
                "\n".join(
                    [
                        f"远端备份仓库可用，共 {len(snapshots)} 个快照。",
                        "最新快照：",
                        _format_snapshot(latest),
                    ]
                )
            )
            return

        if action == "snapshots":
            try:
                limit = _parse_limit(args[1] if len(args) > 1 else None)
            except ValueError:
                await matcher.finish("快照数量必须是大于 0 的整数。")
                return
            snapshots = await service.list_snapshots()
            if not snapshots:
                await matcher.finish("当前远端没有任何备份快照。")
                return
            lines = [f"最近 {min(limit, len(snapshots))} 个快照："]
            for snapshot in snapshots[:limit]:
                lines.append(_format_snapshot(snapshot))
            await matcher.finish("\n".join(lines))
            return

        if action == "run":
            plan = build_default_backup_plan()
            result = await service.run(plan, force=True)
            if result is None:
                await matcher.finish("备份已跳过。")
                return
            await matcher.finish(_format_run_result(result))
            return

        await matcher.finish(f"未知子命令：{action}\n{docs_text}")
    except FinishedException:
        raise
    except Exception as exc:
        await matcher.finish(f"备份操作失败: {exc}")
