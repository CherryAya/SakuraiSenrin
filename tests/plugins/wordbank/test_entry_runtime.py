from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.plugins.wordbank.database.types import WordbankMessageRefRecord
from src.plugins.wordbank.entry_runtime import register_wordbank_runtime_handlers
from src.plugins.wordbank.handlers.reply import ApprovalReplyOutcome
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


def _runtime_exports(
    monkeypatch: pytest.MonkeyPatch,
    *,
    list_message_refs_by_response_item_ids: AsyncMock | None = None,
    deliver_message_plan: AsyncMock | None = None,
) -> dict[str, Any]:
    from src.plugins import wordbank as wordbank_plugin
    from src.plugins.wordbank import entry_runtime as runtime_module

    service = cast(
        Any,
        SimpleNamespace(
            list_message_refs_by_response_item_ids=(
                list_message_refs_by_response_item_ids
                or AsyncMock(return_value=[_approval_submission_message()])
            )
        ),
    )

    monkeypatch.setattr(
        runtime_module,
        "wordbank_service",
        service,
    )
    monkeypatch.setattr(wordbank_plugin, "wordbank_service", service, raising=False)
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


class _CommandStub:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def handle(self) -> Any:
        def _decorator(fn: Any) -> Any:
            self.handlers.append(fn)
            return fn

        return _decorator


