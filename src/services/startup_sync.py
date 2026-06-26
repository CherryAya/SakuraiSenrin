"""Startup backup freshness check and optional remote restore flow."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import shutil

from nonebot.adapters.onebot.v11 import Bot

from src.config import config
from src.lib.db.manager import db_manager
from src.logger import logger
from src.repositories import blacklist_repo, group_repo, member_repo, user_repo
from src.services.backup import (
    BackupService,
    ResticSnapshotInfo,
    build_backup_service_from_config,
)


@dataclass(slots=True, frozen=True)
class StartupSyncStatus:
    local_latest_at: int
    remote_latest_at: int | None
    remote_snapshot: ResticSnapshotInfo | None

    @property
    def has_remote_snapshot(self) -> bool:
        return self.remote_snapshot is not None and self.remote_latest_at is not None


@dataclass(slots=True)
class PendingStartupRestore:
    snapshot_id: str
    remote_latest_at: int
    local_latest_at: int
    prompt_message_id: str


_pending_restore_by_prompt: dict[str, PendingStartupRestore] = {}
_startup_check_lock = asyncio.Lock()
_restore_lock = asyncio.Lock()
_startup_check_completed = False


def is_startup_sync_reply_text(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"y", "yes", "n", "no", "同步", "取消", "恢复", "跳过"}


def resolve_startup_sync_reply_decision(text: str) -> bool | None:
    normalized = text.strip().lower()
    if normalized in {"y", "yes", "同步", "恢复"}:
        return True
    if normalized in {"n", "no", "取消", "跳过"}:
        return False
    return None


async def run_startup_backup_freshness_check(bot: Bot) -> None:
    global _startup_check_completed
    if _startup_check_completed:
        return
    async with _startup_check_lock:
        if _startup_check_completed:
            return
        try:
            status = await _collect_startup_sync_status()
        except Exception as exc:
            logger.warning(f"[StartupSync] status check failed: {exc}")
            _startup_check_completed = True
            return

        local_latest_at = status.local_latest_at
        remote_latest_at = status.remote_latest_at
        if remote_latest_at is None or status.remote_snapshot is None:
            logger.info(
                "[StartupSync] no remote snapshot available; "
                f"local_latest_at={local_latest_at}"
            )
            _startup_check_completed = True
            return

        if local_latest_at > remote_latest_at:
            logger.warning(
                "[StartupSync] local data is newer than remote snapshot; "
                f"local_latest_at={local_latest_at} remote_latest_at={remote_latest_at}. "
                "Please run a backup soon."
            )
            _startup_check_completed = True
            return

        if local_latest_at == remote_latest_at:
            logger.info(
                "[StartupSync] local data is already up to date with remote snapshot"
            )
            _startup_check_completed = True
            return

        await _notify_superusers_for_remote_restore(
            bot,
            snapshot=status.remote_snapshot,
            remote_latest_at=remote_latest_at,
            local_latest_at=local_latest_at,
        )
        _startup_check_completed = True


async def handle_startup_sync_reply(bot: Bot, *, reply_message_id: str, text: str) -> str | None:
    decision = resolve_startup_sync_reply_decision(text)
    if decision is None:
        return None
    pending = _pending_restore_by_prompt.pop(reply_message_id, None)
    if pending is None:
        return None
    if not decision:
        return (
            "已取消本次启动同步。当前进程继续使用本地数据；"
            "如需稍后手动同步，请再执行备份恢复流程。"
        )

    try:
        await restore_latest_remote_snapshot_into_local(snapshot_id=pending.snapshot_id)
    except Exception as exc:
        logger.exception(f"[StartupSync] restore failed: {exc}")
        return f"启动同步失败: {exc}"
    return (
        "远端快照已恢复到本地，并已刷新核心缓存。"
        "建议确认业务数据后再继续高风险写入操作。"
    )


async def restore_latest_remote_snapshot_into_local(*, snapshot_id: str) -> None:
    async with _restore_lock:
        service = build_backup_service_from_config()
        restore_root = service.local_root / "restore" / snapshot_id
        if restore_root.exists():
            await asyncio.to_thread(shutil.rmtree, restore_root, True)
        await service.restore(snapshot=snapshot_id, target=restore_root)
        manifest = _load_restore_manifest(restore_root / "manifest.json")
        await _apply_restore_manifest(manifest, restore_root)
        await _warm_up_core_repositories()


async def _collect_startup_sync_status() -> StartupSyncStatus:
    service = build_backup_service_from_config()
    local_latest_at = await _get_local_latest_data_mtime()
    try:
        snapshots = await service.list_snapshots()
    except Exception as exc:
        logger.warning(f"[StartupSync] remote snapshot listing failed: {exc}")
        return StartupSyncStatus(
            local_latest_at=local_latest_at,
            remote_latest_at=None,
            remote_snapshot=None,
        )
    if not snapshots:
        return StartupSyncStatus(
            local_latest_at=local_latest_at,
            remote_latest_at=None,
            remote_snapshot=None,
        )
    latest = snapshots[0]
    remote_latest_at = _parse_restic_snapshot_time(latest.time)
    return StartupSyncStatus(
        local_latest_at=local_latest_at,
        remote_latest_at=remote_latest_at,
        remote_snapshot=latest,
    )


async def _notify_superusers_for_remote_restore(
    bot: Bot,
    *,
    snapshot: ResticSnapshotInfo,
    remote_latest_at: int,
    local_latest_at: int,
) -> None:
    prompt = (
        "检测到远端备份比本地新。\n"
        f"本地最新时间戳: {local_latest_at}\n"
        f"远端最新时间戳: {remote_latest_at}\n"
        f"快照: {snapshot.short_id or snapshot.id}\n"
        "回复 y / 同步 / 恢复 可立即用最新远端快照覆盖本地数据库；"
        "回复 n / 跳过 / 取消 则继续使用本地数据。"
    )
    for superuser_id in config.SUPERUSERS:
        try:
            send_result = await bot.send_private_msg(
                user_id=int(superuser_id),
                message=prompt,
            )
        except Exception as exc:
            logger.warning(
                f"[StartupSync] failed to notify superuser {superuser_id}: {exc}"
            )
            continue
        message_id = str(send_result.get("message_id", ""))
        if not message_id:
            continue
        _pending_restore_by_prompt[message_id] = PendingStartupRestore(
            snapshot_id=snapshot.id,
            remote_latest_at=remote_latest_at,
            local_latest_at=local_latest_at,
            prompt_message_id=message_id,
        )


async def _apply_restore_manifest(manifest: dict[str, object], restore_root: Path) -> None:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise RuntimeError("restore manifest is invalid: missing files")
    await db_manager.dispose_all()
    for file_meta in files:
        if not isinstance(file_meta, dict):
            continue
        source_path = file_meta.get("source_path")
        snapshot_path = file_meta.get("snapshot_path")
        if not isinstance(source_path, str) or not isinstance(snapshot_path, str):
            continue
        restored_file = restore_root / Path(snapshot_path).name
        if not restored_file.exists():
            raise RuntimeError(f"restored file missing: {restored_file}")
        target = Path(source_path)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, restored_file, target)


def _load_restore_manifest(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise RuntimeError(f"restore manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


async def _warm_up_core_repositories() -> None:
    await user_repo.warm_up()
    await group_repo.warm_up()
    await member_repo.warm_up()
    await blacklist_repo.warm_up()


async def _get_local_latest_data_mtime() -> int:
    data_root = Path("data")
    if not data_root.exists():
        return 0

    def _scan() -> int:
        latest = 0
        for path in data_root.rglob("*"):
            if not path.is_file():
                continue
            try:
                mtime = int(path.stat().st_mtime)
            except OSError:
                continue
            if mtime > latest:
                latest = mtime
        return latest

    return await asyncio.to_thread(_scan)


def _parse_restic_snapshot_time(raw_time: str | None) -> int | None:
    if not raw_time:
        return None
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(raw_time).timestamp())
    except ValueError:
        return None


__all__ = [
    "handle_startup_sync_reply",
    "is_startup_sync_reply_text",
    "resolve_startup_sync_reply_decision",
    "restore_latest_remote_snapshot_into_local",
    "run_startup_backup_freshness_check",
]
