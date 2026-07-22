from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.event import GroupMessageEvent, PrivateMessageEvent
from nonebot.adapters.onebot.v11.message import Message
import pytest

from src.lib.message_plan import render_message_plan_input
from src.lib.messages import text_message
from src.plugins.wordbank.database.types import (
    WordbankGroupDetail,
    WordbankMessageRefRecord,
    WordbankResponseItemDetail,
    WordbankReviewHistoryEntry,
)
from src.plugins.wordbank.handlers.reply import (
    group_detail_page_response_item_ids,
    handle_approval_reply_result,
    handle_reply_command,
    parse_batch_approval_reply,
    parse_group_detail_delete_reply,
    parse_view_reply_for_group_detail,
    parse_view_reply_for_search_result,
)
from src.plugins.wordbank.message_model import (
    MessageShape,
    combine_shapes,
    shape_from_image,
    shape_from_text,
    shape_to_summary_text,
)
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import RuleError
from tests.plugins.water.helpers import (
    build_group_message_event,
    build_private_message_event,
)


def _event_with_reply(message: str = "详情") -> GroupMessageEvent:
    event = build_group_message_event(message, role="admin")
    setattr(event, "reply", SimpleNamespace(message_id=90001))
    return event


def _private_event_with_reply(
    message: str = "y",
    *,
    reply_message_id: int = 123,
    reply_real_id: int = 90001,
) -> PrivateMessageEvent:
    event = build_private_message_event(message, user_id=1)
    reply = SimpleNamespace(message_id=reply_message_id, real_id=reply_real_id)
    setattr(event, "reply", reply)
    return event


def _response_message() -> WordbankMessageRefRecord:
    return WordbankMessageRefRecord(
        message_id="90001",
        ref_kind="response",
        shard_key="2026_06",
        trigger_group_id=12,
        trigger_variant_id=120,
        response_item_id=300,
        group_id="20001",
        user_id="10001",
        message_type="text",
        source_message_id="",
        context_type="",
        current_page=1,
        keyword="",
        field="",
        creator_id="",
        has_image=False,
        group_ids=(),
    )


def _approval_message() -> WordbankMessageRefRecord:
    return WordbankMessageRefRecord(
        message_id="90001",
        ref_kind="approval",
        shard_key="2026_06",
        trigger_group_id=12,
        trigger_variant_id=0,
        response_item_id=300,
        group_id="20001",
        user_id="10001",
        source_message_id="1",
        message_type="approval",
        context_type="",
        current_page=1,
        keyword="",
        field="",
        creator_id="",
        has_image=False,
        group_ids=(),
    )


def _batch_approval_message() -> WordbankMessageRefRecord:
    return WordbankMessageRefRecord(
        message_id="90001",
        ref_kind="approval",
        shard_key="2026_06",
        trigger_group_id=12,
        trigger_variant_id=0,
        response_item_id=301,
        group_id="20001",
        user_id="10001",
        source_message_id="1",
        message_type="approval_batch",
        context_type="pending_batch",
        current_page=1,
        keyword="",
        field="",
        creator_id="",
        has_image=False,
        group_ids=(301, 302, 303, 304),
    )


def _group_detail() -> WordbankGroupDetail:
    return WordbankGroupDetail(
        trigger_group_id=12,
        status="approved",
        enabled=1,
        probability=1.0,
        group_id="20001",
        created_by="10001",
        deleted_at=0,
        trigger_text="晚安",
        trigger_shape=shape_from_text("晚安"),
        trigger_variant_id=120,
        responses=(
            WordbankResponseItemDetail(
                response_item_id=300,
                status="approved",
                enabled=1,
                scope="current_group",
                weight=3,
                rule={},
                group_id="20001",
                created_by="10001",
                approved_by="10002",
                deleted_at=0,
                response_text="做个好梦",
                response_shape=shape_from_text("做个好梦"),
            ),
        ),
        selected_response_item_id=300,
    )


def _review_history_entry(
    *,
    action: str = "approve",
    actor_user_id: str = "10002",
    created_at: int = 1_700_000_000,
    previous_status: str = "pending",
    overwritten: bool = False,
) -> WordbankReviewHistoryEntry:
    return WordbankReviewHistoryEntry(
        action=action,
        actor_user_id=actor_user_id,
        created_at=created_at,
        previous_status=previous_status,
        overwritten=overwritten,
    )


