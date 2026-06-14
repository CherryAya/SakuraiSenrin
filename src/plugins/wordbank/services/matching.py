"""Runtime wordbank matching index."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
import random
from typing import Any

from src.plugins.wordbank.database.types import (
    WordbankResponseItemRecord,
    WordbankTriggerGroupRecord,
    WordbankTriggerVariantRecord,
)
from src.plugins.wordbank.message_model import (
    MessageFingerprint,
    MessageShape,
    normalize_text,
)
from src.plugins.wordbank.services.rules import RuleContext, rule_allows


@dataclass(slots=True, frozen=True)
class RuntimeResponseItem:
    id: int
    trigger_group_id: int
    text: str
    message_shape: MessageShape
    exact_md5: str
    status: str
    enabled: int
    scope: str
    priority: int
    probability: float
    weight: int
    rule: dict[str, Any]
    group_id: str
    created_by: str


@dataclass(slots=True, frozen=True)
class RuntimeTriggerVariant:
    id: int
    trigger_group_id: int
    trigger_text: str
    message_shape: MessageShape
    exact_md5: str
    structure_key: str


@dataclass(slots=True, frozen=True)
class RuntimeTriggerGroup:
    id: int
    status: str
    enabled: int
    group_id: str
    created_by: str
    responses: tuple[RuntimeResponseItem, ...]


@dataclass(slots=True, frozen=True)
class MatchCandidate:
    group: RuntimeTriggerGroup
    trigger: RuntimeTriggerVariant
    matched_text: str = ""


@dataclass(slots=True, frozen=True)
class SelectedMatch:
    candidate: MatchCandidate
    response: RuntimeResponseItem


@dataclass(slots=True)
class RuntimeIndex:
    groups: dict[int, RuntimeTriggerGroup] = field(default_factory=dict)
    exact_match: dict[str, list[RuntimeTriggerVariant]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        records: Sequence[WordbankTriggerGroupRecord],
    ) -> RuntimeIndex:
        groups: dict[int, RuntimeTriggerGroup] = {}
        exact_match: dict[str, list[RuntimeTriggerVariant]] = defaultdict(list)
        for record in records:
            responses = tuple(
                _to_runtime_response(item, parent=record) for item in record.responses
            )
            if not responses:
                continue
            groups[record.id] = RuntimeTriggerGroup(
                id=record.id,
                status=record.status,
                enabled=record.enabled,
                group_id=record.group_id,
                created_by=record.created_by,
                responses=responses,
            )
            for trigger_record in record.trigger_variants:
                trigger = _to_runtime_trigger(trigger_record)
                exact_match[trigger.exact_md5].append(trigger)
        return cls(groups=groups, exact_match=dict(exact_match))

    def find_message(self, fingerprint: MessageFingerprint) -> list[MatchCandidate]:
        candidates: list[MatchCandidate] = []
        for trigger in self.exact_match.get(fingerprint.exact_md5, []):
            group = self.groups.get(trigger.trigger_group_id)
            if group is None:
                continue
            candidates.append(
                MatchCandidate(
                    group=group, trigger=trigger, matched_text=trigger.trigger_text
                )
            )
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
        allowed: list[tuple[MatchCandidate, RuntimeResponseItem]] = []
        for candidate in candidates:
            for response in candidate.group.responses:
                if not rule_allows(
                    scope=response.scope,
                    entry_group_id=response.group_id,
                    entry_created_by=response.created_by,
                    rule=response.rule,
                    context=context,
                    current_call_count=call_counts.get(response.trigger_group_id, 0),
                ):
                    continue
                if response.probability < 1.0 and rng.random() > response.probability:
                    continue
                allowed.append((candidate, response))
        if not allowed:
            return None
        max_priority = max(response.priority for _, response in allowed)
        priority_group = [pair for pair in allowed if pair[1].priority == max_priority]
        weights = [max(pair[1].weight, 1) for pair in priority_group]
        selected_candidate, selected_response = rng.choices(
            priority_group, weights=weights, k=1
        )[0]
        return SelectedMatch(candidate=selected_candidate, response=selected_response)


def _to_runtime_response(
    record: WordbankResponseItemRecord,
    *,
    parent: WordbankTriggerGroupRecord,
) -> RuntimeResponseItem:
    return RuntimeResponseItem(
        id=record.id,
        trigger_group_id=record.trigger_group_id,
        text=record.text,
        message_shape=record.message_shape,
        exact_md5=record.exact_md5,
        status=record.status,
        enabled=record.enabled,
        scope=record.scope,
        priority=record.priority,
        probability=record.probability,
        weight=record.weight,
        rule=dict(record.rule),
        group_id=record.group_id or parent.group_id,
        created_by=record.created_by or parent.created_by,
    )


def _to_runtime_trigger(
    record: WordbankTriggerVariantRecord,
) -> RuntimeTriggerVariant:
    return RuntimeTriggerVariant(
        id=record.id,
        trigger_group_id=record.trigger_group_id,
        trigger_text=record.trigger_text,
        message_shape=record.message_shape,
        exact_md5=record.exact_md5,
        structure_key=record.structure_key,
    )


RuntimeEntry = RuntimeTriggerGroup
RuntimeResponse = RuntimeResponseItem
RuntimeTrigger = RuntimeTriggerVariant

__all__ = [
    "MatchCandidate",
    "MessageFingerprint",
    "MessageShape",
    "RuntimeEntry",
    "RuntimeIndex",
    "RuntimeResponse",
    "RuntimeTrigger",
    "SelectedMatch",
    "normalize_text",
]
