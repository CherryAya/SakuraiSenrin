from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.plugins.wordbank.database.types import WordbankMessageRefRecord
from src.plugins.wordbank.entry_runtime import register_wordbank_runtime_handlers


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


def _runtime_exports(
    monkeypatch: pytest.MonkeyPatch,
    *,
    list_message_refs_by_response_item_ids: AsyncMock | None = None,
    deliver_message_plan: AsyncMock | None = None,
) -> dict[str, Any]:
    from src.plugins.wordbank import entry_runtime as runtime_module

    monkeypatch.setattr(
        runtime_module,
        "wordbank_service",
        cast(
            Any,
            SimpleNamespace(
                list_message_refs_by_response_item_ids=(
                    list_message_refs_by_response_item_ids
                    or AsyncMock(return_value=[_approval_submission_message()])
                )
            ),
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "deliver_message_plan",
        deliver_message_plan
        or AsyncMock(return_value=SimpleNamespace(results=({"message_id": 1},))),
    )
    return register_wordbank_runtime_handlers(
        wordbank_reply_command=SimpleNamespace(handle=lambda: lambda fn: fn),
        wordbank_approval_reply_command=SimpleNamespace(handle=lambda: lambda fn: fn),
        wordbank_view_reply_command=SimpleNamespace(handle=lambda: lambda fn: fn),
        wordbank_passive=SimpleNamespace(handle=lambda: lambda fn: fn),
        wordbank_notice=SimpleNamespace(handle=lambda: lambda fn: fn),
        wordbank_add_command=SimpleNamespace(handle=lambda: lambda fn: fn),
        wordbank_command=SimpleNamespace(handle=lambda: lambda fn: fn),
        initialize_plugin=AsyncMock(return_value=None),
        build_error_message=lambda *args, **kwargs: "ERR",
        cancel_guided_resources=AsyncMock(return_value=None),
        guided_locale=lambda state: "zh-CN",
    )


@pytest.mark.asyncio
async def test_notify_creator_review_result_replies_and_mentions_creator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deliver_plan = AsyncMock(return_value=SimpleNamespace(results=({"message_id": 1},)))
    exports = _runtime_exports(monkeypatch, deliver_message_plan=deliver_plan)

    await exports["notify_creator_review_result"](
        cast(Any, SimpleNamespace()),
        response_item_id=300,
        action="approve",
        locale="zh-CN",
    )

    assert deliver_plan.await_count == 1
    await_args = deliver_plan.await_args
    assert await_args is not None
    plan = await_args.kwargs["plan"]
    entry = plan.messages[0]
    blocks = entry.blocks
    assert blocks[0].message_id == "456"
    assert blocks[1].target_id == "10001"
    assert "已通过审核" in blocks[3].text
    target = await_args.kwargs["target"]
    assert target.kind == "group"
    assert target.target_id == "20001"


@pytest.mark.asyncio
async def test_notify_creator_review_result_uses_approval_message_context_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deliver_plan = AsyncMock(return_value=SimpleNamespace(results=({"message_id": 1},)))
    exports = _runtime_exports(
        monkeypatch,
        list_message_refs_by_response_item_ids=AsyncMock(return_value=[]),
        deliver_message_plan=deliver_plan,
    )

    await exports["notify_creator_review_result"](
        cast(Any, SimpleNamespace()),
        response_item_id=300,
        action="reject",
        locale="zh-CN",
        approval_message=_approval_submission_message(),
    )

    assert deliver_plan.await_count == 1
    await_args = deliver_plan.await_args
    assert await_args is not None
    plan = await_args.kwargs["plan"]
    assert "未通过审核" in plan.messages[0].blocks[3].text