def _reviewed_group_detail(*, status: str = "approved") -> WordbankGroupDetail:
    current_action = "approve" if status == "approved" else "reject"
    previous_status = "rejected" if status == "approved" else "approved"
    alternate_action = "reject" if status == "approved" else "approve"
    return WordbankGroupDetail(
        trigger_group_id=12,
        status=status,
        enabled=1 if status == "approved" else 0,
        probability=1.0,
        group_id="20001",
        created_by="10001",
        deleted_at=0,
        trigger_text="晚安",
        trigger_shape=shape_from_text("晚安"),
        trigger_variant_id=120,
        responses=(
            WordbankResponseItemDetail(
                response_item_id=300,
                status=status,
                enabled=1 if status == "approved" else 0,
                scope="current_group",
                weight=3,
                rule={},
                group_id="20001",
                created_by="10001",
                approved_by="10002",
                deleted_at=0,
                response_text="做个好梦",
                response_shape=shape_from_text("做个好梦"),
                review_history=(
                    _review_history_entry(action=current_action),
                    _review_history_entry(
                        action=alternate_action,
                        actor_user_id="10003",
                        created_at=1_700_000_100,
                        previous_status=previous_status,
                        overwritten=True,
                    ),
                ),
            ),
        ),
        selected_response_item_id=300,
    )


async def test_reply_info_formats_selected_response_item_detail() -> None:
    event = _event_with_reply()
    get_message_ref = AsyncMock(return_value=_response_message())
    get_group_detail = AsyncMock(return_value=_group_detail())
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=get_message_ref,
            get_group_detail=get_group_detail,
        ),
    )

    message = await handle_reply_command(
        service,
        event=event,
        message=event.message,
        text="详情",
        locale="zh-CN",
        media_service=cast(WordbankMediaService, SimpleNamespace()),
    )
    assert message is not None
    rendered = render_message_plan_input(message)

    assert not isinstance(message, str)
    assert "词条详情 #300" in str(rendered)
    assert "触发:\n晚安" in str(rendered)
    assert "响应:\n做个好梦" in str(rendered)
    get_message_ref.assert_awaited_once_with("90001", expected_kind="response")
    get_group_detail.assert_awaited_once_with(12, response_item_id=300)


def test_parse_batch_approval_reply_supports_ranges() -> None:
    parsed = parse_batch_approval_reply(
        "通过 1 3-4",
        available_response_item_ids=(101, 102, 103, 104),
    )

    assert parsed.selected_action == "approve"
    assert parsed.selected_response_item_ids == (101, 103, 104)
    assert parsed.remaining_action == "noop"


def test_parse_group_detail_delete_reply_accepts_visible_response_item_id() -> None:
    parsed = parse_group_detail_delete_reply(
        "删除 300 301",
        available_response_item_ids=(300, 301),
    )

    assert parsed is not None
    assert parsed.response_item_ids == (300, 301)


def test_parse_group_detail_delete_reply_rejects_non_visible_response_item_id() -> None:
    with pytest.raises(RuleError):
        parse_group_detail_delete_reply(
            "删除 999",
            available_response_item_ids=(300, 301),
        )


def test_parse_group_detail_delete_reply_supports_ranges() -> None:
    parsed = parse_group_detail_delete_reply(
        "删除 300-302 305",
        available_response_item_ids=(300, 301, 302, 305),
    )

    assert parsed is not None
    assert parsed.response_item_ids == (300, 301, 302, 305)


def test_group_detail_page_response_item_ids_uses_current_page_slice() -> None:
    detail = WordbankGroupDetail(
        trigger_group_id=12,
        status="approved",
        enabled=1,
        probability=1.0,
        group_id="20001",
        created_by="10001",
        deleted_at=0,
        trigger_text="晚安",
        trigger_shape=shape_from_text("晚安"),
        trigger_variant_id=120,
        responses=tuple(
            WordbankResponseItemDetail(
                response_item_id=300 + index,
                status="approved",
                enabled=1,
                scope="current_group",
                weight=3,
                rule={},
                group_id="20001",
                created_by="10001",
                approved_by="10002",
                deleted_at=0,
                response_text=f"做个好梦{index}",
                response_shape=shape_from_text(f"做个好梦{index}"),
            )
            for index in range(12)
        ),
        selected_response_item_id=300,
    )

    assert group_detail_page_response_item_ids(detail, page=1) == tuple(range(300, 310))
    assert group_detail_page_response_item_ids(detail, page=2) == (310, 311)


