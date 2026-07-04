from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from nonebot.adapters.onebot.v11 import Bot
import pytest

from src.plugins.wordbank.handlers import submission as submission_module
from src.plugins.wordbank.handlers.submission import (
    SubmissionLifecycle,
    finalize_submission,
)
from src.plugins.wordbank.message_model import shape_from_text
from src.plugins.wordbank.services.core import WordbankAddResult, WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.presentation import (
    WordbankBatchAddItemResult,
    WordbankBatchAddResult,
)
from tests.plugins.water.helpers import build_group_message_event


@pytest.mark.asyncio
async def test_finalize_submission_routes_single_result_through_single_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_message = AsyncMock(return_value="RESULT")
    deliver_plan = AsyncMock(return_value=SimpleNamespace(results=({"message_id": 1},)))
    record_submission = AsyncMock(return_value=None)
    record_batch_submission = AsyncMock(return_value=None)
    schedule_notice = Mock(return_value=None)
    matcher = cast(Any, SimpleNamespace(finish=AsyncMock(return_value=None)))

    monkeypatch.setattr(submission_module, "build_add_result_plan_entry", build_message)
    monkeypatch.setattr(submission_module, "deliver_message_plan", deliver_plan)
    monkeypatch.setattr(
        submission_module,
        "record_submission_approval_message",
        record_submission,
    )
    monkeypatch.setattr(
        submission_module,
        "record_batch_submission_approval_message",
        record_batch_submission,
    )
    monkeypatch.setattr(
        submission_module,
        "schedule_submission_approval_notice",
        schedule_notice,
    )

    result = WordbankAddResult(
        trigger_group_id=1,
        trigger_variant_id=2,
        response_item_id=3,
        trigger_text="晚安",
        response_text="做个好梦",
        scope="current_group",
        probability=1.0,
        weight=3,
        trigger_shape=shape_from_text("晚安"),
        response_shape=shape_from_text("做个好梦"),
    )

    await finalize_submission(
        matcher,
        cast(Bot, SimpleNamespace()),
        build_group_message_event("#wordbank.add", message_id=1),
        result,
        locale="zh-CN",
        service=cast(WordbankService, SimpleNamespace()),
        media_service=cast(WordbankMediaService, SimpleNamespace()),
        submission_source_kind="wordbank_submission",
        batch_submission_source_kind="wordbank_batch_submission",
        batch_feedback_nickname="回 - 樱井千凛·Senrinです♡",
    )

    build_message.assert_awaited_once()
    deliver_plan.assert_awaited_once()
    record_submission.assert_awaited_once()
    record_batch_submission.assert_not_awaited()
    schedule_notice.assert_called_once()
    matcher.finish.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_submission_routes_batch_result_through_batch_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send_feedback = AsyncMock(return_value={"message_id": 1})
    record_submission = AsyncMock(return_value=None)
    record_batch_submission = AsyncMock(return_value=None)
    schedule_notice = Mock(return_value=None)
    matcher = cast(Any, SimpleNamespace(finish=AsyncMock(return_value=None)))

    monkeypatch.setattr(submission_module, "send_batch_add_feedback", send_feedback)
    monkeypatch.setattr(
        submission_module,
        "record_submission_approval_message",
        record_submission,
    )
    monkeypatch.setattr(
        submission_module,
        "record_batch_submission_approval_message",
        record_batch_submission,
    )
    monkeypatch.setattr(
        submission_module,
        "schedule_submission_approval_notice",
        schedule_notice,
    )

    batch = WordbankBatchAddResult(
        total=2,
        success=1,
        failed=1,
        items=(
            WordbankBatchAddItemResult(
                index=1,
                ok=True,
                result=WordbankAddResult(
                    trigger_group_id=1,
                    trigger_variant_id=2,
                    response_item_id=3,
                    trigger_text="晚安",
                    response_text="做个好梦",
                    scope="current_group",
                    probability=1.0,
                    weight=3,
                    trigger_shape=shape_from_text("晚安"),
                    response_shape=shape_from_text("做个好梦"),
                ),
            ),
            WordbankBatchAddItemResult(index=2, ok=False, error="boom"),
        ),
    )

    await finalize_submission(
        matcher,
        cast(Bot, SimpleNamespace()),
        build_group_message_event("#study", message_id=1),
        batch,
        locale="zh-CN",
        service=cast(WordbankService, SimpleNamespace()),
        media_service=cast(WordbankMediaService, SimpleNamespace()),
        submission_source_kind="study_submission",
        batch_submission_source_kind="study_batch_submission",
        batch_feedback_nickname="回 - 樱井千凛·Senrinです♡",
    )

    send_feedback.assert_awaited_once()
    assert send_feedback.await_args is not None
    assert send_feedback.await_args.args[0] is matcher
    record_submission.assert_not_awaited()
    record_batch_submission.assert_awaited_once()
    schedule_notice.assert_called_once()
    matcher.finish.assert_awaited_once()


@pytest.mark.asyncio
async def test_submission_lifecycle_binds_plugin_specific_submission_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalize = AsyncMock(return_value=None)
    lifecycle = SubmissionLifecycle(
        service=cast(WordbankService, SimpleNamespace()),
        media_service=cast(WordbankMediaService, SimpleNamespace()),
        submission_source_kind="study_submission",
        batch_submission_source_kind="study_batch_submission",
        batch_feedback_nickname_builder=lambda locale: f"nickname:{locale}",
    )
    result = WordbankAddResult(
        trigger_group_id=1,
        trigger_variant_id=2,
        response_item_id=3,
        trigger_text="晚安",
        response_text="做个好梦",
        scope="current_group",
        probability=1.0,
        weight=3,
        trigger_shape=shape_from_text("晚安"),
        response_shape=shape_from_text("做个好梦"),
    )

    monkeypatch.setattr(submission_module, "finalize_submission", finalize)

    await lifecycle.finalize(
        cast(Any, SimpleNamespace()),
        cast(Bot, SimpleNamespace()),
        build_group_message_event("#study", message_id=1),
        result,
        "zh-CN",
    )

    finalize.assert_awaited_once()
    await_args = finalize.await_args
    assert await_args is not None
    assert await_args.kwargs["submission_source_kind"] == "study_submission"
    assert await_args.kwargs["batch_submission_source_kind"] == "study_batch_submission"
    assert await_args.kwargs["batch_feedback_nickname"] == "nickname:zh-CN"
