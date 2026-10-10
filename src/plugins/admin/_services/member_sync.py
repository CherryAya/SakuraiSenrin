from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from nonebot.adapters.onebot.v11.bot import Bot

from src.config import config as config
from src.lib.admin_notifications import deliver_admin_notification_plan
from src.lib.display import fallback_group_name, format_group_label
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import DeliveryPlan
from src.lib.utils.common import get_current_time
from src.services.sync import MemberSyncReport, sync_members_from_api

SYNC_STATUS_LABEL_KEYS: dict[str, MessageKey] = {
    "running": "admin.sync_members.status.running",
    "completed": "admin.sync_members.status.completed",
    "failed": "admin.sync_members.status.failed",
}

SYNC_STAGE_LABEL_KEYS: dict[str, MessageKey] = {
    "queued": "admin.sync_members.stage.queued",
    "loading_group_list": "admin.sync_members.stage.loading_group_list",
    "syncing_group": "admin.sync_members.stage.syncing_group",
    "reporting_progress": "admin.sync_members.stage.reporting_progress",
    "waiting_between_groups": "admin.sync_members.stage.waiting_between_groups",
    "finalizing": "admin.sync_members.stage.finalizing",
    "failed": "admin.sync_members.stage.failed",
    "done": "admin.sync_members.stage.done",
}

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
    pending_detail_lines: list[str] = field(default_factory=list[str])
    failure_summaries: list[str] = field(default_factory=list[str])
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
    *,
    locale: LocaleCode = "zh-CN",
) -> str:
    if state is None:
        return tr(locale, "admin.sync_members.idle")
    field = _build_field_lines(state, locale=locale)
    field.insert(0, tr(locale, "admin.sync_members.running"))
    field.insert(
        -3,
        tr(
            locale,
            "admin.sync_members.field.fixed_interval",
            value=SYNC_MEMBERS_ALL_INTERVAL_SECONDS,
        ),
    )
    return "\n".join(field)


def build_sync_members_completion_summary(
    state: SyncMembersAllTaskState,
    *,
    locale: LocaleCode = "zh-CN",
) -> str:
    """Build the short completion summary shared by admin notice and matcher."""
    return "\n".join(
        [
            tr(locale, "admin.sync_members.completed"),
            tr(
                locale,
                "admin.sync_members.summary.total",
                count=state.total_groups,
            ),
            tr(locale, "admin.sync_members.summary.succeeded", count=state.succeeded),
            tr(locale, "admin.sync_members.summary.failed", count=state.failed),
            tr(locale, "admin.sync_members.summary.skipped", count=state.skipped),
        ]
    )


def _format_task_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _format_status_label(status: str, *, locale: LocaleCode = "zh-CN") -> str:
    key = SYNC_STATUS_LABEL_KEYS.get(status)
    return tr(locale, key) if key is not None else status


def _format_stage_label(stage: str, *, locale: LocaleCode = "zh-CN") -> str:
    key = SYNC_STAGE_LABEL_KEYS.get(stage)
    return tr(locale, key) if key is not None else stage


def _field(
    locale: LocaleCode,
    key: MessageKey,
    value: object,
) -> str:
    return tr(locale, key, value=value)


def _build_field_lines(
    state: SyncMembersAllTaskState,
    *,
    locale: LocaleCode,
) -> list[str]:
    return [
        _field(locale, "admin.sync_members.field.task_id", state.task_id),
        _field(
            locale,
            "admin.sync_members.field.status",
            _format_status_label(state.status, locale=locale),
        ),
        _field(locale, "admin.sync_members.field.total_groups", state.total_groups),
        _field(locale, "admin.sync_members.field.completed", state.completed),
        _field(locale, "admin.sync_members.field.succeeded", state.succeeded),
        _field(locale, "admin.sync_members.field.failed", state.failed),
        _field(locale, "admin.sync_members.field.skipped", state.skipped),
        _field(locale, "admin.sync_members.field.remaining", state.remaining),
        _field(
            locale,
            "admin.sync_members.field.current_group",
            format_group_label(
                state.current_group_id,
                state.current_group_name,
                locale=locale,
            ),
        ),
        _field(
            locale,
            "admin.sync_members.field.current_stage",
            _format_stage_label(state.current_stage, locale=locale),
        ),
        _field(
            locale,
            "admin.sync_members.field.stage_elapsed",
            state.stage_elapsed_seconds,
        ),
        _field(locale, "admin.sync_members.field.elapsed", state.elapsed_seconds),
    ]


def _build_detail_line(
    index: int,
    total: int,
    report: MemberSyncReport,
    *,
    locale: LocaleCode = "zh-CN",
) -> str:
    prefix = f"[{index:03d}/{max(total, 1):03d}]"
    group_label = format_group_label(
        report.group_id,
        report.group_name,
        locale=locale,
    )
    elapsed = f"{report.elapsed_ms / 1000:.2f}s"
    if report.ok:
        return tr(
            locale,
            "admin.sync_members.detail.ok",
            prefix=prefix,
            group=group_label,
            members=report.member_total,
            elapsed=elapsed,
            source=report.trigger_source,
        )
    return tr(
        locale,
        "admin.sync_members.detail.failed",
        prefix=prefix,
        group=group_label,
        members=report.member_total,
        elapsed=elapsed,
        source=report.trigger_source,
        error_type=report.error_type or "Unknown",
        error_reason=report.error_reason or "-",
    )


def _build_failure_summary(
    state: SyncMembersAllTaskState,
    *,
    locale: LocaleCode = "zh-CN",
) -> list[str]:
    if not state.failure_summaries:
        return []
    visible = state.failure_summaries[:5]
    lines = [tr(locale, "admin.sync_members.failure_summary.title"), *visible]
    if len(state.failure_summaries) > len(visible):
        lines.append(
            tr(
                locale,
                "admin.sync_members.failure_summary.more",
                count=len(state.failure_summaries) - len(visible),
            )
        )
    return lines


def _build_overview_text(
    state: SyncMembersAllTaskState,
    *,
    final: bool,
    locale: LocaleCode = "zh-CN",
) -> str:
    lines = [
        tr(locale, "admin.sync_members.report.title"),
        *_build_field_lines(state, locale=locale),
    ]
    lines.extend(
        [
            _field(
                locale,
                "admin.sync_members.field.started_at",
                _format_task_time(state.started_at),
            ),
            _field(
                locale,
                "admin.sync_members.field.interval",
                SYNC_MEMBERS_ALL_INTERVAL_SECONDS,
            ),
            (
                tr(locale, "admin.sync_members.next_report.final")
                if final
                else tr(
                    locale,
                    "admin.sync_members.next_report.batch",
                    count=SYNC_MEMBERS_ALL_BATCH_SIZE,
                )
            ),
        ]
    )
    if state.latest_error:
        lines.append(
            _field(
                locale,
                "admin.sync_members.field.latest_error",
                state.latest_error,
            )
        )
    lines.extend(_build_failure_summary(state, locale=locale))
    return "\n".join(lines)


def _build_progress_plan(
    state: SyncMembersAllTaskState,
    *,
    final: bool,
    locale: LocaleCode = "zh-CN",
) -> DeliveryPlan:
    messages = [
        _build_overview_text(state, final=final, locale=locale),
        *state.pending_detail_lines,
    ]
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


async def run_sync_members_for_all_groups(
    bot: Bot,
    *,
    locale: LocaleCode = "zh-CN",
) -> SyncMembersAllTaskState:
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
                group_name = str(
                    info.get("group_name", "")
                    or fallback_group_name(group_id, locale=locale)
                )
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
                    group_label = format_group_label(
                        report.group_id,
                        report.group_name,
                        locale=locale,
                    )
                    state.failure_summaries.append(
                        f"{group_label} "
                        f"{report.error_type or 'Unknown'}: "
                        f"{report.error_reason or '-'}"
                    )
                state.pending_detail_lines.append(
                    _build_detail_line(
                        index,
                        state.total_groups,
                        report,
                        locale=locale,
                    )
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
                        _build_progress_plan(state, final=False, locale=locale),
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
                _build_progress_plan(state, final=True, locale=locale),
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
                _build_progress_plan(state, final=True, locale=locale),
            )
            raise
        finally:
            _active_sync_members_all_state = None