def _runtime_with_command_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    deliver_message_plan: AsyncMock | None = None,
) -> tuple[dict[str, Any], Any]:
    from src.plugins import wordbank as wordbank_plugin
    from src.plugins.wordbank import entry_runtime as runtime_module

    approval_reply_command = _CommandStub()
    service = cast(Any, SimpleNamespace())
    monkeypatch.setattr(runtime_module, "wordbank_service", service)
    monkeypatch.setattr(wordbank_plugin, "wordbank_service", service, raising=False)
    monkeypatch.setattr(
        runtime_module,
        "deliver_message_plan",
        deliver_message_plan
        or AsyncMock(return_value=SimpleNamespace(results=({"message_id": 1},))),
    )
    finish_with_message = AsyncMock(return_value=None)
    monkeypatch.setattr(runtime_module, "finish_with_message", finish_with_message)

    exports = register_wordbank_runtime_handlers(
        wordbank_reply_command=_CommandStub(),
        wordbank_approval_reply_command=approval_reply_command,
        wordbank_view_reply_command=_CommandStub(),
        wordbank_passive=_CommandStub(),
        wordbank_notice=_CommandStub(),
        wordbank_add_command=_CommandStub(),
        wordbank_command=_CommandStub(),
        initialize_plugin=AsyncMock(return_value=None),
        build_error_message=lambda *args, **kwargs: "ERR",
        cancel_guided_resources=AsyncMock(return_value=None),
        guided_locale=lambda state: "zh-CN",
    )
    return exports, finish_with_message


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
    deliver_plan = AsyncMock(return_value=SimpleNamespace(results=({"message_id": 1},)))
    exports = _runtime_exports(
        monkeypatch,
        list_message_refs_by_response_item_ids=AsyncMock(
            return_value=[_approval_batch_submission_message()]
        ),
        deliver_message_plan=deliver_plan,
    )

    await exports["notify_creator_review_results"](
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
    exports = _runtime_exports(monkeypatch)
    bot = cast(Any, SimpleNamespace(self_id="99999"))
    matcher = cast(Any, SimpleNamespace())
    event = build_group_message_event("#搜索词条 晚安", message_id=1)
    state: dict[str, Any] = {}

    await exports["send_search_result_view"](
        bot,
        matcher,
        event,
        "zh-CN",
        keyword="晚安",
        image_scores={7: 0.91},
        state=state,
        finish_guided_search=finish_guided_search,
    )

    finish_guided_search.assert_awaited_once()
    await_args = finish_guided_search.await_args
    assert await_args is not None
    assert await_args.args == (bot, matcher, state, event, "zh-CN")
    assert await_args.kwargs["page_number"] == 1
    assert state["wordbank_guided_search_keyword"] == "晚安"
    assert state["wordbank_guided_search_has_image"] is True
    assert state["wordbank_guided_search_image_scores"] == {7: 0.91}


@pytest.mark.asyncio
async def test_approval_reply_handler_sends_single_merged_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank import entry_runtime as runtime_module

    deliver_plan = AsyncMock(return_value=SimpleNamespace(results=({"message_id": 1},)))
    _, finish_with_message = _runtime_with_command_stub(
        monkeypatch,
        deliver_message_plan=deliver_plan,
    )
    approval_reply_command = _CommandStub()
    monkeypatch.setattr(
        runtime_module,
        "deliver_message_plan",
        deliver_plan,
    )
    monkeypatch.setattr(runtime_module, "finish_with_message", finish_with_message)
    monkeypatch.setattr(
        runtime_module,
        "handle_approval_reply_result",
        AsyncMock(
            return_value=ApprovalReplyOutcome(
                message="词条 #300 已通过审核，稍后会参与被动匹配。",
                approval_message=_approval_submission_message(),
                completed=True,
                action="approve",
            )
        ),
    )

    register_wordbank_runtime_handlers(
        wordbank_reply_command=_CommandStub(),
        wordbank_approval_reply_command=approval_reply_command,
        wordbank_view_reply_command=_CommandStub(),
        wordbank_passive=_CommandStub(),
        wordbank_notice=_CommandStub(),
        wordbank_add_command=_CommandStub(),
        wordbank_command=_CommandStub(),
        initialize_plugin=AsyncMock(return_value=None),
        build_error_message=lambda *args, **kwargs: "ERR",
        cancel_guided_resources=AsyncMock(return_value=None),
        guided_locale=lambda state: "zh-CN",
    )
    handler = approval_reply_command.handlers[0]

    await handler(
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        build_group_message_event("[CQ:at,qq=99999] y", role="admin"),
    )

    assert deliver_plan.await_count == 1
    await_args = deliver_plan.await_args
    assert await_args is not None
    plan = await_args.kwargs["plan"]
    assert plan.source_kind == "wordbank_creator_review_notice"
    blocks = plan.messages[0].blocks
    assert blocks[0].message_id == "456"
    assert blocks[1].target_id == "10001"
    assert blocks[3].text == "词条 #300 已通过审核，稍后会参与被动匹配。"
    assert finish_with_message.await_count == 1


@pytest.mark.asyncio
async def test_approval_reply_handler_falls_back_to_source_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank import entry_runtime as runtime_module

    deliver_plan = AsyncMock(
        side_effect=[
            RuntimeError("boom"),
            SimpleNamespace(results=({"message_id": 1},)),
        ]
    )
    finish_with_message = AsyncMock(return_value=None)
    approval_reply_command = _CommandStub()
    monkeypatch.setattr(runtime_module, "deliver_message_plan", deliver_plan)
    monkeypatch.setattr(runtime_module, "finish_with_message", finish_with_message)
    monkeypatch.setattr(
        runtime_module,
        "handle_approval_reply_result",
        AsyncMock(
            return_value=ApprovalReplyOutcome(
                message="词条 #300 已拒绝。",
                approval_message=_approval_submission_message(),
                completed=True,
                action="reject",
            )
        ),
    )

    register_wordbank_runtime_handlers(
        wordbank_reply_command=_CommandStub(),
        wordbank_approval_reply_command=approval_reply_command,
        wordbank_view_reply_command=_CommandStub(),
        wordbank_passive=_CommandStub(),
        wordbank_notice=_CommandStub(),
        wordbank_add_command=_CommandStub(),
        wordbank_command=_CommandStub(),
        initialize_plugin=AsyncMock(return_value=None),
        build_error_message=lambda *args, **kwargs: "ERR",
        cancel_guided_resources=AsyncMock(return_value=None),
        guided_locale=lambda state: "zh-CN",
    )
    handler = approval_reply_command.handlers[0]

    await handler(
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        build_group_message_event("[CQ:at,qq=99999] n", role="admin"),
    )

    assert deliver_plan.await_count == 2
    first_plan = deliver_plan.await_args_list[0].kwargs["plan"]
    second_plan = deliver_plan.await_args_list[1].kwargs["plan"]
    assert first_plan.source_kind == "wordbank_creator_review_notice"
    assert second_plan.source_kind == "wordbank_approval_source_notice"
    assert finish_with_message.await_count == 1
