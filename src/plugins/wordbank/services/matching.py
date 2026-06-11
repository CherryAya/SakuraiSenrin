"""Runtime wordbank matching index."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
import random
from typing import Any

from src.plugins.wordbank.database.types import (
    WordbankEntryRecord,
    WordbankResponseRecord,
    WordbankTriggerRecord,
)
from src.plugins.wordbank.message_model import (
    MessageFingerprint,
    MessageShape,
    normalize_text,
)
from src.plugins.wordbank.services.rules import RuleContext, rule_allows


@dataclass(slots=True, frozen=True)
class RuntimeResponse:
    id: int
    text: str
    message_shape: MessageShape
    exact_md5: str
    weight: int


@dataclass(slots=True, frozen=True)
class RuntimeTrigger:
    id: int
    entry_id: int
    trigger_text: str
    trigger_mode: str
    message_shape: MessageShape
    exact_md5: str
    structure_key: str


@dataclass(slots=True, frozen=True)
class RuntimeEntry:
    id: int
    scope: str
    priority: int
    probability: float
    weight: int
    rule: dict[str, Any]
    group_id: str
    created_by: str
    responses: tuple[RuntimeResponse, ...]


@dataclass(slots=True, frozen=True)
class MatchCandidate:
    entry: RuntimeEntry
    trigger: RuntimeTrigger
    matched_text: str = ""


@dataclass(slots=True, frozen=True)
class SelectedMatch:
    candidate: MatchCandidate
    response: RuntimeResponse


@dataclass(slots=True)
class RuntimeIndex:
    entries: dict[int, RuntimeEntry] = field(default_factory=dict)
    exact_match: dict[str, list[RuntimeTrigger]] = field(default_factory=dict)

    @classmethod
    def build(cls, records: Sequence[WordbankEntryRecord]) -> RuntimeIndex:
        entries: dict[int, RuntimeEntry] = {}
        exact_match: dict[str, list[RuntimeTrigger]] = defaultdict(list)
        for record in records:
            responses = tuple(_to_runtime_response(item) for item in record.responses)
            if not responses:
                continue
            entries[record.id] = RuntimeEntry(
                id=record.id,
                scope=record.scope,
                priority=record.priority,
                probability=record.probability,
                weight=record.weight,
                rule=dict(record.rule or {}),
                group_id=record.group_id,
                created_by=record.created_by,
                responses=responses,
            )
            for trigger_record in record.triggers:
                trigger = _to_runtime_trigger(trigger_record)
                exact_match[trigger.exact_md5].append(trigger)
        return cls(entries=entries, exact_match=dict(exact_match))

    def find_message(self, fingerprint: MessageFingerprint) -> list[MatchCandidate]:
        candidates: list[MatchCandidate] = []
        for trigger in self.exact_match.get(fingerprint.exact_md5, []):
            entry = self.entries.get(trigger.entry_id)
            if entry is None:
                continue
            candidates.append(MatchCandidate(entry, trigger, trigger.trigger_text))
        return candidates

    def select(
        self,
        candidates: Sequence[MatchCandidate],
        *,
        context: RuleContext,
        call_counts: dict[int, int] | None = None,
        rng: random.Random | None = None,
    ) -> SelectedMatch | None:
        rng = rng if rng is not None else random.Random()
        call_counts = call_counts or {}
        allowed: list[MatchCandidate] = []
        for candidate in candidates:
            entry = candidate.entry
            if not rule_allows(
                scope=entry.scope,
                entry_group_id=entry.group_id,
                entry_created_by=entry.created_by,
                rule=entry.rule,
                context=context,
                current_call_count=call_counts.get(entry.id, 0),
            ):
                continue
            if entry.probability < 1.0 and rng.random() > entry.probability:
                continue
            allowed.append(candidate)
        if not allowed:
            return None

        max_priority = max(candidate.entry.priority for candidate in allowed)
        priority_group = [
            candidate
            for candidate in allowed
            if candidate.entry.priority == max_priority
        ]
        trigger_weights = [
            max(candidate.entry.weight, 1) for candidate in priority_group
        ]
        selected_candidate = rng.choices(
            priority_group,
            weights=trigger_weights,
            k=1,
        )[0]
        responses = list(selected_candidate.entry.responses)
        response_weights = [max(response.weight, 1) for response in responses]
        selected_response = rng.choices(responses, weights=response_weights, k=1)[0]
        return SelectedMatch(candidate=selected_candidate, response=selected_response)


def _to_runtime_response(record: WordbankResponseRecord) -> RuntimeResponse:
    return RuntimeResponse(
        id=record.id,
        text=record.text,
        message_shape=record.message_shape,
        exact_md5=record.exact_md5,
        weight=record.weight,
    )


def _to_runtime_trigger(record: WordbankTriggerRecord) -> RuntimeTrigger:
    return RuntimeTrigger(
        id=record.id,
        entry_id=record.entry_id,
        trigger_text=record.trigger_text,
        trigger_mode=record.trigger_mode,
        message_shape=record.message_shape,
        exact_md5=record.exact_md5,
        structure_key=record.structure_key,
    )


__all__ = [
    "MatchCandidate",
    "MessageFingerprint",
    "MessageShape",
    "RuntimeIndex",
    "SelectedMatch",
    "normalize_text",
]
