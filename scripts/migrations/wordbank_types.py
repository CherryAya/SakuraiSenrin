"""Shared data structures for wordbank migration scripts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast


@dataclass(slots=True, frozen=True)
class LegacyPgConfig:
    host: str
    port: int
    user: str
    password: str
    database: str = "senrin_wordbank"


@dataclass(slots=True, frozen=True)
class LegacyImageCatalog:
    image_root: Path
    files_by_name: dict[str, Path]
    files_by_stem: dict[str, Path]
    files_by_md5: dict[str, Path]
    saved_as_by_name: dict[str, str]

    def resolve(self, file_name: str, *, url: str = "") -> Path | None:
        path, _ = self.resolve_with_source(file_name, url=url)
        return path

    def resolve_with_source(
        self,
        file_name: str,
        *,
        url: str = "",
    ) -> tuple[Path | None, str]:
        from .wordbank_rules import extract_md5_candidates

        seen_candidates: set[str] = set()
        pending_candidates = [
            file_name.strip(),
            Path(file_name).name.strip(),
            url.strip(),
            Path(url).name.strip(),
        ]
        md5_candidates: list[str] = []

        while pending_candidates:
            candidate = pending_candidates.pop(0).strip()
            if not candidate or candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)

            mapped_name = self.saved_as_by_name.get(candidate.casefold())
            if mapped_name:
                for mapped_candidate in (mapped_name, Path(mapped_name).name):
                    direct = self.files_by_name.get(mapped_candidate.casefold())
                    if direct is not None:
                        return direct, "mapping"

            direct = self.files_by_name.get(candidate.casefold())
            if direct is not None:
                return direct, "direct_name"

            stem = Path(candidate).stem.strip()
            if stem:
                by_stem = self.files_by_stem.get(stem.casefold())
                if by_stem is not None:
                    return by_stem, "stem"
                pending_candidates.append(stem)

            md5_candidates.extend(extract_md5_candidates(candidate))

        for md5_hex in md5_candidates:
            path = self.files_by_md5.get(md5_hex.casefold())
            if path is not None:
                return path, "md5_index"
            for suffix in (".webp", ".gif", ".png", ".jpg", ".jpeg"):
                candidate = self.image_root / f"{md5_hex.upper()}{suffix}"
                if candidate.is_file():
                    return candidate, "md5_scan"
        return None, "missing"


@dataclass(slots=True, frozen=True)
class LegacyEntryState:
    status: str
    enabled: int
    deleted_at: int


@dataclass(slots=True, frozen=True)
class LegacyImportedLogTarget:
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int


LegacyMigrationProgressCallback = Callable[
    [str, int, int, Mapping[str, object]],
    None,
]


@dataclass(slots=True)
class WordbankMigrationReport:
    total_rows: int = 0
    imported_rows: int = 0
    imported_groups: int = 0
    imported_response_items: int = 0
    imported_entries: int = 0
    skipped_rows: int = 0
    status_counts: Counter[str] = field(default_factory=Counter)
    skipped_reasons: Counter[str] = field(default_factory=Counter)
    image_counts: Counter[str] = field(default_factory=Counter)
    image_resolution_counts: Counter[str] = field(default_factory=Counter)
    imported_entry_ids: dict[int, list[int]] = field(default_factory=dict)
    imported_group_ids: dict[int, int] = field(default_factory=dict)
    response_count_by_group_id: dict[int, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    failure_details: list[dict[str, object]] = field(default_factory=list)
    total_log_rows: int = 0
    imported_log_rows: int = 0
    skipped_log_rows: int = 0
    log_failures: list[str] = field(default_factory=list)
    log_failure_details: list[dict[str, object]] = field(default_factory=list)
    total_trigger_log_rows: int = 0
    imported_trigger_log_rows: int = 0
    skipped_trigger_log_rows: int = 0
    trigger_log_failures: list[str] = field(default_factory=list)
    trigger_log_failure_details: list[dict[str, object]] = field(default_factory=list)
    total_approval_ref_rows: int = 0
    imported_approval_ref_rows: int = 0
    skipped_approval_ref_rows: int = 0
    approval_ref_failures: list[str] = field(default_factory=list)
    approval_ref_failure_details: list[dict[str, object]] = field(default_factory=list)

    def add_failure(
        self,
        response_id: int,
        reason: str,
        *,
        row: Mapping[str, object] | None = None,
    ) -> None:
        from .wordbank_rules import (
            safe_jsonish,
            safe_report_int,
            safe_report_timestamp,
            summarize_legacy_message_payload,
        )

        self.skipped_rows += 1
        self.skipped_reasons[reason] += 1
        self.failures.append(f"{response_id}: {reason}")
        if row is not None:
            self.failure_details.append(
                {
                    "response_id": response_id,
                    "trigger_id": safe_report_int(row.get("trigger_id")),
                    "reason": reason,
                    "approval_status": str(row.get("approval_status") or ""),
                    "priority": safe_report_int(row.get("priority")),
                    "weight": safe_report_int(row.get("weight")),
                    "created_by": str(row.get("created_by") or ""),
                    "created_at": safe_report_timestamp(row.get("created_at")),
                    "trigger": summarize_legacy_message_payload(
                        row.get("trigger_text"),
                        extra_info=row.get("extra_info"),
                    ),
                    "response": summarize_legacy_message_payload(
                        row.get("response_text")
                    ),
                    "response_rule_conditions": safe_jsonish(
                        row.get("response_rule_conditions")
                    ),
                    "trigger_config": safe_jsonish(row.get("trigger_config")),
                    "extra_info": safe_jsonish(row.get("extra_info")),
                }
            )

    def add_log_failure(
        self,
        log_id: int | None,
        reason: str,
        *,
        row: Mapping[str, object] | None = None,
    ) -> None:
        from .wordbank_rules import safe_report_int, safe_report_timestamp

        self.skipped_log_rows += 1
        log_label = str(log_id) if log_id is not None else "unknown"
        self.log_failures.append(f"{log_label}: {reason}")
        if row is not None:
            self.log_failure_details.append(
                {
                    "log_id": log_id,
                    "response_id": safe_report_int(row.get("response_id")),
                    "trigger_id": safe_report_int(row.get("trigger_id")),
                    "reason": reason,
                    "message_id": str(row.get("message_id") or ""),
                    "user_id": str(row.get("user_id") or ""),
                    "call_time": safe_report_timestamp(row.get("call_time")),
                }
            )

    def add_approval_ref_failure(
        self,
        message_id: str,
        reason: str,
        *,
        row: Mapping[str, object] | None = None,
    ) -> None:
        from .wordbank_rules import safe_report_int, safe_report_timestamp

        self.skipped_approval_ref_rows += 1
        self.approval_ref_failures.append(f"{message_id or 'unknown'}: {reason}")
        if row is not None:
            self.approval_ref_failure_details.append(
                {
                    "message_id": message_id,
                    "approval_id": safe_report_int(row.get("approval_id")),
                    "response_id": safe_report_int(row.get("response_id")),
                    "reason": reason,
                    "source_message_id": str(row.get("source_message_id") or ""),
                    "group_id": str(row.get("group_id") or ""),
                    "user_id": str(row.get("user_id") or ""),
                    "created_at": safe_report_timestamp(
                        row.get("created_at")
                        or row.get("approval_created_at")
                        or row.get("add_time")
                    ),
                }
            )

    def add_trigger_log_failure(
        self,
        log_id: int | None,
        reason: str,
        *,
        row: Mapping[str, object] | None = None,
    ) -> None:
        from .wordbank_rules import safe_report_int, safe_report_timestamp

        self.skipped_trigger_log_rows += 1
        log_label = str(log_id) if log_id is not None else "unknown"
        self.trigger_log_failures.append(f"{log_label}: {reason}")
        if row is not None:
            self.trigger_log_failure_details.append(
                {
                    "log_id": log_id,
                    "trigger_id": safe_report_int(row.get("trigger_id")),
                    "reason": reason,
                    "message_id": str(row.get("message_id") or ""),
                    "user_id": str(row.get("user_id") or ""),
                    "call_time": safe_report_timestamp(row.get("call_time")),
                }
            )

    @property
    def response_distribution(self) -> Counter[int]:
        distribution: Counter[int] = Counter()
        for count in self.response_count_by_group_id.values():
            distribution[count] += 1
        return distribution

    def to_failure_categories_dict(self) -> dict[str, object]:
        from .wordbank_rules import categorize_failure_reason

        categories: dict[str, dict[str, object]] = {}
        for detail in self.failure_details:
            reason = str(detail.get("reason") or "")
            category = categorize_failure_reason(reason)
            bucket = categories.setdefault(
                category,
                {"count": 0, "reasons": {}, "items": []},
            )
            bucket["count"] = cast(int, bucket["count"]) + 1
            reasons = bucket["reasons"]
            if isinstance(reasons, dict):
                reasons[reason] = int(reasons.get(reason, 0)) + 1
            items = bucket["items"]
            if isinstance(items, list):
                items.append(detail)
        return {"total_failed": self.skipped_rows, "categories": categories}

    def to_dict(self) -> dict[str, object]:
        return {
            "total_rows": self.total_rows,
            "imported_rows": self.imported_rows,
            "imported_groups": self.imported_groups,
            "imported_response_items": self.imported_response_items,
            "imported_entries": self.imported_entries,
            "skipped_rows": self.skipped_rows,
            "status_counts": dict(self.status_counts),
            "skipped_reasons": dict(self.skipped_reasons),
            "image_counts": dict(self.image_counts),
            "image_resolution_counts": dict(self.image_resolution_counts),
            "imported_entry_ids": self.imported_entry_ids,
            "imported_group_ids": self.imported_group_ids,
            "response_distribution": dict(self.response_distribution),
            "failures": list(self.failures),
            "failure_details": list(self.failure_details),
            "failure_categories": self.to_failure_categories_dict(),
            "total_log_rows": self.total_log_rows,
            "imported_log_rows": self.imported_log_rows,
            "skipped_log_rows": self.skipped_log_rows,
            "log_failures": list(self.log_failures),
            "log_failure_details": list(self.log_failure_details),
            "total_trigger_log_rows": self.total_trigger_log_rows,
            "imported_trigger_log_rows": self.imported_trigger_log_rows,
            "skipped_trigger_log_rows": self.skipped_trigger_log_rows,
            "trigger_log_failures": list(self.trigger_log_failures),
            "trigger_log_failure_details": list(self.trigger_log_failure_details),
            "total_approval_ref_rows": self.total_approval_ref_rows,
            "imported_approval_ref_rows": self.imported_approval_ref_rows,
            "skipped_approval_ref_rows": self.skipped_approval_ref_rows,
            "approval_ref_failures": list(self.approval_ref_failures),
            "approval_ref_failure_details": list(self.approval_ref_failure_details),
        }


@dataclass(slots=True, frozen=True)
class LegacyImportTarget:
    scope: str
    group_id: str
    role: str = "any"
    call_count_min: int = 0
    call_count_max: int = 0
    call_count_window_seconds: int = 0

    @property
    def rule(self) -> dict[str, Any]:
        rule: dict[str, Any] = {}
        if self.role != "any":
            rule["roles"] = self.role
        if self.call_count_window_seconds > 0:
            rule["call_count"] = {
                "window_seconds": self.call_count_window_seconds,
                "min": self.call_count_min,
                "max": self.call_count_max,
            }
        return rule


__all__ = [
    "LegacyEntryState",
    "LegacyImageCatalog",
    "LegacyImportTarget",
    "LegacyImportedLogTarget",
    "LegacyMigrationProgressCallback",
    "LegacyPgConfig",
    "WordbankMigrationReport",
]
