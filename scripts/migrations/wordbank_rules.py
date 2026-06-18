"""Rule normalization helpers for wordbank migration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any

from PIL import Image, UnidentifiedImageError

from src.lib.utils.common import get_current_time

from .wordbank_types import LegacyEntryState, LegacyImportTarget

_HEX_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_HEX_MD5_ANYWHERE_RE = re.compile(r"([0-9a-fA-F]{32})")
_LEGACY_EVENT_NAMES = {
    "AT_MENTIONED": "event:at",
    "POKE_MENTIONED": "event:poke",
    "GROUP_JOIN": "event:join",
    "GROUP_LEAVE": "event:leave",
}
_LEGACY_RULE_KEYS = {"group_id", "user_id", "role", "call_count", "$and", "$or"}
_LEGACY_ROLES = {"owner", "admin", "member"}
_LEGACY_DEFAULT_CALL_WINDOW_SECONDS = 60 * 60 * 24 * 90
_LEGACY_MAX_CALL_WINDOW_SECONDS = _LEGACY_DEFAULT_CALL_WINDOW_SECONDS
_LEGACY_INLINE_TEXT_SPACE_RE = re.compile(r"[^\S\r\n]+")


class MigrationError(Exception):
    """Raised when a legacy wordbank record cannot be migrated."""


def load_legacy_json(value: object) -> Any:
    if value in (None, ""):
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        return json.loads(value)
    raise MigrationError(f"unsupported json payload type: {type(value).__name__}")


def normalize_legacy_probability(trigger_config: object) -> float:
    payload = load_legacy_json(trigger_config)
    if not isinstance(payload, Mapping):
        return 1.0
    probability = payload.get("probability", 1.0)
    try:
        normalized = float(probability)
    except (TypeError, ValueError) as exc:
        raise MigrationError("invalid trigger probability") from exc
    return min(max(normalized, 0.0), 1.0)


def normalize_legacy_timestamp(value: object, *, fallback: int) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return int(value.replace(tzinfo=UTC).timestamp())
        return int(value.timestamp())
    if isinstance(value, date):
        return int(
            datetime(
                value.year,
                value.month,
                value.day,
                tzinfo=UTC,
            ).timestamp()
        )
    if isinstance(value, (int, float)):
        return int(value)
    return fallback


def message_ref_shard_key(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y_%m")


def normalize_legacy_rules(
    *,
    priority: int,
    response_rule_conditions: Mapping[str, Any],
    trigger_config: object = None,
) -> list[LegacyImportTarget]:
    branches = _expand_legacy_rule_tree(response_rule_conditions)
    targets = [
        _normalize_legacy_branch(
            priority=priority,
            branch=branch,
            trigger_config=trigger_config,
        )
        for branch in branches
    ]
    return _prune_subsumed_targets(targets)


def normalize_legacy_scope(
    *,
    priority: int,
    response_rule_conditions: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    targets = normalize_legacy_rules(
        priority=priority,
        response_rule_conditions=response_rule_conditions,
    )
    if len(targets) != 1:
        raise MigrationError("legacy rule expands to multiple variants")
    target = targets[0]
    return target.scope, target.group_id, target.rule


def normalize_legacy_state(
    *,
    approval_status: str,
    response_available: bool,
    migration_time: int,
) -> LegacyEntryState:
    normalized = approval_status.strip().upper()
    if normalized == "APPROVED":
        if response_available:
            return LegacyEntryState(status="approved", enabled=1, deleted_at=0)
        return LegacyEntryState(
            status="approved",
            enabled=0,
            deleted_at=migration_time,
        )
    if normalized == "PENDING":
        return LegacyEntryState(status="pending", enabled=0, deleted_at=0)
    if normalized in {"REJECTED", "WITHDRAWN"}:
        return LegacyEntryState(status="rejected", enabled=0, deleted_at=0)
    raise MigrationError(f"unsupported approval status: {approval_status}")


def _coerce_rule_mapping(value: object) -> Mapping[str, Any]:
    payload = load_legacy_json(value)
    if isinstance(payload, Mapping):
        return payload
    raise MigrationError("legacy rule conditions must be a mapping")


def _coerce_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return int(stripped)
            except ValueError as exc:
                raise MigrationError(f"invalid integer for {field}: {value}") from exc
    raise MigrationError(f"invalid integer for {field}: {value!r}")


def _optional_coerce_int(value: object, *, field: str) -> int | None:
    if value in (None, ""):
        return None
    return _coerce_int(value, field=field)


def _extract_legacy_eq(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    raw_value = value.get("$eq")
    if raw_value in (None, ""):
        return ""
    return str(raw_value)


def _normalize_legacy_branch(
    *,
    priority: int,
    branch: Mapping[str, object],
    trigger_config: object,
) -> LegacyImportTarget:
    group_id = str(branch.get("group_id") or "")
    user_id = str(branch.get("user_id") or "")
    role = str(branch.get("role") or "any")
    has_call_count = isinstance(branch.get("call_count"), tuple)
    has_non_scope_constraints = role != "any" or has_call_count

    if group_id and user_id:
        scope = "self_in_current_group"
    elif user_id:
        scope = "self"
    elif group_id:
        scope = "current_group"
    elif priority == 3:
        scope = "all_groups"
    elif priority == 2:
        scope = "all_groups" if not group_id and not user_id else "current_group"
    elif priority == 1:
        scope = "all_groups" if has_non_scope_constraints else "self"
    else:
        raise MigrationError(f"unsupported priority: {priority}")
    if role not in {"any", *_LEGACY_ROLES}:
        raise MigrationError(f"unsupported role value: {role}")

    call_count_min = 0
    call_count_max = 0
    call_count_window_seconds = 0
    raw_call_count = branch.get("call_count")
    if isinstance(raw_call_count, tuple):
        call_count_min = int(raw_call_count[0])
        call_count_max = int(raw_call_count[1])
        call_count_window_seconds = _resolve_legacy_call_window_seconds(trigger_config)

    return LegacyImportTarget(
        scope=scope,
        group_id=group_id,
        role=role,
        call_count_min=call_count_min,
        call_count_max=call_count_max,
        call_count_window_seconds=call_count_window_seconds,
    )


def _expand_legacy_rule_tree(rule: Mapping[str, Any]) -> list[dict[str, object]]:
    unknown_keys = set(rule) - _LEGACY_RULE_KEYS
    if unknown_keys:
        fields = ",".join(sorted(unknown_keys))
        raise MigrationError(f"unsupported rule keys: {fields}")

    branches: list[dict[str, object]] = [{}]
    for key, value in rule.items():
        field_branches = _expand_legacy_rule_item(key, value)
        branches = _combine_legacy_branches(branches, field_branches)
        if not branches:
            raise MigrationError("legacy rule resolves to no valid branch")
    return branches


def _expand_legacy_rule_item(key: str, value: object) -> list[dict[str, object]]:
    if key == "$and":
        items = _coerce_rule_list(value, field="$and")
        branches: list[dict[str, object]] = [{}]
        for item in items:
            expanded = _expand_legacy_rule_tree(item)
            branches = _combine_legacy_branches(branches, expanded)
        return branches or [{}]
    if key == "$or":
        items = _coerce_rule_list(value, field="$or")
        branches: list[dict[str, object]] = []
        for item in items:
            branches.extend(_expand_legacy_rule_tree(item))
        return _dedupe_legacy_branches(branches or [{}])
    if key == "group_id":
        group_id = _extract_required_legacy_eq(value, field="group_id")
        return [{"group_id": group_id}] if group_id else [{}]
    if key == "user_id":
        user_id = _extract_required_legacy_eq(value, field="user_id")
        return [{"user_id": user_id}] if user_id else [{}]
    if key == "role":
        role = _extract_legacy_role(value)
        return [{"role": role}] if role != "any" else [{}]
    if key == "call_count":
        return [{"call_count": _extract_legacy_call_count_bounds(value)}]
    raise MigrationError(f"unsupported rule keys: {key}")


def _coerce_rule_list(value: object, *, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise MigrationError(f"{field} must be a rule list")
    items: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise MigrationError(f"{field} contains a non-mapping rule")
        items.append(item)
    return items


def _combine_legacy_branches(
    left: Sequence[Mapping[str, object]],
    right: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    combined: list[dict[str, object]] = []
    for left_branch in left:
        for right_branch in right:
            merged = _merge_legacy_branches(left_branch, right_branch)
            if merged is not None:
                combined.append(merged)
    return _dedupe_legacy_branches(combined)


def _merge_legacy_branches(
    left: Mapping[str, object],
    right: Mapping[str, object],
) -> dict[str, object] | None:
    merged = dict(left)
    for key, value in right.items():
        if key not in merged:
            merged[key] = value
            continue
        resolved = _merge_legacy_field_value(key, merged[key], value)
        if resolved is None:
            return None
        merged[key] = resolved
    return merged


def _merge_legacy_field_value(
    key: str,
    left: object,
    right: object,
) -> object | None:
    if key in {"group_id", "user_id", "role"}:
        return left if left == right else None
    if key == "call_count":
        if not isinstance(left, tuple) or not isinstance(right, tuple):
            raise MigrationError("invalid call_count merge state")
        left_min, left_max = int(left[0]), int(left[1])
        right_min, right_max = int(right[0]), int(right[1])
        merged_min = max(left_min, right_min)
        merged_max = _intersect_upper_bound(left_max, right_max)
        if merged_max != 0 and merged_min > merged_max:
            return None
        return (merged_min, merged_max)
    raise MigrationError(f"unsupported rule keys: {key}")


def _dedupe_legacy_branches(
    branches: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[tuple[tuple[str, object], ...]] = set()
    for branch in branches:
        key = tuple(sorted(branch.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(branch))
    return deduped


def _extract_required_legacy_eq(value: object, *, field: str) -> str:
    extracted = _extract_legacy_eq(value)
    if not extracted:
        raise MigrationError(f"{field} must use $eq")
    return extracted


def _extract_legacy_role(value: object) -> str:
    role = _extract_required_legacy_eq(value, field="role").strip().lower()
    if role not in _LEGACY_ROLES:
        raise MigrationError(f"unsupported role value: {role}")
    return role


def _extract_legacy_call_count_bounds(value: object) -> tuple[int, int]:
    if not isinstance(value, Mapping):
        raise MigrationError("call_count must use comparison operators")
    minimum = 0
    maximum = 0
    upper_bounded = False
    for operator, raw in value.items():
        if operator == "$gt":
            minimum = max(minimum, _coerce_int(raw, field="call_count") + 1)
        elif operator == "$gte":
            minimum = max(minimum, _coerce_int(raw, field="call_count"))
        elif operator == "$lt":
            upper_bounded = True
            maximum = _intersect_upper_bound(
                maximum if upper_bounded else 0,
                _coerce_int(raw, field="call_count") - 1,
            )
        elif operator == "$lte":
            upper_bounded = True
            maximum = _intersect_upper_bound(
                maximum if upper_bounded else 0,
                _coerce_int(raw, field="call_count"),
            )
        elif operator == "$range":
            if (
                not isinstance(raw, Sequence)
                or isinstance(raw, (str, bytes, bytearray))
                or len(raw) != 2
            ):
                raise MigrationError("call_count $range must contain two integers")
            minimum = max(minimum, _coerce_int(raw[0], field="call_count"))
            upper_bounded = True
            maximum = _intersect_upper_bound(
                maximum if upper_bounded else 0,
                _coerce_int(raw[1], field="call_count"),
            )
        else:
            raise MigrationError(f"unsupported call_count operator: {operator}")
    if upper_bounded and maximum != 0 and minimum > maximum:
        raise MigrationError("call_count rule is contradictory")
    return (minimum, maximum if upper_bounded else 0)


def _intersect_upper_bound(left: int, right: int) -> int:
    if left == 0:
        return right
    if right == 0:
        return left
    return min(left, right)


def _resolve_legacy_call_window_seconds(trigger_config: object) -> int:
    payload = load_legacy_json(trigger_config)
    if not isinstance(payload, Mapping):
        return _LEGACY_DEFAULT_CALL_WINDOW_SECONDS
    lifecycle = payload.get("lifecycle")
    if lifecycle in (None, ""):
        return _LEGACY_DEFAULT_CALL_WINDOW_SECONDS
    try:
        seconds = int(float(lifecycle))
    except (TypeError, ValueError):
        return _LEGACY_DEFAULT_CALL_WINDOW_SECONDS
    if seconds <= 0:
        return _LEGACY_DEFAULT_CALL_WINDOW_SECONDS
    return min(seconds, _LEGACY_MAX_CALL_WINDOW_SECONDS)


def _prune_subsumed_targets(
    targets: Sequence[LegacyImportTarget],
) -> list[LegacyImportTarget]:
    unique_targets: list[LegacyImportTarget] = []
    seen: set[tuple[object, ...]] = set()
    for target in targets:
        key = (
            target.scope,
            target.group_id,
            target.role,
            target.call_count_min,
            target.call_count_max,
            target.call_count_window_seconds,
        )
        if key in seen:
            continue
        seen.add(key)
        unique_targets.append(target)

    pruned: list[LegacyImportTarget] = []
    for candidate in unique_targets:
        if any(
            other != candidate and _legacy_target_subsumes(other, candidate)
            for other in unique_targets
        ):
            continue
        pruned.append(candidate)
    return pruned


def _legacy_target_subsumes(
    left: LegacyImportTarget,
    right: LegacyImportTarget,
) -> bool:
    return (
        _legacy_scope_subsumes(left, right)
        and _legacy_role_subsumes(left.role, right.role)
        and _legacy_call_count_subsumes(left, right)
    )


def _legacy_scope_subsumes(left: LegacyImportTarget, right: LegacyImportTarget) -> bool:
    if left.scope == right.scope and left.group_id == right.group_id:
        return True
    if left.scope == "all_groups" and right.scope == "current_group":
        return True
    if left.scope == "current_group" and right.scope == "self_in_current_group":
        return left.group_id == right.group_id
    if left.scope == "self" and right.scope == "self_in_current_group":
        return True
    return False


def _legacy_role_subsumes(left: str, right: str) -> bool:
    return left == "any" or left == right


def _legacy_call_count_subsumes(
    left: LegacyImportTarget,
    right: LegacyImportTarget,
) -> bool:
    if left.call_count_window_seconds == 0:
        return True
    if right.call_count_window_seconds == 0:
        return False
    if left.call_count_window_seconds != right.call_count_window_seconds:
        return False
    right_max = right.call_count_max or 10**18
    left_max = left.call_count_max or 10**18
    return left.call_count_min <= right.call_count_min and left_max >= right_max


def extract_md5_candidates(value: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    raw_values = [value.strip(), Path(value).name.strip(), Path(value).stem.strip()]
    for raw in raw_values:
        if not raw:
            continue
        for matched in _HEX_MD5_ANYWHERE_RE.findall(raw):
            normalized = matched.upper()
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(normalized)
        compact = re.sub(r"[^0-9a-fA-F]", "", raw)
        if _HEX_MD5_RE.fullmatch(compact):
            normalized = compact.upper()
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(normalized)
    return candidates


def shape_from_legacy_extra_info(extra_info: object) -> object | None:
    from src.plugins.wordbank.message_model import shape_from_event

    if extra_info in (None, ""):
        return None
    payload = load_legacy_json(extra_info)
    if not isinstance(payload, Mapping):
        return None
    action = str(payload.get("action", "") or "").strip().upper()
    event_name = _LEGACY_EVENT_NAMES.get(action)
    if not event_name:
        raise MigrationError(f"unsupported legacy event action: {action}")
    return shape_from_event(event_name)


def safe_jsonish(value: object) -> object:
    try:
        return load_legacy_json(value)
    except Exception:
        return value


def safe_report_int(value: object) -> int | None:
    try:
        return _coerce_int(value, field="report")
    except Exception:
        return None


def safe_report_timestamp(value: object) -> int | None:
    try:
        return normalize_legacy_timestamp(value, fallback=0)
    except Exception:
        return None


def infer_report_response_available(approval_status: object) -> bool:
    normalized = str(approval_status or "").strip().upper()
    return normalized == "APPROVED"


def rebuild_legacy_row_from_failure_detail(
    detail: Mapping[str, object],
) -> dict[str, object]:
    trigger_summary = detail.get("trigger")
    response_summary = detail.get("response")
    trigger_kind = (
        str(trigger_summary.get("kind") or "")
        if isinstance(trigger_summary, Mapping)
        else ""
    )
    response_kind = (
        str(response_summary.get("kind") or "")
        if isinstance(response_summary, Mapping)
        else ""
    )

    trigger_segments = (
        trigger_summary.get("segments", [])
        if isinstance(trigger_summary, Mapping) and trigger_kind == "message"
        else []
    )
    response_segments = (
        response_summary.get("segments", [])
        if isinstance(response_summary, Mapping) and response_kind == "message"
        else []
    )
    extra_info: object = None
    if isinstance(trigger_summary, Mapping) and trigger_kind == "event":
        extra_info = trigger_summary.get("extra_info")

    return {
        "response_id": _coerce_int(detail.get("response_id"), field="response_id"),
        "trigger_id": _coerce_int(detail.get("trigger_id"), field="trigger_id"),
        "response_text": json.dumps(response_segments, ensure_ascii=False),
        "response_rule_conditions": detail.get("response_rule_conditions") or {},
        "weight": _coerce_int(detail.get("weight", 3), field="weight"),
        "priority": _coerce_int(detail.get("priority", 3), field="priority"),
        "created_by": str(detail.get("created_by") or ""),
        "created_at": normalize_legacy_timestamp(
            detail.get("created_at"),
            fallback=get_current_time(),
        ),
        "response_available": infer_report_response_available(
            detail.get("approval_status")
        ),
        "trigger_text": json.dumps(trigger_segments, ensure_ascii=False),
        "trigger_config": detail.get("trigger_config") or {},
        "extra_info": extra_info,
        "approval_status": str(detail.get("approval_status") or "PENDING"),
    }


def rebuild_legacy_rows_from_failure_details(
    details: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    return [rebuild_legacy_row_from_failure_detail(detail) for detail in details]


def extract_failure_details_from_categorized_report(
    payload: Mapping[str, object],
    *,
    categories: Sequence[str] | None = None,
) -> list[dict[str, object]]:
    raw_categories = payload.get("categories")
    if not isinstance(raw_categories, Mapping):
        raise MigrationError("categorized report missing categories")

    selected_categories = list(categories or ())
    if not selected_categories or selected_categories == ["all"]:
        selected_categories = list(raw_categories.keys())

    details: list[dict[str, object]] = []
    for category in selected_categories:
        bucket = raw_categories.get(category)
        if not isinstance(bucket, Mapping):
            continue
        items = bucket.get("items")
        if not isinstance(items, Sequence) or isinstance(
            items, (str, bytes, bytearray)
        ):
            continue
        for item in items:
            if isinstance(item, Mapping):
                details.append(dict(item))
    return details


def summarize_legacy_message_payload(
    payload: object,
    *,
    extra_info: object = None,
) -> dict[str, object]:
    if extra_info not in (None, ""):
        return {"kind": "event", "extra_info": safe_jsonish(extra_info)}

    loaded = safe_jsonish(payload)
    if not isinstance(loaded, list):
        return {"kind": "raw", "value": loaded}

    summary_segments: list[dict[str, object]] = []
    text_preview_parts: list[str] = []
    for segment in loaded[:12]:
        if not isinstance(segment, Mapping):
            summary_segments.append({"type": "unknown", "value": str(segment)})
            continue
        segment_type = str(segment.get("type") or "")
        if segment_type == "text":
            text_value = str(segment.get("text") or "")
            if text_value:
                text_preview_parts.append(text_value)
            summary_segments.append(
                {"type": "text", "text": truncate_text(text_value, limit=80)}
            )
            continue
        if segment_type == "image":
            summary_segments.append(
                {
                    "type": "image",
                    "file": str(segment.get("file") or ""),
                    "url": str(segment.get("url") or ""),
                }
            )
            continue
        if segment_type == "at":
            summary_segments.append({"type": "at", "qq": str(segment.get("qq") or "")})
            continue
        if segment_type == "face":
            summary_segments.append(
                {"type": "face", "id": str(segment.get("id") or "")}
            )
            continue
        summary_segments.append(
            {"type": segment_type or "unknown", "data": dict(segment)}
        )

    return {
        "kind": "message",
        "segment_count": len(loaded),
        "text_preview": truncate_text("".join(text_preview_parts), limit=120),
        "segments": summary_segments,
    }


def truncate_text(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def normalize_legacy_message_text_preserving_newlines(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(
        _LEGACY_INLINE_TEXT_SPACE_RE.sub(" ", line).strip()
        for line in normalized.split("\n")
    )
    normalized = normalized.strip("\n")
    if normalized:
        return normalized
    from src.plugins.wordbank.message_model import normalize_message_text

    return normalize_message_text(text, preserve_blank_text=True)


def validate_legacy_image_bytes(data: bytes, *, source: str) -> None:
    if not data:
        raise MigrationError(f"image file empty: {source}")
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
    except UnidentifiedImageError as exc:
        raise MigrationError(f"image file is not a valid image: {source}") from exc
    except OSError as exc:
        raise MigrationError(f"image file decode failed: {source}") from exc


def categorize_failure_reason(reason: str) -> str:
    if reason.startswith("image file empty:"):
        return "image_file_empty"
    if reason.startswith("image file is not a valid image:"):
        return "image_file_invalid"
    if reason.startswith("image file decode failed:"):
        return "image_file_decode_failed"
    if reason.startswith("image file not found:"):
        return "image_file_missing"
    if reason.startswith("empty trigger shape"):
        return "trigger_shape_empty"
    if reason.startswith("empty response shape"):
        return "response_shape_empty"
    if "unsupported rule" in reason or "priority=" in reason:
        return "rule_invalid"
    if "invalid trigger probability" in reason:
        return "trigger_config_invalid"
    if "approval status" in reason:
        return "approval_status_invalid"
    return "other"


__all__ = [
    "MigrationError",
    "_coerce_int",
    "_optional_coerce_int",
    "_resolve_legacy_call_window_seconds",
    "categorize_failure_reason",
    "extract_failure_details_from_categorized_report",
    "extract_md5_candidates",
    "infer_report_response_available",
    "load_legacy_json",
    "message_ref_shard_key",
    "normalize_legacy_message_text_preserving_newlines",
    "normalize_legacy_probability",
    "normalize_legacy_rules",
    "normalize_legacy_scope",
    "normalize_legacy_state",
    "normalize_legacy_timestamp",
    "rebuild_legacy_row_from_failure_detail",
    "rebuild_legacy_rows_from_failure_details",
    "safe_jsonish",
    "safe_report_int",
    "safe_report_timestamp",
    "shape_from_legacy_extra_info",
    "summarize_legacy_message_payload",
    "truncate_text",
    "validate_legacy_image_bytes",
]
