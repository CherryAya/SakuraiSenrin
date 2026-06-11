from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.event import MessageEvent
import pytest

from src.plugins.wordbank.handlers.approval import (
    build_add_result_message,
    format_pending_approval_notice,
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
        entry_id=12,
        trigger_text="晚安",
        response_text=response_text,
        trigger_mode="strict",
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
    assert any(segment.type == "text" for segment in segments)
    assert any(segment.type == "image" for segment in segments)
    load_canonical_storage_bytes.assert_awaited_once_with(7)


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