def test_parse_batch_approval_reply_supports_quick_approve_complement() -> None:
    parsed = parse_batch_approval_reply(
        "y 1 3-4",
        available_response_item_ids=(101, 102, 103, 104),
    )

    assert parsed.selected_action == "approve"
    assert parsed.selected_response_item_ids == (101, 103, 104)
    assert parsed.remaining_action == "reject"


def test_parse_batch_approval_reply_supports_quick_reject_suffix() -> None:
    parsed = parse_batch_approval_reply(
        "2 4 n",
        available_response_item_ids=(101, 102, 103, 104),
    )

    assert parsed.selected_action == "reject"
    assert parsed.selected_response_item_ids == (102, 104)
    assert parsed.remaining_action == "approve"


def test_parse_batch_approval_reply_supports_quick_all() -> None:
    approve_all = parse_batch_approval_reply(
        "y",
        available_response_item_ids=(101, 102, 103),
    )
    reject_all = parse_batch_approval_reply(
        "n all",
        available_response_item_ids=(101, 102, 103),
    )

    assert approve_all.selected_response_item_ids == (101, 102, 103)
    assert approve_all.remaining_action == "reject"
    assert reject_all.selected_action == "reject"
    assert reject_all.selected_response_item_ids == (101, 102, 103)
    assert reject_all.remaining_action == "approve"


@pytest.mark.parametrize("text", ["y n", "通过 y 1", "all 1 y", "1 2 3"])
def test_parse_batch_approval_reply_rejects_invalid_mixed_commands(text: str) -> None:
    with pytest.raises(RuleError):
        parse_batch_approval_reply(
            text,
            available_response_item_ids=(101, 102, 103, 104),
        )


async def test_handle_approval_reply_result_supports_pending_batch_reply() -> None:
    approve = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_batch_approval_message()),
            approve_response_item=approve,
        ),
    )
    event = _event_with_reply("通过 1 3-4")

    outcome = await handle_approval_reply_result(
        service,
        event=event,
        text="通过 1 3-4",
        locale="zh-CN",
    )

    assert outcome.approval_message is None
    assert outcome.completed is True
    assert outcome.message is not None
    assert "批量通过完成" in outcome.message
    assert "总数: 3" in outcome.message
    assert "通过: 3" in outcome.message
    assert "失败: 0" in outcome.message
    assert [call.args[0] for call in approve.await_args_list] == [301, 303, 304]
    assert outcome.batch_notices == (
        (301, "approve"),
        (303, "approve"),
        (304, "approve"),
    )


async def test_handle_approval_reply_result_supports_quick_complement_batch_reply() -> (
    None
):
    approve = AsyncMock(return_value=True)
    reject = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_batch_approval_message()),
            approve_response_item=approve,
            reject_response_item=reject,
        ),
    )
    event = _event_with_reply("y 1 3")

    outcome = await handle_approval_reply_result(
        service,
        event=event,
        text="y 1 3",
        locale="zh-CN",
    )

    assert outcome.approval_message is None
    assert outcome.completed is True
    assert outcome.message is not None
    assert "批量审批完成" in outcome.message
    assert "总数: 4" in outcome.message
    assert "通过: 2" in outcome.message
    assert "拒绝: 2" in outcome.message
    assert "失败: 0" in outcome.message
    assert "通过条目: #301, #303" in outcome.message
    assert "拒绝条目: #302, #304" in outcome.message
    assert [call.args[0] for call in approve.await_args_list] == [301, 303]
    assert [call.args[0] for call in reject.await_args_list] == [302, 304]
    assert outcome.batch_notices == (
        (301, "approve"),
        (303, "approve"),
        (302, "reject"),
        (304, "reject"),
    )


