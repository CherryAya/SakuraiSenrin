from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, call

import pytest

from src.lib.message_assets import (
    MessageAsset,
    MessageAssetRecord,
    message_asset_db,
    message_asset_repo,
)
from src.lib.message_delivery import (
    DEFAULT_FORWARD_REUSE_POLICY,
    DeliveryTarget,
    _try_forward_single_message,
    build_forward_batch_descriptor,
    deliver_forward_messages,
)
from src.lib.messages import text_message
from tests.plugins.water.helpers import (
    build_group_message_event,
    build_private_message_event,
)


class _FakeScalarResult:
    def __init__(self, row: MessageAsset | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> MessageAsset | None:
        return self._row


class _FakeReadSession:
    def __init__(self, row: MessageAsset | None) -> None:
        self._row = row

    async def execute(self, stmt: object) -> _FakeScalarResult:
        return _FakeScalarResult(self._row)


def _build_asset_record(
    *,
    asset_key: str,
    asset_kind: str,
    message_id: str,
    origin_message_type: str = "group",
    origin_target_id: str = "20001",
    forward_context_key: str = "",
    forward_sort_key: int = 0,
    created_at: int = 1,
    updated_at: int = 1,
) -> MessageAssetRecord:
    return MessageAssetRecord(
        asset_key=asset_key,
        content_hash=asset_key,
        asset_kind=cast(Any, asset_kind),
        source_kind="help",
        message_id=message_id,
        sender_bot_id="99999",
        origin_message_type=origin_message_type,
        origin_target_id=origin_target_id,
        message_shape_kind="plain",
        forward_context_key=forward_context_key,
        forward_sort_key=forward_sort_key,
        status="active",
        last_verify_error="",
        created_at=created_at,
        updated_at=updated_at,
    )


@pytest.mark.asyncio
async def test_get_asset_filters_stale_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = MessageAsset(
        asset_key="asset-key",
        content_hash="hash",
        asset_kind="single_message",
        source_kind="help",
        message_id="12345",
        sender_bot_id="99999",
        origin_message_type="private",
        origin_target_id="99999",
        message_shape_kind="plain",
        forward_context_key="",
        forward_sort_key=0,
        status="stale",
        last_verify_error="boom",
        created_at=1,
        updated_at=2,
    )

    @asynccontextmanager
    async def fake_read_session() -> AsyncIterator[_FakeReadSession]:
        yield _FakeReadSession(row)

    monkeypatch.setattr(message_asset_db, "read_session", fake_read_session)

    asset = await message_asset_repo.get_asset("asset-key")

    assert asset is None


@pytest.mark.asyncio
async def test_deliver_forward_messages_reuses_bundle_on_exact_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999", call_api=AsyncMock(return_value={"message_id": 88})
        ),
    )
    event = build_group_message_event("#help wordbank")
    messages = (text_message("summary"), text_message("feature"))
    batch = build_forward_batch_descriptor(
        messages,
        policy=DEFAULT_FORWARD_REUSE_POLICY,
    )

    get_forward_bundle_asset = AsyncMock(
        return_value=_build_asset_record(
            asset_key=batch.context_key,
            asset_kind="forward_bundle",
            message_id="5566",
            origin_message_type="group",
            origin_target_id="20001",
            forward_context_key=batch.context_key,
        )
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_bundle_asset",
        get_forward_bundle_asset,
    )
    get_forward_node_asset = AsyncMock()
    mark_stale = AsyncMock()
    upsert_forward_bundle_asset = AsyncMock()
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_node_asset",
        get_forward_node_asset,
    )
    monkeypatch.setattr(
        message_asset_repo,
        "mark_stale",
        mark_stale,
    )
    monkeypatch.setattr(
        message_asset_repo,
        "upsert_forward_bundle_asset",
        upsert_forward_bundle_asset,
    )

    await deliver_forward_messages(
        bot,
        event,
        messages,
        source_kind="help",
        fallback_nickname="fallback",
    )

    assert bot.call_api.await_count == 1
    assert bot.call_api.await_args.args == ("forward_group_single_msg",)
    assert bot.call_api.await_args.kwargs == {
        "message_id": "5566",
        "group_id": "20001",
    }
    get_forward_bundle_asset.assert_awaited_once_with(batch.context_key)
    get_forward_node_asset.assert_not_awaited()
    mark_stale.assert_not_awaited()
    upsert_forward_bundle_asset.assert_not_awaited()


@pytest.mark.asyncio
async def test_try_forward_single_message_prefers_friend_api_for_private_target() -> (
    None
):
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999", call_api=AsyncMock(return_value={"message_id": 88})
        ),
    )

    await _try_forward_single_message(
        bot,
        target=DeliveryTarget(kind="private", target_id="10001"),
        message_id="5566",
        origin_message_type="group",
    )

    assert bot.call_api.await_count == 1
    assert bot.call_api.await_args.args == ("forward_friend_single_msg",)
    assert bot.call_api.await_args.kwargs == {
        "message_id": "5566",
        "user_id": "10001",
    }


@pytest.mark.asyncio
async def test_try_forward_single_message_prefers_group_api_for_group_target() -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999", call_api=AsyncMock(return_value={"message_id": 89})
        ),
    )

    await _try_forward_single_message(
        bot,
        target=DeliveryTarget(kind="group", target_id="20001"),
        message_id="7788",
        origin_message_type="private",
    )

    assert bot.call_api.await_count == 1
    assert bot.call_api.await_args.args == ("forward_group_single_msg",)
    assert bot.call_api.await_args.kwargs == {
        "message_id": "7788",
        "group_id": "20001",
    }


@pytest.mark.asyncio
async def test_deliver_forward_messages_falls_back_when_bundle_reuse_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(
                side_effect=[
                    RuntimeError("bundle-forward-group-failed"),
                    RuntimeError("bundle-forward-friend-failed"),
                    {"message_id": 101},
                    {"message_id": 102},
                    {"nickname": "测试机器人"},
                    {"message_id": 9001},
                ]
            ),
        ),
    )
    event = build_group_message_event("#help wordbank")
    messages = (text_message("summary"), text_message("feature"))
    batch = build_forward_batch_descriptor(
        messages,
        policy=DEFAULT_FORWARD_REUSE_POLICY,
    )

    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_bundle_asset",
        AsyncMock(
            return_value=_build_asset_record(
                asset_key=batch.context_key,
                asset_kind="forward_bundle",
                message_id="5566",
                origin_message_type="group",
                origin_target_id="20001",
                forward_context_key=batch.context_key,
            )
        ),
    )
    get_forward_node_asset = AsyncMock(return_value=None)
    mark_stale = AsyncMock()
    upsert_forward_bundle_asset = AsyncMock()
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_node_asset",
        get_forward_node_asset,
    )
    monkeypatch.setattr(message_asset_repo, "mark_stale", mark_stale)
    monkeypatch.setattr(
        message_asset_repo,
        "upsert_forward_bundle_asset",
        upsert_forward_bundle_asset,
    )

    await deliver_forward_messages(
        bot,
        event,
        messages,
        source_kind="help",
        fallback_nickname="fallback",
    )

    api_calls = [call.args[0] for call in bot.call_api.await_args_list]
    assert api_calls == [
        "forward_group_single_msg",
        "forward_friend_single_msg",
        "send_private_msg",
        "send_private_msg",
        "get_login_info",
        "send_group_forward_msg",
    ]
    mark_stale.assert_awaited_once_with(
        batch.context_key,
        last_verify_error="bundle-forward-friend-failed",
    )
    get_forward_node_asset.assert_awaited_once_with(batch.node_asset_keys[0])
    upsert_forward_bundle_asset.assert_awaited_once_with(
        asset_key=batch.context_key,
        content_hash=batch.context_key,
        source_kind="help",
        message_id="9001",
        sender_bot_id="99999",
        origin_message_type="group",
        origin_target_id="20001",
        forward_context_key=batch.context_key,
    )


@pytest.mark.asyncio
async def test_deliver_forward_messages_reordered_batch_resets_prefix_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(
                side_effect=[
                    {"message_id": 301},
                    {"message_id": 302},
                    {"nickname": "测试机器人"},
                    {"message_id": 9004},
                ]
            ),
        ),
    )
    original_messages = (text_message("summary"), text_message("feature"))
    reordered_messages = (text_message("feature"), text_message("summary"))
    original_batch = build_forward_batch_descriptor(
        original_messages,
        policy=DEFAULT_FORWARD_REUSE_POLICY,
    )
    reordered_batch = build_forward_batch_descriptor(
        reordered_messages,
        policy=DEFAULT_FORWARD_REUSE_POLICY,
    )
    assert original_batch.context_key != reordered_batch.context_key

    get_forward_bundle_asset = AsyncMock(
        side_effect=lambda asset_key: (
            _build_asset_record(
                asset_key=asset_key,
                asset_kind="forward_bundle",
                message_id="bundle-old",
                origin_message_type="group",
                origin_target_id="20001",
                forward_context_key=asset_key,
            )
            if asset_key == original_batch.context_key
            else None
        )
    )
    get_forward_node_asset = AsyncMock(
        side_effect=lambda asset_key: (
            _build_asset_record(
                asset_key=asset_key,
                asset_kind="forward_node",
                message_id="node-old",
                origin_message_type="private",
                origin_target_id="99999",
                forward_context_key=original_batch.node_context_keys[0],
                forward_sort_key=0,
            )
            if asset_key == original_batch.node_asset_keys[0]
            else None
        )
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_bundle_asset",
        get_forward_bundle_asset,
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_node_asset",
        get_forward_node_asset,
    )
    monkeypatch.setattr(message_asset_repo, "mark_stale", AsyncMock())
    upsert_forward_bundle_asset = AsyncMock()
    monkeypatch.setattr(
        message_asset_repo,
        "upsert_forward_bundle_asset",
        upsert_forward_bundle_asset,
    )

    await deliver_forward_messages(
        bot,
        build_group_message_event("#help wordbank"),
        reordered_messages,
        source_kind="help",
        fallback_nickname="fallback",
    )

    api_calls = [call.args[0] for call in bot.call_api.await_args_list]
    assert api_calls == [
        "send_private_msg",
        "send_private_msg",
        "get_login_info",
        "send_group_forward_msg",
    ]
    get_forward_bundle_asset.assert_awaited_once_with(reordered_batch.context_key)
    get_forward_node_asset.assert_awaited_once_with(reordered_batch.node_asset_keys[0])
    first_stage_call = bot.call_api.await_args_list[0]
    second_stage_call = bot.call_api.await_args_list[1]
    assert str(first_stage_call.kwargs["message"]) == "feature"
    assert str(second_stage_call.kwargs["message"]) == "summary"
    upsert_forward_bundle_asset.assert_awaited_once_with(
        asset_key=reordered_batch.context_key,
        content_hash=reordered_batch.context_key,
        source_kind="help",
        message_id="9004",
        sender_bot_id="99999",
        origin_message_type="group",
        origin_target_id="20001",
        forward_context_key=reordered_batch.context_key,
    )


