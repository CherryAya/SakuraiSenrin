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
    shape_from_response_text,
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
    created_by: str = "10001",
    created_at: int = 1_700_000_000,
    rule: dict[str, object] | None = None,
    response_mode: str = "normal",
    forward_source_message_id: str | None = None,
    forward_node_count: int = 0,
    status: str = "pending",
    reused_existing: bool = False,
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
        status=status,
        trigger_shape=trigger_shape,
        response_shape=response_shape,
        created_by=created_by,
        created_at=created_at,
        rule=rule,
        response_mode=response_mode,
        forward_source_message_id=forward_source_message_id,
        forward_node_count=forward_node_count,
        reused_existing=reused_existing,
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


def _message_text(message: Any) -> str:
    return "".join(
        str(segment.data.get("text", ""))
        for segment in message
        if segment.type == "text"
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
        "词条已提交审核\nID: 12\n状态: 待审核\n触发词: 晚安\n响应词:\n"
    )
    assert "\n范围: 当前群\n规则: 概率 1\n权重: 3\n管理员通过前不会触发。" in full_text
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
    assert full_text.startswith("词条已提交审核\nID: 12\n状态: 待审核\n触发词:\n")
    assert "\n响应词:\n" in full_text
    assert "\n范围: 当前群\n规则: 概率 1\n权重: 3\n管理员通过前不会触发。" in full_text
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
        "状态: 待审核\n"
        "触发词: 晚安\n"
        "响应词: 做个好梦\n"
        "范围: 当前群\n"
        "规则: 概率 1\n"
        "权重: 3\n"
        "管理员通过前不会触发。"
    )
    load_canonical_storage_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_add_result_message_warns_on_duplicate_pending_result() -> None:
    load_canonical_storage_bytes = AsyncMock()
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    result = _result(
        response_text="做个好梦",
        response_shape=shape_from_text("做个好梦"),
        created_by="10002",
        created_at=1_700_000_123,
        reused_existing=True,
    )

    message = await _build_add_result_message(
        result,
        locale="zh-CN",
        media_service=media_service,
    )

    assert str(message) == (
        "这个 trigger-response pair 已在待审核列表里，请不要重复添加。\n"
        "ID: 12\n"
        "状态: 待审核\n"
        "触发词: 晚安\n"
        "响应词: 做个好梦\n"
        "范围: 当前群\n"
        "规则: 概率 1\n"
        "权重: 3\n"
        "已复用现有待审词条，并再次通知管理员审核。"
    )
    load_canonical_storage_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_add_result_message_warns_on_duplicate_approved_result() -> None:
    load_canonical_storage_bytes = AsyncMock()
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=load_canonical_storage_bytes),
    )
    result = _result(
        response_text="做个好梦",
        response_shape=shape_from_text("做个好梦"),
        status="approved",
        reused_existing=True,
    )

    message = await _build_add_result_message(
        result,
        locale="zh-CN",
        media_service=media_service,
    )

    assert str(message) == (
        "这个 trigger-response pair 已存在并通过审核，请不要重复添加。\n"
        "ID: 12\n"
        "状态: 已通过\n"
        "触发词: 晚安\n"
        "响应词: 做个好梦\n"
        "范围: 当前群\n"
        "规则: 概率 1\n"
        "权重: 3\n"
        "该词条已可直接使用。"
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


def test_format_pending_approval_notice_preserves_raw_message_text() -> None:
    result = _result(
        response_text="原始文本",
        response_shape=combine_shapes(shape_from_text("做个好梦"), shape_from_image(7)),
    )

    notice = format_pending_approval_notice(
        result,
        event=_event(),
        locale="zh-CN",
    )

    assert "响应词: 原始文本" in notice
    assert "状态: 待审核" in notice
    assert "做个好梦" not in notice


def test_format_pending_approval_notice_summarizes_at_message() -> None:
    result = _result(
        response_text="你好 [@触发者]",
        response_shape=shape_from_response_text("你好 [@触发者]"),
    )

    notice = format_pending_approval_notice(
        result,
        event=_event(),
        locale="zh-CN",
    )

    assert "响应词: 你好 艾特触发者" in notice
    assert "响应词: 你好 [@触发者]" not in notice


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
    full_text = _message_text(message)
    assert full_text.startswith(
        "新增词条待审核\n回复 y / 通过 可通过\n回复 n / 拒绝 可驳回\n\nID: 12\n"
    )
    assert "状态: 待审核" in full_text
    assert "触发词: 晚安" in full_text
    assert "响应词: [图片:7]" in full_text
    assert "创建者: 10001" in full_text
    assert "提交时间: 2023-11-15 06:13" in full_text
    assert "范围: 当前群" in full_text
    assert "权重: 3" in full_text
    assert "规则: 概率 1" in full_text
    assert "响应模式: 普通响应" in full_text
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
    full_text = _message_text(message)
    assert full_text.startswith(
        "新增词条待审核\n回复 y / 通过 可通过\n回复 n / 拒绝 可驳回\n\nID: 12\n"
    )
    assert "状态: 待审核" in full_text
    assert "触发词: [图片:8]" in full_text
    assert "响应词: 做个好梦" in full_text
    assert "响应模式: 普通响应" in full_text
    assert sum(1 for segment in segments if segment.type == "image") == 0
    load_canonical_storage_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_pending_approval_notice_plan_entry_returns_raw_message_text() -> (
    None
):
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

    full_text = _message_text(message)
    assert "回复 y / 通过 可通过" in full_text
    assert "状态: 待审核" in full_text
    assert "触发词: [图片:8]" in full_text
    assert "规则: 概率 1" in full_text
    assert not any(segment.type == "image" for segment in message)


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
async def test_send_pending_approval_notice_embeds_detail_in_single_message() -> None:
    record_message_ref = AsyncMock(return_value=None)
    deliver_plan = AsyncMock(
        return_value=SimpleNamespace(results=({"message_id": 99},), used_forward=True),
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

    assert deliver_plan.await_count == 1
    await_args = deliver_plan.await_args
    assert await_args is not None
    plan = await_args.kwargs["plan"]
    assert plan.should_forward is False
    assert len(plan.messages) == 1
    detail_message = render_message_plan_input(plan.messages[0])
    detail_text = _message_text(detail_message)
    assert "新增词条待审核" in detail_text
    assert "回复 y / 通过 可通过" in detail_text
    assert "回复 n / 拒绝 可驳回" in detail_text
    assert "ID: 12" in str(detail_message)
    assert "响应词:" in str(detail_message)
    assert "[图片:7]" not in str(detail_message)
    assert any(segment.type == "image" for segment in detail_message)
    assert record_message_ref.await_count == 1


@pytest.mark.asyncio
async def test_send_notice_embeds_forward_whole_mode() -> None:
    record_message_ref = AsyncMock(return_value=None)
    deliver_plan = AsyncMock(
        return_value=SimpleNamespace(results=({"message_id": 88},), used_forward=True),
    )
    service = cast(Any, SimpleNamespace(record_message_ref=record_message_ref))
    result = _result(
        response_text="原始合并转发",
        response_shape=shape_from_text("原始合并转发"),
        response_mode="forward_whole",
        forward_source_message_id="456",
        forward_node_count=2,
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
            media_service=None,
        )
    finally:
        approval_module.config.SUPERUSERS = original_superusers
        approval_module.deliver_message_plan = original_deliver

    assert deliver_plan.await_count == 1
    await_args = deliver_plan.await_args
    assert await_args is not None
    plan = await_args.kwargs["plan"]
    assert plan.should_forward is True
    assert len(plan.messages) == 2
    detail_message = render_message_plan_input(plan.messages[0])
    forward_message = render_message_plan_input(plan.messages[1])
    detail_text = _message_text(detail_message)
    assert "新增词条待审核" in detail_text
    assert "响应词: 原始合并转发" in detail_text
    assert "ID: 12" in str(detail_message)
    assert "响应词: 原始合并转发" in str(detail_message)
    forward_segments = list(forward_message)
    assert len(forward_segments) == 1
    assert forward_segments[0].type == "forward"
    assert forward_segments[0].data["id"] == "456"
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
    assert "回复我发送：通过 1 2 5-8、拒绝 全部" in notice
    assert "触发词: 晚安" in notice
    assert "创建者: 10001" in notice
    assert "提交时间: 2023-11-15 06:13" in notice
    assert "范围: 当前群" in notice
    assert "权重: 3" in notice
    assert "规则: 概率 1" in notice
    assert "响应模式: 普通响应" in notice
    assert "待审数量: 2" in notice


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
    assert "回复我发送：通过 1 2 5-8、拒绝 全部" in full_text
    assert "触发词: test_forward" in full_text
    assert "创建者: 10001" in full_text
    assert "待审数量: 2" in full_text
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
    assert first_plan.should_forward is True
    assert len(first_plan.messages) == 3
    first_message = render_message_plan_input(first_plan.messages[0])
    assert "待审核词条" in str(first_message)
    assert record_message_ref.await_count == 2
    first_record = record_message_ref.await_args_list[0].kwargs
    assert first_record["context_type"] == "pending_batch"
    assert first_record["group_ids"] == (12, 23)


@pytest.mark.asyncio
async def test_batch_notice_sends_summary_then_forward_details() -> None:
    record_message_ref = AsyncMock(return_value=None)
    deliver_plan = AsyncMock(
        return_value=SimpleNamespace(results=({"message_id": 2},), used_forward=True),
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

    assert deliver_plan.await_count == 1
    await_args = deliver_plan.await_args
    assert await_args is not None
    plan = await_args.kwargs["plan"]
    assert plan.should_forward is True
    assert len(plan.messages) == 3
    summary_message = render_message_plan_input(plan.messages[0])
    first_detail = render_message_plan_input(plan.messages[1])
    second_detail = render_message_plan_input(plan.messages[2])
    assert "回复我发送：通过 1 2 5-8、拒绝 全部" in str(summary_message)
    assert "待审数量: 2" in str(summary_message)
    assert "序号: 1" in str(first_detail)
    assert "响应词:" in str(first_detail)
    assert any(segment.type == "image" for segment in first_detail)
    assert "序号: 2" in str(second_detail)
    assert "触发词:" in str(second_detail)
    assert any(segment.type == "image" for segment in second_detail)
    assert record_message_ref.await_count == 1


@pytest.mark.asyncio
async def test_record_batch_submission_uses_pending_context() -> None:
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