async def test_reply_info_renders_image_trigger_and_response() -> None:
    event = _event_with_reply()
    get_message_ref = AsyncMock(return_value=_response_message())
    get_group_detail = AsyncMock(
        return_value=WordbankGroupDetail(
            trigger_group_id=12,
            status="approved",
            enabled=1,
            probability=1.0,
            group_id="20001",
            created_by="10001",
            deleted_at=0,
            trigger_text="[图片:8]",
            trigger_shape=shape_from_image(8),
            trigger_variant_id=120,
            responses=(
                WordbankResponseItemDetail(
                    response_item_id=300,
                    status="approved",
                    enabled=1,
                    scope="current_group",
                    weight=3,
                    rule={},
                    group_id="20001",
                    created_by="10001",
                    approved_by="10002",
                    deleted_at=0,
                    response_text="做个好梦 [图片:7]",
                    response_shape=combine_shapes(
                        shape_from_text("做个好梦"),
                        shape_from_image(7),
                    ),
                ),
            ),
            selected_response_item_id=300,
        )
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=AsyncMock(return_value=b"bytes")),
    )
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=get_message_ref,
            get_group_detail=get_group_detail,
        ),
    )

    message = await handle_reply_command(
        service,
        event=event,
        message=event.message,
        text="详情",
        locale="zh-CN",
        media_service=media_service,
    )
    assert message is not None
    rendered = render_message_plan_input(message)

    assert not isinstance(message, str)
    assert "词条详情 #300" in str(rendered)
    assert "[图片:8]" not in str(rendered)
    assert "[图片:7]" not in str(rendered)
    assert sum(1 for segment in rendered if segment.type == "image") == 2


async def test_reply_history_returns_status_summary() -> None:
    event = _event_with_reply("history")
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_response_message()),
            get_group_detail=AsyncMock(return_value=_group_detail()),
        ),
    )

    message = await handle_reply_command(
        service,
        event=event,
        message=event.message,
        text="history",
        locale="zh-CN",
        media_service=cast(WordbankMediaService, SimpleNamespace()),
    )

    assert isinstance(message, str)
    assert "词条 #300 状态摘要" in message
    assert "管理员审核通过后才会变为已通过" in message
    assert "审批历史:" in message


async def test_reply_delete_and_restore_use_response_item_id() -> None:
    delete_response_item = AsyncMock(return_value=True)
    restore_response_item = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_response_message()),
            delete_response_item=delete_response_item,
            restore_response_item=restore_response_item,
        ),
    )

    deleted_event = _event_with_reply("del response")
    deleted = await handle_reply_command(
        service,
        event=deleted_event,
        message=deleted_event.message,
        text="del response",
        locale="zh-CN",
        media_service=cast(WordbankMediaService, SimpleNamespace()),
    )
    restored_event = _event_with_reply("恢复")
    restored = await handle_reply_command(
        service,
        event=restored_event,
        message=restored_event.message,
        text="恢复",
        locale="zh-CN",
        media_service=cast(WordbankMediaService, SimpleNamespace()),
    )

    assert deleted == "词条 #300 已删除。"
    assert restored == "词条 #300 已恢复。"
    delete_response_item.assert_awaited_once_with(
        300,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    restore_response_item.assert_awaited_once_with(
        300,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )


async def test_reply_trigger_prob_uses_trigger_group_id() -> None:
    event = _event_with_reply("trigger prob 0.3")
    update_trigger_probability = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_response_message()),
            update_trigger_probability=update_trigger_probability,
        ),
    )

    message = await handle_reply_command(
        service,
        event=event,
        message=event.message,
        text="trigger prob 0.3",
        locale="zh-CN",
        media_service=cast(WordbankMediaService, SimpleNamespace()),
    )

    assert message == "trigger group #12 的触发概率已更新为 0.3。"
    update_trigger_probability.assert_awaited_once_with(
        12,
        probability=0.3,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )


async def test_reply_response_weight_uses_response_item_id() -> None:
    event = _event_with_reply("response weight 5")
    update_response_weight = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_response_message()),
            update_response_weight=update_response_weight,
        ),
    )

    message = await handle_reply_command(
        service,
        event=event,
        message=event.message,
        text="response weight 5",
        locale="zh-CN",
        media_service=cast(WordbankMediaService, SimpleNamespace()),
    )

    assert message == "词条 #300 的响应权重已更新为 5。"
    update_response_weight.assert_awaited_once_with(
        300,
        weight=5,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )


