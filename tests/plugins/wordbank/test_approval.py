from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.event import MessageEvent
import pytest

from src.plugins.wordbank.handlers.approval import (
    build_add_result_message,
    build_pending_approval_notice_message,
    format_pending_approval_notice,
    send_pending_approval_notice,
)
from src.plugins.wordbank.message_model import (
    MessageShape,
    combine_shapes,
    shape_from_image,
    shape_from_text,
)
from src.plugins.wordbank.services.core import WordbankAddResult
from src.plugins.wordbank.services.media import WordbankMediaService
from tests.plugins.water.helpers import build_group_message_event


def _result(
    *,
    response_text: str = "做个好梦",
    response_shape: MessageShape | None = None,
) -> WordbankAddResult:
    return WordbankAddResult(
        trigger_group_id=10,
        trigger_variant_id=11,
        response_item_id=12,
        trigger_text="晚安",
        response_text=response_text,
        scope="current_group",
        probability=1.0,
        weight=3,
        response_shape=response_shape,
    )


def _event() -> MessageEvent:
    return build_group_message_event("#wordbank add 晚安 => 做个好梦")


@pytest.mark.asyncio
async def test_build_add_result_message_rebuilds_shape_with_image() -> None:
    load_canonical_storage_bytes = AsyncMock(return_value=b"image-bytes")
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    result = _result(
        response_text="做个好梦 [图片:7]",
        response_shape=combine_shapes(shape_from_text("做个好梦"), shape_from_image(7)),
    )

    message = await build_add_result_message(
        result,
        locale="zh-CN",
        media_service=media_service,
    )

    segments = list(message)
    text_values = [str(segment) for segment in segments if segment.type == "text"]
    assert text_values[0].endswith("触发: 晚安\n响应:\n")
    assert text_values[-1].startswith("\n范围: current_group\n概率: 1\n权重: 3\n")
    assert any(segment.type == "image" for segment in segments)
    assert "消息回复如下" not in str(message)
    load_canonical_storage_bytes.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_build_add_result_message_keeps_plain_text_response() -> None:
    load_canonical_storage_bytes = AsyncMock()
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    result = _result(
        response_text="做个好梦",
        response_shape=shape_from_text("做个好梦"),
    )

    message = await build_add_result_message(
        result,
        locale="zh-CN",
        media_service=media_service,
    )

    assert str(message) == (
        "词条已提交审核\n"
        "ID: 12\n"
        "状态: pending\n"
        "触发: 晚安\n"
        "响应: 做个好梦\n"
        "范围: current_group\n"
        "概率: 1\n"
        "权重: 3\n"
        "管理员通过前不会触发。"
    )
    load_canonical_storage_bytes.assert_not_awaited()


def test_format_pending_approval_notice_uses_shape_summary() -> None:
    result = _result(
        response_text="原始文本",
        response_shape=combine_shapes(shape_from_text("做个好梦"), shape_from_image(7)),
    )

    notice = format_pending_approval_notice(
        result,
        event=_event(),
        locale="zh-CN",
    )

    assert "做个好梦 [图片:7]" in notice
    assert "原始文本" not in notice


@pytest.mark.asyncio
async def test_build_pending_approval_notice_message_embeds_image_response() -> None:
    load_canonical_storage_bytes = AsyncMock(return_value=b"image-bytes")
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    result = _result(
        response_text="[图片:7]",
        response_shape=shape_from_image(7),
    )

    message = await build_pending_approval_notice_message(
        result,
        event=_event(),
        locale="zh-CN",
        media_service=media_service,
    )

    segments = list(message)
    text_values = [str(segment) for segment in segments if segment.type == "text"]
    assert text_values[0].endswith("触发: 晚安\n响应:\n")
    assert text_values[-1].startswith(
        "\n范围: current_group\n概率: 1\n权重: 3\n提交者: 10001\n来源群: 20001"
    )
    assert any(segment.type == "image" for segment in segments)
    assert "消息回复如下" not in str(message)
    load_canonical_storage_bytes.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_send_pending_approval_notice_sends_all_superusers_concurrently() -> None:
    calls: list[int] = []

    class _Bot:
        async def send_private_msg(
            self, *, user_id: int, message: object
        ) -> dict[str, int]:
            calls.append(user_id)
            return {"message_id": user_id}

    record_message_ref = AsyncMock(return_value=None)
    service = cast(
        Any,
        SimpleNamespace(record_message_ref=record_message_ref),
    )
    result = _result(response_shape=shape_from_text("做个好梦"))
    bot = cast(Any, _Bot())

    from src.plugins.wordbank.handlers import approval as approval_module

    original_superusers = approval_module.config.SUPERUSERS
    approval_module.config.SUPERUSERS = {"1", "2"}
    try:
        await send_pending_approval_notice(
            bot,
            service,
            event=_event(),
            result=result,
            locale="zh-CN",
            media_service=None,
        )
    finally:
        approval_module.config.SUPERUSERS = original_superusers

    assert sorted(calls) == [1, 2]
    assert record_message_ref.await_count == 2
