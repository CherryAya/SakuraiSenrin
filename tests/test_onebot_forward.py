from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.lib.messages import text_message
from src.lib.onebot_forward import (
    build_custom_forward_nodes,
    resolve_forward_sender,
    send_custom_forward,
)
from tests.plugins.water.helpers import (
    build_group_message_event,
    build_private_message_event,
)


@pytest.mark.asyncio
async def test_resolve_forward_sender_prefers_login_nickname() -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(return_value={"nickname": "测试机器人"}),
        ),
    )

    sender = await resolve_forward_sender(bot, fallback_nickname="fallback")

    assert sender == (99999, "测试机器人")


@pytest.mark.asyncio
async def test_resolve_forward_sender_falls_back_when_login_info_fails() -> None:
    async def fake_call_api(api: str, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    bot = cast(
        Any,
        SimpleNamespace(self_id="99999", call_api=AsyncMock(side_effect=fake_call_api)),
    )

    sender = await resolve_forward_sender(bot, fallback_nickname="fallback")

    assert sender == (99999, "fallback")


def test_build_custom_forward_nodes_uses_node_custom_segments() -> None:
    nodes = build_custom_forward_nodes(
        (text_message("A"), text_message("B")),
        user_id=99999,
        nickname="测试机器人",
    )

    assert len(nodes) == 2
    assert all(node.type == "node" for node in nodes)
    assert nodes[0].data["user_id"] == "99999"
    assert nodes[0].data["nickname"] == "测试机器人"
    assert str(nodes[0].data["content"]) == "A"


@pytest.mark.asyncio
async def test_send_custom_forward_uses_group_forward_api() -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(side_effect=[{"nickname": "测试机器人"}, None]),
        ),
    )
    event = build_group_message_event("#help wordbank")

    await send_custom_forward(
        bot,
        event,
        (text_message("A"), text_message("B")),
        fallback_nickname="fallback",
    )

    assert bot.call_api.await_count == 2
    assert bot.call_api.await_args_list[1].args[0] == "send_group_forward_msg"
    assert bot.call_api.await_args_list[1].kwargs["group_id"] == 20001
    nodes = bot.call_api.await_args_list[1].kwargs["messages"]
    assert len(nodes) == 2
    assert all(node.type == "node" for node in nodes)


@pytest.mark.asyncio
async def test_send_custom_forward_uses_private_forward_api() -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(side_effect=[{"nickname": "测试机器人"}, None]),
        ),
    )
    event = build_private_message_event("#help wordbank")

    await send_custom_forward(
        bot,
        event,
        (text_message("A"), text_message("B")),
        fallback_nickname="fallback",
    )

    assert bot.call_api.await_count == 2
    assert bot.call_api.await_args_list[1].args[0] == "send_private_forward_msg"
    assert bot.call_api.await_args_list[1].kwargs["user_id"] == 10001