@pytest.mark.asyncio
async def test_deliver_forward_messages_only_restages_tail_after_prefix_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(
                side_effect=[
                    {"message_id": 202},
                    {"nickname": "测试机器人"},
                    {"message_id": 9002},
                ]
            ),
        ),
    )
    event = build_group_message_event("#help wordbank")
    messages = (text_message("summary"), text_message("feature"))
    batch = build_forward_batch_descriptor(
        messages,
        policy=DEFAULT_FORWARD_REUSE_POLICY,
    )

    get_forward_node_asset = AsyncMock(
        side_effect=[
            _build_asset_record(
                asset_key=batch.node_asset_keys[0],
                asset_kind="forward_node",
                message_id="node-1",
                origin_message_type="private",
                origin_target_id="99999",
                forward_context_key=batch.node_context_keys[0],
                forward_sort_key=0,
            ),
            None,
        ]
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_bundle_asset",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_node_asset",
        get_forward_node_asset,
    )
    monkeypatch.setattr(message_asset_repo, "mark_stale", AsyncMock())
    upsert_forward_bundle_asset = AsyncMock()
    monkeypatch.setattr(
        message_asset_repo,
        "upsert_forward_bundle_asset",
        upsert_forward_bundle_asset,
    )

    await deliver_forward_messages(
        bot,
        event,
        messages,
        source_kind="help",
        fallback_nickname="fallback",
    )

    api_calls = [call.args[0] for call in bot.call_api.await_args_list]
    assert api_calls == [
        "send_private_msg",
        "get_login_info",
        "send_group_forward_msg",
    ]
    stage_call = bot.call_api.await_args_list[0]
    assert stage_call.kwargs["user_id"] == 99999
    assert str(stage_call.kwargs["message"]) == "feature"
    get_forward_node_asset.assert_has_awaits(
        [
            call(batch.node_asset_keys[0]),
            call(batch.node_asset_keys[1]),
        ]
    )
    upsert_forward_bundle_asset.assert_awaited_once_with(
        asset_key=batch.context_key,
        content_hash=batch.context_key,
        source_kind="help",
        message_id="9002",
        sender_bot_id="99999",
        origin_message_type="group",
        origin_target_id="20001",
        forward_context_key=batch.context_key,
    )


@pytest.mark.asyncio
async def test_deliver_forward_messages_uses_fallback_nickname_for_private_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_api(api: str, **kwargs: Any) -> Any:
        if api == "get_login_info":
            raise RuntimeError("boom")
        if api == "send_private_forward_msg":
            return {"message_id": 321}
        return {"message_id": 123}

    bot = cast(
        Any,
        SimpleNamespace(self_id="99999", call_api=AsyncMock(side_effect=fake_call_api)),
    )
    event = build_private_message_event("#help wordbank")
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_bundle_asset",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_node_asset",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(message_asset_repo, "mark_stale", AsyncMock())
    monkeypatch.setattr(
        message_asset_repo,
        "upsert_forward_bundle_asset",
        AsyncMock(),
    )

    await deliver_forward_messages(
        bot,
        event,
        (text_message("summary"),),
        source_kind="help",
        fallback_nickname="fallback",
    )

    assert bot.call_api.await_args_list[-1].args[0] == "send_private_forward_msg"
    nodes = bot.call_api.await_args_list[-1].kwargs["messages"]
    assert nodes[0].data["nickname"] == "fallback"
    assert str(nodes[0].data["content"]) == "summary"


@pytest.mark.asyncio
async def test_deliver_forward_messages_logs_serialized_payload_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(
                side_effect=[
                    {"message_id": 101},
                    {"nickname": "测试机器人"},
                    {"message_id": 9003},
                ]
            ),
        ),
    )
    event = build_group_message_event("#help wordbank")
    debug = Mock()

    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_bundle_asset",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_node_asset",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(message_asset_repo, "mark_stale", AsyncMock())
    monkeypatch.setattr(
        message_asset_repo,
        "upsert_forward_bundle_asset",
        AsyncMock(),
    )
    monkeypatch.setattr("src.lib.onebot_forward.logger.debug", debug)

    await deliver_forward_messages(
        bot,
        event,
        (text_message("summary"),),
        source_kind="help",
        fallback_nickname="fallback",
    )

    merged_logs = [
        call.args[0]
        for call in debug.call_args_list
        if call.args and "send merged forward payload" in call.args[0]
    ]
    assert merged_logs
    assert "bundle_asset_key=" in merged_logs[0]
    assert "reuse_mode=rebuild_all" in merged_logs[0]
    assert "'nickname': '测试机器人'" in merged_logs[0]
    assert (
        "'content': [{'data': {'text': 'summary'}, 'type': 'text'}]" in merged_logs[0]
    )
