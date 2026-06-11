import random

from src.plugins.wordbank.database.types import (
    WordbankResponseItemRecord,
    WordbankTriggerGroupRecord,
    WordbankTriggerVariantRecord,
)
from src.plugins.wordbank.message_model import (
    fingerprint_shape,
    shape_from_text,
    shape_to_search_text,
    shape_to_summary_text,
)
from src.plugins.wordbank.services.matching import RuntimeIndex
from src.plugins.wordbank.services.rules import RuleContext


def _entry(
    *,
    trigger_group_id: int,
    trigger_text: str,
    response_text: str,
    priority: int = 1,
    weight: int = 1,
    probability: float = 1.0,
) -> WordbankTriggerGroupRecord:
    trigger_shape = shape_from_text(trigger_text)
    response_shape = shape_from_text(response_text)
    trigger_fp = fingerprint_shape(trigger_shape)
    response_fp = fingerprint_shape(response_shape)
    return WordbankTriggerGroupRecord(
        id=trigger_group_id,
        status="approved",
        enabled=1,
        group_id="",
        created_by="10001",
        deleted_at=0,
        trigger_variants=(
            WordbankTriggerVariantRecord(
                id=trigger_group_id * 10 + 1,
                trigger_group_id=trigger_group_id,
                trigger_text=shape_to_summary_text(trigger_shape),
                message_shape=trigger_shape,
                exact_md5=trigger_fp.exact_md5,
                structure_key=trigger_fp.structure_key,
                search_text=shape_to_search_text(trigger_shape),
                search_tokens=trigger_fp.search_tokens,
                image_keys=trigger_fp.image_keys,
            ),
        ),
        responses=(
            WordbankResponseItemRecord(
                id=trigger_group_id * 10 + 2,
                trigger_group_id=trigger_group_id,
                status="approved",
                enabled=1,
                scope="all_groups",
                priority=priority,
                probability=probability,
                weight=weight,
                rule={},
                group_id="",
                created_by="10001",
                approved_by="10002",
                deleted_at=0,
                text=shape_to_summary_text(response_shape),
                message_shape=response_shape,
                exact_md5=response_fp.exact_md5,
                structure_key=response_fp.structure_key,
                search_text=shape_to_search_text(response_shape),
                search_tokens=response_fp.search_tokens,
                image_keys=response_fp.image_keys,
            ),
        ),
    )


def _context() -> RuleContext:
    return RuleContext(
        group_id="20001",
        user_id="10001",
        message_type="group",
        sender_role="member",
    )


def test_runtime_index_matches_only_exact_message_fingerprint() -> None:
    index = RuntimeIndex.build(
        [_entry(trigger_group_id=1, trigger_text="晚安", response_text="好梦")]
    )

    exact = index.find_message(fingerprint_shape(shape_from_text("晚安")))
    miss = index.find_message(fingerprint_shape(shape_from_text("晚")))

    assert len(exact) == 1
    assert exact[0].group.id == 1
    assert miss == []


def test_runtime_index_selects_highest_priority_before_weight() -> None:
    index = RuntimeIndex.build(
        [
            _entry(
                trigger_group_id=1,
                trigger_text="晚安",
                response_text="低优先级",
                priority=1,
            ),
            _entry(
                trigger_group_id=2,
                trigger_text="晚安",
                response_text="高优先级",
                priority=3,
            ),
        ]
    )

    selected = index.select(
        index.find_message(fingerprint_shape(shape_from_text("晚安"))),
        context=_context(),
        rng=random.Random(0),
    )

    assert selected is not None
    assert selected.candidate.group.id == 2
    assert selected.response.text == "高优先级"


def test_runtime_index_applies_probability_after_rule_filter() -> None:
    index = RuntimeIndex.build(
        [
            _entry(
                trigger_group_id=1,
                trigger_text="晚安",
                response_text="好梦",
                probability=0.0,
            )
        ]
    )

    selected = index.select(
        index.find_message(fingerprint_shape(shape_from_text("晚安"))),
        context=_context(),
        rng=random.Random(0),
    )

    assert selected is None
