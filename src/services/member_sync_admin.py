from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from nonebot.adapters.onebot.v11.bot import Bot

from src.config import config as config
from src.lib.admin_notifications import deliver_admin_notification_plan
from src.lib.message_plan import DeliveryPlan
from src.lib.utils.common import get_current_time
from src.services.sync import MemberSyncReport, sync_members_from_api

SYNC_MEMBERS_ALL_BATCH_SIZE = 10
SYNC_MEMBERS_ALL_INTERVAL_SECONDS = 6

_sync_members_all_lock = asyncio.Lock()
_active_sync_members_all_state: SyncMembersAllTaskState | None = None


@dataclass(slots=True)
class SyncMembersAllTaskState:
    task_id: str
    started_at: int
    total_groups: int = 0
    completed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    status: str = "running"
    current_group_id: str = ""
    current_group_name: str = ""
    current_stage: str = "queued"
    current_stage_started_at: int = 0
    pending_detail_lines: list[str] = field(default_factory=list)
    failure_summaries: list[str] = field(default_factory=list)
    latest_error: str = ""

    @property
    def remaining(self) -> int:
        return max(self.total_groups - self.completed, 0)

    @property
    def elapsed_seconds(self) -> int:
        return max(get_current_time() - self.started_at, 0)

    @property
    def stage_elapsed_seconds(self) -> int:
        started_at = self.current_stage_started_at or self.started_at
        return max(get_current_time() - started_at, 0)


def get_active_sync_members_all_state() -> SyncMembersAllTaskState | None:
    return _active_sync_members_all_state


def build_sync_members_all_running_summary(
    state: SyncMembersAllTaskState | None,
) -> str:
    if state is None:
        return "当前没有正在执行的群成员全量同步任务。"
    return "\n".join(
        [
            "已有群成员全量同步任务正在执行。",
            f"任务 ID：{state.task_id}",
            f"任务状态：{_format_status_label(state.status)}",
            f"总群数：{state.total_groups}",
            f"已处理：{state.completed}",
            f"成功：{state.succeeded}",
            f"失败：{state.failed}",
            f"跳过：{state.skipped}",
            f"剩余：{state.remaining}",
            "当前群："
            f"{_format_group_label(state.current_group_id, state.current_group_name)}",
            f"当前阶段：{_format_stage_label(state.current_stage)}",
            f"当前停留：{state.stage_elapsed_seconds}s",
            f"已耗时：{state.elapsed_seconds}s",
            f"固定间隔：{SYNC_MEMBERS_ALL_INTERVAL_SECONDS}s/群",
        ]
    )


def _format_task_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _format_group_label(group_id: str, group_name: str) -> str:
    if not group_id:
        return "-"
    safe_name = group_name or f"群聊_{group_id[-4:]}"
    return f"[{group_id}|{safe_name}]"


def _format_status_label(status: str) -> str:
    return {
        "running": "运行中",
        "completed": "已完成",
        "failed": "已失败",
    }.get(status, status)


def _format_stage_label(stage: str) -> str:
    return {
        "queued": "排队中",
        "loading_group_list": "拉取群列表",
        "syncing_group": "拉取成员列表",
        "reporting_progress": "发送进度报告",
        "waiting_between_groups": "群间等待",
        "finalizing": "发送最终汇总",
        "failed": "任务失败",
        "done": "任务完成",
    }.get(stage, stage)


def _build_detail_line(
    index: int,
    total: int,
    report: MemberSyncReport,
) -> str:
    prefix = f"[{index:03d}/{max(total, 1):03d}]"
    group_label = _format_group_label(report.group_id, report.group_name)
    if report.ok:
        return (
            f"{prefix} {group_label} 成功 "
            f"members={report.member_total} elapsed={report.elapsed_ms / 1000:.2f}s "
            f"source={report.trigger_source}"
        )
    return (
        f"{prefix} {group_label} 失败 "
        f"members={report.member_total} elapsed={report.elapsed_ms / 1000:.2f}s "
        f"source={report.trigger_source} "
        f"error={report.error_type or 'Unknown'}: {report.error_reason or '-'}"
    )


def _build_failure_summary(state: SyncMembersAllTaskState) -> list[str]:
    if not state.failure_summaries:
        return []
    visible = state.failure_summaries[:5]
    lines = ["失败摘要：", *visible]
    if len(state.failure_summaries) > len(visible):
        lines.append(
            f"其余 {len(state.failure_summaries) - len(visible)} 个失败群请看明细。"
        )
    return lines


