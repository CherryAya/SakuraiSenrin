from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.plugins.wordbank import notify as notify_module
from src.plugins.wordbank import views as views_module
from src.plugins.wordbank.database.types import WordbankMessageRefRecord
from tests.plugins.water.helpers import build_group_message_event


def _approval_submission_message() -> WordbankMessageRefRecord:
    return WordbankMessageRefRecord(
        message_id="90001",
        ref_kind="approval",
        shard_key="2026_07",
        trigger_group_id=12,
        trigger_variant_id=0,
        response_item_id=300,
        group_id="20001",
        user_id="10001",
        message_type="submission",
        source_message_id="456",
        context_type="",
        current_page=1,
        keyword="",
        field="",
        creator_id="",
        has_image=False,
        group_ids=(),
    )


def _approval_batch_submission_message() -> WordbankMessageRefRecord:
    return WordbankMessageRefRecord(
        message_id="90002",
        ref_kind="approval",
        shard_key="2026_07",
        trigger_group_id=12,
        trigger_variant_id=0,
        response_item_id=301,
        group_id="20001",
        user_id="10001",
        message_type="submission_batch",
        source_message_id="789",
        context_type="pending_batch",
        current_page=1,
        keyword="",
        field="",
        creator_id="",
        has_image=False,
        group_ids=(301, 302, 303),
    )


def _stub_notify(
    monkeypatch: pytest.MonkeyPatch,
    *,
    list_message_refs_by_response_item_ids: AsyncMock | None = None,
    deliver_message_plan: AsyncMock | None = None,
) -> AsyncMock:
    """给 notify 模块打桩：业务 service + 统一投递入口。"""
    service = cast(
        Any,
        SimpleNamespace(
            list_message_refs_by_response_item_ids=(
                list_message_refs_by_response_item_ids
                or AsyncMock(return_value=[_approval_submission_message()])
            )
        ),
    )
    monkeypatch.setattr(notify_module, "wordbank_service", service)
    plan_stub = deliver_message_plan or AsyncMock(
        return_value=SimpleNamespace(results=({"message_id": 1},))
    )
    monkeypatch.setattr(notify_module, "deliver_message_plan", plan_stub)
    return plan_stub


@pytest.mark.asyncio
async def test_notify_creator_review_result_replies_and_mentions_creator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deliver_plan = _stub_notify(monkeypatch)

    await notify_module.notify_creator_review_result(
        cast(Any, SimpleNamespace()),
        response_item_id=300,
        action="approve",
        locale="zh-CN",
        reviewer_id="10002",
    )

    assert deliver_plan.await_count == 1
    await_args = deliver_plan.await_args
    assert await_args is not None
    plan = await_args.kwargs["plan"]
    entry = plan.messages[0]
    blocks = entry.blocks
    assert blocks[0].message_id == "456"
    assert blocks[1].target_id == "10001"
    assert blocks[3].text == "管理员 10002 已通过该词条。"
    target = await_args.kwargs["target"]
    assert target.kind == "group"
    assert target.target_id == "20001"


@pytest.mark.asyncio
async def test_notify_creator_review_result_uses_approval_message_context_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deliver_plan = _stub_notify(
        monkeypatch,
        list_message_refs_by_response_item_ids=AsyncMock(return_value=[]),
    )

    await notify_module.notify_creator_review_result(
        cast(Any, SimpleNamespace()),
        response_item_id=300,
        action="reject",
        locale="zh-CN",
        approval_message=_approval_submission_message(),
        reviewer_id="10002",
    )

    assert deliver_plan.await_count == 1
    await_args = deliver_plan.await_args
    assert await_args is not None
    plan = await_args.kwargs["plan"]
    assert plan.messages[0].blocks[3].text == "管理员 10002 已拒绝该词条。"


@pytest.mark.asyncio
async def test_notify_creator_review_results_merges_batch_notices_by_source_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deliver_plan = _stub_notify(
        monkeypatch,
        list_message_refs_by_response_item_ids=AsyncMock(
            return_value=[_approval_batch_submission_message()]
        ),
    )

    await notify_module.notify_creator_review_results(
        cast(Any, SimpleNamespace()),
        notices=((301, "approve"), (302, "approve"), (303, "approve")),
        locale="zh-CN",
        reviewer_id="10002",
    )

    assert deliver_plan.await_count == 1
    await_args = deliver_plan.await_args
    assert await_args is not None
    plan = await_args.kwargs["plan"]
    blocks = plan.messages[0].blocks
    assert blocks[0].message_id == "789"
    assert blocks[1].target_id == "10001"
    assert blocks[3].text == "管理员 10002 已批量通过 3 条词条：#301, #302, #303。"


@pytest.mark.asyncio
async def test_send_search_result_view_guided_passes_bot_to_finish_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finish_guided_search = AsyncMock(return_value=None)
    monkeypatch.setattr(views_module, "finish_guided_search_view", finish_guided_search)
    bot = cast(Any, SimpleNamespace(self_id="99999"))
    matcher = cast(Any, SimpleNamespace())
    event = build_group_message_event("#搜索词条 晚安", message_id=1)
    state: dict[str, Any] = {}

    await views_module.send_search_result_view(
        bot,
        matcher,
        event,
        "zh-CN",
        keyword="晚安",
        image_scores={7: 0.91},
        state=state,
    )

    finish_guided_search.assert_awaited_once()
    await_args = finish_guided_search.await_args
    assert await_args is not None
    assert await_args.args == (bot, matcher, state, event, "zh-CN")
    assert await_args.kwargs["page_number"] == 1
    assert state["wordbank_guided_search_keyword"] == "晚安"
    assert state["wordbank_guided_search_has_image"] is True
    assert state["wordbank_guided_search_image_scores"] == {7: 0.91}
