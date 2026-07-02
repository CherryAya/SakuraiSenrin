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


@dataclass(slots=True, frozen=True)
class LongTaskAuditTarget:
    slug: str
    label: str
    path: str
    category: str
    description: str
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
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="wordbank.guided_flow",
        label="Wordbank Guided Flow",
        path="src/plugins/wordbank/guided_flow.py",
        category="plugin",
        description="Guided trigger/response collection and merged-forward import.",
        expect_matcher_sink=True,
    ),
    LongTaskAuditTarget(
        slug="study.command",
        label="Study Command",
        path="src/plugins/study/__init__.py",
        category="plugin",
        description="Study guided flow and direct submission path.",
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
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="picsearch.query",
        label="Picsearch Query",
        path="src/plugins/picsearch/__init__.py",
        category="plugin",
        description="Per-image delayed search prompt.",
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="admin.backup",
        label="Admin Backup",
        path="src/plugins/admin/backup.py",
        category="plugin",
        description="Manual backup and restore commands.",
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="water.admin.settle",
        label="Water Admin Settle",
        path="src/plugins/water/handlers/admin.py",
        category="plugin",
        description="Manual settlement command.",
        expect_logger_sink=True,
        expect_message_event_sink=True,
    ),
    LongTaskAuditTarget(
        slug="wordbank.jobs",
        label="Wordbank Scheduled Jobs",
        path="src/plugins/wordbank/__init__.py",
        category="job",
        description="Archive and media maintenance cron jobs.",
        expect_logger_sink=True,
    ),
    LongTaskAuditTarget(
        slug="water.jobs",
        label="Water Scheduled Jobs",
        path="src/plugins/water/__init__.py",
        category="job",
        description="Settlement/archive/report cron jobs.",
        expect_logger_sink=True,
    ),
    LongTaskAuditTarget(
        slug="backup.scheduler",
        label="Backup Scheduler",
        path="src/services/backup_scheduler.py",
        category="service",
        description="Scheduled backup runner.",
        expect_logger_sink=True,
    ),
    LongTaskAuditTarget(
        slug="startup.restore",
        label="Startup Restore",
        path="src/services/startup_sync.py",
        category="service",
        description="Remote snapshot restore and runtime refresh.",
        expect_logger_sink=True,
    ),
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _target_status(root: Path, target: LongTaskAuditTarget) -> dict[str, Any]:
    path = root / target.path
    source = _read_text(path)
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
            if WAIT_PROMPT_PATTERN.search(line) is None:
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
