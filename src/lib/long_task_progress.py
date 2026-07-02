"""Developer-facing LongTask migration audit helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from src.lib.utils.common import get_current_time

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENDPOINT_PATH = ROOT / "docs" / "development" / "long-task-progress.json"
RUNTIME_SCAN_ROOTS = (
    Path("src/plugins"),
    Path("src/services"),
    Path("src/hooks"),
)
WAIT_PROMPT_PATTERN = re.compile(
    r"(请稍候|请稍等|请稍后|处理中|执行中|整理中|搜索中|加载中)"
)
WAIT_I18N_KEYS = {
    "admin.backup.restore.running",
    "admin.backup.run.running",
    "picsearch.searching",
    "water.admin.settle.running",
    "water.common.working",
    "water.rank.working",
    "wordbank.add.processing_with_media",
}


@dataclass(slots=True, frozen=True)
class LongTaskAuditTarget:
    slug: str
    label: str
    path: str
    category: str
    description: str
    scope_terms: tuple[str, ...] = ()
    context_lines: int = 80
    expect_runner: bool = True
    expect_logger_sink: bool = False
    expect_message_event_sink: bool = False
    expect_matcher_sink: bool = False


@dataclass(slots=True, frozen=True)
class LongTaskLegacyCandidate:
    path: str
    line: int
    snippet: str


DEFAULT_LONG_TASK_AUDIT_TARGETS: tuple[LongTaskAuditTarget, ...] = (
    LongTaskAuditTarget(
        slug="wordbank.entry_add",
        label="Wordbank Direct Add",
        path="src/plugins/wordbank/entry_commands.py",
        category="plugin",
        description="Direct add command with media ingestion.",
        scope_terms=("wordbank.add.media_submission",),
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="wordbank.guided_flow",
        label="Wordbank Guided Flow",
        path="src/plugins/wordbank/guided_flow.py",
        category="plugin",
        description="Guided trigger/response collection and merged-forward import.",
        scope_terms=(
            "record_guided_trigger",
            "record_guided_response",
            "record_guided_forward_response_choice",
        ),
        expect_matcher_sink=True,
    ),
    LongTaskAuditTarget(
        slug="study.command",
        label="Study Command",
        path="src/plugins/study/__init__.py",
        category="plugin",
        description="Study guided flow and direct submission path.",
        scope_terms=(
            "_record_study_trigger",
            "_record_study_response",
            "_record_study_forward_response_choice",
            "@study_command.handle",
        ),
        expect_matcher_sink=True,
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="help.forward",
        label="Help Forward Rendering",
        path="src/plugins/help/__init__.py",
        category="plugin",
        description="Delayed wait prompt for heavy help rendering.",
        scope_terms=("async def _deliver_help_plan",),
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="picsearch.query",
        label="Picsearch Query",
        path="src/plugins/picsearch/__init__.py",
        category="plugin",
        description="Per-image delayed search prompt.",
        scope_terms=("async def run_search",),
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="admin.invite.list",
        label="Admin Invite List",
        path="src/plugins/admin/invite.py",
        category="plugin",
        description="Pending invitation list avatar fetch and image rendering.",
        scope_terms=(
            "async def generate_invitation_image_bytes",
            "async def handle_list",
        ),
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="admin.backup",
        label="Admin Backup",
        path="src/plugins/admin/backup.py",
        category="plugin",
        description="Manual backup and restore commands.",
        scope_terms=('if action == "run"', 'if action == "restore"'),
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="water.admin.settle",
        label="Water Admin Settle",
        path="src/plugins/water/handlers/admin.py",
        category="plugin",
        description="Manual settlement command.",
        scope_terms=("async def handle_settle",),
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="water.query_commands",
        label="Water Query Commands",
        path="src/plugins/water/__init__.py",
        category="plugin",
        description="Rank, report, and profile user query flows.",
        scope_terms=(
            "def _build_water_progress_sink",
            "async def _run_water_query_long_task",
            "@water_query.handle",
            "@water_query.got",
            "@water_profile.handle",
        ),
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="water.period_rank_handler",
        label="Water Period Rank Handler",
        path="src/plugins/water/handlers/rank.py",
        category="plugin",
        description="Legacy period-rank image handler.",
        scope_terms=("async def handle_period_rank",),
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="wordbank.jobs",
        label="Wordbank Scheduled Jobs",
        path="src/plugins/wordbank/__init__.py",
        category="job",
        description="Archive and media maintenance cron jobs.",
        scope_terms=("_wordbank_event_archive_job", "_wordbank_media_maintenance_job"),
        expect_logger_sink=True,
    ),
    LongTaskAuditTarget(
        slug="wordbank.view_runtime",
        label="Wordbank View Runtime",
        path="src/plugins/wordbank/__init__.py",
        category="plugin",
        description="Direct and guided search/detail/pending heavy rendering flows.",
        scope_terms=(
            "async def _send_pending_entries_view",
            "async def _finish_guided_search",
            "def _build_wordbank_progress_sink",
            "async def _send_search_result_view",
            "async def _send_group_detail_view",
        ),
        expect_logger_sink=True,
        expect_message_event_sink=True,
        expect_matcher_sink=True,
    ),
    LongTaskAuditTarget(
        slug="water.jobs",
        label="Water Scheduled Jobs",
        path="src/plugins/water/__init__.py",
        category="job",
        description="Settlement/archive/report cron jobs.",
        scope_terms=(
            "_water_daily_settlement_job",
            "_water_message_archive_job",
            "_water_summary_archive_job",
            "_water_daily_report_push_job",
        ),
        expect_logger_sink=True,
    ),
    LongTaskAuditTarget(
        slug="backup.scheduler",
        label="Backup Scheduler",
        path="src/services/backup_scheduler.py",
        category="service",
        description="Scheduled backup runner.",
        scope_terms=("async def _run_backup_job",),
        expect_logger_sink=True,
    ),
    LongTaskAuditTarget(
        slug="startup.restore",
        label="Startup Restore",
        path="src/services/startup_sync.py",
        category="service",
        description="Remote snapshot restore and runtime refresh.",
        scope_terms=("async def restore_remote_snapshot_into_local",),
        expect_logger_sink=True,
    ),
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _extract_scoped_source(source: str, target: LongTaskAuditTarget) -> str:
    if not target.scope_terms:
        return source

    lines = source.splitlines()
    matched_ranges: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        if any(term in line for term in target.scope_terms):
            start = max(0, index - target.context_lines)
            end = min(len(lines), index + target.context_lines + 1)
            matched_ranges.append((start, end))
    if not matched_ranges:
        return source

    merged: list[tuple[int, int]] = []
    for start, end in sorted(matched_ranges):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return "\n".join("\n".join(lines[start:end]) for start, end in merged)


def _target_status(root: Path, target: LongTaskAuditTarget) -> dict[str, Any]:
    path = root / target.path
    source = _extract_scoped_source(_read_text(path), target)
    exists = path.is_file()
    has_runner = "LongTaskRunner" in source
    has_logger_sink = "LoggerProgressSink" in source
    has_message_event_sink = "MessageEventProgressSink" in source
    has_matcher_sink = "MatcherProgressSink" in source

    missing: list[str] = []
    if target.expect_runner and not has_runner:
        missing.append("LongTaskRunner")
    if target.expect_logger_sink and not has_logger_sink:
        missing.append("LoggerProgressSink")
    if target.expect_message_event_sink and not has_message_event_sink:
        missing.append("MessageEventProgressSink")
    if target.expect_matcher_sink and not has_matcher_sink:
        missing.append("MatcherProgressSink")

    if not exists:
        status = "missing_file"
    elif missing:
        status = "partial"
    else:
        status = "complete"

    return {
        "slug": target.slug,
        "label": target.label,
        "path": target.path,
        "category": target.category,
        "description": target.description,
        "status": status,
        "exists": exists,
        "has_runner": has_runner,
        "has_logger_sink": has_logger_sink,
        "has_message_event_sink": has_message_event_sink,
        "has_matcher_sink": has_matcher_sink,
        "missing": missing,
    }


def _iter_runtime_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for base in RUNTIME_SCAN_ROOTS:
        scan_root = root / base
        if not scan_root.is_dir():
            continue
        files.extend(sorted(scan_root.rglob("*.py")))
    return files


def _collect_legacy_wait_candidates(root: Path) -> list[LongTaskLegacyCandidate]:
    candidates: list[LongTaskLegacyCandidate] = []
    for path in _iter_runtime_python_files(root):
        source = _read_text(path)
        if not source or "LongTaskRunner" in source:
            continue
        for line_number, line in enumerate(source.splitlines(), start=1):
            has_wait_text = WAIT_PROMPT_PATTERN.search(line) is not None
            has_wait_key = any(wait_key in line for wait_key in WAIT_I18N_KEYS)
            if not has_wait_text and not has_wait_key:
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            candidates.append(
                LongTaskLegacyCandidate(
                    path=str(path.relative_to(root)),
                    line=line_number,
                    snippet=stripped,
                )
            )
    return candidates


def build_long_task_progress_report(
    *,
    root: Path = ROOT,
    targets: tuple[LongTaskAuditTarget, ...] = DEFAULT_LONG_TASK_AUDIT_TARGETS,
) -> dict[str, Any]:
    target_rows = [_target_status(root, target) for target in targets]
    legacy_candidates = _collect_legacy_wait_candidates(root)
    complete_count = sum(1 for row in target_rows if row["status"] == "complete")
    partial_count = sum(1 for row in target_rows if row["status"] == "partial")
    missing_file_count = sum(
        1 for row in target_rows if row["status"] == "missing_file"
    )

    return {
        "version": 1,
        "generated_at": datetime.fromtimestamp(get_current_time()).isoformat(),
        "root": str(root),
        "summary": {
            "total_targets": len(target_rows),
            "complete_targets": complete_count,
            "partial_targets": partial_count,
            "missing_file_targets": missing_file_count,
            "legacy_wait_candidates": len(legacy_candidates),
        },
        "targets": target_rows,
        "legacy_wait_candidates": [
            {
                "path": item.path,
                "line": item.line,
                "snippet": item.snippet,
            }
            for item in legacy_candidates
        ],
    }


def write_long_task_progress_endpoint(
    output_path: Path = DEFAULT_ENDPOINT_PATH,
    *,
    root: Path = ROOT,
    targets: tuple[LongTaskAuditTarget, ...] = DEFAULT_LONG_TASK_AUDIT_TARGETS,
) -> dict[str, Any]:
    payload = build_long_task_progress_report(root=root, targets=targets)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload
