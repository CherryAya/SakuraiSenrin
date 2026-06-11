from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.event import GroupMessageEvent

from src.plugins.wordbank.database.types import (
    WordbankApprovalMessageRecord,
    WordbankGroupDetail,
    WordbankResponseItemDetail,
    WordbankResponseMessageRecord,
)
from src.plugins.wordbank.handlers.reply import (
    handle_approval_reply_result,
    handle_reply_command,
    parse_view_reply_for_group_detail,
    parse_view_reply_for_search_result,
)
from src.plugins.wordbank.message_model import shape_from_text
from src.plugins.wordbank.services.core import WordbankService
from tests.plugins.water.helpers import build_group_message_event


def _event_with_reply(message: str = "详情") -> GroupMessageEvent:
    event = build_group_message_event(message, role="admin")
    setattr(event, "reply", SimpleNamespace(message_id=90001))
    return event


def _response_message() -> WordbankResponseMessageRecord:
    return WordbankResponseMessageRecord(
        message_id="90001",
        trigger_group_id=12,
        trigger_variant_id=120,
        response_item_id=300,
        group_id="20001",
        user_id="10001",
        message_type="text",
    )


def _approval_message() -> WordbankApprovalMessageRecord:
    return WordbankApprovalMessageRecord(
        message_id="90001",
        trigger_group_id=12,
        response_item_id=300,
        group_id="20001",
        user_id="10001",
        source_message_id="1",
        message_type="approval",
    )


def _group_detail() -> WordbankGroupDetail:
    return WordbankGroupDetail(
        trigger_group_id=12,
        status="approved",
        enabled=1,
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
                probability=1.0,
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


async def test_reply_info_formats_selected_response_item_detail() -> None:
    get_response_message = AsyncMock(return_value=_response_message())
    get_group_detail = AsyncMock(return_value=_group_detail())
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_response_message=get_response_message,
            get_group_detail=get_group_detail,
        ),
    )

    message = await handle_reply_command(
        service,
        event=_event_with_reply(),
        text="详情",
        locale="zh-CN",
    )

    assert "词条详情 #300" in message
    assert "触发: 晚安" in message
    assert "响应: 做个好梦" in message
    get_response_message.assert_awaited_once_with("90001")
    get_group_detail.assert_awaited_once_with(12, response_item_id=300)


async def test_reply_history_returns_status_summary() -> None:
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_response_message=AsyncMock(return_value=_response_message()),
            get_group_detail=AsyncMock(return_value=_group_detail()),
        ),
    )

    message = await handle_reply_command(
        service,
        event=_event_with_reply("history"),
        text="history",
        locale="zh-CN",
    )

    assert "词条 #300 状态摘要" in message
    assert "管理员审核通过后才变为 approved" in message


async def test_reply_delete_and_restore_use_response_item_id() -> None:
    delete_response_item = AsyncMock(return_value=True)
    restore_response_item = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_response_message=AsyncMock(return_value=_response_message()),
            delete_response_item=delete_response_item,
            restore_response_item=restore_response_item,
            request_delete_vote=AsyncMock(),
        ),
    )

    deleted = await handle_reply_command(
        service,
        event=_event_with_reply("del response"),
        text="del response",
        locale="zh-CN",
    )
    restored = await handle_reply_command(
        service,
        event=_event_with_reply("恢复"),
        text="恢复",
        locale="zh-CN",
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


async def test_approval_reply_approves_response_item() -> None:
    get_approval_message = AsyncMock(return_value=_approval_message())
    approve_response_item = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_approval_message=get_approval_message,
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


def test_parse_view_reply_for_search_result_requires_group_from_current_page() -> None:
    parsed = parse_view_reply_for_search_result(
        "详情 271 2",
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
    page_jump = parse_view_reply_for_group_detail(
        "第5页",
        trigger_group_id=271,
        current_page=2,
    )

    assert next_page.trigger_group_id == 271
    assert next_page.page == 3
    assert page_jump.page == 5
