"""Runtime wordbank matching index."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
import random
from typing import Any

from src.plugins.wordbank.database.types import (
    WordbankEntryRecord,
    WordbankResponseItemRecord,
    WordbankResponseRecord,
    WordbankTriggerGroupRecord,
    WordbankTriggerRecord,
    WordbankTriggerVariantRecord,
)
from src.plugins.wordbank.message_model import (
    MessageFingerprint,
    MessageShape,
    normalize_text,
)
from src.plugins.wordbank.services.rules import RuleContext, rule_allows


@dataclass(slots=True, frozen=True, init=False)
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

    def __init__(
        self,
        *,
        id: int,
        trigger_group_id: int | None = None,
        entry_id: int | None = None,
        text: str,
        message_shape: MessageShape,
        exact_md5: str,
        status: str = "approved",
        enabled: int = 1,
        scope: str = "all_groups",
        priority: int = 1,
        probability: float = 1.0,
        weight: int = 1,
        rule: dict[str, Any] | None = None,
        group_id: str = "",
        created_by: str = "",
    ) -> None:
        object.__setattr__(self, "id", id)
        object.__setattr__(
            self,
            "trigger_group_id",
            trigger_group_id if trigger_group_id is not None else entry_id or 0,
        )
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "message_shape", message_shape)
        object.__setattr__(self, "exact_md5", exact_md5)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "weight", weight)
        object.__setattr__(self, "rule", dict(rule or {}))
        object.__setattr__(self, "group_id", group_id)
        object.__setattr__(self, "created_by", created_by)

    @property
    def response_id(self) -> int:
        return self.id


@dataclass(slots=True, frozen=True, init=False)
class RuntimeTriggerVariant:
    id: int
    trigger_group_id: int
    trigger_text: str
    trigger_mode: str
    message_shape: MessageShape
    exact_md5: str
    structure_key: str

    def __init__(
        self,
        *,
        id: int,
        trigger_group_id: int | None = None,
        entry_id: int | None = None,
        trigger_text: str,
        trigger_mode: str,
        message_shape: MessageShape,
        exact_md5: str,
        structure_key: str,
    ) -> None:
        object.__setattr__(self, "id", id)
        object.__setattr__(
            self,
            "trigger_group_id",
            trigger_group_id if trigger_group_id is not None else entry_id or 0,
        )
        object.__setattr__(self, "trigger_text", trigger_text)
        object.__setattr__(self, "trigger_mode", trigger_mode)
        object.__setattr__(self, "message_shape", message_shape)
        object.__setattr__(self, "exact_md5", exact_md5)
        object.__setattr__(self, "structure_key", structure_key)

    @property
    def entry_id(self) -> int:
        return self.trigger_group_id


@dataclass(slots=True, frozen=True, init=False)
class RuntimeTriggerGroup:
    id: int
    status: str
    enabled: int
    group_id: str
    created_by: str
    trigger_mode: str
    responses: tuple[RuntimeResponseItem, ...]

    def __init__(
        self,
        *,
        id: int,
        status: str = "approved",
        enabled: int = 1,
        group_id: str = "",
        created_by: str = "",
        trigger_mode: str = "strict",
        responses: tuple[RuntimeResponseItem, ...] = (),
        scope: str | None = None,
        priority: int | None = None,
        probability: float | None = None,
        weight: int | None = None,
        rule: dict[str, Any] | None = None,
    ) -> None:
        _ = scope, priority, probability, weight, rule
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "group_id", group_id)
        object.__setattr__(self, "created_by", created_by)
        object.__setattr__(self, "trigger_mode", trigger_mode)
        object.__setattr__(self, "responses", responses)

    @property
    def entry_id(self) -> int:
        return self.id


@dataclass(slots=True, frozen=True, init=False)
class MatchCandidate:
    group: RuntimeTriggerGroup
    trigger: RuntimeTriggerVariant
    matched_text: str = ""

    def __init__(
        self,
        *,
        group: RuntimeTriggerGroup | None = None,
        entry: RuntimeTriggerGroup | None = None,
        trigger: RuntimeTriggerVariant,
        matched_text: str = "",
    ) -> None:
        object.__setattr__(self, "group", group if group is not None else entry)
        object.__setattr__(self, "trigger", trigger)
        object.__setattr__(self, "matched_text", matched_text)

    @property
    def entry(self) -> RuntimeTriggerGroup:
        return self.group


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
        records: Sequence[WordbankTriggerGroupRecord | WordbankEntryRecord],
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
                trigger_mode=record.trigger_mode,
                responses=responses,
            )
            for trigger_record in record.trigger_variants:
                trigger = _to_runtime_trigger(
                    trigger_record, trigger_mode=record.trigger_mode
                )
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
                    current_call_count=call_counts.get(response.id, 0),
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
    record: WordbankResponseItemRecord | WordbankResponseRecord,
    *,
    parent: WordbankTriggerGroupRecord | WordbankEntryRecord | None = None,
) -> RuntimeResponseItem:
    scope = getattr(record, "scope", None)
    priority = getattr(record, "priority", None)
    probability = getattr(record, "probability", None)
    rule = getattr(record, "rule", None)
    created_by = getattr(record, "created_by", "")
    group_id = getattr(record, "group_id", "")
    return RuntimeResponseItem(
        id=record.id,
        trigger_group_id=getattr(
            record, "trigger_group_id", getattr(record, "entry_id")
        ),
        text=record.text,
        message_shape=record.message_shape,
        exact_md5=record.exact_md5,
        status=getattr(record, "status", getattr(parent, "status", "approved")),
        enabled=getattr(record, "enabled", getattr(parent, "enabled", 1)),
        scope=scope if scope is not None else getattr(parent, "scope", "all_groups"),
        priority=priority if priority is not None else getattr(parent, "priority", 1),
        probability=(
            probability
            if probability is not None
            else getattr(parent, "probability", 1.0)
        ),
        weight=record.weight,
        rule=dict(rule if rule is not None else getattr(parent, "rule", {}) or {}),
        group_id=group_id or getattr(parent, "group_id", ""),
        created_by=created_by or getattr(parent, "created_by", ""),
    )


def _to_runtime_trigger(
    record: WordbankTriggerVariantRecord | WordbankTriggerRecord,
    *,
    trigger_mode: str,
) -> RuntimeTriggerVariant:
    return RuntimeTriggerVariant(
        id=record.id,
        trigger_group_id=getattr(
            record, "trigger_group_id", getattr(record, "entry_id")
        ),
        trigger_text=record.trigger_text,
        trigger_mode=trigger_mode,
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
