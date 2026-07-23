from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.lib.reply_router import (
    ReplyContextSpec,
    build_reply_message_hash,
    record_reply_context_from_send_result,
    reply_context_repo,
    resolve_reply_target,
)
from tests.plugins.water.helpers import (
    attach_reply_message,
    build_group_message_event,
    build_private_message_event,
)


@pytest.fixture(autouse=True)
async def _clear_reply_contexts() -> None:
    await reply_context_repo.clear_all_contexts()


@pytest.mark.asyncio
async def test_resolve_reply_target_hits_message_id_without_get_msg() -> None:
    await reply_context_repo.upsert_context(
        context_kind="test.route",
        message_id="90001",
        message_hash="hash-1",
        sender_bot_id="99999",
        origin_message_type="private",
        origin_target_id="1",
        source_kind="test",
        payload={"entry_id": 1},
    )
    event = build_private_message_event("y", user_id=1)
    attach_reply_message(event, message_id=90001, user_id=2)
    bot = cast(Any, SimpleNamespace(self_id="99999", call_api=AsyncMock()))

    resolved = await resolve_reply_target(
        bot,
        event,
        allowed_context_kinds=("test.route",),
    )

    assert resolved is not None
    assert resolved.resolved_by == "message_id"
    assert resolved.record.message_id == "90001"
    bot.call_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_and_resolve_reply_target_by_message_hash() -> None:
    get_msg = AsyncMock(
        return_value={
            "message": [{"type": "text", "data": {"text": "审批消息"}}],
            "sender": {"user_id": "99999"},
        }
    )
    bot = cast(Any, SimpleNamespace(self_id="99999", call_api=get_msg))

    await record_reply_context_from_send_result(
        bot,
        send_result={"message_id": 90001},
        context_spec=ReplyContextSpec(
            context_kind="test.route",
            payload={"entry_id": 300},
        ),
        source_kind="test",
        origin_message_type="private",
        origin_target_id="1",
        fallback_message="fallback",
    )

    event = build_private_message_event("y", user_id=1)
    attach_reply_message(event, message_id=123, user_id=2)
    assert event.reply is not None
    event.reply.real_id = 456
    bot.call_api = AsyncMock(
        return_value={
            "message": [{"type": "text", "data": {"text": "审批消息"}}],
            "sender": {"user_id": "99999"},
        }
    )

    resolved = await resolve_reply_target(
        bot,
        event,
        allowed_context_kinds=("test.route",),
    )

    assert resolved is not None
    assert resolved.resolved_by == "message_hash"
    assert resolved.record.message_id == "90001"
    assert resolved.record.payload["entry_id"] == 300


@pytest.mark.asyncio
async def test_resolve_reply_target_rejects_sender_mismatch_on_hash_fallback() -> None:
    message_hash = build_reply_message_hash("审批消息", sender_bot_id="99999")
    await reply_context_repo.upsert_context(
        context_kind="test.route",
        message_id="90001",
        message_hash=message_hash,
        sender_bot_id="99999",
        origin_message_type="private",
        origin_target_id="1",
        source_kind="test",
        payload={"entry_id": 1},
    )
    event = build_private_message_event("y", user_id=1)
    attach_reply_message(event, message_id=123, user_id=2)
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(
                return_value={
                    "message": [{"type": "text", "data": {"text": "审批消息"}}],
                    "sender": {"user_id": "10000"},
                }
            ),
        ),
    )

    resolved = await resolve_reply_target(
        bot,
        event,
        allowed_context_kinds=("test.route",),
    )

    assert resolved is None


@pytest.mark.asyncio
async def test_resolve_reply_target_dedupes_same_payload_hash_candidates() -> None:
    message_hash = build_reply_message_hash("审批消息", sender_bot_id="99999")
    await reply_context_repo.upsert_context(
        context_kind="test.route",
        message_id="90001",
        message_hash=message_hash,
        sender_bot_id="99999",
        origin_message_type="private",
        origin_target_id="1",
        source_kind="test",
        payload={"entry_id": 300},
    )
    await reply_context_repo.upsert_context(
        context_kind="test.route",
        message_id="90002",
        message_hash=message_hash,
        sender_bot_id="99999",
        origin_message_type="private",
        origin_target_id="2",
        source_kind="test",
        payload={"entry_id": 300},
    )
    event = build_group_message_event("y", role="admin")
    attach_reply_message(event, message_id=123, user_id=2)
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(
                return_value={
                    "message": [{"type": "text", "data": {"text": "审批消息"}}],
                    "sender": {"user_id": "99999"},
                }
            ),
        ),
    )

    resolved = await resolve_reply_target(
        bot,
        event,
        allowed_context_kinds=("test.route",),
    )

    assert resolved is not None
    assert resolved.resolved_by == "message_hash"
    assert resolved.record.payload["entry_id"] == 300
