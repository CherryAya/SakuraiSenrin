"""Runtime wordbank matching index."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
import random
import re
from typing import Any
import unicodedata

from src.logger import logger
from src.plugins.wordbank.database.types import (
    WordbankEntryRecord,
    WordbankResponseRecord,
    WordbankTriggerRecord,
)
from src.plugins.wordbank.services.rules import RuleContext, rule_allows

try:
    import ahocorasick
except ImportError:  # pragma: no cover - depends on optional binary package
    ahocorasick = None


_SPACE_RE = re.compile(r"\s+")


def normalize_text(text: str, *, casefold: bool = True) -> str:
    normalized = unicodedata.normalize("NFKC", text).strip()
    normalized = _SPACE_RE.sub(" ", normalized)
    return normalized.casefold() if casefold else normalized


@dataclass(slots=True, frozen=True)
class RuntimeResponse:
    id: int
    kind: str
    text: str
    canonical_image_id: int | None
    weight: int


@dataclass(slots=True, frozen=True)
class RuntimeTrigger:
    id: int
    entry_id: int
    kind: str
    text: str
    mode: str
    canonical_image_id: int | None


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


class _ContainsIndex:
    def __init__(self, triggers: Iterable[RuntimeTrigger]) -> None:
        self._by_text: dict[str, list[RuntimeTrigger]] = defaultdict(list)
        for trigger in triggers:
            self._by_text[trigger.text].append(trigger)
        automaton_module = ahocorasick
        self._use_aho = automaton_module is not None and bool(self._by_text)
        self._automaton: Any | None = None
        self._fallback: dict[str, list[RuntimeTrigger]] = defaultdict(list)
        if automaton_module is not None and self._by_text:
            automaton = automaton_module.Automaton()
            for text in self._by_text:
                automaton.add_word(text, text)
            automaton.make_automaton()
            self._automaton = automaton
            return

        if self._by_text:
            logger.warning("[Wordbank] pyahocorasick unavailable, using fallback index")
        for text, items in self._by_text.items():
            first = text[:1]
            if first:
                self._fallback[first].extend(items)

    @property
    def backend(self) -> str:
        return "aho" if self._use_aho else "fallback"

    def search(self, text: str) -> list[tuple[RuntimeTrigger, str]]:
        if not text:
            return []
        if self._automaton is not None:
            found: list[tuple[RuntimeTrigger, str]] = []
            for _, key in self._automaton.iter(text):
                for trigger in self._by_text[str(key)]:
                    found.append((trigger, trigger.text))
            return found

        seen: set[tuple[int, str]] = set()
        found = []
        for char in set(text):
            for trigger in self._fallback.get(char, []):
                key = (trigger.id, trigger.text)
                if key in seen:
                    continue
                seen.add(key)
                if trigger.text in text:
                    found.append((trigger, trigger.text))
        return found


@dataclass(slots=True)
class RuntimeIndex:
    entries: dict[int, RuntimeEntry] = field(default_factory=dict)
    fullmatch: dict[str, list[RuntimeTrigger]] = field(default_factory=dict)
    prefix: dict[str, list[RuntimeTrigger]] = field(default_factory=dict)
    image_triggers: dict[int, list[RuntimeTrigger]] = field(default_factory=dict)
    contains: _ContainsIndex = field(default_factory=lambda: _ContainsIndex(()))

    @classmethod
    def build(cls, records: Sequence[WordbankEntryRecord]) -> RuntimeIndex:
        entries: dict[int, RuntimeEntry] = {}
        contains: list[RuntimeTrigger] = []
        fullmatch: dict[str, list[RuntimeTrigger]] = defaultdict(list)
        prefix: dict[str, list[RuntimeTrigger]] = defaultdict(list)
        image_triggers: dict[int, list[RuntimeTrigger]] = defaultdict(list)

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
                if trigger.kind == "image" and trigger.canonical_image_id is not None:
                    image_triggers[trigger.canonical_image_id].append(trigger)
                elif trigger.mode == "fullmatch":
                    fullmatch.setdefault(trigger.text, []).append(trigger)
                elif trigger.mode == "prefix":
                    first = trigger.text[:1]
                    if first:
                        prefix.setdefault(first, []).append(trigger)
                else:
                    contains.append(trigger)

        for bucket in prefix.values():
            bucket.sort(key=lambda item: len(item.text), reverse=True)

        return cls(
            entries=entries,
            fullmatch=dict(fullmatch),
            prefix=dict(prefix),
            image_triggers=dict(image_triggers),
            contains=_ContainsIndex(contains),
        )

    @property
    def backend(self) -> str:
        return self.contains.backend

    def find_text(self, text: str) -> list[MatchCandidate]:
        normalized = normalize_text(text)
        candidates: list[MatchCandidate] = []
        for trigger in self.fullmatch.get(normalized, []):
            entry = self.entries.get(trigger.entry_id)
            if entry:
                candidates.append(MatchCandidate(entry, trigger, normalized))

        for trigger in self.prefix.get(normalized[:1], []):
            if normalized.startswith(trigger.text):
                entry = self.entries.get(trigger.entry_id)
                if entry:
                    candidates.append(MatchCandidate(entry, trigger, trigger.text))

        for trigger, matched_text in self.contains.search(normalized):
            entry = self.entries.get(trigger.entry_id)
            if entry:
                candidates.append(MatchCandidate(entry, trigger, matched_text))

        return _dedupe_candidates(candidates)

    def find_texts(self, texts: Sequence[str]) -> list[MatchCandidate]:
        candidates: list[MatchCandidate] = []
        for text in texts:
            candidates.extend(self.find_text(text))
        return _dedupe_candidates(candidates)

    def find_images(self, canonical_image_ids: Sequence[int]) -> list[MatchCandidate]:
        candidates: list[MatchCandidate] = []
        for canonical_id in canonical_image_ids[:4]:
            for trigger in self.image_triggers.get(canonical_id, []):
                entry = self.entries.get(trigger.entry_id)
                if entry:
                    candidates.append(MatchCandidate(entry, trigger, ""))
        return _dedupe_candidates(candidates)

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
        candidate = _weighted_choice(
            priority_group,
            [max(1, item.entry.weight) for item in priority_group],
            rng,
        )
        response = _weighted_choice(
            list(candidate.entry.responses),
            [max(1, response.weight) for response in candidate.entry.responses],
            rng,
        )
        return SelectedMatch(candidate=candidate, response=response)


def _to_runtime_response(record: WordbankResponseRecord) -> RuntimeResponse:
    return RuntimeResponse(
        id=record.id,
        kind=record.kind,
        text=record.text,
        canonical_image_id=record.canonical_image_id,
        weight=record.weight,
    )


def _to_runtime_trigger(record: WordbankTriggerRecord) -> RuntimeTrigger:
    return RuntimeTrigger(
        id=record.id,
        entry_id=record.entry_id,
        kind=record.kind,
        text=record.normalized_text,
        mode=record.trigger_mode,
        canonical_image_id=record.canonical_image_id,
    )


def _dedupe_candidates(candidates: Sequence[MatchCandidate]) -> list[MatchCandidate]:
    seen: set[tuple[int, int]] = set()
    deduped: list[MatchCandidate] = []
    for candidate in candidates:
        key = (candidate.entry.id, candidate.trigger.id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _weighted_choice[T](
    items: Sequence[T],
    weights: Sequence[int],
    rng: random.Random,
) -> T:
    total = sum(weights)
    if total <= 0:
        return items[0]
    target = rng.uniform(0, total)
    upto = 0.0
    for item, weight in zip(items, weights, strict=True):
        upto += weight
        if upto >= target:
            return item
    return items[-1]