async def test_reply_trigger_set_uses_trigger_group_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank.handlers import commands as commands_module

    event = _event_with_reply("trigger set 新触发")
    update_trigger_content = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_response_message()),
            update_trigger_content=update_trigger_content,
        ),
    )

    async def _build_shape(
        _media_service: WordbankMediaService,
        *,
        text: str,
        message: Message,
    ) -> MessageShape:
        assert text == "新触发"
        assert str(message) == "trigger set 新触发"
        return shape_from_text(text)

    monkeypatch.setattr(
        commands_module,
        "build_shape_from_text_and_images",
        _build_shape,
    )

    message = await handle_reply_command(
        service,
        event=event,
        message=text_message("trigger set 新触发"),
        text="trigger set 新触发",
        locale="zh-CN",
        media_service=cast(WordbankMediaService, SimpleNamespace()),
    )

    assert message == "trigger group #12 的触发词已更新，该组响应已重新进入待审核。"
    assert update_trigger_content.await_args is not None
    kwargs = update_trigger_content.await_args.kwargs
    assert kwargs["actor_user_id"] == "10001"
    assert kwargs["can_moderate_group"] is True
    assert kwargs["is_superuser"] is False
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "新触发"


async def test_reply_trigger_set_returns_permission_error_when_denied() -> None:
    event = _event_with_reply("trigger set 新触发")
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_response_message()),
            update_trigger_content=AsyncMock(return_value=False),
        ),
    )

    message = await handle_reply_command(
        service,
        event=event,
        message=text_message("trigger set 新触发"),
        text="trigger set 新触发",
        locale="zh-CN",
        media_service=cast(WordbankMediaService, SimpleNamespace()),
    )

    assert message == "未找到可修改的 trigger group #12，或你没有操作权限。"


async def test_reply_response_set_uses_response_item_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank.handlers import media_helpers

    event = _event_with_reply("response set 新响应")
    update_response_content = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_response_message()),
            update_response_content=update_response_content,
        ),
    )

    async def _build_shape(
        _media_service: WordbankMediaService | None,
        message: Message,
    ) -> MessageShape:
        assert str(message) == "新响应"
        return shape_from_text("新响应 修改")

    monkeypatch.setattr(
        media_helpers,
        "build_response_shape_from_message",
        _build_shape,
    )

    message = await handle_reply_command(
        service,
        event=event,
        message=text_message("response set 新响应"),
        text="response set 新响应",
        locale="zh-CN",
        media_service=cast(WordbankMediaService, SimpleNamespace()),
    )

    assert message == "词条 #300 的响应内容已更新，并重新进入待审核。"
    assert update_response_content.await_args is not None
    kwargs = update_response_content.await_args.kwargs
    assert kwargs["actor_user_id"] == "10001"
    assert kwargs["can_moderate_group"] is True
    assert kwargs["is_superuser"] is False
    assert shape_to_summary_text(kwargs["response_shape"]) == "新响应 修改"


async def test_reply_response_set_returns_permission_error_when_denied() -> None:
    event = _event_with_reply("response set 新响应")
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_response_message()),
            update_response_content=AsyncMock(return_value=False),
        ),
    )

    message = await handle_reply_command(
        service,
        event=event,
        message=text_message("response set 新响应"),
        text="response set 新响应",
        locale="zh-CN",
        media_service=cast(WordbankMediaService, SimpleNamespace()),
    )

    assert message == "未找到可修改的词条 #300，或你没有操作权限。"


async def test_reply_ignores_non_command_text() -> None:
    event = _event_with_reply("随便回一句")
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_response_message()),
        ),
    )

    message = await handle_reply_command(
        service,
        event=event,
        message=event.message,
        text="随便回一句",
        locale="zh-CN",
        media_service=cast(WordbankMediaService, SimpleNamespace()),
    )

    assert message is None


async def test_approval_reply_approves_response_item() -> None:
    get_message_ref = AsyncMock(return_value=_approval_message())
    approve_response_item = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=get_message_ref,
            approve_response_item=approve_response_item,
        ),
    )

    outcome = await handle_approval_reply_result(
        service,
        event=_event_with_reply("y"),
        text="y",
        locale="zh-CN",
    )

    assert outcome.message == "审批已完成：词条 #300 已通过。"
    assert outcome.completed
    approve_response_item.assert_awaited_once()


