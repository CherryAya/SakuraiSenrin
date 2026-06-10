from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.event import GroupMessageEvent

from src.plugins.wordbank.database.types import (
    WordbankApprovalMessageRecord,
    WordbankEntryDetail,
    WordbankResponseMessageRecord,
)
from src.plugins.wordbank.handlers.reply import (
    handle_approval_reply,
    handle_approval_reply_result,
    handle_reply_command,
)
from src.plugins.wordbank.services.core import WordbankService
from tests.plugins.water.helpers import build_group_message_event


def _event_with_reply(message: str = "详情") -> GroupMessageEvent:
    event = build_group_message_event(message, role="admin")
    setattr(event, "reply", SimpleNamespace(message_id=90001))
    return event


def _response_message() -> WordbankResponseMessageRecord:
    return WordbankResponseMessageRecord(
        message_id="90001",
        entry_id=12,
        trigger_id=120,
        response_id=300,
        group_id="20001",
        user_id="10001",
        message_type="text",
    )


def _approval_message() -> WordbankApprovalMessageRecord:
    return WordbankApprovalMessageRecord(
        message_id="90001",
        entry_id=12,
        group_id="20001",
        user_id="10001",
        source_message_id="1",
        message_type="approval",
    )


def _entry_detail() -> WordbankEntryDetail:
    return WordbankEntryDetail(
        entry_id=12,
        status="approved",
        enabled=1,
        scope="current_group",
        probability=1.0,
        weight=3,
        group_id="20001",
        created_by="10001",
        deleted_at=0,
        trigger_text="晚安",
        trigger_mode="contains",
        response_text="做个好梦",
    )


async def test_reply_info_formats_entry_detail() -> None:
    get_response_message = AsyncMock(return_value=_response_message())
    get_entry_detail = AsyncMock(return_value=_entry_detail())
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_response_message=get_response_message,
            get_entry_detail=get_entry_detail,
        ),
    )

    message = await handle_reply_command(
        service,
        event=_event_with_reply(),
        text="详情",
        locale="zh-CN",
    )

    assert "词条详情 #12" in message
    assert "触发: [contains] 晚安" in message
    assert "响应: 做个好梦" in message
    get_response_message.assert_awaited_once_with("90001")
    get_entry_detail.assert_awaited_once_with(12, trigger_id=120, response_id=300)


async def test_reply_history_returns_status_summary() -> None:
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_response_message=AsyncMock(return_value=_response_message()),
            get_entry_detail=AsyncMock(return_value=_entry_detail()),
        ),
    )

    message = await handle_reply_command(
        service,
        event=_event_with_reply("history"),
        text="history",
        locale="zh-CN",
    )

    assert "词条 #12 状态摘要" in message
    assert "管理员审核通过后才变为 approved" in message


async def test_reply_delete_reuses_entry_delete_permissions() -> None:
    delete_entry = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_response_message=AsyncMock(return_value=_response_message()),
            delete_entry=delete_entry,
            request_delete_vote=AsyncMock(),
        ),
    )

    message = await handle_reply_command(
        service,
        event=_event_with_reply("del response"),
        text="del response",
        locale="zh-CN",
    )

    assert message == "词条 #12 已删除。"
    delete_entry.assert_awaited_once_with(
        12,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )


async def test_reply_restore_reuses_entry_restore_permissions() -> None:
    restore_entry = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_response_message=AsyncMock(return_value=_response_message()),
            restore_entry=restore_entry,
        ),
    )

    message = await handle_reply_command(
        service,
        event=_event_with_reply("恢复"),
        text="恢复",
        locale="zh-CN",
    )

    assert message == "词条 #12 已恢复。"
    restore_entry.assert_awaited_once_with(
        12,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )


async def test_reply_target_missing_and_unknown_message() -> None:
    missing_reply_service = cast(
        WordbankService,
        SimpleNamespace(get_response_message=AsyncMock()),
    )
    no_reply_event = build_group_message_event("详情")

    assert (
        await handle_reply_command(
            missing_reply_service,
            event=no_reply_event,
            text="详情",
            locale="zh-CN",
        )
        == "请回复一条词库自动回复后再使用该快捷命令。"
    )

    get_response_message = AsyncMock(return_value=None)
    unknown_service = cast(
        WordbankService,
        SimpleNamespace(get_response_message=get_response_message),
    )

    assert (
        await handle_reply_command(
            unknown_service,
            event=_event_with_reply(),
            text="详情",
            locale="zh-CN",
        )
        == "未找到消息 90001 对应的词条记录。"
    )


async def test_approval_reply_approves_mapped_pending_entry() -> None:
    get_approval_message = AsyncMock(return_value=_approval_message())
    approve_entry = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_approval_message=get_approval_message,
            approve_entry=approve_entry,
        ),
    )

    outcome = await handle_approval_reply_result(
        service,
        event=_event_with_reply("y"),
        text="y",
        locale="zh-CN",
    )

    assert outcome.message == "审批已完成：词条 #12 已通过。"
    assert outcome.completed
    assert outcome.action == "approve"
    assert outcome.approval_message == _approval_message()
    get_approval_message.assert_awaited_once_with("90001")
    approve_entry.assert_awaited_once_with(
        12,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )


async def test_approval_reply_rejects_mapped_pending_entry() -> None:
    reject_entry = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(
            get_approval_message=AsyncMock(return_value=_approval_message()),
            reject_entry=reject_entry,
        ),
    )

    message = await handle_approval_reply(
        service,
        event=_event_with_reply("拒绝"),
        text="拒绝",
        locale="zh-CN",
    )

    assert message == "审批已完成：词条 #12 已拒绝。"
    reject_entry.assert_awaited_once_with(
        12,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )


async def test_approval_reply_unknown_message_reports_target_not_found() -> None:
    service = cast(
        WordbankService,
        SimpleNamespace(get_approval_message=AsyncMock(return_value=None)),
    )

    message = await handle_approval_reply(
        service,
        event=_event_with_reply("y"),
        text="y",
        locale="zh-CN",
    )

    assert message == "未找到消息 90001 对应的待审核词条。"