def _build_overview_text(
    state: SyncMembersAllTaskState,
    *,
    final: bool,
) -> str:
    lines = [
        "群成员全量同步进度",
        f"任务 ID：{state.task_id}",
        f"任务状态：{_format_status_label(state.status)}",
        f"总群数：{state.total_groups}",
        f"已处理：{state.completed}",
        f"成功：{state.succeeded}",
        f"失败：{state.failed}",
        f"跳过：{state.skipped}",
        f"剩余：{state.remaining}",
        "当前群："
        f"{_format_group_label(state.current_group_id, state.current_group_name)}",
        f"当前阶段：{_format_stage_label(state.current_stage)}",
        f"当前停留：{state.stage_elapsed_seconds}s",
        f"开始时间：{_format_task_time(state.started_at)}",
        f"已耗时：{state.elapsed_seconds}s",
        f"群间间隔：{SYNC_MEMBERS_ALL_INTERVAL_SECONDS}s",
        (
            "下次上报：本次为最终汇总。"
            if final
            else f"下次上报：每完成 {SYNC_MEMBERS_ALL_BATCH_SIZE} 个群或任务结束。"
        ),
    ]
    if state.latest_error:
        lines.append(f"任务错误：{state.latest_error}")
    lines.extend(_build_failure_summary(state))
    return "\n".join(lines)


def _build_progress_plan(
    state: SyncMembersAllTaskState,
    *,
    final: bool,
) -> DeliveryPlan:
    messages = [_build_overview_text(state, final=final), *state.pending_detail_lines]
    return DeliveryPlan(
        messages=tuple(messages),
        source_kind="admin_group_sync_members_all_progress",
        allow_asset_reuse=False,
        force_forward=True,
    )


async def _notify_superusers(bot: Bot, plan: DeliveryPlan) -> None:
    await deliver_admin_notification_plan(bot, plan=plan)


def _set_stage(
    state: SyncMembersAllTaskState,
    stage: str,
    *,
    group_id: str = "",
    group_name: str = "",
) -> None:
    state.current_stage = stage
    state.current_stage_started_at = get_current_time()
    state.current_group_id = group_id
    state.current_group_name = group_name


async def run_sync_members_for_all_groups(bot: Bot) -> SyncMembersAllTaskState:
    global _active_sync_members_all_state
    if _sync_members_all_lock.locked():
        raise RuntimeError("sync members all task already running")

    task_id = f"sync-members-all-{get_current_time()}"
    state = SyncMembersAllTaskState(
        task_id=task_id,
        started_at=get_current_time(),
        current_stage_started_at=get_current_time(),
    )

    async with _sync_members_all_lock:
        _active_sync_members_all_state = state
        try:
            _set_stage(state, "loading_group_list")
            raw_groups = await bot.get_group_list()
            seen_group_ids: set[str] = set()
            groups: list[tuple[str, str]] = []
            for info in raw_groups:
                group_id = str(info.get("group_id", "")).strip()
                if not group_id or not group_id.isdigit():
                    state.skipped += 1
                    continue
                if group_id in seen_group_ids:
                    state.skipped += 1
                    continue
                seen_group_ids.add(group_id)
                group_name = str(info.get("group_name", "") or f"群聊_{group_id[-4:]}")
                groups.append((group_id, group_name))
            groups.sort(key=lambda item: int(item[0]))
            state.total_groups = len(groups)

            for index, (group_id, group_name) in enumerate(groups, start=1):
                _set_stage(
                    state,
                    "syncing_group",
                    group_id=group_id,
                    group_name=group_name,
                )
                report = await sync_members_from_api(
                    bot,
                    group_id,
                    trigger_source="admin_sync_all",
                )
                state.completed += 1
                if report.ok:
                    state.succeeded += 1
                else:
                    state.failed += 1
                    state.failure_summaries.append(
                        f"{_format_group_label(report.group_id, report.group_name)} "
                        f"{report.error_type or 'Unknown'}: "
                        f"{report.error_reason or '-'}"
                    )
                state.pending_detail_lines.append(
                    _build_detail_line(index, state.total_groups, report)
                )

                if state.completed % SYNC_MEMBERS_ALL_BATCH_SIZE == 0:
                    _set_stage(
                        state,
                        "reporting_progress",
                        group_id=group_id,
                        group_name=group_name,
                    )
                    await _notify_superusers(
                        bot,
                        _build_progress_plan(state, final=False),
                    )
                    state.pending_detail_lines.clear()

                if index < state.total_groups:
                    _set_stage(
                        state,
                        "waiting_between_groups",
                        group_id=group_id,
                        group_name=group_name,
                    )
                    await asyncio.sleep(SYNC_MEMBERS_ALL_INTERVAL_SECONDS)

            state.status = "completed"
            _set_stage(state, "finalizing")
            await _notify_superusers(
                bot,
                _build_progress_plan(state, final=True),
            )
            state.pending_detail_lines.clear()
            _set_stage(state, "done")
            return state
        except Exception as exc:
            state.status = "failed"
            state.latest_error = f"{type(exc).__name__}: {exc}"
            _set_stage(state, "failed")
            await _notify_superusers(
                bot,
                _build_progress_plan(state, final=True),
            )
            raise
        finally:
            _active_sync_members_all_state = None