async def test_approval_reply_uses_private_reply_real_id_when_message_id_differs() -> (
    None
):
    async def _get_message_ref(
        message_id: str,
        *,
        expected_kind: str | None = None,
    ) -> WordbankMessageRefRecord | None:
        assert expected_kind == "approval"
        if message_id == "90001":
            return _approval_message()
        return None

    approve_response_item = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(side_effect=_get_message_ref),
            approve_response_item=approve_response_item,
        ),
    )

    outcome = await handle_approval_reply_result(
        service,
        event=_private_event_with_reply(),
        text="y",
        locale="zh-CN",
    )

    assert outcome.message == "审批已完成：词条 #300 已通过。"
    assert outcome.completed is True
    approve_response_item.assert_awaited_once()


async def test_approval_reply_rejects_response_item() -> None:
    get_message_ref = AsyncMock(return_value=_approval_message())
    reject_response_item = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=get_message_ref,
            reject_response_item=reject_response_item,
        ),
    )

    outcome = await handle_approval_reply_result(
        service,
        event=_event_with_reply("n"),
        text="n",
        locale="zh-CN",
    )

    assert outcome.message == "审批已完成：词条 #300 已拒绝。"
    assert outcome.completed
    reject_response_item.assert_awaited_once()


async def test_approval_reply_prompts_before_overwriting_review() -> None:
    approve_response_item = AsyncMock(return_value=False)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_approval_message()),
            get_response_item_record=AsyncMock(
                return_value=SimpleNamespace(trigger_group_id=12)
            ),
            get_group_detail=AsyncMock(return_value=_reviewed_group_detail()),
            approve_response_item=approve_response_item,
        ),
    )

    outcome = await handle_approval_reply_result(
        service,
        event=_event_with_reply("通过"),
        text="通过",
        locale="zh-CN",
    )

    assert outcome.message is not None
    assert "词条 #300 已经被审批过。" in outcome.message
    assert "审批历史:" in outcome.message
    assert "继续审批将覆盖此前结果。" in outcome.message
    assert "如需继续通过，请继续发送：通过 覆盖" in outcome.message
    assert "如需改为另一结果，请继续发送：拒绝 覆盖" in outcome.message
    assert outcome.completed is False
    assert approve_response_item.await_args is not None
    assert approve_response_item.await_args.kwargs["allow_overwrite"] is False


async def test_approval_reply_supports_overwrite_confirmation() -> None:
    reject_response_item = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_approval_message()),
            reject_response_item=reject_response_item,
        ),
    )

    outcome = await handle_approval_reply_result(
        service,
        event=_event_with_reply("拒绝 覆盖"),
        text="拒绝 覆盖",
        locale="zh-CN",
    )

    assert outcome.message == "审批已完成：词条 #300 已拒绝。"
    assert outcome.completed is True
    assert reject_response_item.await_args is not None
    assert reject_response_item.await_args.kwargs["allow_overwrite"] is True


async def test_approval_reply_ignores_non_command_text() -> None:
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_message_ref=AsyncMock(return_value=_approval_message()),
        ),
    )

    outcome = await handle_approval_reply_result(
        service,
        event=_event_with_reply("随便回一句"),
        text="随便回一句",
        locale="zh-CN",
    )

    assert outcome.message is None
    assert outcome.completed is False
    assert outcome.action == ""


def test_parse_view_reply_for_search_result_requires_group_from_current_page() -> None:
    parsed = parse_view_reply_for_search_result(
        "详情271 2",
        available_group_ids=(271, 300),
    )

    assert parsed.trigger_group_id == 271
    assert parsed.page == 2


def test_parse_view_reply_for_group_detail_supports_navigation_aliases() -> None:
    next_page = parse_view_reply_for_group_detail(
        "下一页",
        trigger_group_id=271,
        current_page=2,
    )
    compact_jump = parse_view_reply_for_group_detail(
        "详情271 4",
        trigger_group_id=271,
        current_page=2,
    )
    page_jump = parse_view_reply_for_group_detail(
        "第5页",
        trigger_group_id=271,
        current_page=2,
    )

    assert next_page.trigger_group_id == 271
    assert next_page.page == 3
    assert compact_jump.page == 4
    assert page_jump.page == 5
