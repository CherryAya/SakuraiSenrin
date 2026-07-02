from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.lib.message_assets import MessageAsset, message_asset_db, message_asset_repo
from src.lib.message_delivery import deliver_forward_messages
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
async def test_deliver_forward_messages_uses_node_custom_direct_group_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(side_effect=[{"nickname": "测试机器人"}, None]),
        ),
    )
    event = build_group_message_event("#help wordbank")
    get_asset = AsyncMock()
    upsert_asset = AsyncMock()
    monkeypatch.setattr(message_asset_repo, "get_asset", get_asset)
    monkeypatch.setattr(message_asset_repo, "upsert_asset", upsert_asset)

    await deliver_forward_messages(
        bot,
        event,
        (text_message("summary"), text_message("feature")),
        source_kind="help",
        fallback_nickname="fallback",
    )

    assert bot.call_api.await_args_list[0].args[0] == "get_login_info"
    assert bot.call_api.await_args_list[1].args[0] == "send_group_forward_msg"
    nodes = bot.call_api.await_args_list[1].kwargs["messages"]
    assert len(nodes) == 2
    assert all(node.type == "node" for node in nodes)
    assert nodes[0].data["nickname"] == "测试机器人"
    assert str(nodes[0].data["content"]) == "summary"
    get_asset.assert_not_awaited()
    upsert_asset.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_forward_messages_uses_fallback_nickname_for_private_path() -> (
    None
):
    async def fake_call_api(api: str, **kwargs: Any) -> Any:
        if api == "get_login_info":
            raise RuntimeError("boom")
        return None

    bot = cast(
        Any,
        SimpleNamespace(self_id="99999", call_api=AsyncMock(side_effect=fake_call_api)),
    )
    event = build_private_message_event("#help wordbank")

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
