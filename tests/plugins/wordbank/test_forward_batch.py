from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
import pytest

from src.plugins.wordbank.forward_batch import (
    FORWARD_BATCH_MAX_DEPTH,
    build_forward_batch_payload,
    build_response_input_payload,
    extract_forward_source_message_id,
    is_forward_input,
)
from src.plugins.wordbank.handlers import media_helpers
from src.plugins.wordbank.message_model import shape_to_summary_text
from src.plugins.wordbank.services.errors import WordbankUserError
from tests.plugins.water.helpers import (
    attach_reply_message,
    build_group_message_event,
    build_private_message_event,
)


def test_forward_input_accepts_direct_forward_message() -> None:
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])

    assert is_forward_input(event) is True
    assert extract_forward_source_message_id(event) == "7657605421581295285"


def test_forward_input_accepts_direct_forward_message_in_private() -> None:
    event = build_private_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])

    assert is_forward_input(event) is True
    assert extract_forward_source_message_id(event) == "7657605421581295285"


def test_forward_input_accepts_reply_forward_message() -> None:
    event = build_group_message_event("response", message_id=1)
    attach_reply_message(event, MessageSegment.forward("7657605421581295285"))

    assert is_forward_input(event) is True
    assert extract_forward_source_message_id(event) == "7657605421581295285"


def test_forward_input_preserves_nondigit_forward_message_id() -> None:
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("forward-msg-abc")])

    assert is_forward_input(event) is True
    assert extract_forward_source_message_id(event) == "forward-msg-abc"


@pytest.mark.asyncio
async def test_build_forward_batch_payload_fetches_forward_msg_content() -> None:
    call_api = AsyncMock(
        return_value={
            "messages": [
                Message([MessageSegment.text("第一条")]),
                Message([MessageSegment.text("第二条")]),
            ]
        }
    )
    bot = cast(Bot, SimpleNamespace(call_api=call_api))
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])

    payload = await build_forward_batch_payload(
        bot,
        event,
        media_service=SimpleNamespace(),
    )

    call_api.assert_awaited_once_with(
        "get_forward_msg",
        message_id="7657605421581295285",
    )
    assert payload.source_message_id == "7657605421581295285"
    assert payload.node_count == 2
    assert not payload.whole_shape.is_empty()
    assert len(payload.split_shapes) == 2


@pytest.mark.asyncio
async def test_build_forward_batch_payload_falls_back_to_int_message_id() -> None:
    call_api = AsyncMock(
        side_effect=[
            RuntimeError("string id failed"),
            {
                "messages": [
                    Message([MessageSegment.text("第一条")]),
                    Message([MessageSegment.text("第二条")]),
                ]
            },
        ]
    )
    bot = cast(Bot, SimpleNamespace(call_api=call_api))
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])

    payload = await build_forward_batch_payload(
        bot,
        event,
        media_service=SimpleNamespace(),
    )

    assert call_api.await_args_list[0].kwargs == {
        "message_id": "7657605421581295285",
    }
    assert call_api.await_args_list[1].kwargs == {
        "message_id": 7657605421581295285,
    }
    assert payload.source_message_id == "7657605421581295285"
    assert payload.node_count == 2


@pytest.mark.asyncio
async def test_build_forward_batch_payload_accepts_nested_onebot_nodes() -> None:
    call_api = AsyncMock(
        return_value={
            "data": {
                "messages": [
                    {
                        "content": [
                            {
                                "type": "text",
                                "data": {"text": "第一条"},
                            }
                        ]
                    },
                    {
                        "data": {
                            "message": {
                                "type": "text",
                                "data": {"text": "第二条"},
                            }
                        }
                    },
                ]
            }
        }
    )
    bot = cast(Bot, SimpleNamespace(call_api=call_api))
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])

    payload = await build_forward_batch_payload(
        bot,
        event,
        media_service=SimpleNamespace(),
    )

    assert payload.node_count == 2
    assert len(payload.split_shapes) == 2
    assert shape_to_summary_text(payload.split_shapes[0]) == "第一条"
    assert shape_to_summary_text(payload.split_shapes[1]) == "第二条"


