"""Shared helpers and bundle types for the wordbank repository."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
import unicodedata

from .tables import WordbankResponseItem, WordbankTriggerGroup, WordbankTriggerVariant

_SPACE_RE = re.compile(r"\s+")
_MAX_GRAM_SIZE = 3
_SEARCH_RESULT_CANDIDATE_MULTIPLIER = 6
_SEARCH_RESULT_MIN_CANDIDATES = 64
_SEARCH_PREVIEW_LIMIT = 3
_MESSAGE_REF_SHARD_FMT = "%Y_%m"


@dataclass(slots=True)
class GroupBundle:
    group: WordbankTriggerGroup
    variants: list[WordbankTriggerVariant]
    responses: list[WordbankResponseItem]


def normalize_search_text(text_value: str) -> str:
    normalized = unicodedata.normalize("NFKC", text_value).strip()
    normalized = _SPACE_RE.sub(" ", normalized)
    return normalized.casefold()


def condensed_search_text(text_value: str) -> str:
    return normalize_search_text(text_value).replace(" ", "")


def build_fts_query(text_value: str) -> str:
    condensed = condensed_search_text(text_value)
    if not condensed:
        return ""
    gram_size = min(_MAX_GRAM_SIZE, len(condensed))
    tokens = [
        condensed[index : index + gram_size]
        for index in range(0, len(condensed) - gram_size + 1)
    ]
    return " AND ".join(f'"{token}"' for token in dict.fromkeys(tokens))


def parse_image_keys(image_keys: str) -> tuple[int, ...]:
    values = [part for part in image_keys.strip("|").split("|") if part]
    parsed: list[int] = []
    for value in values:
        if value.isdigit():
            parsed.append(int(value))
    return tuple(parsed)


def merge_image_keys(image_keys_values: Sequence[str]) -> str:
    merged: list[int] = []
    seen: set[int] = set()
    for image_keys in image_keys_values:
        for image_id in parse_image_keys(image_keys):
            if image_id in seen:
                continue
            seen.add(image_id)
            merged.append(image_id)
    if not merged:
        return ""
    return "|" + "|".join(str(image_id) for image_id in merged) + "|"


def first_image_id(image_keys: str) -> int | None:
    parsed = parse_image_keys(image_keys)
    return parsed[0] if parsed else None


def message_ref_shard_key_from_timestamp(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime(_MESSAGE_REF_SHARD_FMT)


def message_ref_time_ctx(shard_key: str) -> datetime:
    return datetime.strptime(shard_key, _MESSAGE_REF_SHARD_FMT).replace(tzinfo=UTC)


def decode_group_ids(group_ids_json: str) -> tuple[int, ...]:
    raw_group_ids = json.loads(group_ids_json or "[]")
    return tuple(
        int(item)
        for item in raw_group_ids
        if isinstance(item, int) or (isinstance(item, str) and item.isdigit())
    )


def group_status_from_responses(
    responses: Sequence[WordbankResponseItem],
) -> tuple[str, int, int]:
    undeleted = [response for response in responses if response.deleted_at == 0]
    active = [
        response
        for response in undeleted
        if response.status == "approved" and response.enabled == 1
    ]
    if active:
        return "approved", 1, 0
    pending = [response for response in undeleted if response.status == "pending"]
    if pending:
        return "pending", 0, 0
    rejected = [response for response in undeleted if response.status == "rejected"]
    if rejected:
        return "rejected", 0, 0
    deleted_at = max((response.deleted_at for response in responses), default=0)
    return "empty", 0, deleted_at


def representative_response(
    responses: Sequence[WordbankResponseItem],
) -> WordbankResponseItem | None:
    if not responses:
        return None

    def sort_key(response: WordbankResponseItem) -> tuple[int, int, int]:
        active_rank = (
            0
            if response.deleted_at == 0
            and response.status == "approved"
            and response.enabled == 1
            else 1
        )
        deleted_rank = 0 if response.deleted_at == 0 else 1
        return (active_rank, deleted_rank, response.id)

    return min(responses, key=sort_key)


def search_preview_responses(
    responses: Sequence[WordbankResponseItem],
) -> tuple[WordbankResponseItem, ...]:
    visible = [response for response in responses if response.deleted_at == 0]
    visible.sort(
        key=lambda response: (
            0 if response.status == "approved" and response.enabled == 1 else 1,
            response.id,
        )
    )
    return tuple(visible[:_SEARCH_PREVIEW_LIMIT])


__all__ = [
    "GroupBundle",
    "_SEARCH_PREVIEW_LIMIT",
    "_SEARCH_RESULT_CANDIDATE_MULTIPLIER",
    "_SEARCH_RESULT_MIN_CANDIDATES",
    "build_fts_query",
    "condensed_search_text",
    "decode_group_ids",
    "first_image_id",
    "group_status_from_responses",
    "merge_image_keys",
    "message_ref_shard_key_from_timestamp",
    "message_ref_time_ctx",
    "normalize_search_text",
    "parse_image_keys",
    "representative_response",
    "search_preview_responses",
]
