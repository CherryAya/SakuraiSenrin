import random

import pytest

from src.plugins.wordbank.database.types import (
    WordbankEntryRecord,
    WordbankResponseRecord,
    WordbankTriggerRecord,
)
from src.plugins.wordbank.services import matching
from src.plugins.wordbank.services.matching import RuntimeIndex, normalize_text
from src.plugins.wordbank.services.rules import RuleContext


def _entry(
    entry_id: int,
    trigger_text: str,
    response_text: str,
    *,
    mode: str = "contains",
    scope: str = "current_group",
    priority: int = 30,
    probability: float = 1.0,
    weight: int = 3,
    group_id: str = "20001",
    created_by: str = "10001",
) -> WordbankEntryRecord:
    return WordbankEntryRecord(
        id=entry_id,
        status="approved",
        enabled=1,
        scope=scope,
        priority=priority,
        probability=probability,
        weight=weight,
        rule={},
        group_id=group_id,
        created_by=created_by,
        deleted_at=0,
        triggers=(
            WordbankTriggerRecord(
                id=entry_id * 10,
                entry_id=entry_id,
                kind="text",
                trigger_text=trigger_text,
                normalized_text=normalize_text(trigger_text),
                trigger_mode=mode,
                canonical_image_id=None,
            ),
        ),
        responses=(
            WordbankResponseRecord(
                id=entry_id * 100,
                entry_id=entry_id,
                kind="text",
                text=response_text,
                canonical_image_id=None,
                weight=weight,
            ),
        ),
    )


def test_runtime_index_matches_contains_fullmatch_and_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(matching, "ahocorasick", None)
    index = RuntimeIndex.build(
        [
            _entry(1, "晚安", "做个好梦", mode="contains"),
            _entry(2, "早", "早呀", mode="fullmatch"),
            _entry(3, "天气", "天气不错", mode="prefix"),
        ]
    )

    assert index.backend == "fallback"
    assert [item.entry.id for item in index.find_text("大家晚安")] == [1]
    assert [item.entry.id for item in index.find_text("早")] == [2]
    assert [item.entry.id for item in index.find_text("天气怎么样")] == [3]
    assert normalize_text("  Ａ  B\nC ") == "a b c"


def test_runtime_index_selects_highest_priority_before_weight() -> None:
    index = RuntimeIndex.build(
        [
            _entry(1, "ping", "global", scope="all_groups", priority=10, weight=5),
            _entry(2, "ping", "local", scope="current_group", priority=30, weight=1),
        ]
    )
    selected = index.select(
        index.find_text("ping"),
        context=RuleContext(
            group_id="20001",
            user_id="10001",
            message_type="group",
        ),
        rng=random.Random(1),
    )

    assert selected is not None
    assert selected.candidate.entry.id == 2
    assert selected.response.text == "local"


def test_runtime_index_probability_after_rule() -> None:
    index = RuntimeIndex.build([_entry(1, "ping", "pong", probability=0.0)])

    selected = index.select(
        index.find_text("ping"),
        context=RuleContext(
            group_id="20001",
            user_id="10001",
            message_type="group",
        ),
        rng=random.Random(1),
    )

    assert selected is None
