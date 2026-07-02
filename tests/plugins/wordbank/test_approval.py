from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.event import MessageEvent
import pytest

from src.lib.message_plan import render_message_plan_input
from src.plugins.wordbank.handlers.approval import (
    build_add_result_message,
    build_add_result_plan_entry,
    build_pending_approval_notice_message,
    build_pending_approval_notice_plan_entry,
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
    trigger_text: str = "晚安",
    trigger_shape: MessageShape | None = None,
    response_text: str = "做个好梦",
    response_shape: MessageShape | None = None,
) -> WordbankAddResult:
    return WordbankAddResult(
        trigger_group_id=10,
        trigger_variant_id=11,
        response_item_id=12,
        trigger_text=trigger_text,
        response_text=response_text,
        scope="current_group",
        probability=1.0,
        weight=3,
        trigger_shape=trigger_shape,
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
    full_text = "".join(text_values)
    assert full_text.startswith(
        "词条已提交审核\nID: 12\n状态: pending\n触发: 晚安\n响应:\n"
    )
    assert (
        "\n范围: current_group\n概率: 1\n权重: 3\n管理员通过前不会触发。" in full_text
    )
    assert "做个好梦" in full_text
    assert any(segment.type == "image" for segment in segments)
    assert "消息回复如下" not in str(message)
    load_canonical_storage_bytes.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_build_add_result_message_rebuilds_trigger_and_response_shapes() -> None:
    load_canonical_storage_bytes = AsyncMock(return_value=b"image-bytes")
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    result = _result(
        trigger_text="[图片:8]",
        trigger_shape=shape_from_image(8),
        response_text="[图片:7]",
        response_shape=shape_from_image(7),
    )

    message = await build_add_result_message(
        result,
        locale="zh-CN",
        media_service=media_service,
    )

    segments = list(message)
    text_values = [str(segment) for segment in segments if segment.type == "text"]
    full_text = "".join(text_values)
    assert full_text.startswith("词条已提交审核\nID: 12\n状态: pending\n触发:\n")
    assert "\n响应:\n" in full_text
    assert (
        "\n范围: current_group\n概率: 1\n权重: 3\n管理员通过前不会触发。" in full_text
    )
    assert sum(1 for segment in segments if segment.type == "image") == 2
    assert "[图片:8]" not in str(message)
    assert "[图片:7]" not in str(message)
    assert [call.args for call in load_canonical_storage_bytes.await_args_list] == [
        (8,),
        (7,),
    ]


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


@pytest.mark.asyncio
async def test_build_add_result_plan_entry_renders_image_response() -> None:
    load_canonical_storage_bytes = AsyncMock(return_value=b"image-bytes")
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    result = _result(
        response_text="[图片:7]",
        response_shape=shape_from_image(7),
    )

    entry = await build_add_result_plan_entry(
        result,
        locale="zh-CN",
        media_service=media_service,
    )
    message = render_message_plan_input(entry)

    assert any(segment.type == "image" for segment in message)
    assert "[图片:7]" not in str(message)


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
    full_text = "".join(text_values)
    assert full_text.startswith("新增词条待审核\nID: 12\n触发: 晚安\n响应:\n")
    assert (
        "\n范围: current_group\n概率: 1\n权重: 3\n提交者: 10001\n来源群: 20001"
        in full_text
    )
    assert any(segment.type == "image" for segment in segments)
    assert "消息回复如下" not in str(message)
    load_canonical_storage_bytes.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_build_pending_approval_notice_message_rebuilds_image_trigger() -> None:
    load_canonical_storage_bytes = AsyncMock(return_value=b"image-bytes")
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    result = _result(
        trigger_text="[图片:8]",
        trigger_shape=shape_from_image(8),
        response_text="做个好梦",
        response_shape=shape_from_text("做个好梦"),
    )

    message = await build_pending_approval_notice_message(
        result,
        event=_event(),
        locale="zh-CN",
        media_service=media_service,
    )

    segments = list(message)
    text_values = [str(segment) for segment in segments if segment.type == "text"]
    full_text = "".join(text_values)
    assert full_text.startswith("新增词条待审核\nID: 12\n触发:\n")
    assert "\n响应: 做个好梦\n" in full_text
    assert sum(1 for segment in segments if segment.type == "image") == 1
    assert "[图片:8]" not in str(message)
    load_canonical_storage_bytes.assert_awaited_once_with(8)


@pytest.mark.asyncio
async def test_build_pending_approval_notice_plan_entry_renders_image_trigger() -> None:
    load_canonical_storage_bytes = AsyncMock(return_value=b"image-bytes")
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    result = _result(
        trigger_text="[图片:8]",
        trigger_shape=shape_from_image(8),
    )

    entry = await build_pending_approval_notice_plan_entry(
        result,
        event=_event(),
        locale="zh-CN",
        media_service=media_service,
    )
    message = render_message_plan_input(entry)

    assert any(segment.type == "image" for segment in message)
    assert "[图片:8]" not in str(message)


@pytest.mark.asyncio
async def test_send_pending_approval_notice_sends_all_superusers_concurrently() -> None:
    record_message_ref = AsyncMock(return_value=None)
    deliver_message = AsyncMock(
        side_effect=[{"message_id": 1}, {"message_id": 2}],
    )
    service = cast(
        Any,
        SimpleNamespace(record_message_ref=record_message_ref),
    )
    result = _result(response_shape=shape_from_text("做个好梦"))
    bot = cast(Any, SimpleNamespace())

    from src.plugins.wordbank.handlers import approval as approval_module

    original_superusers = approval_module.config.SUPERUSERS
    original_deliver = approval_module.deliver_single_message
    approval_module.config.SUPERUSERS = {"1", "2"}
    approval_module.deliver_single_message = deliver_message
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
        approval_module.deliver_single_message = original_deliver

    targets = [
        call.kwargs["target"].target_id for call in deliver_message.await_args_list
    ]
    assert sorted(targets) == ["1", "2"]
    assert record_message_ref.await_count == 2
