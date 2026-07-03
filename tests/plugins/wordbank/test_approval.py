from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.event import MessageEvent
import pytest

from src.lib.message_plan import render_message_plan_input
from src.plugins.wordbank.handlers.approval import (
    build_add_result_plan_entry,
    build_pending_approval_notice_plan_entry,
    build_pending_batch_approval_notice_plan_entry,
    format_pending_approval_notice,
    format_pending_batch_approval_notice,
    record_batch_submission_approval_message,
    send_pending_approval_notice,
    send_pending_batch_approval_notice,
)
from src.plugins.wordbank.message_model import (
    MessageShape,
    combine_shapes,
    shape_from_image,
    shape_from_text,
)
from src.plugins.wordbank.services.core import WordbankAddResult
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.presentation import (
    WordbankBatchAddItemResult,
    WordbankBatchAddResult,
)
from tests.plugins.water.helpers import build_group_message_event


def _result(
    *,
    trigger_group_id: int = 10,
    trigger_variant_id: int = 11,
    response_item_id: int = 12,
    trigger_text: str = "晚安",
    trigger_shape: MessageShape | None = None,
    response_text: str = "做个好梦",
    response_shape: MessageShape | None = None,
) -> WordbankAddResult:
    return WordbankAddResult(
        trigger_group_id=trigger_group_id,
        trigger_variant_id=trigger_variant_id,
        response_item_id=response_item_id,
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


async def _build_add_result_message(*args: Any, **kwargs: Any) -> Any:
    return render_message_plan_input(await build_add_result_plan_entry(*args, **kwargs))


async def _build_pending_approval_notice_message(*args: Any, **kwargs: Any) -> Any:
    return render_message_plan_input(
        await build_pending_approval_notice_plan_entry(*args, **kwargs)
    )


async def _build_pending_batch_approval_notice_message(
    *args: Any, **kwargs: Any
) -> Any:
    return render_message_plan_input(
        await build_pending_batch_approval_notice_plan_entry(*args, **kwargs)
    )


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

    message = await _build_add_result_message(
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

    message = await _build_add_result_message(
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

    message = await _build_add_result_message(
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

    message = await _build_pending_approval_notice_message(
        result,
        event=_event(),
        locale="zh-CN",
        media_service=media_service,
    )

    segments = list(message)
    text_values = [str(segment) for segment in segments if segment.type == "text"]
    full_text = "".join(text_values)
    assert full_text.startswith("新增词条待审核\nID: 12\n")
    assert (
        "详细触发词 / 响应词见下一条合并转发。\n"
        "范围: current_group\n"
        "概率: 1\n"
        "权重: 3\n"
        "提交者: 10001\n"
        "来源群: 20001" in full_text
    )
    assert "请回复本条消息发送 y / approve / 通过" in full_text
    assert not any(segment.type == "image" for segment in segments)
    load_canonical_storage_bytes.assert_not_awaited()


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

    message = await _build_pending_approval_notice_message(
        result,
        event=_event(),
        locale="zh-CN",
        media_service=media_service,
    )

    segments = list(message)
    text_values = [str(segment) for segment in segments if segment.type == "text"]
    full_text = "".join(text_values)
    assert full_text.startswith("新增词条待审核\nID: 12\n")
    assert "详细触发词 / 响应词见下一条合并转发。" in full_text
    assert "请回复本条消息发送 y / approve / 通过" in full_text
    assert sum(1 for segment in segments if segment.type == "image") == 0
    load_canonical_storage_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_pending_approval_notice_plan_entry_returns_summary() -> None:
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

    assert not any(segment.type == "image" for segment in message)
    assert "详细触发词 / 响应词见下一条合并转发。" in str(message)


@pytest.mark.asyncio
async def test_send_pending_approval_notice_sends_all_superusers_concurrently() -> None:
    record_message_ref = AsyncMock(return_value=None)
    deliver_plan = AsyncMock(
        side_effect=[
            SimpleNamespace(results=({"message_id": 1},)),
            SimpleNamespace(results=({"message_id": 2},)),
        ],
    )
    service = cast(
        Any,
        SimpleNamespace(record_message_ref=record_message_ref),
    )
    result = _result(response_shape=shape_from_text("做个好梦"))
    bot = cast(Any, SimpleNamespace())

    from src.plugins.wordbank.handlers import approval as approval_module

    original_superusers = approval_module.config.SUPERUSERS
    original_deliver = approval_module.deliver_message_plan
    approval_module.config.SUPERUSERS = {"1", "2"}
    approval_module.deliver_message_plan = deliver_plan
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
        approval_module.deliver_message_plan = original_deliver

    targets = [call.kwargs["target"].target_id for call in deliver_plan.await_args_list]
    assert sorted(targets) == ["1", "2"]
    assert record_message_ref.await_count == 2


@pytest.mark.asyncio
async def test_send_pending_approval_notice_sends_detail_as_forward_to_admin() -> None:
    record_message_ref = AsyncMock(return_value=None)
    deliver_plan = AsyncMock(
        side_effect=[
            SimpleNamespace(results=({"message_id": 1},), used_forward=False),
            SimpleNamespace(results=({"message_id": 99},), used_forward=True),
        ],
    )
    load_canonical_storage_bytes = AsyncMock(return_value=b"image-bytes")
    service = cast(Any, SimpleNamespace(record_message_ref=record_message_ref))
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    result = _result(
        response_text="[图片:7]",
        response_shape=shape_from_image(7),
    )
    bot = cast(Any, SimpleNamespace())

    from src.plugins.wordbank.handlers import approval as approval_module

    original_superusers = approval_module.config.SUPERUSERS
    original_deliver = approval_module.deliver_message_plan
    approval_module.config.SUPERUSERS = {"1"}
    approval_module.deliver_message_plan = deliver_plan
    try:
        await send_pending_approval_notice(
            bot,
            service,
            event=_event(),
            result=result,
            locale="zh-CN",
            media_service=media_service,
        )
    finally:
        approval_module.config.SUPERUSERS = original_superusers
        approval_module.deliver_message_plan = original_deliver

    assert deliver_plan.await_count == 2
    summary_plan = deliver_plan.await_args_list[0].kwargs["plan"]
    detail_plan = deliver_plan.await_args_list[1].kwargs["plan"]
    assert summary_plan.force_forward is None
    assert "请回复本条消息发送 y / approve / 通过" in str(summary_plan.messages[0])
    assert "详细触发词 / 响应词见下一条合并转发。" in str(summary_plan.messages[0])
    assert detail_plan.force_forward is True
    assert detail_plan.fallback_nickname == "待审核词条"
    detail_message = render_message_plan_input(detail_plan.messages[0])
    assert any(segment.type == "image" for segment in detail_message)
    assert "晚安" in str(detail_message)
    assert record_message_ref.await_count == 1


def test_format_pending_batch_approval_notice_lists_all_pending_items() -> None:
    batch = WordbankBatchAddResult(
        total=2,
        success=2,
        failed=0,
        items=(
            WordbankBatchAddItemResult(index=1, ok=True, result=_result()),
            WordbankBatchAddItemResult(
                index=2,
                ok=True,
                result=_result(
                    trigger_text="晚安 2",
                    response_text="做个好梦 2",
                    response_shape=shape_from_text("做个好梦 2"),
                ),
            ),
        ),
    )

    notice = format_pending_batch_approval_notice(
        batch,
        event=_event(),
        locale="zh-CN",
    )

    assert "待审核词条" in notice
    assert "#12 [current_group] 晚安 => 做个好梦" in notice
    assert "#12 [current_group] 晚安 2 => 做个好梦 2" in notice


@pytest.mark.asyncio
async def test_build_pending_batch_approval_notice_message_embeds_image_shapes() -> (
    None
):
    load_canonical_storage_bytes = AsyncMock(return_value=b"image-bytes")
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    batch = WordbankBatchAddResult(
        total=2,
        success=2,
        failed=0,
        items=(
            WordbankBatchAddItemResult(
                index=1,
                ok=True,
                result=_result(
                    trigger_text="test_forward",
                    trigger_shape=shape_from_text("test_forward"),
                    response_text="[图片:3069]",
                    response_shape=shape_from_image(3069),
                ),
            ),
            WordbankBatchAddItemResult(
                index=2,
                ok=True,
                result=_result(
                    trigger_variant_id=22,
                    response_item_id=23,
                    trigger_text="[图片:3070]",
                    trigger_shape=shape_from_image(3070),
                    response_text="做个好梦",
                    response_shape=shape_from_text("做个好梦"),
                ),
            ),
        ),
    )

    message = await _build_pending_batch_approval_notice_message(
        batch,
        event=_event(),
        locale="zh-CN",
        media_service=media_service,
    )

    segments = list(message)
    full_text = "".join(str(segment) for segment in segments if segment.type == "text")
    assert "待审核词条" in full_text
    assert "回复我发送：通过 1 2 5-8，或拒绝 all" in full_text
    assert "详细触发词 / 响应词见下一条合并转发。" in full_text
    assert "test_forward" not in full_text
    assert "做个好梦" not in full_text
    assert sum(1 for segment in segments if segment.type == "image") == 0
    load_canonical_storage_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_pending_batch_approval_notice_records_pending_batch_context() -> (
    None
):
    record_message_ref = AsyncMock(return_value=None)
    deliver_plan = AsyncMock(
        side_effect=[
            SimpleNamespace(results=({"message_id": 1},)),
            SimpleNamespace(results=({"message_id": 2},)),
        ],
    )
    service = cast(Any, SimpleNamespace(record_message_ref=record_message_ref))
    batch = WordbankBatchAddResult(
        total=2,
        success=2,
        failed=0,
        items=(
            WordbankBatchAddItemResult(index=1, ok=True, result=_result()),
            WordbankBatchAddItemResult(
                index=2,
                ok=True,
                result=_result(
                    trigger_variant_id=22,
                    response_item_id=23,
                    trigger_text="晚安 2",
                    response_text="做个好梦 2",
                    response_shape=shape_from_text("做个好梦 2"),
                ),
            ),
        ),
    )
    bot = cast(Any, SimpleNamespace())

    from src.plugins.wordbank.handlers import approval as approval_module

    original_superusers = approval_module.config.SUPERUSERS
    original_deliver = approval_module.deliver_message_plan
    approval_module.config.SUPERUSERS = {"1", "2"}
    approval_module.deliver_message_plan = deliver_plan
    try:
        await send_pending_batch_approval_notice(
            bot,
            service,
            event=_event(),
            batch=batch,
            locale="zh-CN",
        )
    finally:
        approval_module.config.SUPERUSERS = original_superusers
        approval_module.deliver_message_plan = original_deliver

    assert deliver_plan.await_count == 2
    targets = [call.kwargs["target"].target_id for call in deliver_plan.await_args_list]
    assert sorted(targets) == ["1", "2"]
    first_plan = deliver_plan.await_args_list[0].kwargs["plan"]
    assert "待审核词条" in str(first_plan.messages[0])
    assert record_message_ref.await_count == 2
    first_record = record_message_ref.await_args_list[0].kwargs
    assert first_record["context_type"] == "pending_batch"
    assert first_record["group_ids"] == (12, 23)


@pytest.mark.asyncio
async def test_batch_notice_sends_summary_then_forward_details() -> None:
    record_message_ref = AsyncMock(return_value=None)
    deliver_plan = AsyncMock(
        side_effect=[
            SimpleNamespace(results=({"message_id": 1},), used_forward=False),
            SimpleNamespace(results=({"message_id": 2},), used_forward=True),
        ],
    )
    load_canonical_storage_bytes = AsyncMock(return_value=b"image-bytes")
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    service = cast(Any, SimpleNamespace(record_message_ref=record_message_ref))
    batch = WordbankBatchAddResult(
        total=2,
        success=2,
        failed=0,
        items=(
            WordbankBatchAddItemResult(
                index=1,
                ok=True,
                result=_result(
                    response_text="[图片:3069]",
                    response_shape=shape_from_image(3069),
                ),
            ),
            WordbankBatchAddItemResult(
                index=2,
                ok=True,
                result=_result(
                    trigger_variant_id=22,
                    response_item_id=23,
                    trigger_text="[图片:3070]",
                    trigger_shape=shape_from_image(3070),
                    response_text="做个好梦",
                    response_shape=shape_from_text("做个好梦"),
                ),
            ),
        ),
    )
    bot = cast(Any, SimpleNamespace())

    from src.plugins.wordbank.handlers import approval as approval_module

    original_superusers = approval_module.config.SUPERUSERS
    original_deliver = approval_module.deliver_message_plan
    approval_module.config.SUPERUSERS = {"1"}
    approval_module.deliver_message_plan = deliver_plan
    try:
        await send_pending_batch_approval_notice(
            bot,
            service,
            event=_event(),
            batch=batch,
            locale="zh-CN",
            media_service=media_service,
        )
    finally:
        approval_module.config.SUPERUSERS = original_superusers
        approval_module.deliver_message_plan = original_deliver

    assert deliver_plan.await_count == 2
    summary_plan = deliver_plan.await_args_list[0].kwargs["plan"]
    detail_plan = deliver_plan.await_args_list[1].kwargs["plan"]
    assert summary_plan.force_forward is None
    assert "回复我发送：通过 1 2 5-8，或拒绝 all" in str(summary_plan.messages[0])
    assert "详细触发词 / 响应词见下一条合并转发。" in str(summary_plan.messages[0])
    assert detail_plan.force_forward is True
    assert detail_plan.fallback_nickname == "待审核词条"
    assert len(detail_plan.messages) == 2
    first_detail = render_message_plan_input(detail_plan.messages[0])
    second_detail = render_message_plan_input(detail_plan.messages[1])
    assert any(segment.type == "image" for segment in first_detail)
    assert any(segment.type == "image" for segment in second_detail)
    assert record_message_ref.await_count == 1


@pytest.mark.asyncio
async def test_record_batch_submission_approval_message_uses_pending_batch_context() -> (
    None
):
    record_message_ref = AsyncMock(return_value=None)
    service = cast(Any, SimpleNamespace(record_message_ref=record_message_ref))
    batch = WordbankBatchAddResult(
        total=2,
        success=2,
        failed=0,
        items=(
            WordbankBatchAddItemResult(index=1, ok=True, result=_result()),
            WordbankBatchAddItemResult(
                index=2,
                ok=True,
                result=_result(
                    trigger_variant_id=22,
                    response_item_id=23,
                    trigger_text="晚安 2",
                    response_text="做个好梦 2",
                    response_shape=shape_from_text("做个好梦 2"),
                ),
            ),
        ),
    )

    await record_batch_submission_approval_message(
        service,
        event=_event(),
        batch=batch,
        send_result={"message_id": 99},
    )

    record_message_ref.assert_awaited_once()
    await_args = record_message_ref.await_args
    assert await_args is not None
    kwargs = await_args.kwargs
    assert kwargs["message_id"] == "99"
    assert kwargs["context_type"] == "pending_batch"
    assert kwargs["message_type"] == "submission_batch"
    assert kwargs["group_ids"] == (12, 23)