@pytest.mark.asyncio
async def test_build_response_input_payload_flattens_nested_forward_messages() -> None:
    call_api = AsyncMock(
        side_effect=[
            {
                "messages": [
                    Message([MessageSegment.text("外层一")]),
                    Message([MessageSegment.forward("222")]),
                ]
            },
            {
                "messages": [
                    Message([MessageSegment.text("内层一")]),
                    Message([MessageSegment.text("内层二")]),
                ]
            },
        ]
    )
    bot = cast(Bot, SimpleNamespace(call_api=call_api))
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("111")])

    payload = await build_response_input_payload(
        bot,
        event,
        media_service=SimpleNamespace(),
    )

    assert payload.input_kind == "forward"
    assert payload.source_message_id == "111"
    assert [shape_to_summary_text(shape) for shape in payload.split_shapes] == [
        "外层一",
        "内层一",
        "内层二",
    ]
    assert call_api.await_args_list[0].kwargs == {
        "message_id": "111",
    }
    assert call_api.await_args_list[1].kwargs == {
        "message_id": "222",
    }


@pytest.mark.asyncio
async def test_build_response_input_payload_rejects_forward_depth_over_limit() -> None:
    nested_id_chain = [str(100 + index) for index in range(FORWARD_BATCH_MAX_DEPTH + 1)]
    call_api = AsyncMock(
        side_effect=[
            {
                "messages": [
                    Message([MessageSegment.forward(nested_id_chain[index + 1])])
                ]
            }
            for index in range(FORWARD_BATCH_MAX_DEPTH)
        ]
    )
    bot = cast(Bot, SimpleNamespace(call_api=call_api))
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward(nested_id_chain[0])])

    with pytest.raises(WordbankUserError) as exc_info:
        await build_response_input_payload(
            bot,
            event,
            media_service=SimpleNamespace(),
        )

    assert exc_info.value.key == "wordbank.error.forward_message_too_deep"


@pytest.mark.asyncio
async def test_build_forward_batch_payload_reuses_downloads_for_duplicate_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_api = AsyncMock(
        return_value={
            "messages": [
                Message(
                    [
                        MessageSegment(
                            "image",
                            {
                                "url": "https://example.test/shared.png",
                                "file": "shared.png",
                            },
                        )
                    ]
                ),
                Message(
                    [
                        MessageSegment(
                            "image",
                            {
                                "url": "https://example.test/shared.png",
                                "file": "shared.png",
                            },
                        )
                    ]
                ),
            ]
        }
    )
    fetch_image_bytes = AsyncMock(return_value=b"shared-bytes")
    ingest_image_bytes = AsyncMock(return_value=SimpleNamespace(canonical_id=17))
    bot = cast(Bot, SimpleNamespace(call_api=call_api))
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])
    media_service = SimpleNamespace(
        resolve_canonical_id_from_hints=lambda _hints: None,
        resolve_canonical_id=lambda *_args, **_kwargs: None,
        ingest_image_bytes=ingest_image_bytes,
    )
    monkeypatch.setattr(
        media_helpers,
        "fetch_image_bytes_with_retry",
        fetch_image_bytes,
    )

    payload = await build_forward_batch_payload(
        bot,
        event,
        media_service=media_service,
    )

    assert payload.node_count == 2
    assert [shape_to_summary_text(shape) for shape in payload.split_shapes] == [
        "[图片:17]",
        "[图片:17]",
    ]
    assert fetch_image_bytes.await_count == 1
    assert ingest_image_bytes.await_count == 1


@pytest.mark.asyncio
async def test_build_forward_batch_payload_keeps_text_when_image_download_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_api = AsyncMock(
        return_value={
            "messages": [
                Message(
                    [
                        MessageSegment.text("第一条"),
                        MessageSegment("image", {"url": "https://example.test/a.png"}),
                    ]
                ),
                Message([MessageSegment.text("第二条")]),
            ]
        }
    )
    fetch_image_bytes = AsyncMock(return_value=None)
    bot = cast(Bot, SimpleNamespace(call_api=call_api))
    event = build_group_message_event("", message_id=1)
    event.message = Message([MessageSegment.forward("7657605421581295285")])
    media_service = SimpleNamespace(
        resolve_canonical_id_from_hints=lambda _hints: None,
        resolve_canonical_id=lambda *_args, **_kwargs: None,
        ingest_image_bytes=AsyncMock(),
    )
    monkeypatch.setattr(
        media_helpers,
        "fetch_image_bytes_with_retry",
        fetch_image_bytes,
    )

    payload = await build_forward_batch_payload(
        bot,
        event,
        media_service=media_service,
    )

    assert [shape_to_summary_text(shape) for shape in payload.split_shapes] == [
        "第一条",
        "第二条",
    ]
    assert media_service.ingest_image_bytes.await_count == 0
